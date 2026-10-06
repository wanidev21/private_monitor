import os
import unittest

from monitor import fonts


class FontsTest(unittest.TestCase):
    def test_resolves_system_fonts(self):
        path = fonts.resolve_font_file("Segoe UI Semibold")
        self.assertIsNotNone(path)
        self.assertEqual(os.path.basename(path).lower(), "seguisb.ttf")
        self.assertEqual(os.path.basename(fonts.resolve_font_file("  arial ")).lower(), "arial.ttf")

    def test_unknown_font(self):
        self.assertIsNone(fonts.resolve_font_file("No Such Font 12345"))
        self.assertIsNone(fonts.resolve_font_file(""))
        self.assertIsNone(fonts.resolve_font_file(None))


if __name__ == "__main__":
    unittest.main()
