import unittest

from monitor.config import DEFAULTS
from monitor.notifier import AlertEvaluator, Notifier
from tests.test_display import sample_data

THRESHOLDS = DEFAULTS["alert_thresholds"]  # cpu 90% / 10초, ram 90%, gpu 85°C, disk 95%


def data_at(t, cpu=45.0, ram=51.0, gpu_temp=62, disk_c=58.0):
    d = sample_data()
    d["timestamp"] = float(t)
    d["cpu"]["percent"] = cpu
    d["ram"]["percent"] = ram
    d["gpu"]["temp"] = gpu_temp
    d["disks"][0]["percent"] = disk_c
    return d


class AlertEvaluatorTest(unittest.TestCase):
    def keys(self, evaluator, data):
        return [a.key for a in evaluator.evaluate(data, THRESHOLDS)]

    def test_nothing_below_thresholds(self):
        self.assertEqual(self.keys(AlertEvaluator(), data_at(0)), [])

    def test_equal_to_threshold_is_not_exceeded(self):
        self.assertEqual(self.keys(AlertEvaluator(), data_at(0, ram=90.0, gpu_temp=85, disk_c=95.0)), [])

    def test_cpu_needs_sustained_duration(self):
        e = AlertEvaluator()
        for t in range(0, 10, 2):  # 0~8초
            self.assertEqual(self.keys(e, data_at(t, cpu=95)), [], t)
        self.assertEqual(self.keys(e, data_at(10, cpu=95)), ["cpu"])

    def test_cpu_drop_resets_duration(self):
        e = AlertEvaluator()
        self.keys(e, data_at(0, cpu=95))
        self.keys(e, data_at(8, cpu=50))  # 한 번 내려감
        self.assertEqual(self.keys(e, data_at(10, cpu=95)), [])
        self.assertEqual(self.keys(e, data_at(20, cpu=95)), ["cpu"])

    def test_immediate_alerts_and_cooldown(self):
        e = AlertEvaluator()
        self.assertEqual(self.keys(e, data_at(0, ram=95, gpu_temp=90, disk_c=97)), ["ram", "gpu_temp", "disk:C:\\"])
        self.assertEqual(self.keys(e, data_at(100, ram=95, gpu_temp=90, disk_c=97)), [])
        self.assertEqual(self.keys(e, data_at(301, ram=95)), ["ram"])

    def test_disks_are_independent(self):
        d = data_at(0, disk_c=97)
        d["disks"].append({"mount": "D:\\", "used_gb": 990.0, "total_gb": 1000.0, "percent": 99.0,
                           "read_mb_s": 0.0, "write_mb_s": 0.0})
        alerts = AlertEvaluator().evaluate(d, THRESHOLDS)
        self.assertEqual([a.key for a in alerts], ["disk:C:\\", "disk:D:\\"])
        self.assertEqual(alerts[1].title, "D: 드라이브 공간 부족")
        self.assertIn("남은 공간 10.0 GB", alerts[1].message)

    def test_missing_gpu_is_skipped(self):
        d = data_at(0)
        d["gpu"] = None
        self.assertEqual(self.keys(AlertEvaluator(), d), [])


class FakeCollector:
    def __init__(self):
        self.latest = None


class FakeConfig:
    def __init__(self):
        self.alerts_enabled = True
        self.alert_thresholds = THRESHOLDS


class NotifierTest(unittest.TestCase):
    def setUp(self):
        self.sent = []
        self.collector = FakeCollector()
        self.config = FakeConfig()
        self.notifier = Notifier(self.collector, self.config, sender=lambda t, m: self.sent.append(t),
                                 async_send=False)

    def test_sends_once_per_sample(self):
        self.collector.latest = data_at(0, ram=95)
        self.notifier.check()
        self.notifier.check()  # 같은 샘플
        self.assertEqual(self.sent, ["메모리 사용률 높음"])

    def test_disabled_sends_nothing(self):
        self.config.alerts_enabled = False
        self.collector.latest = data_at(0, ram=95)
        self.notifier.check()
        self.assertEqual(self.sent, [])

    def test_sender_errors_are_swallowed(self):
        notifier = Notifier(self.collector, self.config, sender=lambda t, m: 1 / 0, async_send=False)
        with self.assertLogs("monitor.notifier", level="ERROR"):
            notifier.send_test()

    def test_send_test(self):
        self.notifier.send_test()
        self.assertEqual(self.sent, ["PC Monitor 테스트 알림"])


if __name__ == "__main__":
    unittest.main()
