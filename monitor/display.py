"""표시용 문자열/색상 계산. UI 프레임워크와 무관한 순수 함수만 둔다 (트레이/팝업/웹 공통)."""
from collections import namedtuple

GREEN = "#4CAF50"
ORANGE = "#FF9800"
RED = "#F44336"
GRAY = "#9E9E9E"

TOOLTIP_MAX = 127  # Windows 트레이 툴팁 szTip[128] (널 포함)
EMPTY_TOOLTIP = "PC Monitor (좌클릭: 상세 / 우클릭: 메뉴)"
WAITING_TOOLTIP = "PC Monitor (수집 중...)"


def level_color(value: float | None) -> str:
    """부하 색상. 값이 없으면 회색."""
    if value is None:
        return GRAY
    if value <= 50:
        return GREEN
    if value <= 80:
        return ORANGE
    return RED


def format_rate(kb_s: float, compact: bool = False) -> str:
    """KB/s 값을 사람이 읽기 좋은 단위로. compact: '125K', '2.0M'."""
    if kb_s < 1024:
        return f"{kb_s:.0f}K" if compact else f"{kb_s:.0f} KB/s"
    mb_s = kb_s / 1024
    if mb_s < 1024:
        return f"{mb_s:.1f}M" if compact else f"{mb_s:.1f} MB/s"
    gb_s = mb_s / 1024
    return f"{gb_s:.1f}G" if compact else f"{gb_s:.1f} GB/s"


def format_size_gb(gb: float) -> str:
    """16.0 → '16', 31.8 → '31.8'."""
    return f"{gb:.0f}" if gb == int(gb) else f"{gb:.1f}"


def disk_letter(mount: str) -> str:
    return mount[:1].upper()


def find_disk(data: dict, mount: str) -> dict | None:
    for disk in data.get("disks") or []:
        if disk["mount"].upper() == mount.upper():
            return disk
    return None


def _circle_tooltip_parts(data: dict, items: list[str], compact: bool) -> list[str]:
    parts = []
    grouped = set()
    for item in items:
        if item == "cpu":
            cpu = data.get("cpu")
            parts.append(f"CPU: {cpu['percent']:.0f}%" if cpu else "CPU: N/A")

        elif item == "ram":
            ram = data.get("ram")
            if not ram:
                parts.append("RAM: N/A")
            elif compact:
                parts.append(f"RAM: {ram['percent']:.0f}%")
            else:
                parts.append(f"RAM: {ram['used_gb']:.1f}/{format_size_gb(ram['total_gb'])}GB ({ram['percent']:.0f}%)")

        elif item in ("gpu", "gpu_temp"):
            if "gpu" in grouped:
                continue
            grouped.add("gpu")
            gpu = data.get("gpu")
            if not gpu:
                parts.append("GPU: N/A")
                continue
            values = []
            if "gpu" in items:
                values.append(f"{gpu['percent']}%")
            if "gpu_temp" in items:
                values.append(f"{gpu['temp']}°C")
            parts.append("GPU: " + " ".join(values))

        elif item in ("net_down", "net_up"):
            if "net" in grouped:
                continue
            grouped.add("net")
            net = data.get("network")
            if not net:
                parts.append("NET: N/A")
                continue
            values = []
            if "net_down" in items:
                values.append("↓" + format_rate(net["download_kb_s"], compact=True))
            if "net_up" in items:
                values.append("↑" + format_rate(net["upload_kb_s"], compact=True))
            parts.append(" ".join(values))

        elif item.startswith("disk:"):
            disk = find_disk(data, item[5:])
            if disk is None:  # 분리된 드라이브는 표시만 건너뜀
                continue
            letter = disk_letter(disk["mount"])
            parts.append(f"{letter}:{disk['percent']:.0f}%" if compact else f"Disk {letter}: {disk['percent']:.0f}%")
    return parts


