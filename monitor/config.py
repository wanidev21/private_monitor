"""설정 파일(config.json) 로드/검증/저장.

collector, 웹서버, tkinter, pystray 스레드가 동시에 접근하므로 모든 접근은 Lock으로 보호하고,
저장은 임시 파일 작성 후 os.replace로 교체하는 원자적 쓰기로 수행한다.
"""
import copy
import json
import logging
import os
import re
import tempfile
import threading
import time

from monitor import paths

log = logging.getLogger(__name__)

# 작업표시줄 오버레이 디자인 (모든 모니터 공통). 트레이 아이콘 표시 방식 설정은 없다 — 오버레이만 사용
OVERLAY_LOOKS = ("label", "badge", "gauge", "ring", "spark", "text")

DEFAULTS = {
    "web_port": 18099,
    "update_interval_sec": 2,
    "alerts_enabled": True,
    "alert_thresholds": {
        "cpu_percent": 90,
        "cpu_duration_sec": 10,
        "ram_percent": 90,
        "gpu_temp": 85,
        "disk_percent": 95,
    },
    "auto_start": True,
    "tray_auto_promote": True,
    "overlay_icon_look": "label",
    "overlay_icon_size": 32,
    "overlay_font": "Segoe UI Semibold",
    "tray_visible_items": ["cpu", "ram", "gpu", "gpu_temp", "net_down", "net_up"],
    "ui_theme": {
        "bg": "#1E1E1E",
        "fg": "#E0E0E0",
        "fg_dim": "#888888",
        "accent_cpu": "#4FC3F7",
        "accent_ram": "#81C784",
        "accent_gpu": "#FFB74D",
        "accent_net_up": "#EF5350",
        "accent_net_down": "#42A5F5",
        "accent_disk": "#CE93D8",
        "graph_bg": "#2A2A2A",
        "section_gap": 12,
        "font_family": "Segoe UI",
        "font_size_title": 11,
        "font_size_value": 10,
        "font_size_small": 9,
    },
}


# ---------------------------------------------------------------------------
# 값 검증 규칙. 각 규칙은 정규화된 값을 반환하거나 ValueError를 던진다.
# ---------------------------------------------------------------------------

def _int(lo: int, hi: int):
    def check(value):
        if isinstance(value, bool) or not isinstance(value, (int, float)) or value != int(value):
            raise ValueError("정수여야 합니다")
        value = int(value)
        if not lo <= value <= hi:
            raise ValueError(f"{lo}~{hi} 범위여야 합니다")
        return value
    return check


def _bool(value):
    if not isinstance(value, bool):
        raise ValueError("true/false여야 합니다")
    return value


def _choice(options):
    def check(value):
        if value not in options:
            raise ValueError(f"{', '.join(options)} 중 하나여야 합니다")
        return value
    return check


_COLOR_RE = re.compile(r"^#[0-9A-Fa-f]{6}$")


def _color(value):
    if not isinstance(value, str) or not _COLOR_RE.match(value):
        raise ValueError("#RRGGBB 형식이어야 합니다")
    return value.upper()


def _font_family(value):
    if not isinstance(value, str) or not value.strip() or len(value) > 100:
        raise ValueError("1~100자 문자열이어야 합니다")
    return value.strip()


_SIMPLE_ITEMS = ("cpu", "ram", "gpu", "gpu_temp", "net_down", "net_up")
_DISK_ITEM_RE = re.compile(r"^disk:([A-Za-z]):\\$")


def _tray_items(value):
    """트레이 표시 항목 목록. 구버전 'network'는 net_down/net_up으로 치환, 중복/알 수 없는 항목은 제거."""
    if not isinstance(value, list):
        raise ValueError("배열이어야 합니다")
    result = []
    for item in value:
        if not isinstance(item, str):
            raise ValueError("항목은 문자열이어야 합니다")
        if item == "network":
            candidates = ["net_down", "net_up"]
        elif item in _SIMPLE_ITEMS:
            candidates = [item]
        else:
            m = _DISK_ITEM_RE.match(item)
            if not m:
                log.warning("unknown tray item ignored: %r", item)
                continue
            candidates = [f"disk:{m.group(1).upper()}:\\"]
        for c in candidates:
            if c not in result:
                result.append(c)
    return result


SCHEMA = {
    "web_port": _int(1024, 65535),
    "update_interval_sec": _int(1, 10),
    "alerts_enabled": _bool,
    "alert_thresholds": {
        "cpu_percent": _int(1, 100),
        "cpu_duration_sec": _int(1, 600),
        "ram_percent": _int(1, 100),
        "gpu_temp": _int(30, 110),
        "disk_percent": _int(1, 100),
    },
    "auto_start": _bool,
    "tray_auto_promote": _bool,
    "overlay_icon_look": _choice(OVERLAY_LOOKS),
    "overlay_icon_size": _int(24, 40),
    "overlay_font": _font_family,
    "tray_visible_items": _tray_items,
    "ui_theme": {
        "bg": _color,
        "fg": _color,
        "fg_dim": _color,
        "accent_cpu": _color,
        "accent_ram": _color,
        "accent_gpu": _color,
        "accent_net_up": _color,
        "accent_net_down": _color,
        "accent_disk": _color,
        "graph_bg": _color,
        "section_gap": _int(0, 50),
        "font_family": _font_family,
        "font_size_title": _int(6, 32),
        "font_size_value": _int(6, 32),
        "font_size_small": _int(6, 32),
    },
}


