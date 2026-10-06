"""로그.

- setup_app_logging: app.log (앱 오류 로그)
- MetricsLogger: 수치 CSV 로그 (1분 평균, 날짜별 파일, 7일 보관)
"""
import csv
import datetime
import logging
import os
import re
import sys
import threading
import time
from logging.handlers import RotatingFileHandler

from monitor import paths

_FORMAT = "%(asctime)s %(levelname)s [%(threadName)s] %(name)s: %(message)s"


def setup_app_logging(level: int = logging.INFO) -> None:
    """%USERPROFILE%/.pc-monitor/logs/app.log (1MB x 3). 처리되지 않은 예외도 기록한다.

    콘솔 없는 exe에서 오류 원인을 확인할 유일한 수단이므로 main.py 시작 직후 호출.
    """
    root = logging.getLogger()
    root.setLevel(level)
    formatter = logging.Formatter(_FORMAT)

    file_handler = RotatingFileHandler(
        os.path.join(paths.get_log_dir(), "app.log"),
        maxBytes=1_000_000, backupCount=3, encoding="utf-8",
    )
    file_handler.setFormatter(formatter)
    root.addHandler(file_handler)

    console = logging.StreamHandler()
    console.setFormatter(formatter)
    root.addHandler(console)

    uncaught = logging.getLogger("uncaught")

    def excepthook(exc_type, exc_value, exc_tb):
        uncaught.critical("unhandled exception", exc_info=(exc_type, exc_value, exc_tb))

    def thread_excepthook(args):
        if args.exc_type is SystemExit:
            return
        name = args.thread.name if args.thread else "?"
        uncaught.critical("unhandled exception in thread %s", name,
                          exc_info=(args.exc_type, args.exc_value, args.exc_traceback))

    sys.excepthook = excepthook
    threading.excepthook = thread_excepthook


# ---------------------------------------------------------------------------
# 수치 CSV 로그
# ---------------------------------------------------------------------------

BASE_COLUMNS = ["timestamp", "cpu_pct", "ram_pct", "gpu_pct", "gpu_temp", "net_down_kb", "net_up_kb"]
_CSV_NAME_RE = re.compile(r"^(\d{4}-\d{2}-\d{2})\.csv$")


def sample_values(data: dict) -> dict[str, float | None]:
    """샘플 하나 → CSV 열별 값 (없으면 None)."""
    cpu, ram, gpu, net = data.get("cpu"), data.get("ram"), data.get("gpu"), data.get("network")
    values = {
        "cpu_pct": cpu["percent"] if cpu else None,
        "ram_pct": ram["percent"] if ram else None,
        "gpu_pct": gpu["percent"] if gpu else None,
        "gpu_temp": gpu["temp"] if gpu else None,
        "net_down_kb": net["download_kb_s"] if net else None,
        "net_up_kb": net["upload_kb_s"] if net else None,
    }
    for disk in data.get("disks") or []:
        values[disk_column(disk["mount"])] = disk["percent"]
    return values


def disk_column(mount: str) -> str:
    return f"disk_{mount[:1].upper()}_pct"


