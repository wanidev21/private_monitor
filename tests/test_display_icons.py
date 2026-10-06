import unittest

from monitor import display
from monitor.config import DEFAULTS
from tests.test_display import sample_data

THEME = DEFAULTS["ui_theme"]
KB_PER_MB = 1024
KB_PER_GB = 1024 * 1024


class NetUnitTest(unittest.TestCase):
    def test_natural_units(self):
        self.assertEqual(display.choose_net_unit(50), "KB")
        self.assertEqual(display.choose_net_unit(150), "MB")
        self.assertEqual(display.choose_net_unit(100 * KB_PER_MB), "GB")

    def test_up_is_immediate(self):
        self.assertEqual(display.choose_net_unit(150, "KB"), "MB")
        self.assertEqual(display.choose_net_unit(100 * KB_PER_MB, "MB"), "GB")

    def test_down_uses_hysteresis(self):
        self.assertEqual(display.choose_net_unit(95, "MB"), "MB")     # 100KB의 90% 이상 → 유지
        self.assertEqual(display.choose_net_unit(80, "MB"), "KB")
        self.assertEqual(display.choose_net_unit(95000, "GB"), "GB")  # 102400KB의 90%=92160 이상
        self.assertEqual(display.choose_net_unit(90000, "GB"), "MB")
        self.assertEqual(display.choose_net_unit(10, "GB"), "KB")

    def test_icon_values_fit_two_chars(self):
        cases = [
            (12.4, "KB", "12"),
            (99.9, "KB", "99"),
            (512, "MB", ".5"),
            (1000, "MB", ".9"),
            (95, "MB", ".1"),
            (2048.7, "MB", "2"),
            (500 * KB_PER_MB, "GB", ".4"),
            (2 * KB_PER_GB, "GB", "2"),
            (200 * KB_PER_GB, "GB", "99"),
        ]
        for kb_s, unit, expected in cases:
            self.assertEqual(display.format_net_icon_value(kb_s, unit), expected, (kb_s, unit))


class IconFaceTest(unittest.TestCase):
    def face(self, item, data=None, prev=None):
        return display.icon_face(item, sample_data() if data is None else data, THEME, prev)

    def test_ratio_items(self):
        self.assertEqual(self.face("cpu")[0], ("CPU", "45", display.GREEN))
        self.assertEqual(self.face("gpu")[0], ("GPU", "23", display.GREEN))
        self.assertEqual(self.face("gpu_temp")[0], ("G°C", "62", display.ORANGE))
        self.assertEqual(self.face("disk:C:\\")[0], ("C:", "58", display.ORANGE))

    def test_value_is_capped_at_99(self):
        data = sample_data()
        data["cpu"]["percent"] = 100.0
        self.assertEqual(self.face("cpu", data)[0], ("CPU", "99", display.RED))

    def test_missing_values(self):
        data = sample_data()
        data["gpu"] = None
        self.assertEqual(self.face("gpu_temp", data)[0], ("G°C", "--", display.GRAY))
        self.assertEqual(self.face("disk:Z:\\")[0], ("Z:", "--", display.GRAY))
        self.assertEqual(display.icon_face("cpu", None, THEME)[0], ("CPU", "--", display.GRAY))

    def test_network(self):
        face, unit = self.face("net_down")
        self.assertEqual(face, ("↓MB", "2", THEME["accent_net_down"]))
        self.assertEqual(unit, "MB")
        face, unit = self.face("net_up")
        self.assertEqual(face, ("↑MB", ".1", THEME["accent_net_up"]))

    def test_network_hysteresis_carries_over(self):
        data = sample_data()
        data["network"]["upload_kb_s"] = 95.0
        self.assertEqual(self.face("net_up", data, prev="MB")[0].label, "↑MB")
        self.assertEqual(self.face("net_up", data, prev=None)[0].label, "↑KB")


class IconTooltipTest(unittest.TestCase):
    def test_texts(self):
        data = sample_data()
        self.assertEqual(display.icon_tooltip("cpu", data), "CPU 사용률 45%")
        self.assertEqual(display.icon_tooltip("ram", data), "RAM 사용률 51% (8.2 / 16.0 GB)")
        self.assertEqual(display.icon_tooltip("gpu", data), "GPU 사용률 23% (VRAM 2.1 / 6.0 GB)")
        self.assertEqual(display.icon_tooltip("gpu_temp", data), "GPU 온도 62°C")
        self.assertEqual(display.icon_tooltip("net_down", data), "다운로드 2.0 MB/s (오늘 누적 2 MB)")
        self.assertEqual(display.icon_tooltip("net_up", data), "업로드 125 KB/s (오늘 누적 1 MB)")
        self.assertEqual(display.icon_tooltip("disk:C:\\", data),
                         "C: 드라이브 58% (120 / 256 GB)  R 1.0MB/s  W 0.0MB/s")
        self.assertEqual(display.icon_tooltip("disk:Z:\\", data), "Z: 드라이브 연결 안 됨")
        self.assertEqual(display.icon_tooltip("cpu", None), display.WAITING_TOOLTIP)

    def test_today_amount_in_gb(self):
        self.assertEqual(display.format_amount_mb(1228.8), "1.2 GB")


if __name__ == "__main__":
    unittest.main()
