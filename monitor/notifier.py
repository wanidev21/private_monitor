"""알림 (Windows 토스트, winotify).

- AlertEvaluator: 수집 데이터와 임계값으로 보낼 알림을 판정하는 순수 로직 (CPU 지속 시간, 항목별 5분 중복 방지)
- Notifier: daemon 스레드에서 collector의 새 샘플마다 판정하고, 발송은 별도 daemon 스레드에서 실행
"""
import logging
import threading
import winreg
from collections import namedtuple

from monitor import display

log = logging.getLogger(__name__)

APP_ID = "PCMonitor.SystemMonitor"
APP_DISPLAY_NAME = "PC Monitor"
COOLDOWN_SEC = 300          # 같은 항목 알림은 5분에 한 번
CHECK_INTERVAL_SEC = 1.0    # 새 샘플 확인 주기 (같은 timestamp는 한 번만 판정)
_AUMID_KEY = rf"Software\Classes\AppUserModelId\{APP_ID}"

Alert = namedtuple("Alert", "key title message")


class AlertEvaluator:
    """임계값 '초과'(>) 판정. 시간은 샘플의 timestamp 기준 (샘플 개수가 아님)."""

    def __init__(self, cooldown_sec: float = COOLDOWN_SEC):
        self._cooldown = cooldown_sec
        self._last_sent: dict[str, float] = {}
        self._cpu_over_since: float | None = None

    def reset(self) -> None:
        """알림이 꺼져 있던 동안의 CPU 연속 초과 구간을 이어 붙이지 않도록 초기화."""
        self._cpu_over_since = None

    def evaluate(self, data: dict | None, thresholds: dict, now: float | None = None) -> list[Alert]:
        if not data:
            return []
        now = data["timestamp"] if now is None else now
        candidates = []

        cpu = data.get("cpu")
        if cpu and cpu["percent"] > thresholds["cpu_percent"]:
            if self._cpu_over_since is None:
                self._cpu_over_since = now
            if now - self._cpu_over_since >= thresholds["cpu_duration_sec"]:
                candidates.append(Alert(
                    "cpu", "CPU 사용률 높음",
                    f"CPU 사용률이 {thresholds['cpu_duration_sec']}초 넘게 {thresholds['cpu_percent']}%를 넘었습니다 "
                    f"(현재 {cpu['percent']:.0f}%)"))
        else:
            self._cpu_over_since = None

        ram = data.get("ram")
        if ram and ram["percent"] > thresholds["ram_percent"]:
            candidates.append(Alert(
                "ram", "메모리 사용률 높음",
                f"RAM 사용률 {ram['percent']:.0f}% ({ram['used_gb']:.1f} / {ram['total_gb']:.1f} GB)"))

        gpu = data.get("gpu")
        if gpu and gpu["temp"] > thresholds["gpu_temp"]:
            candidates.append(Alert(
                "gpu_temp", "GPU 온도 높음",
                f"GPU 온도 {gpu['temp']}°C (기준 {thresholds['gpu_temp']}°C, 사용률 {gpu['percent']}%)"))

        for disk in data.get("disks") or []:
            if disk["percent"] > thresholds["disk_percent"]:
                letter = display.disk_letter(disk["mount"])
                free_gb = max(0.0, disk["total_gb"] - disk["used_gb"])
                candidates.append(Alert(
                    f"disk:{disk['mount'].upper()}", f"{letter}: 드라이브 공간 부족",
                    f"사용률 {disk['percent']:.1f}%, 남은 공간 {free_gb:.1f} GB (기준 {thresholds['disk_percent']}%)"))

        alerts = []
        for alert in candidates:
            last = self._last_sent.get(alert.key)
            if last is not None and now - last < self._cooldown:
                continue
            self._last_sent[alert.key] = now
            alerts.append(alert)
        return alerts


def ensure_app_id_registered() -> None:
    """토스트 발신자(AppUserModelID)를 HKCU에 등록. 등록되지 않은 ID의 알림은 Win11에서 표시되지 않을 수 있다."""
    try:
        with winreg.CreateKey(winreg.HKEY_CURRENT_USER, _AUMID_KEY) as key:
            winreg.SetValueEx(key, "DisplayName", 0, winreg.REG_SZ, APP_DISPLAY_NAME)
    except OSError:
        log.warning("cannot register toast app id", exc_info=True)


def send_toast(title: str, message: str) -> None:
    """winotify는 PowerShell을 숨김 창으로 실행해 토스트를 띄운다 (콘솔 창 깜빡임 없음)."""
    from winotify import Notification

    Notification(app_id=APP_ID, title=title, msg=message, duration="short").show()


class Notifier:
    def __init__(self, collector, config, sender=send_toast, async_send: bool = True):
        self._collector = collector
        self._config = config
        self._sender = sender
        self._async_send = async_send
        self._evaluator = AlertEvaluator()
        self._last_timestamp = None
        self._stop_event = threading.Event()

    def run(self) -> None:
        """daemon 스레드 진입점."""
        ensure_app_id_registered()
        while not self._stop_event.is_set():
            try:
                self.check()
            except Exception:
                log.exception("alert check failed")
            self._stop_event.wait(CHECK_INTERVAL_SEC)

    def stop(self) -> None:
        self._stop_event.set()

    def check(self) -> None:
        data = self._collector.latest
        if not data or data["timestamp"] == self._last_timestamp:
            return
        self._last_timestamp = data["timestamp"]
        if not self._config.alerts_enabled:
            self._evaluator.reset()
            return
        for alert in self._evaluator.evaluate(data, self._config.alert_thresholds):
            self.send(alert.title, alert.message)

    def send_test(self) -> None:
        self.send("PC Monitor 테스트 알림", "알림이 정상적으로 표시됩니다. (GPU 60°C 같은 기호도 확인)")

    def send(self, title: str, message: str) -> None:
        log.info("alert: %s / %s", title, message)
        if self._async_send:  # PowerShell 실행이 느려도 판정 스레드를 막지 않도록
            threading.Thread(target=self._send_safely, args=(title, message), name="toast", daemon=True).start()
        else:
            self._send_safely(title, message)

    def _send_safely(self, title: str, message: str) -> None:
        try:
            self._sender(title, message)
        except Exception:
            log.exception("cannot show toast")
