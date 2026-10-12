import datetime
import unittest
from collections import namedtuple
from unittest import mock

from monitor import collector as collector_mod
from monitor.collector import KB, MB, Collector

Net = namedtuple("Net", "bytes_sent bytes_recv")
Io = namedtuple("Io", "read_bytes write_bytes")
Part = namedtuple("Part", "device mountpoint fstype opts")
Usage = namedtuple("Usage", "total used free percent")
Mem = namedtuple("Mem", "total used percent")


class FakeSystem:
    """psutil 대체. 테스트에서 카운터 값을 직접 조작한다."""

    def __init__(self):
        self.net = Net(0, 0)
        self.io = {"PhysicalDrive0": Io(0, 0)}
        self.partitions = [
            Part("C:\\", "C:\\", "NTFS", "rw,fixed"),
            Part("D:\\", "D:\\", "NTFS", "rw,fixed"),
            Part("E:\\", "E:\\", "", "cdrom"),
        ]
        self.missing_mounts = set()
        self.cpu_error = False

    def cpu_percent(self, interval=None, percpu=False):
        if self.cpu_error:
            raise RuntimeError("boom")
        return [10.0, 20.0] if percpu else 15.0

    def virtual_memory(self):
        return Mem(16 * 1024 ** 3, 8 * 1024 ** 3, 50.0)

    def disk_partitions(self, all=False):
        return self.partitions

    def disk_usage(self, mount):
        if mount in self.missing_mounts:
            raise OSError("not ready")
        return Usage(100 * 1024 ** 3, 25 * 1024 ** 3, 75 * 1024 ** 3, 25.0)

    def disk_io_counters(self, perdisk=False):
        return dict(self.io)

    def net_io_counters(self):
        return self.net