def build_circle_tooltip(data: dict | None, items: list[str]) -> str:
    """circle 모드 툴팁. 한 줄에 두 항목씩, 127자를 넘으면 축약 → 그래도 넘으면 자름."""
    if not items:
        return EMPTY_TOOLTIP
    if data is None:
        return WAITING_TOOLTIP
    text = ""
    for compact in (False, True):
        parts = _circle_tooltip_parts(data, items, compact)
        if not parts:
            return EMPTY_TOOLTIP
        text = "\n".join(" | ".join(parts[i:i + 2]) for i in range(0, len(parts), 2))
        if len(text) <= TOOLTIP_MAX:
            return text
    return text[:TOOLTIP_MAX]


# ---------------------------------------------------------------------------
# 숫자 아이콘 (label / badge 모드)
# ---------------------------------------------------------------------------

IconFace = namedtuple("IconFace", "label text color")

NET_UNITS = ("KB", "MB", "GB")
_NET_UNIT_FLOOR_KB = {"KB": 0, "MB": 100, "GB": 100 * 1024}
NET_UNIT_HYSTERESIS = 0.9


def _natural_net_unit(kb_s: float) -> str:
    if kb_s < 100:
        return "KB"
    if kb_s < 100 * 1024:
        return "MB"
    return "GB"


def choose_net_unit(kb_s: float, prev_unit: str | None = None) -> str:
    """네트워크 아이콘 단위. 올릴 때는 즉시, 내릴 때는 현재 단위 하한의 90% 미만일 때만 (경계 깜빡임 방지)."""
    unit = _natural_net_unit(kb_s)
    if prev_unit not in NET_UNITS or NET_UNITS.index(unit) >= NET_UNITS.index(prev_unit):
        return unit
    if kb_s >= _NET_UNIT_FLOOR_KB[prev_unit] * NET_UNIT_HYSTERESIS:
        return prev_unit
    return unit


def _two_char_amount(value: float) -> str:
    """1 미만은 '.1'~'.9' (버림, 최소 .1), 이상은 정수 최대 99."""
    if value < 1:
        return "." + str(max(1, int(value * 10)))
    return str(min(int(value), 99))


def format_net_icon_value(kb_s: float, unit: str) -> str:
    if unit == "KB":
        return str(min(int(kb_s), 99))
    if unit == "MB":
        return _two_char_amount(kb_s / 1024)
    return _two_char_amount(kb_s / 1024 / 1024)


def item_label(item: str) -> str:
    if item.startswith("disk:"):
        return disk_letter(item[5:]) + ":"
    return {"cpu": "CPU", "ram": "RAM", "gpu": "GPU", "gpu_temp": "G°C"}.get(item, item[:3].upper())


def item_value(item: str, data: dict) -> float | None:
    """비율/온도 항목의 값. 없으면 None."""
    if item == "cpu":
        cpu = data.get("cpu")
        return cpu["percent"] if cpu else None
    if item == "ram":
        ram = data.get("ram")
        return ram["percent"] if ram else None
    if item in ("gpu", "gpu_temp"):
        gpu = data.get("gpu")
        if not gpu:
            return None
        return gpu["percent"] if item == "gpu" else gpu["temp"]
    if item.startswith("disk:"):
        disk = find_disk(data, item[5:])
        return disk["percent"] if disk else None
    return None


def icon_face(item: str, data: dict | None, theme: dict, prev_net_unit: str | None = None):
    """숫자 아이콘에 그릴 (라벨, 숫자, 색상)과 네트워크 단위 상태.

    반환: (IconFace, net_unit | None). net_unit은 다음 호출의 prev_net_unit으로 넘긴다.
    """
    if item in ("net_down", "net_up"):
        arrow = "↓" if item == "net_down" else "↑"
        net = data.get("network") if data else None
        if not net:
            return IconFace(arrow + (prev_net_unit or "KB"), "--", GRAY), prev_net_unit
        kb_s = net["download_kb_s"] if item == "net_down" else net["upload_kb_s"]
        unit = choose_net_unit(kb_s, prev_net_unit)
        color = theme["accent_net_down"] if item == "net_down" else theme["accent_net_up"]
        return IconFace(arrow + unit, format_net_icon_value(kb_s, unit), color), unit

    label = item_label(item)
    value = item_value(item, data) if data else None
    if value is None:
        return IconFace(label, "--", GRAY), None
    return IconFace(label, str(min(int(round(value)), 99)), level_color(value)), None


