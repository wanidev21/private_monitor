import unittest

from monitor.tray import CIRCLE_ICON_ITEM, desired_layout


class DesiredLayoutTest(unittest.TestCase):
    def test_no_tray_icon_while_overlay_is_shown(self):
        self.assertEqual(desired_layout(False), ("none", ()))

    def test_circle_icon_when_overlay_cannot_be_shown(self):
        self.assertEqual(desired_layout(True), ("circle", (CIRCLE_ICON_ITEM,)))


if __name__ == "__main__":
    unittest.main()
