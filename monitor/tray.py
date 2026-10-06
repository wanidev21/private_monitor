"""대체 트레이 아이콘 (pystray).

평소에는 트레이 아이콘을 표시하지 않는다 (사용자 결정: 작업표시줄 오버레이만 사용).
오버레이를 어느 모니터에도 표시할 수 없을 때(공간 부족, 작업표시줄 구조를 찾지 못함, 표시 항목 없음)만
circle 아이콘 1개를 띄워 팝업/메뉴에 접근할 수 있게 한다.

관리 스레드(run)가 아이콘 생성/제거/갱신을 전담한다. 메뉴 콜백은 pystray 스레드에서 실행되므로
설정만 바꾸고 관리 스레드를 깨운다 (콜백 안에서 자기 아이콘을 stop하면 안 됨).
tkinter를 건드리는 콜백(on_show_popup, on_quit)은 호출하는 쪽(main.py)에서 root.after로 위임해야 한다.
"""
import logging
import threading

import pystray

from monitor import display, iconrender, notifyicon, startup

log = logging.getLogger(__name__)

APP_NAME = "PCMonitor"
DEFAULT_INTERVAL_SEC = 2
CIRCLE_ICON_ITEM = "main"  # 아이콘 GUID 항목 이름 (바꾸면 Win11이 새 아이콘으로 인식)
ICON_ADD_TIMEOUT_SEC = 3
ICON_STOP_TIMEOUT_SEC = 2


def desired_layout(overlay_fallback: bool) -> tuple[str, tuple[str, ...]]:
    """오버레이를 표시할 수 없을 때만 circle 아이콘 1개."""
    return ("circle", (CIRCLE_ICON_ITEM,)) if overlay_fallback else ("none", ())


def _show_icon(icon) -> None:
    icon.visible = True


