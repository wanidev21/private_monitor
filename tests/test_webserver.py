import os
import shutil
import tempfile
import unittest

from fastapi.testclient import TestClient

from monitor import paths
from monitor.config import OVERLAY_LOOKS, Config
from monitor.webserver import create_app, host_allowed
from tests.test_display import sample_data

PNG_SIGNATURE = b"\x89PNG\r\n\x1a\n"


class FakeCollector:
    def __init__(self, latest):
        self.latest = latest

    def get_history(self, window_sec=60):
        return [self.latest] if self.latest else []


class FakeTray:
    def __init__(self):
        self.wakes = 0

    def wake(self):
        self.wakes += 1


class HostAllowedTest(unittest.TestCase):
    def test_hosts(self):
        self.assertTrue(host_allowed("localhost:18099"))
        self.assertTrue(host_allowed("127.0.0.1:18099"))
        self.assertTrue(host_allowed("LOCALHOST"))
        self.assertFalse(host_allowed("evil.example:18099"))
        self.assertFalse(host_allowed("localhost.evil.example"))
        self.assertFalse(host_allowed("[::1]:18099"))
        self.assertFalse(host_allowed(""))


class FakeNotifier:
    def __init__(self):
        self.tests = 0

    def send_test(self):
        self.tests += 1


class WebserverTest(unittest.TestCase):
    def setUp(self):
        self.home = tempfile.mkdtemp(prefix="pcmon-web-")
        self._old_home = os.environ.get(paths.HOME_ENV)
        os.environ[paths.HOME_ENV] = self.home
        self.addCleanup(self._restore_home)

        self.config = Config()
        self.collector = FakeCollector(sample_data())
        self.tray = FakeTray()
        self.notifier = FakeNotifier()
        app = create_app(self.collector, self.config, ["Segoe UI", "Arial"], self.tray, notifier=self.notifier)
        self.client = TestClient(app, base_url="http://localhost:18099")

    def test_auto_start_change_is_applied(self):
        applied = []
        app = create_app(self.collector, self.config, [], self.tray, sync_auto_start=applied.append)
        client = TestClient(app, base_url="http://localhost:18099")
        self.assertEqual(client.post("/api/config", json={"auto_start": False}).status_code, 200)
        self.assertEqual(applied, [False])
        client.post("/api/config", json={"overlay_icon_look": "ring"})  # 다른 설정은 레지스트리를 건드리지 않음
        self.assertEqual(applied, [False])

    def test_alerts_test(self):
        self.assertEqual(self.client.post("/api/alerts/test", json={}).status_code, 200)
        self.assertEqual(self.notifier.tests, 1)
        form = self.client.post("/api/alerts/test", content="x=1",
                                headers={"content-type": "application/x-www-form-urlencoded"})
        self.assertEqual(form.status_code, 415)
        self.assertEqual(self.notifier.tests, 1)
        no_notifier = TestClient(create_app(self.collector, self.config, [], self.tray),
                                 base_url="http://localhost:18099")
        self.assertEqual(no_notifier.post("/api/alerts/test", json={}).status_code, 503)

    def _restore_home(self):
        if self._old_home is None:
            os.environ.pop(paths.HOME_ENV, None)
        else:
            os.environ[paths.HOME_ENV] = self._old_home
        shutil.rmtree(self.home, ignore_errors=True)

    def test_index(self):
        res = self.client.get("/")
        self.assertEqual(res.status_code, 200)
        self.assertIn("PC System Monitor", res.text)
        self.assertIn('"overlay_icon_look": "label"', res.text)
        self.assertNotIn("tray-preview", res.text)

    def test_foreign_host_is_rejected(self):
        res = self.client.get("/api/status", headers={"host": "evil.example"})
        self.assertEqual(res.status_code, 403)

    def test_status(self):
        res = self.client.get("/api/status")
        self.assertEqual(res.status_code, 200)
        self.assertEqual(res.json()["cpu"]["percent"], 45.2)
        self.collector.latest = None
        self.assertEqual(self.client.get("/api/status").status_code, 503)

    def test_history_and_fonts(self):
        self.assertEqual(len(self.client.get("/api/history").json()), 1)
        self.assertEqual(self.client.get("/api/fonts").json(), ["Segoe UI", "Arial"])

    def test_get_config(self):
        body = self.client.get("/api/config").json()
        self.assertEqual(body["overlay_font"], "Segoe UI Semibold")
        self.assertNotIn("tray_icon_style", body)

    def test_post_config_updates_and_wakes_tray(self):
        res = self.client.post("/api/config", json={"overlay_icon_look": "spark", "ui_theme": {"accent_cpu": "#123456"}})
        self.assertEqual(res.status_code, 200)
        self.assertTrue(res.json()["changed"])
        self.assertEqual(self.config.overlay_icon_look, "spark")
        self.assertEqual(self.config.ui_theme["accent_cpu"], "#123456")
        self.assertEqual(self.tray.wakes, 1)

    def test_post_config_invalid_value(self):
        res = self.client.post("/api/config", json={"overlay_icon_look": "circle"})
        self.assertEqual(res.status_code, 422)
        self.assertIn("overlay_icon_look", res.json()["detail"])
        self.assertEqual(self.config.overlay_icon_look, "label")
        self.assertEqual(self.tray.wakes, 0)

    def test_post_config_requires_json_content_type(self):
        res = self.client.post("/api/config", content="overlay_icon_look=badge",
                               headers={"content-type": "application/x-www-form-urlencoded"})
        self.assertEqual(res.status_code, 415)
        self.assertEqual(self.config.overlay_icon_look, "label")

    def test_post_config_rejects_non_object(self):
        self.assertEqual(self.client.post("/api/config", json=[1, 2]).status_code, 422)
        bad = self.client.post("/api/config", content="{nope", headers={"content-type": "application/json"})
        self.assertEqual(bad.status_code, 400)

    def test_overlay_preview(self):
        for look in OVERLAY_LOOKS:
            res = self.client.get(f"/api/overlay-preview?look={look}")
            self.assertEqual(res.status_code, 200, look)
            self.assertEqual(res.headers["content-type"], "image/png")
            self.assertTrue(res.content.startswith(PNG_SIGNATURE))
        self.assertEqual(self.client.get("/api/overlay-preview?look=nope").status_code, 422)
        self.assertEqual(self.client.get("/api/tray-preview?style=label").status_code, 404)

    def test_overlay_preview_without_data(self):
        self.collector.latest = None
        self.assertEqual(self.client.get("/api/overlay-preview?look=spark").status_code, 200)


if __name__ == "__main__":
    unittest.main()
