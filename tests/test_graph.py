import unittest

from monitor import graph
from monitor.popup import core_grid


def sample(t, cpu=None, down=None, up=None):
    return {
        "timestamp": t,
        "cpu": {"percent": cpu, "per_core": []} if cpu is not None else None,
        "network": {"download_kb_s": down, "upload_kb_s": up} if down is not None else None,
    }


class BlendTest(unittest.TestCase):
    def test_blend(self):
        self.assertEqual(graph.blend("#FFFFFF", "#000000", 1.0), "#FFFFFF")
        self.assertEqual(graph.blend("#FFFFFF", "#000000", 0.0), "#000000")
        self.assertEqual(graph.blend("#646464", "#000000", 0.5), "#323232")


class SeriesTest(unittest.TestCase):
    def test_series_from_history(self):
        history = [sample(1, cpu=10, down=100, up=5), sample(2)]
        self.assertEqual(graph.series_from_history(history, "cpu"), [(1, 10), (2, None)])
        self.assertEqual(graph.series_from_history(history, "net_up"), [(1, 5), (2, None)])

    def test_series_for_temperature_and_disk(self):
        history = [
            {"timestamp": 1, "gpu": {"percent": 20, "temp": 55}, "disks": [{"mount": "C:\\", "percent": 40.0}]},
            {"timestamp": 2, "gpu": None, "disks": []},
        ]
        self.assertEqual(graph.series_from_history(history, "gpu_temp"), [(1, 55), (2, None)])
        self.assertEqual(graph.series_from_history(history, "disk:C:\\"), [(1, 40.0), (2, None)])

    def test_auto_scale(self):
        self.assertEqual(graph.auto_scale([]), graph.NET_MIN_SCALE_KB)
        self.assertAlmostEqual(graph.auto_scale([1000.0, 200.0]), 1150.0)


class SegmentsTest(unittest.TestCase):
    def test_mapping(self):
        # now=100, 너비 61 → 1초당 1px. 최신 샘플은 오른쪽 끝, 60초 전은 왼쪽 끝
        segs = graph.to_segments([(40, 0), (70, 50), (100, 100)], now=100, width=61, height=41, vmax=100)
        self.assertEqual(segs, [[(0.0, 40.0), (30.0, 20.0), (60.0, 0.0)]])

    def test_old_samples_dropped_and_gaps_split(self):
        series = [(10, 50), (50, 10), (60, None), (70, 20), (80, 30)]
        segs = graph.to_segments(series, now=100, width=61, height=41, vmax=100)
        self.assertEqual(len(segs), 2)
        self.assertEqual(len(segs[0]), 1)
        self.assertEqual(len(segs[1]), 2)

    def test_values_clamped(self):
        segs = graph.to_segments([(100, 250)], now=100, width=61, height=41, vmax=100)
        self.assertEqual(segs, [[(60.0, 0.0)]])


class CoreGridTest(unittest.TestCase):
    def test_grid(self):
        self.assertEqual(core_grid(0), (0, 0))
        self.assertEqual(core_grid(1), (1, 1))
        self.assertEqual(core_grid(8), (2, 4))
        self.assertEqual(core_grid(16), (4, 4))
        self.assertEqual(core_grid(12), (4, 3))


if __name__ == "__main__":
    unittest.main()