class CollectorTest(unittest.TestCase):
    def setUp(self):
        self.system = FakeSystem()
        self.now = 1000.0
        self.day = datetime.date(2026, 9, 15)
        patches = [
            mock.patch.object(collector_mod, "psutil", self.system),
            mock.patch.object(collector_mod, "init_gpu", return_value=None),
            mock.patch.object(collector_mod.winapi, "get_physical_drive_name",
                              side_effect={"C": "PhysicalDrive0", "D": None}.get),
        ]
        for p in patches:
            p.start()
            self.addCleanup(p.stop)
        self.collector = Collector(config=None)
        self.collector.clock = lambda: self.now
        self.collector.wall_clock = lambda: self.now
        self.collector.today = lambda: self.day

    def test_first_sample_has_zero_rates(self):
        data = self.collector.collect_once()
        self.assertEqual(data["cpu"], {"percent": 15.0, "per_core": [10.0, 20.0]})
        self.assertEqual(data["ram"]["percent"], 50.0)
        self.assertIsNone(data["gpu"])
        self.assertEqual(data["network"]["upload_kb_s"], 0.0)
        mounts = [d["mount"] for d in data["disks"]]
        self.assertEqual(mounts, ["C:\\", "D:\\"])  # cdrom 제외
        self.assertEqual(data["disks"][0]["read_mb_s"], 0.0)
        self.assertIsNone(data["disks"][1]["read_mb_s"])  # 매핑 실패 드라이브

    def test_gpu_reinitializes_after_driver_update(self):
        info = {"name": "GPU", "percent": 10, "temp": 40, "vram_used_gb": 1.0, "vram_total_gb": 6.0}
        reads = {"fail": False}

        def read(handle):
            if reads["fail"] or handle != "h":
                raise RuntimeError("NVML_ERROR_UNINITIALIZED")
            return info

        with mock.patch.object(collector_mod, "read_gpu_info", side_effect=read), \
                mock.patch.object(collector_mod, "shutdown_gpu") as shutdown, \
                mock.patch.object(collector_mod, "init_gpu", return_value="h") as init:
            self.collector._gpu_handle = "h"
            self.assertEqual(self.collector.collect_once()["gpu"], info)

            reads["fail"] = True  # 드라이버 교체로 핸들 무효
            with self.assertLogs(collector_mod.log, "WARNING"):
                self.assertIsNone(self.collector.collect_once()["gpu"])
            shutdown.assert_called_once()
            reads["fail"] = False

            self.now += Collector.GPU_RETRY_SEC - 1  # 재시도 간격 전에는 초기화하지 않음
            self.assertIsNone(self.collector.collect_once()["gpu"])
            init.assert_not_called()

            self.now += 1
            self.assertEqual(self.collector.collect_once()["gpu"], info)
            init.assert_called_once_with(log_failure=False)

    def test_gpu_retries_when_unavailable_at_start(self):
        info = {"name": "GPU", "percent": 1, "temp": 30, "vram_used_gb": 0.5, "vram_total_gb": 6.0}
        with mock.patch.object(collector_mod, "read_gpu_info", return_value=info), \
                mock.patch.object(collector_mod, "init_gpu", side_effect=[None, "h"]) as init:
            self.assertIsNone(self.collector.collect_once()["gpu"])
            self.now += Collector.GPU_RETRY_SEC
            self.assertIsNone(self.collector.collect_once()["gpu"])  # 아직 실패
            self.now += Collector.GPU_RETRY_SEC
            self.assertEqual(self.collector.collect_once()["gpu"], info)
            self.assertEqual(init.call_count, 2)

    def test_rates_use_elapsed_time(self):
        self.collector.collect_once()
        self.now += 2
        self.system.net = Net(bytes_sent=200 * KB, bytes_recv=4 * MB)
        self.system.io = {"PhysicalDrive0": Io(read_bytes=10 * MB, write_bytes=6 * MB)}
        data = self.collector.collect_once()
        self.assertEqual(data["network"]["upload_kb_s"], 100.0)
        self.assertEqual(data["network"]["download_kb_s"], 2048.0)
        self.assertEqual(data["disks"][0]["read_mb_s"], 5.0)
        self.assertEqual(data["disks"][0]["write_mb_s"], 3.0)
        self.assertAlmostEqual(data["network"]["today_download_mb"], 4.0)

    def test_today_totals_reset_at_midnight(self):
        self.collector.collect_once()
        self.now += 2
        self.system.net = Net(MB, 10 * MB)
        self.assertEqual(self.collector.collect_once()["network"]["today_download_mb"], 10.0)
        self.now += 2
        self.day = self.day + datetime.timedelta(days=1)
        self.system.net = Net(2 * MB, 12 * MB)
        data = self.collector.collect_once()
        self.assertEqual(data["network"]["today_download_mb"], 2.0)
        self.assertEqual(data["network"]["today_upload_mb"], 1.0)

    def test_counter_reset_is_not_negative(self):
        self.system.net = Net(10 * MB, 10 * MB)
        self.collector.collect_once()
        self.now += 2
        self.system.net = Net(0, 0)
        data = self.collector.collect_once()
        self.assertEqual(data["network"]["upload_kb_s"], 0.0)
        self.assertEqual(data["network"]["download_kb_s"], 0.0)

    def test_section_failure_is_isolated(self):
        self.system.cpu_error = True
        with self.assertLogs(collector_mod.log, level="ERROR"):
            data = self.collector.collect_once()
        self.assertIsNone(data["cpu"])
        self.assertIsNotNone(data["ram"])
        self.assertEqual(len(data["disks"]), 2)

    def test_missing_disk_is_skipped(self):
        self.system.missing_mounts = {"D:\\"}
        data = self.collector.collect_once()
        self.assertEqual([d["mount"] for d in data["disks"]], ["C:\\"])

    def test_history_window(self):
        for _ in range(40):
            self.collector.collect_once()
            self.now += 2
        history = self.collector.get_history(60)
        self.assertEqual(len(history), 30)
        self.assertIs(self.collector.latest, self.collector.get_history()[-1])


class MockDataTest(unittest.TestCase):
    def test_mock_shape(self):
        data = Collector(config=None, mock=True).collect_once()
        self.assertEqual(set(data), {"cpu", "ram", "gpu", "disks", "network", "timestamp"})
        self.assertIn("today_download_mb", data["network"])


if __name__ == "__main__":
    unittest.main()
