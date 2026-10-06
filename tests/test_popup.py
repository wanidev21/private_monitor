import unittest

from monitor import display, winapi
from monitor.popup import calc_popup_position
from tests.test_display import sample_data

WORK = (0, 0, 1920, 1032)  # 하단 작업표시줄 48px


class PopupPositionTest(unittest.TestCase):
    def cursor(self, x, y, work=WORK):
        return winapi.CursorMonitor(x, y, work, 1.0)

    def test_bottom_taskbar_right_corner(self):
        self.assertEqual(calc_popup_position(self.cursor(1700, 1050), 400, 300), (1920 - 400 - 10, 1032 - 300 - 10))

    def test_top_left_corner(self):
        self.assertEqual(calc_popup_position(self.cursor(20, 5), 400, 300), (10, 10))

    def test_secondary_monitor_with_negative_origin(self):
        work = (1920, -726, 3360, 1786)
        self.assertEqual(calc_popup_position(self.cursor(3000, 1800, work), 400, 300),
                         (3360 - 400 - 10, 1786 - 300 - 10))


class PopupTextTest(unittest.TestCase):
    def test_sections_order_and_grouping(self):
        items = ["net_up", "disk:D:\\", "gpu_temp", "ram", "disk:C:\\", "cpu"]
        self.assertEqual(display.popup_sections(items, sample_data()),
                         ["cpu", "ram", "gpu", "disk:D:\\", "disk:C:\\", "net"])

    def test_disconnected_disk_is_skipped(self):
        self.assertEqual(display.popup_sections(["disk:Z:\\"], sample_data()), [])
        self.assertEqual(display.popup_sections(["disk:Z:\\"], None), ["disk:Z:\\"])

    def test_texts(self):
        data = sample_data()
        self.assertEqual(display.popup_texts("cpu", data), ("45.2%", "", None))
        self.assertEqual(display.popup_texts("ram", data), ("51%", "8.2 / 16.0 GB", None))
        self.assertEqual(display.popup_texts("gpu", data), ("62°C   23%", "VRAM 2.1 / 6.0 GB", None))
        self.assertEqual(display.popup_texts("disk:C:\\", data),
                         ("120 / 256 GB (58%)", "R 1.0MB/s   W 0.0MB/s", 58.0))
        self.assertEqual(display.popup_texts("net", data), ("↓ 2.0 MB/s\n↑ 125 KB/s", "오늘 ↓ 2 MB   ↑ 1 MB", None))
        self.assertEqual(display.popup_texts("cpu", None), ("--", "", None))

    def test_missing_values(self):
        data = sample_data()
        data["gpu"] = None
        self.assertEqual(display.popup_texts("gpu", data), ("N/A", "", None))
        self.assertEqual(display.popup_texts("disk:Z:\\", data), ("연결 안 됨", "", None))

    def test_titles_and_sublines(self):
        self.assertEqual(display.popup_title("disk:C:\\"), "C:")
        self.assertEqual(display.popup_title("net"), "Network")
        self.assertTrue(display.popup_has_subline("disk:C:\\"))
        self.assertFalse(display.popup_has_subline("cpu"))


class CursorMonitorApiTest(unittest.TestCase):
    def test_cursor_monitor(self):
        cursor = winapi.get_cursor_monitor()
        left, top, right, bottom = cursor.work
        self.assertLess(left, right)
        self.assertLess(top, bottom)
        self.assertGreater(cursor.scale, 0)


if __name__ == "__main__":
    unittest.main()
