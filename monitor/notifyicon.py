"""트레이 아이콘 식별(고정 GUID)과 Win11 작업표시줄 자동 표시.

pystray는 NOTIFYICONDATAW의 uID를 잘못된 필드명(hID)으로 넘겨서 모든 아이콘이 uID=0으로 등록된다.
그러면 한 프로그램의 여러 아이콘을 Windows가 구분하지 못하므로, 아이콘마다 고정 GUID(NIF_GUID)를 붙인다.

Win11은 아이콘별 설정을 HKCU\\Control Panel\\NotifyIconSettings\\<id>에 저장한다
(IconGuid, ExecutablePath, IsPromoted 등). IsPromoted=1이면 숨김 영역(^) 밖 작업표시줄에 표시된다.
공식 API가 아니므로 실패해도 앱 동작에는 영향이 없어야 한다.
"""
import ctypes
import logging
import threading
import time
import uuid
import winreg
from ctypes import wintypes

from pystray import _win32
from pystray._util import win32

log = logging.getLogger(__name__)

NOTIFY_ICON_SETTINGS_KEY = r"Control Panel\NotifyIconSettings"
# 이 값을 바꾸면 모든 아이콘 GUID가 바뀌어 Win11이 새 아이콘으로 인식한다. 변경 금지.
_GUID_NAMESPACE = uuid.UUID("7d3c1f5e-2b9a-4c61-9f0e-5a8b6c4d2e10")

PROMOTE_TIMEOUT_SEC = 30
PROMOTE_POLL_SEC = 1.0


def process_image_path() -> str:
    """현재 프로세스의 실제 실행 파일 경로. venv의 python.exe는 기본 인터프리터를 자식으로 띄우므로
    sys.executable이 아니라 이 값이 Windows가 기록하는 ExecutablePath와 일치한다."""
    k32 = ctypes.WinDLL("kernel32", use_last_error=True)
    k32.GetCurrentProcess.restype = wintypes.HANDLE
    k32.QueryFullProcessImageNameW.argtypes = [wintypes.HANDLE, wintypes.DWORD, wintypes.LPWSTR,
                                               ctypes.POINTER(wintypes.DWORD)]
    buf = ctypes.create_unicode_buffer(32768)
    size = wintypes.DWORD(len(buf))
    if not k32.QueryFullProcessImageNameW(k32.GetCurrentProcess(), 0, buf, ctypes.byref(size)):
        raise ctypes.WinError(ctypes.get_last_error())
    return buf.value


def icon_guid(item: str, exe_path: str | None = None) -> uuid.UUID:
    """실행 파일 경로 + 항목 이름으로 결정되는 고정 GUID.

    Windows는 GUID 아이콘을 등록한 실행 파일 경로에 묶기 때문에, 경로가 다르면(개발용 python.exe ↔ 빌드된 exe)
    다른 GUID를 써야 등록이 실패하지 않는다.
    """
    exe = (exe_path or process_image_path()).lower()
    return uuid.uuid5(_GUID_NAMESPACE, f"{exe}|{item}")


class GuidIcon(_win32.Icon):
    """고정 GUID로 등록되는 pystray 아이콘.

    on_added(guid): 아이콘이 트레이에 추가된 직후 pystray 스레드에서 호출된다.
    """

    def __init__(self, guid: uuid.UUID, *args, on_added=None, **kwargs):
        self._guid = guid
        self._on_added = on_added
        super().__init__(*args, **kwargs)

    @property
    def guid(self) -> uuid.UUID:
        return self._guid

    def _message(self, code, flags, **kwargs):
        guid = win32.NOTIFYICONDATAW.GUID.from_buffer_copy(self._guid.bytes_le)
        if code == win32.NIM_ADD:
            # 비정상 종료로 같은 GUID 아이콘이 남아 있으면 NIM_ADD가 실패하므로 먼저 제거
            self._shell(win32.NIM_DELETE, win32.NIF_GUID, guid)
        ok = self._shell(code, flags | win32.NIF_GUID, guid, **kwargs)
        if code == win32.NIM_ADD:
            if not ok:
                log.warning("tray icon add failed (guid=%s)", self._guid)
            elif self._on_added is not None:
                try:
                    self._on_added(self._guid)
                except Exception:
                    log.exception("on_added callback failed")

    def _shell(self, code, flags, guid, **kwargs) -> bool:
        return bool(win32.Shell_NotifyIcon(code, win32.NOTIFYICONDATAW(
            cbSize=ctypes.sizeof(win32.NOTIFYICONDATAW),
            hWnd=self._hwnd,
            uFlags=flags,
            guidItem=guid,
            **kwargs)))


def find_settings_key(guid: uuid.UUID, base_key: str = NOTIFY_ICON_SETTINGS_KEY) -> str | None:
    """IconGuid가 일치하는 NotifyIconSettings 하위 키 이름. 없으면 None."""
    target = "{" + str(guid).upper() + "}"
    try:
        root = winreg.OpenKey(winreg.HKEY_CURRENT_USER, base_key)
    except FileNotFoundError:
        return None
    with root:
        index = 0
        while True:
            try:
                name = winreg.EnumKey(root, index)
            except OSError:
                return None
            index += 1
            try:
                with winreg.OpenKey(root, name) as key:
                    value, _ = winreg.QueryValueEx(key, "IconGuid")
            except OSError:
                continue
            if isinstance(value, str) and value.upper() == target:
                return name


def promote_icon(guid: uuid.UUID, base_key: str = NOTIFY_ICON_SETTINGS_KEY) -> str:
    """아이콘을 작업표시줄에 표시하도록 설정. 사용자가 한 번이라도 켜거나 끈 적이 있으면(IsPromoted 존재) 건드리지 않는다.

    반환: "promoted" | "already_set" | "not_found"
    """
    name = find_settings_key(guid, base_key)
    if name is None:
        return "not_found"
    with winreg.OpenKey(winreg.HKEY_CURRENT_USER, f"{base_key}\\{name}", 0,
                        winreg.KEY_QUERY_VALUE | winreg.KEY_SET_VALUE) as key:
        try:
            winreg.QueryValueEx(key, "IsPromoted")
            return "already_set"
        except FileNotFoundError:
            winreg.SetValueEx(key, "IsPromoted", 0, winreg.REG_DWORD, 1)
            return "promoted"


def promote_when_registered(guid: uuid.UUID, timeout_sec: float = PROMOTE_TIMEOUT_SEC,
                            poll_sec: float = PROMOTE_POLL_SEC,
                            base_key: str = NOTIFY_ICON_SETTINGS_KEY) -> str:
    """탐색기가 설정 키를 만들 때까지(보통 수 초) 기다렸다가 promote_icon. 블로킹이므로 daemon 스레드에서 호출."""
    deadline = time.monotonic() + timeout_sec
    while True:
        try:
            result = promote_icon(guid, base_key)
        except OSError:
            log.warning("tray auto-promote failed (guid=%s)", guid, exc_info=True)
            return "error"
        if result != "not_found" or time.monotonic() >= deadline:
            log.info("tray auto-promote %s: %s", guid, result)
            return result
        time.sleep(poll_sec)


def start_auto_promote(guid: uuid.UUID) -> None:
    threading.Thread(target=promote_when_registered, args=(guid,), name="tray-promote", daemon=True).start()
