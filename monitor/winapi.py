"""Windows API(ctypes) 헬퍼."""
import ctypes
import os
import winreg
from collections import namedtuple
from ctypes import wintypes

# ---------------------------------------------------------------------------
# 작업표시줄 / 오버레이 창
# ---------------------------------------------------------------------------

TaskbarGeometry = namedtuple("TaskbarGeometry", "hwnd bar notify tasks monitor dpi hidden")
"""hwnd: Shell_TrayWnd, bar/notify/tasks/monitor: (left, top, right, bottom) 물리 픽셀, hidden: 자동 숨김 등으로 안 보임"""

GWL_EXSTYLE = -20
GWLP_HWNDPARENT = -8
GA_ROOT = 2
WS_EX_TOOLWINDOW = 0x80
WS_EX_LAYERED = 0x80000
WS_EX_NOACTIVATE = 0x08000000
SWP_NOACTIVATE = 0x10
SWP_SHOWWINDOW = 0x40
MONITOR_DEFAULTTONEAREST = 2
# SHQueryUserNotificationState: 전체화면 앱/프레젠테이션 중이면 오버레이를 숨긴다
QUNS_BUSY = 2
QUNS_RUNNING_D3D_FULL_SCREEN = 3
QUNS_PRESENTATION_MODE = 4


class MONITORINFO(ctypes.Structure):
    _fields_ = [("cbSize", wintypes.DWORD), ("rcMonitor", wintypes.RECT),
                ("rcWork", wintypes.RECT), ("dwFlags", wintypes.DWORD)]


_u32 = ctypes.WinDLL("user32", use_last_error=True)
_u32.FindWindowW.restype = wintypes.HWND
_u32.FindWindowW.argtypes = [wintypes.LPCWSTR, wintypes.LPCWSTR]
_u32.FindWindowExW.restype = wintypes.HWND
_u32.FindWindowExW.argtypes = [wintypes.HWND, wintypes.HWND, wintypes.LPCWSTR, wintypes.LPCWSTR]
_u32.GetWindowRect.argtypes = [wintypes.HWND, ctypes.POINTER(wintypes.RECT)]
_u32.IsWindowVisible.argtypes = [wintypes.HWND]
_u32.MonitorFromWindow.restype = wintypes.HANDLE
_u32.MonitorFromWindow.argtypes = [wintypes.HWND, wintypes.DWORD]
_u32.GetMonitorInfoW.argtypes = [wintypes.HANDLE, ctypes.POINTER(MONITORINFO)]
_u32.GetDpiForWindow.restype = wintypes.UINT
_u32.GetDpiForWindow.argtypes = [wintypes.HWND]
_u32.GetAncestor.restype = wintypes.HWND
_u32.GetAncestor.argtypes = [wintypes.HWND, wintypes.UINT]
_u32.GetWindowLongPtrW.restype = ctypes.c_ssize_t
_u32.GetWindowLongPtrW.argtypes = [wintypes.HWND, ctypes.c_int]
_u32.SetWindowLongPtrW.restype = ctypes.c_ssize_t
_u32.SetWindowLongPtrW.argtypes = [wintypes.HWND, ctypes.c_int, ctypes.c_ssize_t]
_u32.SetWindowPos.argtypes = [wintypes.HWND, wintypes.HWND, ctypes.c_int, ctypes.c_int,
                              ctypes.c_int, ctypes.c_int, wintypes.UINT]
_HWND_TOPMOST = wintypes.HWND(-1)


def _rect(hwnd) -> tuple[int, int, int, int] | None:
    r = wintypes.RECT()
    if not hwnd or not _u32.GetWindowRect(hwnd, ctypes.byref(r)):
        return None
    return r.left, r.top, r.right, r.bottom


def _monitor_rect_of(hwnd) -> tuple[int, int, int, int]:
    info = MONITORINFO()
    info.cbSize = ctypes.sizeof(MONITORINFO)
    _u32.GetMonitorInfoW(_u32.MonitorFromWindow(hwnd, MONITOR_DEFAULTTONEAREST), ctypes.byref(info))
    m = info.rcMonitor
    return m.left, m.top, m.right, m.bottom


def _taskbar_geometry(hwnd, notify_rect, tasks_rect) -> TaskbarGeometry | None:
    bar = _rect(hwnd)
    if bar is None:
        return None
    monitor = _monitor_rect_of(hwnd)
    visible_height = min(bar[3], monitor[3]) - max(bar[1], monitor[1])
    hidden = not _u32.IsWindowVisible(hwnd) or visible_height < (bar[3] - bar[1]) // 2
    dpi = _u32.GetDpiForWindow(hwnd) or 96
    return TaskbarGeometry(hwnd, bar, notify_rect, tasks_rect, monitor, dpi, hidden)