def _normalize(schema: dict, defaults: dict, data: dict, strict: bool, prefix: str = "") -> dict:
    """스키마에 맞춰 값을 검증/정규화. 없는 키는 기본값, 알 수 없는 키는 버린다.

    strict=True: 잘못된 값이 있으면 ValueError (웹에서 들어온 변경 요청)
    strict=False: 잘못된 값은 경고 로그 후 기본값으로 대체 (파일 로드)
    """
    result = {}
    for key, rule in schema.items():
        name = prefix + key
        if key not in data:
            result[key] = copy.deepcopy(defaults[key])
            continue
        value = data[key]
        try:
            if isinstance(rule, dict):
                if not isinstance(value, dict):
                    raise ValueError("객체여야 합니다")
                result[key] = _normalize(rule, defaults[key], value, strict, name + ".")
            else:
                result[key] = rule(value)
        except ValueError as e:
            if strict:
                if str(e).startswith(name + ".") or str(e).startswith(name + ":"):
                    raise
                raise ValueError(f"{name}: {e}") from None
            log.warning("invalid config %s=%r (%s); using default", name, value, e)
            result[key] = copy.deepcopy(defaults[key])
    return result


def _deep_merge(base: dict, changes: dict) -> dict:
    """base에 changes를 덮어쓴 새 dict. dict끼리만 재귀 병합하고 리스트 등은 통째로 교체."""
    merged = copy.deepcopy(base)
    for key, value in changes.items():
        if isinstance(value, dict) and isinstance(merged.get(key), dict):
            merged[key] = _deep_merge(merged[key], value)
        else:
            merged[key] = copy.deepcopy(value)
    return merged


class Config:
    """config.json 래퍼. `config.web_port`처럼 속성으로 읽고, `update()`로 변경/저장한다.

    속성으로 읽은 dict/list는 복사본이므로 수정해도 설정에 반영되지 않는다.
    """

    def __init__(self):
        self._lock = threading.RLock()
        self._version = 0
        self._data = self._load()

    # --- 읽기 ---------------------------------------------------------------

    def __getattr__(self, name):
        # __init__ 이전이나 내부 속성 조회는 일반 규칙을 따른다
        if name.startswith("_") or name not in DEFAULTS:
            raise AttributeError(name)
        with self._lock:
            return copy.deepcopy(self._data[name])

    @property
    def version(self) -> int:
        """설정이 바뀔 때마다 1씩 증가. 트레이/팝업이 이전 값과 비교해 재적용 여부를 판단."""
        with self._lock:
            return self._version

    def snapshot(self) -> dict:
        with self._lock:
            return copy.deepcopy(self._data)

    # --- 쓰기 ---------------------------------------------------------------

    def update(self, changes: dict) -> bool:
        """일부 키만 담은 dict로 설정 변경. 검증 실패 시 ValueError, 저장 실패 시 OSError.

        실제로 값이 바뀌었으면 True. 실패하면 메모리/파일 모두 변경되지 않는다.
        """
        if not isinstance(changes, dict):
            raise ValueError("설정 변경은 객체여야 합니다")
        with self._lock:
            merged = _deep_merge(self._data, changes)
            new_data = _normalize(SCHEMA, DEFAULTS, merged, strict=True)
            if new_data == self._data:
                return False
            self._write(new_data)
            self._data = new_data
            self._version += 1
            return True

    def set(self, key: str, value) -> bool:
        return self.update({key: value})

    # --- 파일 I/O -----------------------------------------------------------

    def _load(self) -> dict:
        path = paths.get_config_path()
        if not os.path.exists(path):
            data = copy.deepcopy(DEFAULTS)
            self._try_write(data)
            return data

        try:
            with open(path, "r", encoding="utf-8") as f:
                raw = json.load(f)
        except OSError:
            log.exception("cannot read %s; using defaults without saving", path)
            return copy.deepcopy(DEFAULTS)
        except ValueError:  # JSONDecodeError, UnicodeDecodeError
            raw = None

        if not isinstance(raw, dict):
            backup = path + ".bak"
            log.error("config file is corrupted; moved to %s and recreated with defaults", backup)
            try:
                os.replace(path, backup)
            except OSError:
                log.exception("cannot back up corrupted config")
            data = copy.deepcopy(DEFAULTS)
            self._try_write(data)
            return data

        data = _normalize(SCHEMA, DEFAULTS, raw, strict=False)
        if data != raw:  # 누락 키 채움, 구버전 값 치환, 잘못된 값 교정 → 파일에도 반영
            self._try_write(data)
        return data

    def _try_write(self, data: dict) -> None:
        try:
            self._write(data)
        except OSError:
            log.exception("cannot save config")

    @staticmethod
    def _write(data: dict) -> None:
        path = paths.get_config_path()
        fd, tmp = tempfile.mkstemp(prefix=".config-", suffix=".tmp", dir=os.path.dirname(path))
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as f:
                json.dump(data, f, ensure_ascii=False, indent=4)
                f.flush()
                os.fsync(f.fileno())
            # 백신/인덱서가 잠깐 파일을 잡고 있으면 PermissionError가 날 수 있어 몇 번 재시도
            for attempt in range(5):
                try:
                    os.replace(tmp, path)
                    break
                except PermissionError:
                    if attempt == 4:
                        raise
                    time.sleep(0.05)
        except BaseException:
            try:
                os.remove(tmp)
            except OSError:
                pass
            raise


def main():
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s: %(message)s")
    config = Config()
    print(f"config file: {paths.get_config_path()}")
    print(json.dumps(config.snapshot(), ensure_ascii=False, indent=4))


if __name__ == "__main__":
    main()
