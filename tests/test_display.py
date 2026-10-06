import unittest

from monitor import display


def sample_data():
    return {
        "cpu": {"percent": 45.2, "per_core": [40.0, 50.0]},
        "ram": {"used_gb": 8.2, "total_gb": 16.0, "percent": 51.3},
        "gpu": {"name": "RTX 2060", "percent": 23, "temp": 62, "vram_used_gb": 2.1, "vram_total_gb": 6.0},
        "disks": [
            {"mount": "C:\\", "used_gb": 120.0, "total_gb": 256.0, "percent": 58.0, "read_mb_s": 1.0, "write_mb_s": 0.0},
            {"mount": "D:\\", "used_gb": 800.0, "total_gb": 1000.0, "percent": 80.0, "read_mb_s": 0.0, "write_mb_s": 0.0},
        ],
        "network": {"upload_kb_s": 125.3, "download_kb_s": 2048.7, "today_upload_mb": 1.0, "today_download_mb": 2.0},
        "timestamp": 0.0,
    }


class LevelColorTest(unittest.TestCase):
    def test_boundaries(self):
        self.assertEqual(display.level_color(50), display.GREEN)
        self.assertEqual(display.level_color(50.4), display.ORANGE)
        self.assertEqual(display.level_color(80), display.ORANGE)
        self.assertEqual(display.level_color(80.1), display.RED)
        self.assertEqual(display.level_color(None), display.GRAY)


class FormatRateTest(unittest.TestCase):
    def test_units(self):
        self.assertEqual(display.format_rate(125.3), "125 KB/s")
        self.assertEqual(display.format_rate(2048.7), "2.0 MB/s")
        self.assertEqual(display.format_rate(2048.7, compact=True), "2.0M")
        self.assertEqual(display.format_rate(3 * 1024 * 1024, compact=True), "3.0G")


class CircleTooltipTest(unittest.TestCase):
    def test_doc_example(self):
        text = display.build_circle_tooltip(sample_data(), ["cpu", "ram", "gpu", "gpu_temp", "disk:C:\\"])
        self.assertEqual(text, "CPU: 45% | RAM: 8.2/16GB (51%)\nGPU: 23% 62°C | Disk C: 58%")

    def test_network_grouped(self):
        text = display.build_circle_tooltip(sample_data(), ["net_up", "net_down"])
        self.assertEqual(text, "↓2.0M ↑125K")

    def test_empty_and_waiting(self):
        self.assertEqual(display.build_circle_tooltip(sample_data(), []), display.EMPTY_TOOLTIP)
        self.assertEqual(display.build_circle_tooltip(None, ["cpu"]), display.WAITING_TOOLTIP)

    def test_missing_gpu_and_disk(self):
        data = sample_data()
        data["gpu"] = None
        text = display.build_circle_tooltip(data, ["gpu_temp", "disk:Z:\\"])
        self.assertEqual(text, "GPU: N/A")

    def test_long_tooltip_is_compacted_then_truncated(self):
        data = sample_data()
        data["disks"] = [
            {"mount": f"{c}:\\", "used_gb": 1.0, "total_gb": 2.0, "percent": 50.0, "read_mb_s": 0.0, "write_mb_s": 0.0}
            for c in "CDEFGHIJKLMNOPQRSTUVWXYZ"
        ]
        items = ["cpu", "ram", "gpu", "gpu_temp", "net_down", "net_up"] + [f"disk:{c}:\\" for c in "CDEFGHIJ"]
        text = display.build_circle_tooltip(data, items)
        self.assertLessEqual(len(text), display.TOOLTIP_MAX)
        self.assertIn("C:50%", text)  # 축약형

        items += [f"disk:{c}:\\" for c in "KLMNOPQRSTUVWXYZ"]
        self.assertEqual(len(display.build_circle_tooltip(data, items)), display.TOOLTIP_MAX)


if __name__ == "__main__":
    unittest.main()
