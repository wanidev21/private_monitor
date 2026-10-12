"""시스템 정보 수집.

daemon 스레드에서 config.update_interval_sec마다 수집한다.
- 최신 값: `collector.latest` (dict를 통째로 교체하므로 읽기에 Lock 불필요)
- 히스토리: `collector.get_history()` (최근 60초)
수집된 dict는 만든 뒤 절대 수정하지 않는다. 여러 스레드가 같은 객체를 읽기 때문.
"""
import argparse
import collections
import datetime
import json
import logging
import random
import threading
import time

import psutil

from monitor import winapi

try:
    import pynvml  # nvidia-ml-py
except ImportError:  # pragma: no cover
    pynvml = None

log = logging.getLogger(__name__)

KB = 1024
MB = 1024 ** 2
GB = 1024 ** 3


# ---------------------------------------------------------------------------
# GPU
# ---------------------------------------------------------------------------

def init_gpu(log_failure: bool = True):
    """NVML 초기화 후 첫 번째 GPU 핸들. 드라이버가 없거나 실패하면 None."""
    if pynvml is None:
        return None
    try:
        pynvml.nvmlInit()
        return pynvml.nvmlDeviceGetHandleByIndex(0)
    except Exception:
        if log_failure:
            log.info("GPU unavailable", exc_info=True)
        return None


def shutdown_gpu() -> None:
    if pynvml is None:
        return
    try:
        pynvml.nvmlShutdown()
    except Exception:
        log.debug("nvmlShutdown failed", exc_info=True)


def read_gpu_info(handle) -> dict:
    """GPU 정보. 실패하면 예외 (드라이버 업데이트 등으로 핸들이 무효가 된 경우)."""
    util = pynvml.nvmlDeviceGetUtilizationRates(handle)
    temp = pynvml.nvmlDeviceGetTemperature(handle, pynvml.NVML_TEMPERATURE_GPU)
    mem = pynvml.nvmlDeviceGetMemoryInfo(handle)
    name = pynvml.nvmlDeviceGetName(handle)
    return {
        "name": name if isinstance(name, str) else name.decode(),
        "percent": util.gpu,
        "temp": temp,
        "vram_used_gb": round(mem.used / GB, 1),
        "vram_total_gb": round(mem.total / GB, 1),
    }


# ---------------------------------------------------------------------------
# Mock
# ---------------------------------------------------------------------------

def get_mock_data() -> dict:
    return {
        "cpu": {
            "percent": round(random.uniform(10, 95), 1),
            "per_core": [round(random.uniform(5, 100), 1) for _ in range(12)],
        },
        "ram": {
            "used_gb": round(random.uniform(4, 14), 1),
            "total_gb": 16.0,
            "percent": round(random.uniform(25, 90), 1),
        },
        "gpu": {
            "name": "NVIDIA GeForce RTX 2060",
            "percent": random.randint(0, 100),
            "temp": random.randint(35, 82),
            "vram_used_gb": round(random.uniform(0.5, 5.5), 1),
            "vram_total_gb": 6.0,
        },
        "disks": [
            {"mount": "C:\\", "used_gb": 120.0, "total_gb": 256.0, "percent": 46.9,
             "read_mb_s": round(random.uniform(0, 50), 2), "write_mb_s": round(random.uniform(0, 30), 2)},
            {"mount": "D:\\", "used_gb": 800.0, "total_gb": 1000.0, "percent": 80.0,
             "read_mb_s": round(random.uniform(0, 100), 2), "write_mb_s": round(random.uniform(0, 80), 2)},
        ],
        "network": {
            "upload_kb_s": round(random.uniform(0, 500), 1),
            "download_kb_s": round(random.uniform(0, 5000), 1),
            "today_upload_mb": 85.0,
            "today_download_mb": 1228.8,
        },
        "timestamp": time.time(),
    }


# ---------------------------------------------------------------------------
# Collector
# ---------------------------------------------------------------------------