def find_primary_taskbar() -> TaskbarGeometry | None:
    """주 모니터 작업표시줄과 트레이 영역(^ ~ 시계) 위치. 구조를 찾지 못하면 None."""
    tray = _u32.FindWindowW("Shell_TrayWnd", None)
    if not tray:
        return None
    notify_rect = _rect(_u32.FindWindowExW(tray, None, "TrayNotifyWnd", None))
    if notify_rect is None:
        return None
    return _taskbar_geometry(tray, notify_rect, _rect(_u32.FindWindowExW(tray, None, "ReBarWindow32", None)))


def find_taskbars() -> list[TaskbarGeometry]:
    """모든 작업표시줄. 주 작업표시줄(notify=트레이 영역) + 보조 작업표시줄들(notify=None, 오른쪽 끝에 시계만 있음).

    보조 작업표시줄의 앱 버튼 영역은 ReBarWindow32가 아니라 WorkerW다 (이 PC에서 확인).
    """
    taskbars = []
    primary = find_primary_taskbar()
    if primary is not None:
        taskbars.append(primary)
    hwnd = None
    while True:
        hwnd = _u32.FindWindowExW(None, hwnd, "Shell_SecondaryTrayWnd", None)
        if not hwnd:
            break
        geometry = _taskbar_geometry(hwnd, None, _rect(_u32.FindWindowExW(hwnd, None, "WorkerW", None)))
        if geometry is not None:
            taskbars.append(geometry)
    return taskbars


def get_root_hwnd(child_hwnd: int) -> int:
    """tkinter winfo_id()(내부 자식 창) → 최상위 창 핸들."""
    return _u32.GetAncestor(child_hwnd, GA_ROOT)


def add_ex_style(hwnd: int, flags: int) -> None:
    style = _u32.GetWindowLongPtrW(hwnd, GWL_EXSTYLE)
    _u32.SetWindowLongPtrW(hwnd, GWL_EXSTYLE, style | flags)


def set_window_owner(hwnd: int, owner_hwnd: int) -> None:
    """소유자 지정. 소유된 창은 항상 소유자보다 위에 그려진다 (작업표시줄을 클릭해도 가려지지 않음)."""
    _u32.SetWindowLongPtrW(hwnd, GWLP_HWNDPARENT, owner_hwnd)


def place_topmost(hwnd: int, x: int, y: int, width: int, height: int) -> None:
    _u32.SetWindowPos(hwnd, _HWND_TOPMOST, x, y, width, height, SWP_NOACTIVATE | SWP_SHOWWINDOW)


def is_fullscreen_app_running() -> bool:
    state = ctypes.c_int()
    try:
        hr = ctypes.windll.shell32.SHQueryUserNotificationState(ctypes.byref(state))
    except (OSError, AttributeError):
        return False
    return hr == 0 and state.value in (QUNS_BUSY, QUNS_RUNNING_D3D_FULL_SCREEN, QUNS_PRESENTATION_MODE)


# ---------------------------------------------------------------------------
# 팝업 위치 / 포커스
# ---------------------------------------------------------------------------

CursorMonitor = namedtuple("CursorMonitor", "x y work scale")
"""x, y: 커서 위치, work: 커서가 있는 모니터의 작업 영역 (left, top, right, bottom), scale: DPI 배율"""

_u32.GetCursorPos.argtypes = [ctypes.POINTER(wintypes.POINT)]
_u32.MonitorFromPoint.restype = wintypes.HANDLE
_u32.MonitorFromPoint.argtypes = [wintypes.POINT, wintypes.DWORD]
_u32.GetForegroundWindow.restype = wintypes.HWND
_u32.GetWindowThreadProcessId.restype = wintypes.DWORD
_u32.GetWindowThreadProcessId.argtypes = [wintypes.HWND, ctypes.POINTER(wintypes.DWORD)]

try:
    _shcore = ctypes.WinDLL("shcore")
    _shcore.GetDpiForMonitor.argtypes = [wintypes.HANDLE, ctypes.c_int,
                                         ctypes.POINTER(wintypes.UINT), ctypes.POINTER(wintypes.UINT)]
except OSError:  # pragma: no cover
    _shcore = None