class MetricsLogger:
    """collector의 샘플을 1분 단위로 평균 내 `logs/YYYY-MM-DD.csv`에 한 줄씩 추가한다.

    - 분(로컬 시각) 경계를 넘는 샘플이 들어오면 이전 분을 기록. 종료 시 flush()로 남은 분도 기록.
    - 디스크 열은 그날 파일을 처음 만들 때 그 분에 관측된 드라이브로 정한다. 이미 있는 파일(같은 날 재시작)은
      기존 헤더를 그대로 따르며, 새로 연결된 드라이브는 다음 날 파일부터 포함, 없는 값은 빈칸.
    - 7일 이상 된 CSV는 시작 시와 날짜가 바뀔 때 삭제 (앱이 며칠씩 켜져 있으므로).
    - 파일이 잠겨 있으면(Excel로 열어둠 등) 줄을 보관했다가 다음 기록 때 다시 시도.
    """

    RETENTION_DAYS = 7
    MAX_PENDING_ROWS = 1440  # 하루치. 계속 실패하면 오래된 줄부터 버린다
    POLL_SEC = 1.0

    def __init__(self, collector, log_dir: str | None = None, clock=time.time):
        self._collector = collector
        self._dir = log_dir
        self._clock = clock
        self._lock = threading.Lock()
        self._stop_event = threading.Event()
        self._last_timestamp = None
        self._minute = None
        self._sums: dict[str, float] = {}
        self._counts: dict[str, int] = {}
        self._disks_seen: list[str] = []
        self._pending: list[tuple[str, list[str], dict]] = []
        self._cleanup_date = None
        self._warned_write = False

    # --- 수명 주기 ----------------------------------------------------------

    def run(self) -> None:
        """daemon 스레드 진입점."""
        self.cleanup()
        while not self._stop_event.is_set():
            try:
                self.poll()
            except Exception:
                logging.getLogger(__name__).exception("metrics log failed")
            self._stop_event.wait(self.POLL_SEC)

    def stop(self) -> None:
        self._stop_event.set()

    def poll(self) -> None:
        data = self._collector.latest
        if not data or data["timestamp"] == self._last_timestamp:
            return
        self._last_timestamp = data["timestamp"]
        self.add_sample(data)

    # --- 집계 ---------------------------------------------------------------

    def add_sample(self, data: dict) -> None:
        with self._lock:
            minute = int(data["timestamp"] // 60)
            if self._minute is not None and minute != self._minute:
                self._close_minute()
            self._minute = minute
            for column, value in sample_values(data).items():
                if value is None:
                    continue
                self._sums[column] = self._sums.get(column, 0.0) + value
                self._counts[column] = self._counts.get(column, 0) + 1
                if column.startswith("disk_") and column not in self._disks_seen:
                    self._disks_seen.append(column)
            self._write_pending()

    def flush(self) -> None:
        """진행 중인 분을 (부분 평균으로) 기록. 종료 시 호출."""
        with self._lock:
            if self._minute is not None:
                self._close_minute()
                self._minute = None
            self._write_pending()

    def _close_minute(self) -> None:
        local = time.localtime(self._minute * 60)
        row = {"timestamp": time.strftime("%Y-%m-%d %H:%M", local)}
        for column, total in self._sums.items():
            row[column] = total / self._counts[column]
        self._pending.append((time.strftime("%Y-%m-%d", local), list(self._disks_seen), row))
        self._sums, self._counts, self._disks_seen = {}, {}, []

    # --- 파일 ---------------------------------------------------------------

    def _log_dir(self) -> str:
        if self._dir is None:
            self._dir = paths.get_log_dir()
        os.makedirs(self._dir, exist_ok=True)
        return self._dir

    def _write_pending(self) -> None:
        remaining = []
        for index, (date_str, disks, row) in enumerate(self._pending):
            try:
                self._append_row(date_str, disks, row)
            except OSError:
                if not self._warned_write:
                    logging.getLogger(__name__).warning("cannot write metrics csv; will retry", exc_info=True)
                    self._warned_write = True
                remaining = self._pending[index:]  # 순서를 지키기 위해 실패한 줄부터 모두 보관
                break
        else:
            self._warned_write = False
        self._pending = remaining[-self.MAX_PENDING_ROWS:]

        today = datetime.date.fromtimestamp(self._clock())
        if self._cleanup_date != today:
            self.cleanup(today)

    def _append_row(self, date_str: str, disks: list[str], row: dict) -> None:
        path = os.path.join(self._log_dir(), f"{date_str}.csv")
        header = None
        if os.path.exists(path):
            with open(path, "r", newline="", encoding="utf-8") as f:
                header = next(csv.reader(f), None)
        if header:
            with open(path, "a", newline="", encoding="utf-8") as f:
                csv.writer(f).writerow(self._format(header, row))
        else:
            header = BASE_COLUMNS + sorted(disks)
            with open(path, "w", newline="", encoding="utf-8") as f:
                writer = csv.writer(f)
                writer.writerow(header)
                writer.writerow(self._format(header, row))

    @staticmethod
    def _format(header: list[str], row: dict) -> list[str]:
        cells = []
        for column in header:
            value = row.get(column)
            if value is None:
                cells.append("")
            elif isinstance(value, str):
                cells.append(value)
            else:
                cells.append(f"{value:.1f}")
        return cells

    def cleanup(self, today: datetime.date | None = None) -> int:
        """RETENTION_DAYS일 이상 된 CSV 삭제. 삭제한 개수 반환."""
        today = today or datetime.date.fromtimestamp(self._clock())
        self._cleanup_date = today
        removed = 0
        try:
            names = os.listdir(self._log_dir())
        except OSError:
            return 0
        for name in names:
            match = _CSV_NAME_RE.match(name)
            if not match:
                continue
            try:
                file_date = datetime.date.fromisoformat(match.group(1))
            except ValueError:
                continue
            if (today - file_date).days >= self.RETENTION_DAYS:
                try:
                    os.remove(os.path.join(self._log_dir(), name))
                    removed += 1
                except OSError:
                    logging.getLogger(__name__).warning("cannot delete old log %s", name, exc_info=True)
        if removed:
            logging.getLogger(__name__).info("deleted %d old metrics log(s)", removed)
        return removed
