"""Windows 시작 시 자동 실행 (HKCU\\Software\\Microsoft\\Windows\\CurrentVersion\\Run).

앱 이름(PCMonitor) 값 하나만 만들고 지운다. 다른 프로그램의 자동 실행 항목은 건드리지 않는다.
"""
import logging
import os
import sys
import winreg

log = logging.getLogger(__name__)

APP_NAME = "PCMonitor"
RUN_KEY = r"Software\Microsoft\Windows\CurrentVersion\Run"


def get_launch_command() -> str:
    """자동 실행에 등록할 명령. 경로에 공백이 있을 수 있으므로 따옴표로 감싼다.

    - 빌드된 exe: "...\\PCMonitor.exe"
    - 개발 중: 현재 가상환경의 pythonw.exe(콘솔 창 없음) + main.py 절대 경로
      (PATH의 python은 3.10이라 패키지가 없으므로 반드시 가상환경 경로를 쓴다)
    """
    if getattr(sys, "frozen", False):
        return f'"{sys.executable}"'
    pythonw = os.path.join(os.path.dirname(sys.executable), "pythonw.exe")
    if not os.path.exists(pythonw):
        pythonw = sys.executable
    main_py = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "main.py"))
    return f'"{pythonw}" "{main_py}"'


def get_registered_command(run_key: str = RUN_KEY) -> str | None:
    try:
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, run_key, 0, winreg.KEY_READ) as key:
            value, _ = winreg.QueryValueEx(key, APP_NAME)
            return value if isinstance(value, str) else None
    except FileNotFoundError:
        return None


def is_auto_start_enabled(run_key: str = RUN_KEY) -> bool:
    return get_registered_command(run_key) is not None


def set_auto_start(enable: bool, command: str | None = None, run_key: str = RUN_KEY) -> None:
    if enable:
        with winreg.CreateKeyEx(winreg.HKEY_CURRENT_USER, run_key, 0, winreg.KEY_SET_VALUE) as key:
            winreg.SetValueEx(key, APP_NAME, 0, winreg.REG_SZ, command or get_launch_command())
    else:
        try:
            with winreg.OpenKey(winreg.HKEY_CURRENT_USER, run_key, 0, winreg.KEY_SET_VALUE) as key:
                winreg.DeleteValue(key, APP_NAME)
        except FileNotFoundError:
            pass


def sync_auto_start(enabled: bool, command: str | None = None, run_key: str = RUN_KEY) -> str:
    """레지스트리를 설정에 맞춘다. 반환: "registered" | "updated" | "removed" | "unchanged".

    켜져 있는데 등록된 명령이 현재 실행 경로와 다르면(exe 이동, 개발 → exe 빌드) 새 경로로 다시 등록.
    """
    command = command or get_launch_command()
    registered = get_registered_command(run_key)
    if enabled:
        if registered is None:
            set_auto_start(True, command, run_key)
            result = "registered"
        elif registered.strip().lower() != command.strip().lower():
            set_auto_start(True, command, run_key)
            result = "updated"
        else:
            result = "unchanged"
    elif registered is not None:
        set_auto_start(False, run_key=run_key)
        result = "removed"
    else:
        result = "unchanged"
    if result != "unchanged":
        log.info("auto start %s: %s", result, command if enabled else "")
    return result


def apply_auto_start(config, enabled: bool) -> None:
    """메뉴 토글용: 설정 저장 + 레지스트리 반영. 실패는 로그만 남긴다."""
    try:
        config.set("auto_start", bool(enabled))
        sync_auto_start(bool(enabled))
    except (ValueError, OSError):
        log.exception("cannot change auto start")