class TrayIcon:
    def __init__(self, collector, config, on_show_popup, on_open_web, on_quit, is_web_available=None):
        self._collector = collector
        self._config = config
        self._on_show_popup = on_show_popup
        self._on_open_web = on_open_web
        self._on_quit = on_quit
        self._is_web_available = is_web_available or (lambda: True)

        self._lock = threading.RLock()
        self._icon = None
        self._thread = None
        self._layout = None
        self._last_color = None
        self._overlay_fallback = False
        self._stop_event = threading.Event()
        self._wake_event = threading.Event()

    # --- 수명 주기 ----------------------------------------------------------

    def run(self) -> None:
        """daemon 스레드 진입점. stop() 전까지 대체 아이콘을 필요에 따라 만들고 갱신한다."""
        while not self._stop_event.is_set():
            try:
                layout = desired_layout(self._overlay_fallback)
                if layout != self._layout:
                    self._rebuild(layout)
                self.refresh()
            except Exception:
                log.exception("tray update failed")
            self._wake_event.wait(self._interval())
            self._wake_event.clear()
        with self._lock:
            self._remove()

    def stop(self) -> None:
        self._stop_event.set()
        self._wake_event.set()
        with self._lock:
            self._remove()

    def wake(self) -> None:
        """설정 변경 등을 즉시 반영하도록 관리 스레드를 깨운다. 어느 스레드에서나 호출 가능."""
        self._wake_event.set()

    def set_overlay_fallback(self, value: bool) -> None:
        """오버레이를 표시할 수 없으면 True → circle 트레이 아이콘으로 대체. 오버레이(메인 스레드)가 호출."""
        self._overlay_fallback = bool(value)
        self._wake_event.set()

    def _interval(self) -> float:
        try:
            return self._config.update_interval_sec
        except Exception:
            return DEFAULT_INTERVAL_SEC

    # --- 아이콘 -------------------------------------------------------------

    def _rebuild(self, layout) -> None:
        with self._lock:
            self._remove()
            if self._stop_event.is_set():
                return
            if layout[0] == "circle":
                self._create_icon()
            self._layout = layout
        log.info("tray icon: %s", layout[0])

    def _create_icon(self) -> None:
        added = threading.Event()

        def on_added(guid):
            added.set()
            if self._config.tray_auto_promote:
                notifyicon.start_auto_promote(guid)

        self._last_color = self._color()
        icon = notifyicon.GuidIcon(
            notifyicon.icon_guid(CIRCLE_ICON_ITEM),
            APP_NAME,
            icon=iconrender.render_circle(self._last_color),
            title=self._title(),
            menu=self._build_menu(),
            on_added=on_added,
        )
        thread = threading.Thread(target=icon.run, kwargs={"setup": _show_icon}, name="tray-icon", daemon=True)
        thread.start()
        if not added.wait(ICON_ADD_TIMEOUT_SEC):
            log.warning("tray icon was not added within %ss", ICON_ADD_TIMEOUT_SEC)
        self._icon, self._thread = icon, thread

    def _remove(self) -> None:
        """호출자가 self._lock을 잡고 있어야 한다."""
        icon, thread = self._icon, self._thread
        self._icon = self._thread = None
        self._layout = None
        if icon is not None:
            try:
                icon.stop()
            except Exception:
                log.debug("icon.stop failed", exc_info=True)
        if thread is not None:
            thread.join(ICON_STOP_TIMEOUT_SEC)

    def refresh(self) -> None:
        with self._lock:
            icon = self._icon
            if icon is None:
                return
            color = self._color()
            if color != self._last_color:  # 색상 구간이 바뀔 때만 이미지 재생성
                icon.icon = iconrender.render_circle(color)
                self._last_color = color
            title = self._title()
            if icon.title != title:
                icon.title = title

    def _color(self) -> str:
        data = self._collector.latest
        cpu = data.get("cpu") if data else None
        return display.level_color(cpu["percent"] if cpu else None)

    def _title(self) -> str:
        return display.build_circle_tooltip(self._collector.latest, self._config.tray_visible_items)

    # --- 메뉴 ---------------------------------------------------------------

    def _build_menu(self) -> pystray.Menu:
        looks = [
            pystray.MenuItem(label, self._look_action(value), checked=self._look_checked(value), radio=True)
            for value, label in display.OVERLAY_LOOK_LABELS
        ]
        return pystray.Menu(
            # default=True 항목은 좌클릭 시 실행된다
            pystray.MenuItem("상세 보기", self._handle_show_popup, default=True, visible=False),
            pystray.MenuItem("대시보드 열기", self._handle_open_web,
                             enabled=lambda item: self._is_web_available()),
            pystray.MenuItem("오버레이 디자인", pystray.Menu(*looks)),
            pystray.MenuItem("알림", self._handle_toggle_alerts, checked=lambda item: self._config.alerts_enabled),
            pystray.MenuItem("Windows 시작 시 자동 실행",
                             lambda icon, item: startup.apply_auto_start(self._config, not self._config.auto_start),
                             checked=lambda item: self._config.auto_start),
            pystray.Menu.SEPARATOR,
            pystray.MenuItem("종료", self._handle_quit),
        )

    def _look_action(self, value: str):
        def action(icon, item):
            try:
                self._config.set("overlay_icon_look", value)
            except (ValueError, OSError):
                log.exception("cannot change overlay look")
            self._wake_event.set()
        return action

    def _look_checked(self, value: str):
        return lambda item: self._config.overlay_icon_look == value

    def _handle_toggle_alerts(self, icon, item) -> None:
        try:
            self._config.set("alerts_enabled", not self._config.alerts_enabled)
        except (ValueError, OSError):
            log.exception("cannot toggle alerts")

    def _handle_show_popup(self, icon, item) -> None:
        self._on_show_popup()

    def _handle_open_web(self, icon, item) -> None:
        self._on_open_web()

    def _handle_quit(self, icon, item) -> None:
        self._on_quit()