def get_cursor_monitor() -> CursorMonitor:
    """클릭 시점의 커서 위치와 그 모니터의 작업 영역. 다중 모니터/작업표시줄 방향과 무관하게 정확."""
    pt = wintypes.POINT()
    _u32.GetCursorPos(ctypes.byref(pt))
    hmon = _u32.MonitorFromPoint(pt, MONITOR_DEFAULTTONEAREST)
    info = MONITORINFO()
    info.cbSize = ctypes.sizeof(MONITORINFO)
    _u32.GetMonitorInfoW(hmon, ctypes.byref(info))
    w = info.rcWork

    scale = 1.0
    if _shcore is not None:
        dpi_x, dpi_y = wintypes.UINT(), wintypes.UINT()
        if _shcore.GetDpiForMonitor(hmon, 0, ctypes.byref(dpi_x), ctypes.byref(dpi_y)) == 0 and dpi_x.value:
            scale = dpi_x.value / 96
    return CursorMonitor(pt.x, pt.y, (w.left, w.top, w.right, w.bottom), scale)


def foreground_window() -> tuple[int | None, int]:
    """(전경 창 핸들, 그 창의 프로세스 ID). 전경 창이 없으면 (None, 0)."""
    hwnd = _u32.GetForegroundWindow()
    if not hwnd:
        return None, 0
    pid = wintypes.DWORD()
    _u32.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
    return hwnd, pid.value


_u32.GetClassNameW.argtypes = [wintypes.HWND, wintypes.LPWSTR, ctypes.c_int]
_SHELL_CLASSES = ("Progman", "WorkerW", "Shell_TrayWnd", "Shell_SecondaryTrayWnd")


def foreground_fullscreen_monitor() -> tuple[int, int, int, int] | None:
    """전경 창이 모니터 전체를 덮고 있으면(유튜브 전체화면, 게임 등) 그 모니터 영역. 아니면 None.

    최대화 창은 작업표시줄 영역을 덮지 않으므로 해당하지 않는다. 바탕화면/작업표시줄은 제외.
    """
    hwnd = _u32.GetForegroundWindow()
    if not hwnd:
        return None
    buf = ctypes.create_unicode_buffer(64)
    _u32.GetClassNameW(hwnd, buf, 64)
    if buf.value in _SHELL_CLASSES:
        return None
    rect = _rect(hwnd)
    if rect is None:
        return None
    monitor = _monitor_rect_of(hwnd)
    if rect[0] <= monitor[0] and rect[1] <= monitor[1] and rect[2] >= monitor[2] and rect[3] >= monitor[3]:
        return monitor
    return None


# ---------------------------------------------------------------------------
# 전체화면 창에 가려진 작업표시줄 (Z-order 기준)
# ---------------------------------------------------------------------------

GW_HWNDNEXT = 2
DWMWA_CLOAKED = 14
WS_EX_TRANSPARENT = 0x20

_u32.GetTopWindow.restype = wintypes.HWND
_u32.GetTopWindow.argtypes = [wintypes.HWND]
_u32.GetWindow.restype = wintypes.HWND
_u32.GetWindow.argtypes = [wintypes.HWND, wintypes.UINT]
_u32.IsIconic.argtypes = [wintypes.HWND]
try:
    _dwm = ctypes.WinDLL("dwmapi")
    _dwm.DwmGetWindowAttribute.argtypes = [wintypes.HWND, wintypes.DWORD, ctypes.c_void_p, wintypes.DWORD]
except OSError:  # pragma: no cover
    _dwm = None

ZWindow = namedtuple("ZWindow", "hwnd rect eligible")
"""Z-order 창 하나. eligible: 전체화면 판정 대상 (표시 중, 최소화·숨김(cloaked)·클릭 통과·도구 창·셸·자기 창 아님)."""


def _is_cloaked(hwnd) -> bool:
    if _dwm is None:
        return False
    value = ctypes.c_int()
    hr = _dwm.DwmGetWindowAttribute(hwnd, DWMWA_CLOAKED, ctypes.byref(value), ctypes.sizeof(value))
    return hr == 0 and value.value != 0


def zorder_windows() -> list[ZWindow]:
    """최상위 창을 Z-order 위에서 아래 순서로."""
    own_pid = os.getpid()
    windows = []
    hwnd = _u32.GetTopWindow(None)
    buf = ctypes.create_unicode_buffer(64)
    while hwnd:
        eligible = False
        rect = None
        if _u32.IsWindowVisible(hwnd) and not _u32.IsIconic(hwnd):
            _u32.GetClassNameW(hwnd, buf, 64)
            pid = wintypes.DWORD()
            _u32.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
            ex_style = _u32.GetWindowLongPtrW(hwnd, GWL_EXSTYLE)
            rect = _rect(hwnd)
            eligible = (rect is not None
                        and buf.value not in _SHELL_CLASSES
                        and pid.value != own_pid
                        and not ex_style & (WS_EX_TRANSPARENT | WS_EX_TOOLWINDOW)  # 드라이버 오버레이 등 제외
                        and not _is_cloaked(hwnd))
        windows.append(ZWindow(hwnd, rect, eligible))
        hwnd = _u32.GetWindow(hwnd, GW_HWNDNEXT)
    return windows


