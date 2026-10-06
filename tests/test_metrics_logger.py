import csv
import datetime
import os
import shutil
import tempfile
import unittest

from monitor.logger import BASE_COLUMNS, MetricsLogger
from tests.test_display import sample_data


def ts(day=15, hour=10, minute=0, second=0):
    return datetime.datetime(2026, 9, day, hour, minute, second).timestamp()


def sample(t, cpu=10.0, gpu=True, disks=("C",)):
    d = sample_data()
    d["timestamp"] = t
    d["cpu"]["percent"] = cpu
    if not gpu:
        d["gpu"] = None
    d["disks"] = [{"mount": f"{letter}:\\", "used_gb": 1.0, "total_gb": 2.0, "percent": 50.0,
                   "read_mb_s": 0.0, "write_mb_s": 0.0} for letter in disks]
    return d


class MetricsLoggerTest(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.mkdtemp(prefix="pcmon-csv-")
        self.addCleanup(shutil.rmtree, self.dir, True)
        self.now = ts()
        self.logger = self.make_logger()

    def make_logger(self):
        return MetricsLogger(collector=None, log_dir=self.dir, clock=lambda: self.now)

    def rows(self, day=15):
        with open(os.path.join(self.dir, f"2026-09-{day:02d}.csv"), newline="", encoding="utf-8") as f:
            return list(csv.reader(f))

    def test_minute_average_is_written_when_next_minute_starts(self):
        for second, cpu in ((0, 10.0), (20, 20.0), (40, 30.0)):
            self.logger.add_sample(sample(ts(second=second), cpu=cpu))
        self.assertFalse(os.path.exists(os.path.join(self.dir, "2026-09-15.csv")))
        self.logger.add_sample(sample(ts(minute=1, second=5), cpu=90.0))
        header, row = self.rows()
        self.assertEqual(header, BASE_COLUMNS + ["disk_C_pct"])
        self.assertEqual(row[0], "2026-09-15 10:00")
        self.assertEqual(row[1], "20.0")          # cpu 평균
        self.assertEqual(row[header.index("gpu_temp")], "62.0")
        self.assertEqual(row[header.index("disk_C_pct")], "50.0")

    def test_flush_writes_partial_minute(self):
        self.logger.add_sample(sample(ts(second=0), cpu=40.0))
        self.logger.flush()
        self.assertEqual(self.rows()[1][1], "40.0")
        self.logger.flush()  # 두 번 불러도 중복 기록 없음
        self.assertEqual(len(self.rows()), 2)

    def test_missing_gpu_is_blank(self):
        self.logger.add_sample(sample(ts(), gpu=False))
        self.logger.flush()
        header, row = self.rows()
        self.assertEqual(row[header.index("gpu_pct")], "")
        self.assertEqual(row[header.index("gpu_temp")], "")

    def test_restart_same_day_keeps_existing_header(self):
        self.logger.add_sample(sample(ts(), disks=("C",)))
        self.logger.flush()
        restarted = self.make_logger()
        restarted.add_sample(sample(ts(minute=5), disks=("D",)))  # C 분리, D 새로 연결
        restarted.flush()
        header, _, row = self.rows()
        self.assertEqual(header, BASE_COLUMNS + ["disk_C_pct"])
        self.assertEqual(row[header.index("disk_C_pct")], "")
        self.assertEqual(len(row), len(header))

    def test_new_day_file_uses_current_disks(self):
        self.logger.add_sample(sample(ts(hour=23, minute=59), disks=("C",)))
        self.logger.add_sample(sample(ts(day=16, hour=0, minute=0), disks=("C", "D")))
        self.logger.flush()
        self.assertEqual(self.rows(15)[0], BASE_COLUMNS + ["disk_C_pct"])
        self.assertEqual(self.rows(16)[0], BASE_COLUMNS + ["disk_C_pct", "disk_D_pct"])

    def test_cleanup_deletes_files_seven_days_or_older(self):
        for name in ("2026-09-08.csv", "2026-09-09.csv", "2026-09-15.csv", "app.log", "notes.csv"):
            open(os.path.join(self.dir, name), "w").close()
        removed = self.logger.cleanup(datetime.date(2026, 9, 15))
        self.assertEqual(removed, 1)
        self.assertEqual(sorted(os.listdir(self.dir)), ["2026-09-09.csv", "2026-09-15.csv", "app.log", "notes.csv"])

    def test_cleanup_runs_when_date_changes(self):
        self.logger.cleanup(datetime.date(2026, 9, 15))
        open(os.path.join(self.dir, "2026-09-09.csv"), "w").close()
        self.now = ts(day=16, hour=0, minute=1)
        self.logger.add_sample(sample(ts(day=16, hour=0, minute=1)))
        self.assertFalse(os.path.exists(os.path.join(self.dir, "2026-09-09.csv")))

    def test_locked_file_rows_are_retried_in_order(self):
        original = self.logger._append_row
        calls = {"n": 0}

        def flaky(date_str, disks, row):
            calls["n"] += 1
            if calls["n"] == 1:
                raise PermissionError("locked by Excel")
            original(date_str, disks, row)

        self.logger._append_row = flaky
        self.logger.add_sample(sample(ts(minute=0), cpu=10.0))
        with self.assertLogs("monitor.logger", level="WARNING"):
            self.logger.add_sample(sample(ts(minute=1), cpu=20.0))  # 10:00 기록 실패 → 보관
        self.logger.add_sample(sample(ts(minute=2), cpu=30.0))      # 10:00, 10:01 순서대로 기록
        self.assertEqual([r[0] for r in self.rows()[1:]], ["2026-09-15 10:00", "2026-09-15 10:01"])


class PollTest(unittest.TestCase):
    def test_same_sample_is_counted_once(self):
        directory = tempfile.mkdtemp(prefix="pcmon-csv-")
        self.addCleanup(shutil.rmtree, directory, True)

        class Collector:
            latest = sample(ts(), cpu=10.0)

        logger = MetricsLogger(Collector, log_dir=directory, clock=lambda: ts())
        logger.poll()
        logger.poll()
        Collector.latest = sample(ts(second=30), cpu=40.0)
        logger.poll()
        logger.flush()
        with open(os.path.join(directory, "2026-09-15.csv"), newline="", encoding="utf-8") as f:
            self.assertEqual(list(csv.reader(f))[1][1], "25.0")


if __name__ == "__main__":
    unittest.main()