def format_amount_mb(mb: float) -> str:
    return f"{mb:.0f} MB" if mb < 1024 else f"{mb / 1024:.1f} GB"


def format_disk_rate(mb_s: float | None) -> str:
    if mb_s is None:
        return "-"
    return f"{mb_s:.1f}MB/s" if mb_s < 10 else f"{mb_s:.0f}MB/s"


def icon_tooltip(item: str, data: dict | None) -> str:
    """숫자 아이콘별 상세 툴팁."""
    if data is None:
        return WAITING_TOOLTIP
    text = _icon_tooltip(item, data)
    return text[:TOOLTIP_MAX]


# ---------------------------------------------------------------------------
# 작업표시줄 오버레이 디자인
# ---------------------------------------------------------------------------

# (config 값, 메뉴/대시보드 표시 이름). 순서는 config.OVERLAY_LOOKS와 같아야 한다
OVERLAY_LOOK_LABELS = (
    ("label", "라벨형"),
    ("badge", "배지형"),
    ("gauge", "게이지형"),
    ("ring", "원형 링"),
    ("spark", "미니 그래프형"),
    ("text", "두 줄 텍스트"),
)

_TEXT_LABELS = {"cpu": "CPU", "ram": "RAM", "gpu": "GPU", "gpu_temp": "G°C", "net_down": "↓", "net_up": "↑"}


def text_design_parts(item: str, data: dict | None) -> tuple[str, str, str | None]:
    """두 줄 텍스트 디자인의 (라벨, 값, 값 색상 | None=기본 글자색). 비율/온도가 80 초과면 부하 색."""
    label = _TEXT_LABELS.get(item) or item_label(item)
    if item in ("net_down", "net_up"):
        net = data.get("network") if data else None
        if not net:
            return label, "--", None
        return label, format_rate(net["download_kb_s" if item == "net_down" else "upload_kb_s"], compact=True), None
    value = item_value(item, data) if data else None
    if value is None:
        return label, "--", None
    text = f"{value:.0f}°" if item == "gpu_temp" else f"{value:.0f}%"
    return label, text, (level_color(value) if value > 80 else None)


# ---------------------------------------------------------------------------
# 상세 팝업
# ---------------------------------------------------------------------------

SECTIONS_WITH_SUBLINE = ("ram", "gpu", "net")


def popup_sections(items: list[str], data: dict | None) -> list[str]:
    """팝업에 표시할 섹션 순서: cpu, ram, gpu, 디스크(선택 순서), net.

    gpu는 gpu/gpu_temp 중 하나라도, net은 net_down/net_up 중 하나라도 선택되면 표시.
    분리된 드라이브는 건너뛴다 (데이터가 아직 없으면 선택된 드라이브를 모두 표시).
    """
    sections = [s for s in ("cpu", "ram") if s in items]
    if "gpu" in items or "gpu_temp" in items:
        sections.append("gpu")
    for item in items:
        if item.startswith("disk:") and item not in sections:
            if data is None or find_disk(data, item[5:]) is not None:
                sections.append(item)
    if "net_down" in items or "net_up" in items:
        sections.append("net")
    return sections


def popup_title(section: str) -> str:
    if section.startswith("disk:"):
        return disk_letter(section[5:]) + ":"
    return {"cpu": "CPU", "ram": "RAM", "gpu": "GPU", "net": "Network"}.get(section, section)


