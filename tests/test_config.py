import json
import os
import shutil
import tempfile
import unittest

from monitor import paths
from monitor.config import DEFAULTS, OVERLAY_LOOKS, Config


class ConfigTest(unittest.TestCase):
    def setUp(self):
        self.home = tempfile.mkdtemp(prefix="pcmon-test-")
        self._old_home = os.environ.get(paths.HOME_ENV)
        os.environ[paths.HOME_ENV] = self.home
        self.path = paths.get_config_path()

    def tearDown(self):
        if self._old_home is None:
            os.environ.pop(paths.HOME_ENV, None)
        else:
            os.environ[paths.HOME_ENV] = self._old_home
        shutil.rmtree(self.home, ignore_errors=True)

    def _write_raw(self, text: str):
        with open(self.path, "w", encoding="utf-8") as f:
            f.write(text)

    def _read_file(self) -> dict:
        with open(self.path, encoding="utf-8") as f:
            return json.load(f)

    def test_creates_default_file(self):
        config = Config()
        self.assertEqual(config.snapshot(), DEFAULTS)
        self.assertEqual(self._read_file(), DEFAULTS)
        self.assertEqual(config.web_port, 18099)

    def test_fills_missing_keys_and_keeps_existing(self):
        self._write_raw(json.dumps({"web_port": 20000, "ui_theme": {"bg": "#000000"}}))
        config = Config()
        self.assertEqual(config.web_port, 20000)
        self.assertEqual(config.ui_theme["bg"], "#000000")
        self.assertEqual(config.ui_theme["fg"], DEFAULTS["ui_theme"]["fg"])
        self.assertEqual(self._read_file()["overlay_icon_look"], "label")

    def test_removed_tray_icon_style_is_dropped(self):
        self._write_raw(json.dumps({"tray_icon_style": "badge", "overlay_icon_look": "ring"}))
        config = Config()
        self.assertEqual(config.overlay_icon_look, "ring")
        self.assertNotIn("tray_icon_style", self._read_file())
        with self.assertRaises(AttributeError):
            config.tray_icon_style

    def test_corrupted_file_is_backed_up(self):
        self._write_raw("{ not json")
        config = Config()
        self.assertEqual(config.snapshot(), DEFAULTS)
        self.assertTrue(os.path.exists(self.path + ".bak"))

    def test_invalid_values_on_load_fall_back_to_default(self):
        self._write_raw(json.dumps({"update_interval_sec": 99, "overlay_icon_look": "weird",
                                    "ui_theme": {"bg": "red"}}))
        config = Config()
        self.assertEqual(config.update_interval_sec, 2)
        self.assertEqual(config.overlay_icon_look, "label")
        self.assertEqual(config.ui_theme["bg"], "#1E1E1E")

    def test_legacy_network_item_is_split(self):
        self._write_raw(json.dumps({"tray_visible_items": ["cpu", "network", "disk:c:\\", "bogus", "cpu"]}))
        config = Config()
        self.assertEqual(config.tray_visible_items, ["cpu", "net_down", "net_up", "disk:C:\\"])

    def test_update_partial_nested(self):
        config = Config()
        changed = config.update({"alert_thresholds": {"cpu_percent": 80}})
        self.assertTrue(changed)
        self.assertEqual(config.alert_thresholds["cpu_percent"], 80)
        self.assertEqual(config.alert_thresholds["ram_percent"], 90)
        self.assertEqual(self._read_file()["alert_thresholds"]["cpu_percent"], 80)
        self.assertEqual(config.version, 1)

    def test_update_same_value_does_not_bump_version(self):
        config = Config()
        self.assertFalse(config.set("web_port", 18099))
        self.assertEqual(config.version, 0)

    def test_update_invalid_raises_and_changes_nothing(self):
        config = Config()
        with self.assertRaises(ValueError) as ctx:
            config.update({"web_port": 18100, "alert_thresholds": {"gpu_temp": 500}})
        self.assertIn("alert_thresholds.gpu_temp", str(ctx.exception))
        self.assertEqual(config.web_port, 18099)
        self.assertEqual(self._read_file()["web_port"], 18099)
        self.assertEqual(config.version, 0)

    def test_bool_is_not_accepted_as_int(self):
        config = Config()
        with self.assertRaises(ValueError):
            config.set("update_interval_sec", True)

    def test_returned_values_are_copies(self):
        config = Config()
        items = config.tray_visible_items
        items.append("disk:C:\\")
        self.assertNotIn("disk:C:\\", config.tray_visible_items)

    def test_color_is_uppercased(self):
        config = Config()
        config.update({"ui_theme": {"accent_cpu": "#abcdef"}})
        self.assertEqual(config.ui_theme["accent_cpu"], "#ABCDEF")

    def test_overlay_settings_validation(self):
        config = Config()
        self.assertEqual((config.overlay_icon_look, config.overlay_icon_size, config.overlay_font),
                         ("label", 32, "Segoe UI Semibold"))
        with self.assertRaises(ValueError):
            config.set("overlay_icon_size", 48)
        with self.assertRaises(ValueError):
            config.set("overlay_icon_look", "circle")
        for look in OVERLAY_LOOKS:
            config.set("overlay_icon_look", look)
            self.assertEqual(config.overlay_icon_look, look)


if __name__ == "__main__":
    unittest.main()
