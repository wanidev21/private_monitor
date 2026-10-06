import unittest
import uuid
import winreg

from monitor import notifyicon


def _delete_tree(root, path):
    try:
        with winreg.OpenKey(root, path, 0, winreg.KEY_ALL_ACCESS) as key:
            while True:
                try:
                    child = winreg.EnumKey(key, 0)
                except OSError:
                    break
                _delete_tree(root, f"{path}\\{child}")
        winreg.DeleteKey(root, path)
    except FileNotFoundError:
        pass


class IconGuidTest(unittest.TestCase):
    def test_deterministic_and_distinct(self):
        a = notifyicon.icon_guid("cpu", r"C:\App\PCMonitor.exe")
        self.assertEqual(a, notifyicon.icon_guid("cpu", r"c:\app\pcmonitor.exe"))
        self.assertNotEqual(a, notifyicon.icon_guid("ram", r"C:\App\PCMonitor.exe"))
        self.assertNotEqual(a, notifyicon.icon_guid("cpu", r"C:\Python314\python.exe"))

    def test_process_image_path(self):
        self.assertTrue(notifyicon.process_image_path().lower().endswith(".exe"))


class PromoteTest(unittest.TestCase):
    """실제 NotifyIconSettings 대신 테스트 전용 HKCU 키를 사용."""

    def setUp(self):
        self.base = f"Software\\PCMonitorTest-{uuid.uuid4().hex}\\NotifyIconSettings"
        self.addCleanup(_delete_tree, winreg.HKEY_CURRENT_USER, self.base.rsplit("\\", 1)[0])
        self.guid = uuid.uuid4()

    def _add_entry(self, name, guid, is_promoted=None):
        with winreg.CreateKey(winreg.HKEY_CURRENT_USER, f"{self.base}\\{name}") as key:
            winreg.SetValueEx(key, "IconGuid", 0, winreg.REG_SZ, "{" + str(guid).upper() + "}")
            winreg.SetValueEx(key, "ExecutablePath", 0, winreg.REG_SZ, r"C:\Python314\python.exe")
            if is_promoted is not None:
                winreg.SetValueEx(key, "IsPromoted", 0, winreg.REG_DWORD, is_promoted)

    def _is_promoted(self, name):
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, f"{self.base}\\{name}") as key:
            return winreg.QueryValueEx(key, "IsPromoted")[0]

    def test_not_found(self):
        self._add_entry("111", uuid.uuid4())
        self.assertEqual(notifyicon.promote_icon(self.guid, self.base), "not_found")

    def test_missing_base_key(self):
        self.assertEqual(notifyicon.promote_icon(self.guid, self.base + "-none"), "not_found")

    def test_promotes_new_entry(self):
        self._add_entry("111", uuid.uuid4())
        self._add_entry("222", self.guid)
        self.assertEqual(notifyicon.promote_icon(self.guid, self.base), "promoted")
        self.assertEqual(self._is_promoted("222"), 1)

    def test_respects_user_choice(self):
        self._add_entry("222", self.guid, is_promoted=0)
        self.assertEqual(notifyicon.promote_icon(self.guid, self.base), "already_set")
        self.assertEqual(self._is_promoted("222"), 0)

    def test_wait_gives_up_after_timeout(self):
        result = notifyicon.promote_when_registered(self.guid, timeout_sec=0.05, poll_sec=0.01, base_key=self.base)
        self.assertEqual(result, "not_found")


if __name__ == "__main__":
    unittest.main()
