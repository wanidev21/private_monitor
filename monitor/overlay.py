"""작업표시줄 오버레이: 모니터마다 작업표시줄 오른쪽에 수치를 표시한다.

- 주 작업표시줄: 트레이 영역(^ ~ 시계) 바로 왼쪽
- 보조 작업표시줄: 트레이 영역이 없고 시계만 있으므로, 화면 픽셀로 시계 왼쪽 끝을 찾아 그 왼쪽
- 디자인(overlay_icon_look)과 표시 항목은 모든 모니터 공통

tkinter 창이므로 모든 동작은 메인 스레드에서 root.after 주기로 실행된다.
- 창 소유자를 작업표시줄로 지정 → 작업표시줄을 클릭해도 가려지거나 깜빡이지 않음 (TOPMOST 반복 재지정 금지)
- 배경은 작업표시줄 실제 색을 샘플링한 키 색으로 투명 처리
- 어느 모니터에도 표시할 수 없으면(공간 부족 등) tray에 알려 circle 트레이 아이콘으로 대체
"""
import logging
import time
import tkinter as tk

from PIL import Image, ImageGrab, ImageTk

from monitor import display, fonts, iconrender, startup, winapi
from monitor.graph import WINDOW_SEC

log = logging.getLogger(__name__)

TICK_MS = 1000
FULLSCREEN_POLL_MS = 200  # 1초 주기만으로는 전체화면 전환 직후 오버레이가 잠깐 보임 (사용자 확인)
KEY_RESAMPLE_SEC = 60
CLOCK_RESCAN_SEC = 30

PRIMARY_MARGIN = 8          # 트레이 영역 / 앱 버튼 영역과의 간격 (100% 배율 px)
SECONDARY_MARGIN = 12       # 보조 작업표시줄 시계와의 간격 (자기 오버레이는 탐지 때 ignore 구간으로 제외)
CLOCK_FALLBACK_WIDTH = 150  # 시계를 찾지 못하면 오른쪽 끝에서 이만큼 비워둔다
CLOCK_SEARCH_WIDTH = 220    # 오른쪽 끝에서 이 폭 안에서만 시계를 찾는다 (알림 종 + 시계)
CLOCK_DIFF_THRESHOLD = 40   # 배경색과 RGB 차이 합이 이 이상이면 글자 픽셀
# 이 폭 미만의 틈은 이어 붙인다. 시계 글자 사이(수 px)뿐 아니라 시계 오른쪽의 알림 종 아이콘과의 틈(약 14px)도
# 이어 붙여야 한다 — 6px일 때 종만 시계로 잡혀 오버레이가 시계를 덮는 문제를 캡처로 확인
CLOCK_GAP_TOLERANCE = 20

_TOOLTIP_BG = "#F9F9F9"
_TOOLTIP_FG = "#1A1A1A"
_TOOLTIP_BORDER = "#C8C8C8"


def compute_placement(anchor_right: int, bar, tasks, width: int, margin: int) -> tuple[int, int, int, int] | None:
    """오버레이 창 (x, y, 폭, 높이). anchor_right 왼쪽에 오른쪽 정렬, 앱 버튼 영역과 겹치면 None."""
    if width <= 0:
        return None
    x = anchor_right - margin - width
    left_limit = (tasks[2] if tasks else bar[0]) + margin
    if x < left_limit:
        return None
    return x, bar[1], width, bar[3] - bar[1]