class Collector:
    DRIVE_REFRESH_SEC = 60
    HISTORY_WINDOW_SEC = 60
    DEFAULT_INTERVAL_SEC = 2
    GPU_RETRY_SEC = 30

    def __init__(self, config, mock: bool = False):
        self._config = config
        self._mock = mock
        self._latest = None
        self._history = collections.deque(maxlen=600)
        self._history_lock = threading.Lock()
        self._stop_event = threading.Event()
        self._failing_sections = set()

        # 테스트에서 교체할 수 있도록 시간 함수를 속성으로 둔다
        self.clock = time.monotonic
        self.wall_clock = time.time
        self.today = datetime.date.today

        self._gpu_handle = None
        self._gpu_retry_at = None  # 핸들이 없을 때 다시 초기화를 시도할 monotonic 시각
        self._gpu_lost = False     # 동작하던 GPU가 실패해 재연결을 기다리는 중
        self._drives = []  # [(mount, physical_drive_name | None)]
        self._drives_checked_at = None
        self._prev_sample_at = None
        self._prev_disk_io = None
        self._prev_net = None
        self._net_day = None
        self._today_up_bytes = 0
        self._today_down_bytes = 0

        if not mock:
            self._gpu_handle = init_gpu()
            # 첫 호출은 항상 0을 반환하므로 기준점을 미리 잡아둔다
            psutil.cpu_percent(interval=None)
            psutil.cpu_percent(interval=None, percpu=True)

    # --- 공개 API -----------------------------------------------------------

    @property
    def latest(self) -> dict | None:
        return self._latest

    def get_history(self, window_sec: float = HISTORY_WINDOW_SEC) -> list[dict]:
        cutoff = self.wall_clock() - window_sec
        with self._history_lock:
            return [d for d in self._history if d["timestamp"] >= cutoff]

    def collect_once(self) -> dict:
        data = get_mock_data() if self._mock else self._collect_real()
        with self._history_lock:
            self._history.append(data)
            self._latest = data
        return data

    def run(self) -> None:
        """daemon 스레드 진입점. stop() 호출 전까지 주기적으로 수집."""
        while not self._stop_event.is_set():
            started = self.clock()
            try:
                self.collect_once()
            except Exception:
                log.exception("collect loop error")
            try:
                interval = self._config.update_interval_sec
            except Exception:
                interval = self.DEFAULT_INTERVAL_SEC
            self._stop_event.wait(max(0.1, interval - (self.clock() - started)))

    def stop(self) -> None:
        self._stop_event.set()

    def shutdown(self) -> None:
        self.stop()
        if self._gpu_handle is not None:
            shutdown_gpu()
            self._gpu_handle = None

    # --- 수집 ---------------------------------------------------------------

    def _collect_real(self) -> dict:
        now = self.clock()
        elapsed = None
        if self._prev_sample_at is not None and now > self._prev_sample_at:
            elapsed = now - self._prev_sample_at
        self._prev_sample_at = now

        return {
            "cpu": self._section("cpu", self._collect_cpu),
            "ram": self._section("ram", self._collect_ram),
            "gpu": self._section("gpu", self._collect_gpu),
            "disks": self._section("disks", lambda: self._collect_disks(elapsed)) or [],
            "network": self._section("network", lambda: self._collect_network(elapsed)),
            "timestamp": self.wall_clock(),
        }

    def _section(self, name: str, fn):
        """수집 항목 하나를 격리 실행. 실패하면 None, 같은 오류 로그는 복구될 때까지 한 번만."""
        try:
            value = fn()
        except Exception:
            if name not in self._failing_sections:
                self._failing_sections.add(name)
                log.exception("collect %s failed", name)
            return None
        if name in self._failing_sections:
            self._failing_sections.discard(name)
            log.info("collect %s recovered", name)
        return value

    def _collect_gpu(self) -> dict | None:
        """GPU 정보. 드라이버 업데이트·재설치로 핸들이 무효가 되면 NVML을 닫고
        GPU_RETRY_SEC마다 다시 초기화한다 (처음에 실패한 경우도 같은 방식으로 재시도)."""
        if self._gpu_handle is None:
            if self._gpu_retry_at is None:
                self._gpu_retry_at = self.clock() + self.GPU_RETRY_SEC
            if self.clock() < self._gpu_retry_at:
                return None
            self._gpu_handle = init_gpu(log_failure=False)
            if self._gpu_handle is None:
                self._gpu_retry_at = self.clock() + self.GPU_RETRY_SEC
                return None
        try:
            info = read_gpu_info(self._gpu_handle)
        except Exception:
            if not self._gpu_lost:
                log.warning("GPU read failed; reinitializing NVML every %ds", self.GPU_RETRY_SEC,
                            exc_info=True)
            self._gpu_lost = True
            shutdown_gpu()
            self._gpu_handle = None
            self._gpu_retry_at = self.clock() + self.GPU_RETRY_SEC
            return None
        if self._gpu_lost:
            self._gpu_lost = False
            log.info("GPU recovered")
        return info

    @staticmethod
    def _collect_cpu() -> dict:
        return {
            "percent": round(psutil.cpu_percent(interval=None), 1),
            "per_core": [round(p, 1) for p in psutil.cpu_percent(interval=None, percpu=True)],
        }

    @staticmethod
    def _collect_ram() -> dict:
        mem = psutil.virtual_memory()
        return {
            "used_gb": round(mem.used / GB, 1),
            "total_gb": round(mem.total / GB, 1),
            "percent": round(mem.percent, 1),
        }

    @staticmethod
    def _detect_drives() -> list[tuple[str, str | None]]:
        drives = []
        for part in psutil.disk_partitions(all=False):
            if not part.fstype or "cdrom" in part.opts:
                continue
            drives.append((part.mountpoint, winapi.get_physical_drive_name(part.mountpoint[:1])))
        return drives

    def _collect_disks(self, elapsed: float | None) -> list[dict]:
        now = self.clock()
        if self._drives_checked_at is None or now - self._drives_checked_at >= self.DRIVE_REFRESH_SEC:
            self._drives = self._detect_drives()
            self._drives_checked_at = now

        try:
            io = psutil.disk_io_counters(perdisk=True) or {}
        except Exception:
            log.debug("disk_io_counters failed", exc_info=True)
            io = {}
        prev_io = self._prev_disk_io
        self._prev_disk_io = io

        disks = []
        for mount, physical in self._drives:
            try:
                usage = psutil.disk_usage(mount)
            except Exception:  # 분리된 USB 등. 다음 드라이브 목록 갱신 때 사라진다
                log.debug("disk_usage(%s) failed", mount, exc_info=True)
                continue

            read_mb_s = write_mb_s = None
            if physical and physical in io:
                if elapsed and prev_io and physical in prev_io:
                    cur, prev = io[physical], prev_io[physical]
                    read_mb_s = round(max(0, cur.read_bytes - prev.read_bytes) / elapsed / MB, 2)
                    write_mb_s = round(max(0, cur.write_bytes - prev.write_bytes) / elapsed / MB, 2)
                else:
                    read_mb_s = write_mb_s = 0.0

            disks.append({
                "mount": mount,
                "used_gb": round(usage.used / GB, 1),
                "total_gb": round(usage.total / GB, 1),
                "percent": round(usage.percent, 1),
                "read_mb_s": read_mb_s,
                "write_mb_s": write_mb_s,
            })
        return disks

    def _collect_network(self, elapsed: float | None) -> dict:
        counters = psutil.net_io_counters()
        prev = self._prev_net
        self._prev_net = counters

        today = self.today()
        if self._net_day != today:
            self._net_day = today
            self._today_up_bytes = 0
            self._today_down_bytes = 0

        up_kb_s = down_kb_s = 0.0
        if prev is not None and elapsed:
            # 어댑터 재시작 등으로 카운터가 줄어들면 0으로 취급
            sent = max(0, counters.bytes_sent - prev.bytes_sent)
            recv = max(0, counters.bytes_recv - prev.bytes_recv)
            self._today_up_bytes += sent
            self._today_down_bytes += recv
            up_kb_s = sent / elapsed / KB
            down_kb_s = recv / elapsed / KB

        return {
            "upload_kb_s": round(up_kb_s, 1),
            "download_kb_s": round(down_kb_s, 1),
            "today_upload_mb": round(self._today_up_bytes / MB, 1),
            "today_download_mb": round(self._today_down_bytes / MB, 1),
        }


def main(argv=None):
    parser = argparse.ArgumentParser(description="현재 시스템 상태를 JSON으로 출력")
    parser.add_argument("--mock", action="store_true", help="실제 수집 대신 더미 데이터")
    args = parser.parse_args(argv)
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s: %(message)s")

    collector = Collector(config=None, mock=args.mock)
    try:
        if not args.mock:
            collector.collect_once()
            time.sleep(1)  # 속도(I/O, 네트워크)는 두 번째 샘플부터 계산된다
        print(json.dumps(collector.collect_once(), ensure_ascii=False, indent=4))
    finally:
        collector.shutdown()


if __name__ == "__main__":
    main()
