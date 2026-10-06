import unittest

from monitor import display, iconrender
from monitor.config import DEFAULTS, OVERLAY_LOOKS
from tests.test_display import sample_data

THEME = DEFAULTS["ui_theme"]
ITEMS = ["cpu", "ram", "gpu", "gpu_temp", "disk:C:\\", "net_down", "net_up"]


def has_visible_pixels(image) -> bool:
    return any(image.getchannel("A").tobytes())


def history():
    data = sample_data()
    return [dict(data, timestamp=float(t)) for t in range(0, 61, 2)]


class OverlayIconTest(unittest.TestCase):
    def test_label_and_badge_icons(self):
        for size in (24, 32, 40):
            for look in ("label", "badge"):
                for label in ("CPU", "G°C", "↓MB", "↑KB", "C:"):
                    image = iconrender.render_overlay_icon(look, label, "88", "#42A5F5", size)
                    self.assertEqual(image.size, (size, size))
                    self.assertTrue(has_visible_pixels(image), (look, size, label))

    @staticmethod
    def _rendered_degree_gap(font, kerned: bool) -> int:
        """'°C'를 실제로 그렸을 때 '°' 잉크 끝과 'C' 잉크 시작 사이 픽셀 수."""
        from PIL import Image, ImageDraw

        size = font.size
        image = Image.new("L", (size * 6, size * 3), 0)
        draw = ImageDraw.Draw(image)
        if kerned:
            iconrender._draw_text(draw, (size, size // 2), "°C", font, 255)
        else:
            draw.text((size, size // 2), "°C", font=font, fill=255)
        degree_right = size + iconrender._ink_range(font, "°")[1]
        columns = [x for x in range(image.width) if any(image.getpixel((x, y)) for y in range(image.height))]
        return min(x for x in columns if x >= degree_right) - degree_right

    def test_degree_sign_is_tightened_per_font(self):
        from monitor import fonts

        font_files = ["malgunbd.ttf", "malgun.ttf", iconrender.DEFAULT_OVERLAY_FONT_FILE]
        user_font = fonts.resolve_font_file("DX영화자막 M")  # 사용자가 쓰는 글꼴 (이 PC에 있을 때만)
        if user_font:
            font_files.append(user_font)
        for font_file in font_files:
            for px in (10, 12, 20):
                font = iconrender._truetype(font_file, px)
                kern = iconrender._degree_kern_px(font, "C")
                before = self._rendered_degree_gap(font, kerned=False)
                after = self._rendered_degree_gap(font, kerned=True)
                min_gap = iconrender.DEGREE_MIN_GAP_RATIO * px
                # 넓던 간격은 최소 간격 근처까지 줄고, 원래 좁던 글꼴은 건드리지 않아 겹치지 않는다
                self.assertLessEqual(after, max(before if kern == 0 else 0, min_gap + 1.5), (font_file, px))
                self.assertGreaterEqual(after, min(before, min_gap - 1.5), (font_file, px))
                self.assertEqual(iconrender._text_length("G°C", font), font.getlength("G°C") - kern)
                self.assertEqual(iconrender._text_length("62°", font), font.getlength("62°"))  # 끝의 °는 그대로
        if user_font:
            wide = iconrender._truetype(user_font, 12)
            self.assertGreater(iconrender._degree_kern_px(wide, "C"), 0)  # 벌어져 보이던 글꼴은 실제로 당겨짐

    def test_unknown_font_falls_back(self):
        image = iconrender.render_overlay_icon("label", "CPU", "45", "#4CAF50", 32, font_file="nope.ttf")
        self.assertTrue(has_visible_pixels(image))

    def test_circle(self):
        self.assertEqual(iconrender.render_circle("#4CAF50").size, (64, 64))


class OverlayStripTest(unittest.TestCase):
    def test_all_looks_and_sizes(self):
        for look in OVERLAY_LOOKS:
            for size in (24, 32, 40):
                strip, units = iconrender.render_overlay_strip(
                    look, ITEMS, sample_data(), history(), THEME, size, 48, now=60.0)
                image = strip.image
                self.assertEqual(image.height, 48, look)
                self.assertTrue(has_visible_pixels(image), (look, size))
                self.assertEqual([box[0] for box in strip.hitboxes], ITEMS, look)
                for _, x0, y0, x1, y1 in strip.hitboxes:
                    self.assertTrue(0 <= x0 < x1 <= image.width and 0 <= y0 < y1 <= image.height, (look, size))

    def test_designs_fit_in_short_taskbar(self):
        for look in OVERLAY_LOOKS:
            strip, _ = iconrender.render_overlay_strip(look, ITEMS, sample_data(), history(), THEME, 40, 40, now=60.0)
            self.assertEqual(strip.image.height, 40)
            for _, _, y0, _, y1 in strip.hitboxes:
                self.assertLessEqual(y1, 40, look)

    def test_without_data_or_history(self):
        for look in OVERLAY_LOOKS:
            strip, _ = iconrender.render_overlay_strip(look, ITEMS, None, [], THEME, 32, 48)
            self.assertEqual(len(strip.hitboxes), len(ITEMS), look)

    def test_width_does_not_change_with_values(self):
        low, high = sample_data(), sample_data()
        low["cpu"]["percent"], high["cpu"]["percent"] = 3.0, 100.0
        low["network"]["download_kb_s"], high["network"]["download_kb_s"] = 1.0, 900000.0
        for look in OVERLAY_LOOKS:
            a, _ = iconrender.render_overlay_strip(look, ITEMS, low, [], THEME, 32, 48)
            b, _ = iconrender.render_overlay_strip(look, ITEMS, high, [], THEME, 32, 48)
            self.assertEqual(a.image.width, b.image.width, look)

    def test_net_units_are_carried(self):
        _, units = iconrender.render_overlay_strip("label", ["net_down"], sample_data(), [], THEME, 32, 48)
        self.assertEqual(units, {"net_down": "MB"})

    def test_preview(self):
        for look in OVERLAY_LOOKS:
            image = iconrender.render_overlay_preview(look, ITEMS, sample_data(), history(), THEME)
            self.assertEqual(image.height, 48)
        self.assertEqual(iconrender.render_overlay_preview("label", [], None, [], THEME).height, 48)
        self.assertGreater(iconrender.build_preview().height, 0)


class TextDesignTest(unittest.TestCase):
    def test_parts(self):
        data = sample_data()
        self.assertEqual(display.text_design_parts("cpu", data), ("CPU", "45%", None))
        self.assertEqual(display.text_design_parts("gpu_temp", data), ("G°C", "62°", None))
        self.assertEqual(display.text_design_parts("net_down", data), ("↓", "2.0M", None))
        self.assertEqual(display.text_design_parts("disk:C:\\", data), ("C:", "58%", None))
        self.assertEqual(display.text_design_parts("cpu", None), ("CPU", "--", None))
        data["ram"]["percent"] = 92.0
        self.assertEqual(display.text_design_parts("ram", data), ("RAM", "92%", display.RED))

    def test_look_labels_match_config(self):
        self.assertEqual(tuple(value for value, _ in display.OVERLAY_LOOK_LABELS), OVERLAY_LOOKS)


if __name__ == "__main__":
    unittest.main()
