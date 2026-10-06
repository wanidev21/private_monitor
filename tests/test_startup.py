import os
import sys
import unittest
import uuid
import winreg
from unittest import mock

from monitor import startup


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


class LaunchCommandTest(unittest.TestCase):
    def test_dev_command_uses_venv_pythonw_and_absolute_main(self):
        command = startup.get_launch_command()
        self.assertTrue(command.startswith('"') and command.endswith('"'))
        exe, main_py = command[1:-1].split('" "')
        self.assertTrue(os.path.isabs(exe) and os.path.exists(exe))
        self.assertEqual(os.path.basename(main_py), "main.py")
        self.assertTrue(os.path.exists(main_py))

    def test_frozen_command_is_quoted_exe(self):
        with mock.patch.object(sys, "frozen", True, create=True), \
                mock.patch.object(sys, "executable", r"C:\Program Files\PC Monitor\PCMonitor.exe"):
            self.assertEqual(startup.get_launch_command(), r'"C:\Program Files\PC Monitor\PCMonitor.exe"')


class SyncTest(unittest.TestCase):
    """실제 Run 키 대신 테스트 전용 HKCU 키 사용."""

    def setUp(self):
        self.root = f"Software\\PCMonitorTest-{uuid.uuid4().hex}"
        self.key = self.root + "\\Run"
        self.addCleanup(_delete_tree, winreg.HKEY_CURRENT_USER, self.root)
        with winreg.CreateKey(winreg.HKEY_CURRENT_USER, self.key) as key:
            winreg.SetValueEx(key, "OtherApp", 0, winreg.REG_SZ, r'"C:\Other\app.exe"')

    def other_app(self):
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, self.key) as key:
            return winreg.QueryValueEx(key, "OtherApp")[0]

    def test_register_update_remove(self):
        self.assertEqual(startup.sync_auto_start(True, '"C:\\a\\PCMonitor.exe"', self.key), "registered")
        self.assertTrue(startup.is_auto_start_enabled(self.key))
        self.assertEqual(startup.sync_auto_start(True, '"c:\\A\\pcmonitor.exe"', self.key), "unchanged")
        self.assertEqual(startup.sync_auto_start(True, '"D:\\moved\\PCMonitor.exe"', self.key), "updated")
        self.assertEqual(startup.get_registered_command(self.key), '"D:\\moved\\PCMonitor.exe"')
        self.assertEqual(startup.sync_auto_start(False, run_key=self.key), "removed")
        self.assertFalse(startup.is_auto_start_enabled(self.key))
        self.assertEqual(startup.sync_auto_start(False, run_key=self.key), "unchanged")
        self.assertEqual(self.other_app(), r'"C:\Other\app.exe"')  # 다른 프로그램 항목은 그대로

    def test_remove_when_not_registered_is_safe(self):
        startup.set_auto_start(False, run_key=self.key)
        self.assertIsNone(startup.get_registered_command(self.key))


if __name__ == "__main__":
    unittest.main()