def find_clock_left(strip: Image.Image, search_width: int, ignore: tuple[int, int] | None = None) -> int | None:
    """작업표시줄 캡처(RGB)에서 오른쪽 끝 시계 덩어리의 왼쪽 x (strip 좌표). 못 찾으면 None.

    오른쪽 끝에서 왼쪽으로 열을 훑어 배경과 다른 픽셀이 있는 첫 덩어리를 찾고, 글자 사이의 작은 틈은 이어 붙인다.
    ignore=(x0, x1) 구간은 내용으로 보지 않는다 (자기 오버레이가 캡처에 들어간 경우).
    """
    width, height = strip.size
    if width < 3 or height < 10:
        return None
    px = strip.load()
    start = max(0, width - search_width)
    samples = [px[x, y] for x in range(width - 2, width) for y in range(height)]
    samples += [px[x, 1] for x in range(start, width)]
    background = max(set(samples), key=samples.count)

    def is_content(x: int) -> bool:
        if ignore and ignore[0] <= x < ignore[1]:
            return False
        for y in range(4, height - 4):
            r, g, b = px[x, y]
            if abs(r - background[0]) + abs(g - background[1]) + abs(b - background[2]) >= CLOCK_DIFF_THRESHOLD:
                return True
        return False

    left, gap = None, 0
    for x in range(width - 1, start - 1, -1):
        if is_content(x):
            left, gap = x, 0
        elif left is not None:
            gap += 1
            if gap >= CLOCK_GAP_TOLERANCE:
                break
    return left