def rect_covers(outer, inner) -> bool:
    return outer[0] <= inner[0] and outer[1] <= inner[1] and outer[2] >= inner[2] and outer[3] >= inner[3]


def find_covered_taskbars(windows: list[ZWindow], monitors: dict[int, tuple]) -> set[int]:
    """Z-order에서 작업표시줄보다 위에 있고 그 모니터 전체를 덮는 창이 있으면 그 작업표시줄은 가려진 것.

    monitors: {작업표시줄 hwnd: 그 모니터 영역}. 순수 함수 (테스트용으로 분리).
    """
    covered, pending, covering = set(), dict(monitors), []
    for window in windows:
        if window.hwnd in pending:
            monitor = pending.pop(window.hwnd)
            if any(rect_covers(rect, monitor) for rect in covering):
                covered.add(window.hwnd)
            if not pending:
                break
        elif window.eligible and window.rect is not None:
            covering.append(window.rect)
    return covered


def covered_taskbars(taskbars: list[TaskbarGeometry]) -> set[int]:
    """전체화면 창(영상, 게임 등)에 가려진 작업표시줄 hwnd 집합.

    전경 창 여부나 SHQueryUserNotificationState에 의존하지 않는다: 한 모니터에서 전체화면 영상을 켜 둔 채
    다른 모니터를 클릭하면 전경 창은 전체화면이 아니지만 전역 상태는 'busy'라서, 모든 모니터 오버레이를
    숨기는 오판이 생겼다 (exe 확인 중 발견).
    """
    if not taskbars:
        return set()
    return find_covered_taskbars(zorder_windows(), {geo.hwnd: geo.monitor for geo in taskbars})

_PERSONALIZE_KEY = r"Software\Microsoft\Windows\CurrentVersion\Themes\Personalize"


def is_light_taskbar() -> bool:
    """작업표시줄(시스템) 테마가 라이트면 True. 값이 없으면 Windows 기본값인 다크로 간주."""
    try:
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, _PERSONALIZE_KEY) as key:
            value, _ = winreg.QueryValueEx(key, "SystemUsesLightTheme")
            return bool(value)
    except OSError:
        return False

IOCTL_STORAGE_GET_DEVICE_NUMBER = 0x2D1080
_OPEN_EXISTING = 3
_FILE_SHARE_READ_WRITE = 0x1 | 0x2


class STORAGE_DEVICE_NUMBER(ctypes.Structure):
    _fields_ = [("DeviceType", wintypes.DWORD),
                ("DeviceNumber", wintypes.DWORD),
                ("PartitionNumber", wintypes.DWORD)]


_k32 = ctypes.WinDLL("kernel32", use_last_error=True)
_k32.CreateFileW.restype = wintypes.HANDLE
_k32.CreateFileW.argtypes = [wintypes.LPCWSTR, wintypes.DWORD, wintypes.DWORD, wintypes.LPVOID,
                             wintypes.DWORD, wintypes.DWORD, wintypes.HANDLE]
_k32.DeviceIoControl.argtypes = [wintypes.HANDLE, wintypes.DWORD, wintypes.LPVOID, wintypes.DWORD,
                                 wintypes.LPVOID, wintypes.DWORD, ctypes.POINTER(wintypes.DWORD),
                                 wintypes.LPVOID]
_k32.CloseHandle.argtypes = [wintypes.HANDLE]
_INVALID_HANDLE = wintypes.HANDLE(-1).value


def get_physical_drive_name(letter: str) -> str | None:
    """드라이브 문자 → psutil.disk_io_counters(perdisk=True)의 키. 'C' → 'PhysicalDrive0'. 실패 시 None.

    관리자 권한 불필요 (access=0으로 볼륨을 열어 장치 번호만 조회).
    스토리지 공간/동적 디스크처럼 여러 물리 디스크에 걸친 볼륨은 실패한다.
    """
    handle = _k32.CreateFileW(rf"\\.\{letter}:", 0, _FILE_SHARE_READ_WRITE, None, _OPEN_EXISTING, 0, None)
    if handle is None or handle == _INVALID_HANDLE:
        return None
    try:
        sdn = STORAGE_DEVICE_NUMBER()
        returned = wintypes.DWORD()
        ok = _k32.DeviceIoControl(handle, IOCTL_STORAGE_GET_DEVICE_NUMBER, None, 0,
                                  ctypes.byref(sdn), ctypes.sizeof(sdn), ctypes.byref(returned), None)
        return f"PhysicalDrive{sdn.DeviceNumber}" if ok else None
    finally:
        _k32.CloseHandle(handle)