def popup_has_subline(section: str) -> bool:
    return section in SECTIONS_WITH_SUBLINE or section.startswith("disk:")


def popup_texts(section: str, data: dict | None) -> tuple[str, str, float | None]:
    """(값 문자열, 보조 줄 문자열, 진행 막대 %). 진행 막대는 디스크만."""
    if data is None:
        return "--", "", None
    if section == "cpu":
        cpu = data.get("cpu")
        return (f"{cpu['percent']:.1f}%" if cpu else "N/A"), "", None
    if section == "ram":
        ram = data.get("ram")
        if not ram:
            return "N/A", "", None
        # 큰 글씨 값은 짧게 (오른쪽 200px 그래프와 한 줄에 들어가야 함), 용량은 보조 줄로
        return f"{ram['percent']:.0f}%", f"{ram['used_gb']:.1f} / {ram['total_gb']:.1f} GB", None
    if section == "gpu":
        gpu = data.get("gpu")
        if not gpu:
            return "N/A", "", None
        return (f"{gpu['temp']}°C   {gpu['percent']}%",
                f"VRAM {gpu['vram_used_gb']:.1f} / {gpu['vram_total_gb']:.1f} GB", None)
    if section == "net":
        net = data.get("network")
        if not net:
            return "N/A", "", None
        return (f"↓ {format_rate(net['download_kb_s'])}\n↑ {format_rate(net['upload_kb_s'])}",
                f"오늘 ↓ {format_amount_mb(net['today_download_mb'])}   ↑ {format_amount_mb(net['today_upload_mb'])}",
                None)
    if section.startswith("disk:"):
        disk = find_disk(data, section[5:])
        if disk is None:
            return "연결 안 됨", "", None
        return (f"{format_size_gb(disk['used_gb'])} / {format_size_gb(disk['total_gb'])} GB ({disk['percent']:.0f}%)",
                f"R {format_disk_rate(disk['read_mb_s'])}   W {format_disk_rate(disk['write_mb_s'])}",
                disk["percent"])
    return "", "", None


def _icon_tooltip(item: str, data: dict) -> str:
    if item == "cpu":
        cpu = data.get("cpu")
        return f"CPU 사용률 {cpu['percent']:.0f}%" if cpu else "CPU 정보 없음"
    if item == "ram":
        ram = data.get("ram")
        if not ram:
            return "RAM 정보 없음"
        return f"RAM 사용률 {ram['percent']:.0f}% ({ram['used_gb']:.1f} / {ram['total_gb']:.1f} GB)"
    if item in ("gpu", "gpu_temp"):
        gpu = data.get("gpu")
        if not gpu:
            return "GPU 정보 없음"
        if item == "gpu":
            return f"GPU 사용률 {gpu['percent']}% (VRAM {gpu['vram_used_gb']:.1f} / {gpu['vram_total_gb']:.1f} GB)"
        return f"GPU 온도 {gpu['temp']}°C"
    if item in ("net_down", "net_up"):
        net = data.get("network")
        name = "다운로드" if item == "net_down" else "업로드"
        if not net:
            return f"{name} 정보 없음"
        if item == "net_down":
            return f"{name} {format_rate(net['download_kb_s'])} (오늘 누적 {format_amount_mb(net['today_download_mb'])})"
        return f"{name} {format_rate(net['upload_kb_s'])} (오늘 누적 {format_amount_mb(net['today_upload_mb'])})"
    if item.startswith("disk:"):
        letter = disk_letter(item[5:])
        disk = find_disk(data, item[5:])
        if disk is None:
            return f"{letter}: 드라이브 연결 안 됨"
        return (f"{letter}: 드라이브 {disk['percent']:.0f}% "
                f"({format_size_gb(disk['used_gb'])} / {format_size_gb(disk['total_gb'])} GB)  "
                f"R {format_disk_rate(disk['read_mb_s'])}  W {format_disk_rate(disk['write_mb_s'])}")
    return item
