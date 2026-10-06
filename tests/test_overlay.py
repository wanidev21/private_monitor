import unittest

from PIL import Image, ImageDraw

from monitor import winapi
from monitor.overlay import CLOCK_GAP_TOLERANCE, compute_placement, find_clock_left

BAR = (0, 1032, 1920, 1080)
TASKS = (55, 1032, 671, 1080)


class ComputePlacementTest(unittest.TestCase):
    def test_right_aligned_next_to_anchor(self):
        self.assertEqual(compute_placement(1514, BAR, TASKS, 240, 8), (1514 - 8 - 240, 1032, 240, 48))

    def test_hidden_when_it_would_overlap_app_buttons(self):
        self.assertIsNone(compute_placement(900, BAR, (55, 1032, 700, 1080), 240, 8))

    def test_exact_fit_is_allowed(self):
        # x = 1000 - 8 - 240 = 752, 왼쪽 한계 = 744 + 8 = 752
        self.assertIsNotNone(compute_placement(1000, BAR, (55, 1032, 744, 1080), 240, 8))

    def test_without_task_buttons_uses_bar_left(self):
        self.assertIsNotNone(compute_placement(1514, BAR, None, 240, 8))

    def test_empty_strip(self):
        self.assertIsNone(compute_placement(1514, BAR, TASKS, 0, 8))


class FindClockLeftTest(unittest.TestCase):
    BG = (220, 220, 220)

    def strip(self, blocks, width=400):
        image = Image.new("RGB", (width, 48), self.BG)
        draw = ImageDraw.Draw(image)
        for x0, x1 in blocks:
            draw.rectangle((x0, 12, x1 - 1, 36), fill=(30, 30, 30))
        return image

    def test_finds_clock_with_small_gaps(self):
        # 시계 글자: 330~350, 353~380 (틈 3px은 이어 붙임)
        self.assertEqual(find_clock_left(self.strip([(330, 350), (353, 380)]), 180), 330)

    def test_includes_notification_bell_right_of_clock(self):
        # 시계(300~360) + 틈 14px + 알림 종(374~392): 종만 시계로 잡으면 오버레이가 시계를 덮는다
        self.assertLess(14, CLOCK_GAP_TOLERANCE)
        self.assertEqual(find_clock_left(self.strip([(300, 360), (374, 392)]), 180), 300)

    def test_stops_at_wide_gap(self):
        # 앱 버튼(200~250)과 시계(330~) 사이 틈이 허용치보다 넓으면 시계만 찾는다
        self.assertEqual(find_clock_left(self.strip([(200, 330 - CLOCK_GAP_TOLERANCE - 10), (330, 380)]), 180), 330)

    def test_ignores_own_overlay(self):
        # 자기 오버레이(250~318)는 시계와 12px 떨어져 있어 틈 허용치 안이지만 ignore로 제외
        image = self.strip([(250, 318), (330, 380)])
        self.assertEqual(find_clock_left(image, 180, ignore=(250, 318)), 330)

    def test_no_content(self):
        self.assertIsNone(find_clock_left(self.strip([]), 180))

    def test_content_outside_search_width_is_ignored(self):
        self.assertIsNone(find_clock_left(self.strip([(10, 60)]), 180))


class TaskbarApiTest(unittest.TestCase):
    """실제 작업표시줄이 있는 Windows 11 환경에서만 의미 있는 확인."""

    def test_find_taskbars(self):
        taskbars = winapi.find_taskbars()
        if not taskbars:
            self.skipTest("no taskbar")
        primary = taskbars[0]
        self.assertIsNotNone(primary.notify)
        self.assertLess(primary.notify[0], primary.bar[2])
        for secondary in taskbars[1:]:
            self.assertIsNone(secondary.notify)
            self.assertGreaterEqual(secondary.dpi, 96)

    def test_covered_taskbars_types(self):
        taskbars = winapi.find_taskbars()
        covered = winapi.covered_taskbars(taskbars)
        self.assertIsInstance(covered, set)
        self.assertTrue(covered <= {geo.hwnd for geo in taskbars})
        self.assertTrue(all(isinstance(w, winapi.ZWindow) for w in winapi.zorder_windows()[:5]))


class FindCoveredTaskbarsTest(unittest.TestCase):
    PRIMARY, SECONDARY = 100, 200
    MONITORS = {PRIMARY: (0, 0, 1920, 1080), SECONDARY: (1920, -726, 3360, 1834)}

    def z(self, hwnd, rect=None, eligible=True):
        return winapi.ZWindow(hwnd, rect, eligible)

    def test_fullscreen_window_above_taskbar_covers_only_that_monitor(self):
        windows = [self.z(1, (0, 0, 1920, 1080)), self.z(self.PRIMARY, (0, 1032, 1920, 1080)),
                   self.z(self.SECONDARY, (1920, 1786, 3360, 1834))]
        self.assertEqual(winapi.find_covered_taskbars(windows, self.MONITORS), {self.PRIMARY})

    def test_fullscreen_window_below_taskbar_does_not_cover(self):
        # 전체화면 창이 전경이 아니게 되어 작업표시줄이 그 위로 올라온 경우
        windows = [self.z(self.PRIMARY, (0, 1032, 1920, 1080)), self.z(1, (0, 0, 1920, 1080)),
                   self.z(self.SECONDARY, (1920, 1786, 3360, 1834))]
        self.assertEqual(winapi.find_covered_taskbars(windows, self.MONITORS), set())

    def test_maximized_or_ineligible_windows_do_not_cover(self):
        windows = [self.z(1, (-8, -8, 1928, 1040)),                  # 최대화: 작업표시줄 영역을 덮지 않음
                   self.z(2, (0, 0, 1920, 1080), eligible=False),     # 클릭 통과 오버레이/숨김 창
                   self.z(self.PRIMARY), self.z(self.SECONDARY)]
        self.assertEqual(winapi.find_covered_taskbars(windows, self.MONITORS), set())

    def test_window_spanning_both_monitors_covers_both(self):
        windows = [self.z(1, (0, -726, 3360, 1834)), self.z(self.SECONDARY), self.z(self.PRIMARY)]
        self.assertEqual(winapi.find_covered_taskbars(windows, self.MONITORS), {self.PRIMARY, self.SECONDARY})


if __name__ == "__main__":
    unittest.main()