class _OverlayWindow:
    """작업표시줄 하나에 붙는 오버레이 창."""

    def __init__(self, manager: "TaskbarOverlay", taskbar_hwnd: int):
        self.taskbar_hwnd = taskbar_hwnd
        self.monitor = None
        self.visible = False
        self.placement = None
        self.hitboxes = []
        self.bar_top = 0
        self.net_units = {}
        self._owner = None
        self._key_rgb = None
        self._key_light = None
        self._key_at = 0.0
        self._clock_left = None
        self._clock_bar = None
        self._clock_at = 0.0
        self._last_drawn = None
        self._photo = None

        win = tk.Toplevel(manager.root)
        win.withdraw()
        win.overrideredirect(True)
        canvas = tk.Canvas(win, highlightthickness=0, bd=0)
        canvas.pack(fill="both", expand=True)
        self._image_id = canvas.create_image(0, 0, anchor="nw")
        canvas.bind("<Button-1>", lambda e: manager.on_left_click())
        canvas.bind("<Button-3>", manager.show_menu)
        canvas.bind("<Motion>", lambda e: manager.on_motion(self, e))
        canvas.bind("<Leave>", lambda e: manager.hide_tooltip())
        win.update_idletasks()
        self.win, self.canvas = win, canvas
        self.hwnd = winapi.get_root_hwnd(win.winfo_id())
        # 작업표시줄 버튼/Alt+Tab에 표시하지 않고 클릭해도 포커스를 뺏지 않음. 클릭은 받아야 하므로 TRANSPARENT 미사용
        winapi.add_ex_style(self.hwnd, winapi.WS_EX_LAYERED | winapi.WS_EX_TOOLWINDOW | winapi.WS_EX_NOACTIVATE)

    def update(self, geometry: winapi.TaskbarGeometry, look, items, size, font_file, light, data, history,
               theme) -> bool:
        """그리고 배치한다. 공간이 없어 표시하지 못하면 False."""
        self.monitor = geometry.monitor
        scale = geometry.dpi / 96
        bar_height = geometry.bar[3] - geometry.bar[1]
        anchor, margin = self._anchor(geometry, scale)
        strip, self.net_units = iconrender.render_overlay_strip(
            look, items, data, history, theme, round(size * scale), bar_height, light, font_file, self.net_units)
        placement = compute_placement(anchor, geometry.bar, geometry.tasks, strip.image.width, margin)
        if placement is None:
            self.hide()
            return False

        self._sample_key_color(anchor, margin, geometry.bar, light)
        self._draw(strip.image)
        self.hitboxes = strip.hitboxes
        self.bar_top = geometry.bar[1]

        if geometry.hwnd != self._owner:
            winapi.set_window_owner(self.hwnd, geometry.hwnd)  # 탐색기 재시작 시 핸들이 바뀜
            self._owner = geometry.hwnd
        if not self.visible or placement != self.placement:
            self.canvas.configure(width=placement[2], height=placement[3])
            self.win.deiconify()
            winapi.place_topmost(self.hwnd, *placement)
            self.visible = True
            self.placement = placement
        return True

    def hide(self) -> None:
        if self.visible:
            self.win.withdraw()
        self.visible = False
        self.placement = None

    def destroy(self) -> None:
        try:
            self.win.destroy()
        except tk.TclError:
            pass

    # --- 내부 ---------------------------------------------------------------

    def _anchor(self, geometry, scale: float) -> tuple[int, int]:
        """(오버레이 오른쪽 기준 x, 간격)."""
        if geometry.notify is not None:
            return geometry.notify[0], round(PRIMARY_MARGIN * scale)
        now = time.monotonic()
        if (self._clock_left is None or self._clock_bar != geometry.bar
                or now - self._clock_at >= CLOCK_RESCAN_SEC):
            self._clock_left = self._scan_clock(geometry, scale)
            self._clock_bar, self._clock_at = geometry.bar, now
        return self._clock_left, round(SECONDARY_MARGIN * scale)

    def _scan_clock(self, geometry, scale: float) -> int:
        left, _, right, _ = geometry.bar
        ignore = None
        if self.visible and self.placement:
            ignore = (self.placement[0] - left, self.placement[0] + self.placement[2] - left)
        try:
            strip = ImageGrab.grab(bbox=geometry.bar, all_screens=True).convert("RGB")
            found = find_clock_left(strip, round(CLOCK_SEARCH_WIDTH * scale), ignore)
        except OSError:
            found = None
        if found is None:
            log.info("secondary taskbar clock not found; reserving %spx", CLOCK_FALLBACK_WIDTH)
            return right - round(CLOCK_FALLBACK_WIDTH * scale)
        return left + found

    def _sample_key_color(self, anchor: int, margin: int, bar, light: bool) -> None:
        """오버레이와 기준 사이 빈 작업표시줄 픽셀에서 배경색을 가져와 투명 키 색으로 쓴다."""
        now = time.monotonic()
        if self._key_rgb is not None and light == self._key_light and now - self._key_at < KEY_RESAMPLE_SEC:
            return
        x, y = anchor - max(1, margin // 2), bar[1] + 3
        try:
            rgb = tuple(ImageGrab.grab(bbox=(x, y, x + 1, y + 1), all_screens=True).convert("RGB").getpixel((0, 0)))
        except OSError:
            rgb = (238, 238, 238) if light else (32, 32, 32)
        if rgb != self._key_rgb:
            key = "#{:02X}{:02X}{:02X}".format(*rgb)
            self.win.configure(bg=key)
            self.win.attributes("-transparentcolor", key)
            self.canvas.configure(bg=key)
            self._last_drawn = None
        self._key_rgb, self._key_light, self._key_at = rgb, light, now

    def _draw(self, image: Image.Image) -> None:
        signature = (image.size, self._key_rgb, image.tobytes())
        if signature == self._last_drawn:
            return
        self._last_drawn = signature
        # 반투명 가장자리를 작업표시줄 색과 미리 섞어 키 색 투명화 시 테두리 번짐을 없앤다
        base = Image.new("RGBA", image.size, self._key_rgb + (255,))
        base.alpha_composite(image)
        self._photo = ImageTk.PhotoImage(base.convert("RGB"), master=self.win)  # 참조 유지 필수
        self.canvas.itemconfigure(self._image_id, image=self._photo)


class TaskbarOverlay:
    def __init__(self, root: tk.Tk, collector, config, tray, on_show_popup, on_open_web, on_quit,
                 is_web_available=None):
        self.root = root
        self._collector = collector
        self._config = config
        self._tray = tray
        self._on_show_popup = on_show_popup
        self._on_open_web = on_open_web
        self._on_quit = on_quit
        self._is_web_available = is_web_available or (lambda: True)

        self._windows: dict[int, _OverlayWindow] = {}
        self._taskbars: list[winapi.TaskbarGeometry] = []
        self._fallback = None
        self._warned_no_taskbar = False
        self._tooltip = None
        self._tooltip_label = None
        self._hover = None  # (창, 항목)
        self._menu = None
        self._look_var = tk.StringVar(root)
        self._alerts_var = tk.BooleanVar(root)
        self._auto_start_var = tk.BooleanVar(root)

    # --- 수명 주기 ----------------------------------------------------------

    def start(self) -> None:
        self.root.after(0, self._tick)
        self.root.after(FULLSCREEN_POLL_MS, self._fullscreen_tick)

    def destroy(self) -> None:
        self.hide_tooltip()
        for window in self._windows.values():
            window.destroy()
        self._windows.clear()
        for widget in (self._tooltip, self._menu):
            if widget is not None:
                try:
                    widget.destroy()
                except tk.TclError:
                    pass
        self._tooltip = self._menu = None

    def _tick(self) -> None:
        try:
            self._update()
        except Exception:
            log.exception("overlay update failed")
            self._hide_all()
            self._set_fallback(True)
        self.root.after(TICK_MS, self._tick)

    def _fullscreen_tick(self) -> None:
        """전체화면 창이 작업표시줄을 덮으면 1초 주기를 기다리지 않고 그 모니터의 오버레이를 바로 숨긴다."""
        try:
            if any(window.visible for window in self._windows.values()):
                covered = winapi.covered_taskbars(self._taskbars)
                for hwnd, window in self._windows.items():
                    if window.visible and hwnd in covered:
                        window.hide()
                        if self._hover and self._hover[0] is window:
                            self.hide_tooltip()
        except Exception:
            log.debug("fullscreen check failed", exc_info=True)
        self.root.after(FULLSCREEN_POLL_MS, self._fullscreen_tick)

    # --- 상태 갱신 ----------------------------------------------------------

    def _update(self) -> None:
        items = tuple(self._config.tray_visible_items)
        taskbars = winapi.find_taskbars() if items else []
        if items and not taskbars:
            if not self._warned_no_taskbar:
                log.warning("taskbar not found; overlay disabled, using tray icon")
                self._warned_no_taskbar = True
        elif taskbars:
            self._warned_no_taskbar = False

        self._taskbars = taskbars
        live = {geometry.hwnd for geometry in taskbars}
        for hwnd in [h for h in self._windows if h not in live]:  # 모니터 분리, 탐색기 재시작
            if self._hover and self._hover[0].taskbar_hwnd == hwnd:
                self.hide_tooltip()
            self._windows.pop(hwnd).destroy()

        if not items or not taskbars:
            self._hide_all()
            self._set_fallback(True)
            return

        covered = winapi.covered_taskbars(taskbars)
        look = self._config.overlay_icon_look
        size = self._config.overlay_icon_size
        font_file = fonts.resolve_font_file(self._config.overlay_font)
        light = winapi.is_light_taskbar()
        data = self._collector.latest
        history = self._collector.get_history(WINDOW_SEC)
        theme = self._config.ui_theme

        any_visible = any_suppressed = False
        for geometry in taskbars:
            window = self._windows.get(geometry.hwnd)
            if window is None:
                window = self._windows[geometry.hwnd] = _OverlayWindow(self, geometry.hwnd)
            if geometry.hidden or geometry.hwnd in covered:
                window.monitor = geometry.monitor
                window.hide()
                any_suppressed = True  # 자동 숨김/전체화면은 대체 트레이 아이콘을 띄울 이유가 아님
                continue
            if window.update(geometry, look, items, size, font_file, light, data, history, theme):
                any_visible = True

        self._set_fallback(not any_visible and not any_suppressed)
        if self._hover is not None:
            self._show_tooltip(*self._hover)

    def _hide_all(self) -> None:
        self.hide_tooltip()
        for window in self._windows.values():
            window.hide()

    def _set_fallback(self, value: bool) -> None:
        if value != self._fallback:
            self._fallback = value
            self._tray.set_overlay_fallback(value)

    # --- 마우스 -------------------------------------------------------------

    def on_left_click(self) -> None:
        self._on_show_popup()

    def on_motion(self, window: _OverlayWindow, event) -> None:
        item = next((box[0] for box in window.hitboxes
                     if box[1] <= event.x < box[3] and box[2] <= event.y < box[4]), None)
        if item is None:
            self.hide_tooltip()
        elif self._hover != (window, item):
            self._show_tooltip(window, item)

    def _show_tooltip(self, window: _OverlayWindow, item: str) -> None:
        box = next((b for b in window.hitboxes if b[0] == item), None)
        if box is None or not window.visible or window.placement is None:
            self.hide_tooltip()
            return
        if self._tooltip is None:
            tip = tk.Toplevel(self.root)
            tip.withdraw()
            tip.overrideredirect(True)
            tip.attributes("-topmost", True)
            tip.configure(bg=_TOOLTIP_BORDER)
            label = tk.Label(tip, bg=_TOOLTIP_BG, fg=_TOOLTIP_FG, font=("Segoe UI", 9), padx=8, pady=4)
            label.pack(padx=1, pady=1)
            self._tooltip, self._tooltip_label = tip, label
        text = display.icon_tooltip(item, self._collector.latest)
        if self._tooltip_label.cget("text") != text or self._hover != (window, item):
            self._tooltip_label.configure(text=text)
            self._tooltip.update_idletasks()
            width, height = self._tooltip.winfo_reqwidth(), self._tooltip.winfo_reqheight()
            center = window.placement[0] + (box[1] + box[3]) // 2
            left, right = (window.monitor[0], window.monitor[2]) if window.monitor else (0, center + width)
            x = max(left, min(center - width // 2, right - width))
            self._tooltip.geometry(f"+{x}+{window.bar_top - height - 6}")
        self._tooltip.deiconify()
        self._hover = (window, item)

    def hide_tooltip(self) -> None:
        if self._tooltip is not None:
            self._tooltip.withdraw()
        self._hover = None

    def show_menu(self, event) -> None:
        self.hide_tooltip()
        if self._menu is None:
            menu = tk.Menu(self.root, tearoff=0)
            menu.add_command(label="대시보드 열기", command=self._on_open_web)
            looks = tk.Menu(menu, tearoff=0)
            for value, label in display.OVERLAY_LOOK_LABELS:
                looks.add_radiobutton(label=label, value=value, variable=self._look_var,
                                      command=lambda v=value: self._change_look(v))
            menu.add_cascade(label="오버레이 디자인", menu=looks)
            menu.add_checkbutton(label="알림", variable=self._alerts_var, command=self._toggle_alerts)
            menu.add_checkbutton(label="Windows 시작 시 자동 실행", variable=self._auto_start_var,
                                 command=lambda: startup.apply_auto_start(self._config, self._auto_start_var.get()))
            menu.add_separator()
            menu.add_command(label="종료", command=self._on_quit)
            self._menu = menu
        self._menu.entryconfigure(0, state="normal" if self._is_web_available() else "disabled")
        self._look_var.set(self._config.overlay_icon_look)
        self._alerts_var.set(self._config.alerts_enabled)
        self._auto_start_var.set(self._config.auto_start)
        try:
            self._menu.tk_popup(event.x_root, event.y_root)
        finally:
            self._menu.grab_release()

    def _change_look(self, value: str) -> None:
        try:
            self._config.set("overlay_icon_look", value)
        except (ValueError, OSError):
            log.exception("cannot change overlay look")
        self.root.after(0, self._update)

    def _toggle_alerts(self) -> None:
        try:
            self._config.set("alerts_enabled", bool(self._alerts_var.get()))
        except (ValueError, OSError):
            log.exception("cannot toggle alerts")
