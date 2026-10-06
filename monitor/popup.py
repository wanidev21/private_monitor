"""상세 팝업 (tkinter). 트레이 아이콘/오버레이 좌클릭 시 커서가 있는 모니터의 작업표시줄 근처에 표시.

모든 메서드는 메인 스레드에서만 호출한다 (다른 스레드는 root.after로 위임).
섹션: CPU/RAM/GPU/Network는 왼쪽 수치 + 오른쪽 미니 그래프, CPU는 코어별 막대, 디스크는 사용률 막대.
"""
import logging
import math
import os
import time
import tkinter as tk

from monitor import display, winapi
from monitor.graph import WINDOW_SEC, MiniGraph

log = logging.getLogger(__name__)

BASE_WIDTH = 400
GRAPH_WIDTH, GRAPH_HEIGHT = 200, 40
MARGIN = 10
TOGGLE_DEBOUNCE_SEC = 0.3
FOCUS_CHECK_DELAY_MS = 100
FOREGROUND_POLL_MS = 200
DEFAULT_INTERVAL_SEC = 2

_ACCENT_KEYS = {"cpu": "accent_cpu", "ram": "accent_ram", "gpu": "accent_gpu", "net": "accent_net_down"}
_GRAPH_LINES = {
    "cpu": [("cpu", "accent_cpu")],
    "ram": [("ram", "accent_ram")],
    "gpu": [("gpu", "accent_gpu")],
    "net": [("net_down", "accent_net_down"), ("net_up", "accent_net_up")],
}


def calc_popup_position(cursor: winapi.CursorMonitor, width: int, height: int,
                        margin: int = MARGIN) -> tuple[int, int]:
    """커서에 가까운 작업 영역 모서리에 배치 (작업표시줄 방향 무관)."""
    left, top, right, bottom = cursor.work
    x = right - width - margin if cursor.x >= (left + right) // 2 else left + margin
    y = bottom - height - margin if cursor.y >= (top + bottom) // 2 else top + margin
    return x, y


def core_grid(count: int) -> tuple[int, int]:
    """코어별 막대 격자 (열, 행). 8개 초과면 4열, 2~8개면 2열."""
    if count <= 0:
        return 0, 0
    cols = 4 if count > 8 else (2 if count > 1 else 1)
    return cols, math.ceil(count / cols)


class _Row:
    def __init__(self, section, value, sub=None, bar=None, cores=None, graph=None, accent="#FFFFFF"):
        self.section = section
        self.value = value
        self.sub = sub
        self.bar = bar
        self.cores = cores
        self.graph = graph
        self.accent = accent
        self.progress = None  # 마지막 디스크 사용률 (<Configure> 재그리기용)
        self.per_core = []    # 마지막 코어별 사용률


class PopupWindow:
    def __init__(self, root: tk.Tk, collector, config, on_open_web, is_web_available=None):
        self._is_web_available = is_web_available or (lambda: True)
        self._web_button = None
        self._root = root
        self._collector = collector
        self._config = config
        self._on_open_web = on_open_web

        self._win = None
        self._content = None
        self._rows: list[_Row] = []
        self._built_key = None
        self._scale = 1.0
        self._cursor = None
        self._theme = None
        self._visible = False
        self._last_hidden_at = 0.0
        self._refresh_job = None
        self._poll_job = None
        self._foreground_at_show = None
        self._had_focus = False

    # --- 공개 API -----------------------------------------------------------

    def is_visible(self) -> bool:
        return self._visible

    def toggle(self) -> None:
        if self._visible:
            self.hide()
        elif time.monotonic() - self._last_hidden_at < TOGGLE_DEBOUNCE_SEC:
            return  # 트레이/오버레이 클릭으로 방금 닫힘 → 다시 열지 않음
        else:
            self.show()

    def show(self) -> None:
        self._cursor = winapi.get_cursor_monitor()
        self._scale = self._cursor.scale
        self._ensure_window()
        # 값을 먼저 채워야 높이가 정확하다 (빈 라벨 상태로 높이를 재면 하단 버튼이 잘림 — 캡처로 확인)
        self._refresh()
        self._place()
        self._win.deiconify()
        self._win.lift()
        self._win.attributes("-topmost", True)
        self._win.focus_force()
        self._visible = True

        self._had_focus = False
        self._foreground_at_show = winapi.foreground_window()[0]
        self._schedule_refresh()
        self._poll_job = self._win.after(FOREGROUND_POLL_MS, self._poll_foreground)

    def hide(self) -> None:
        if not self._visible:
            return
        self._visible = False
        self._last_hidden_at = time.monotonic()
        for job in (self._refresh_job, self._poll_job):
            if job is not None:
                try:
                    self._win.after_cancel(job)
                except tk.TclError:
                    pass
        self._refresh_job = self._poll_job = None
        self._win.withdraw()

    # --- 창 / 레이아웃 ------------------------------------------------------

    def _ensure_window(self) -> None:
        if self._win is not None:
            return
        win = tk.Toplevel(self._root)
        win.withdraw()
        win.overrideredirect(True)
        win.attributes("-topmost", True)
        win.bind("<FocusOut>", self._on_focus_out)
        win.bind("<Escape>", lambda e: self.hide())
        self._win = win

    def _place(self) -> None:
        cursor = self._cursor
        self._win.update_idletasks()
        width = round(BASE_WIDTH * cursor.scale)
        max_height = cursor.work[3] - cursor.work[1] - 2 * MARGIN
        height = min(self._content.winfo_reqheight() + 2, max_height)
        x, y = calc_popup_position(cursor, width, height)
        self._win.geometry(f"{width}x{height}+{x}+{y}")

    def _core_count(self) -> int:
        data = self._collector.latest
        cpu = data.get("cpu") if data else None
        return len(cpu["per_core"]) if cpu else 0

    def _rebuild_if_needed(self) -> bool:
        sections = display.popup_sections(self._config.tray_visible_items, self._collector.latest)
        key = (self._config.version, tuple(sections), round(self._scale, 2), self._core_count())
        if key == self._built_key and self._content is not None:
            return False
        self._built_key = key
        self._build(sections)
        return True

    def _build(self, sections: list[str]) -> None:
        theme = self._theme = self._config.ui_theme
        s = self._scale
        bg, fg, dim, graph_bg = theme["bg"], theme["fg"], theme["fg_dim"], theme["graph_bg"]
        family = theme["font_family"]
        title_font = (family, theme["font_size_title"], "bold")
        value_font = (family, theme["font_size_value"])
        big_value_font = (family, theme["font_size_value"] + 4, "bold")
        small_font = (family, theme["font_size_small"])
        gap = round(theme["section_gap"] * s)

        if self._content is not None:
            self._content.destroy()
        self._win.configure(bg=graph_bg)  # 1px 테두리 색
        content = tk.Frame(self._win, bg=bg, padx=round(14 * s), pady=round(12 * s))
        content.pack(fill="both", expand=True, padx=1, pady=1)

        self._rows = []
        if not sections:
            tk.Label(content, text="표시할 항목이 없습니다.\n대시보드에서 트레이 표시 항목을 선택하세요.",
                     bg=bg, fg=dim, font=small_font, justify="left").pack(anchor="w", pady=(0, gap))

        for section in sections:
            accent = theme["accent_disk"] if section.startswith("disk:") else theme[_ACCENT_KEYS[section]]
            frame = tk.Frame(content, bg=bg)
            frame.pack(fill="x", pady=(0, gap))
            title = display.popup_title(section)

            if section.startswith("disk:"):
                head = tk.Frame(frame, bg=bg)
                head.pack(fill="x")
                tk.Label(head, text=title, bg=bg, fg=accent, font=title_font).pack(side="left")
                value = tk.Label(head, bg=bg, fg=fg, font=value_font)
                value.pack(side="right")
                bar = tk.Canvas(frame, height=round(6 * s), bg=graph_bg, highlightthickness=0, bd=0)
                bar.pack(fill="x", pady=(round(4 * s), round(2 * s)))
                sub = tk.Label(frame, bg=bg, fg=dim, font=small_font, anchor="w")
                sub.pack(fill="x")
                row = _Row(section, value, sub=sub, bar=bar, accent=accent)
                # 창이 화면에 배치되기 전에는 폭이 1px이므로 실제 크기가 정해질 때 다시 그린다
                bar.bind("<Configure>", lambda e, r=row: self._draw_disk_bar(r.bar, r.progress, r.accent))
                self._rows.append(row)
                continue

            top = tk.Frame(frame, bg=bg)
            top.pack(fill="x")
            left = tk.Frame(top, bg=bg)
            left.pack(side="left", anchor="n")
            tk.Label(left, text=title, bg=bg, fg=accent, font=title_font).pack(anchor="w")
            value = tk.Label(left, bg=bg, fg=fg, font=big_value_font, justify="left")
            value.pack(anchor="w")
            lines = [(key, theme[color_key]) for key, color_key in _GRAPH_LINES[section]]
            graph = MiniGraph(top, round(GRAPH_WIDTH * s), round(GRAPH_HEIGHT * s), graph_bg, lines,
                              auto=(section == "net"), label_color=dim, label_font=(family, 7))
            graph.canvas.pack(side="right", anchor="n")

            cores = None
            if section == "cpu":
                _, rows = core_grid(self._core_count())
                if rows:
                    height = rows * round(4 * s) + (rows - 1) * round(3 * s)
                    cores = tk.Canvas(frame, height=height, bg=bg, highlightthickness=0, bd=0)
                    cores.pack(fill="x", pady=(round(6 * s), 0))
            sub = None
            if display.popup_has_subline(section):
                sub = tk.Label(frame, bg=bg, fg=dim, font=small_font, anchor="w")
                sub.pack(fill="x", pady=(round(2 * s), 0))
            row = _Row(section, value, sub=sub, cores=cores, graph=graph, accent=accent)
            if cores is not None:
                cores.bind("<Configure>", lambda e, r=row: self._draw_cores(r.cores, r.per_core, r.accent))
            self._rows.append(row)

        self._web_button = tk.Button(content, text="대시보드 열기", command=self._open_web, font=value_font,
                                     bg=graph_bg, fg=fg, activebackground=theme["accent_cpu"], activeforeground=bg,
                                     disabledforeground=dim, relief="flat", bd=0, padx=10, pady=round(6 * s),
                                     cursor="hand2")
        self._web_button.pack(fill="x", pady=(2, 0))
        self._content = content

    # --- 갱신 ---------------------------------------------------------------

    def _refresh(self) -> None:
        rebuilt = self._rebuild_if_needed()
        if self._web_button is not None:  # 포트 충돌 등으로 서버가 없으면 비활성화
            self._web_button.configure(state="normal" if self._is_web_available() else "disabled")
        data = self._collector.latest
        history = self._collector.get_history(WINDOW_SEC)
        now = time.time()
        for row in self._rows:
            text, subline, progress = display.popup_texts(row.section, data)
            row.value.configure(text=text)
            if row.sub is not None:
                row.sub.configure(text=subline)
            row.progress = progress
            if row.bar is not None:
                self._draw_disk_bar(row.bar, progress, row.accent)
            if row.cores is not None:
                cpu = data.get("cpu") if data else None
                row.per_core = cpu["per_core"] if cpu else []
                self._draw_cores(row.cores, row.per_core, row.accent)
            if row.graph is not None:
                row.graph.draw(history, now)
        if rebuilt and self._visible:
            self._place()  # 항목 구성이 바뀌면 값을 채운 뒤 높이/위치 다시 계산

    def _draw_disk_bar(self, canvas, progress, accent) -> None:
        canvas.delete("all")
        width, height = canvas.winfo_width(), canvas.winfo_height()
        if progress is None or width <= 1:
            return
        color = accent if progress <= 80 else display.level_color(progress)
        canvas.create_rectangle(0, 0, width * min(progress, 100) / 100, height, fill=color, width=0)

    def _draw_cores(self, canvas, per_core: list[float], accent: str) -> None:
        canvas.delete("all")
        width = canvas.winfo_width()
        cols, _ = core_grid(len(per_core))
        if not cols or width <= 1:
            return
        s = self._scale
        bar_h, row_gap, col_gap = round(4 * s), round(3 * s), round(8 * s)
        cell_w = (width - col_gap * (cols - 1)) / cols
        track = self._theme["graph_bg"]
        for i, percent in enumerate(per_core):
            r, c = divmod(i, cols)
            x0 = c * (cell_w + col_gap)
            y0 = r * (bar_h + row_gap)
            canvas.create_rectangle(x0, y0, x0 + cell_w, y0 + bar_h, fill=track, width=0)
            color = accent if percent <= 80 else display.level_color(percent)
            canvas.create_rectangle(x0, y0, x0 + cell_w * min(percent, 100) / 100, y0 + bar_h, fill=color, width=0)

    def _schedule_refresh(self) -> None:
        try:
            interval = self._config.update_interval_sec
        except Exception:
            interval = DEFAULT_INTERVAL_SEC
        self._refresh_job = self._win.after(int(interval * 1000), self._refresh_tick)

    def _refresh_tick(self) -> None:
        if not self._visible:
            return
        try:
            self._refresh()
        except Exception:
            log.exception("popup refresh failed")
        self._schedule_refresh()

    # --- 닫기 ---------------------------------------------------------------

    def _on_focus_out(self, event) -> None:
        # 내부 위젯 간 포커스 이동에도 발생하므로 약간 지연시켜 실제 포커스 목적지를 확인
        self._win.after(FOCUS_CHECK_DELAY_MS, self._check_focus)

    def _check_focus(self) -> None:
        if not self._visible:
            return
        try:
            focused = self._win.focus_get()
            if focused is None or not str(focused).startswith(str(self._win)):
                self.hide()
        except KeyError:
            self.hide()

    def _poll_foreground(self) -> None:
        """보조 닫기: 포커스를 못 받아 FocusOut이 오지 않는 경우에도 다른 프로그램 창이 전경이 되면 닫는다."""
        if not self._visible:
            return
        hwnd, pid = winapi.foreground_window()
        if pid == os.getpid():
            self._had_focus = True
        elif self._had_focus or hwnd != self._foreground_at_show:
            self.hide()
            return
        self._poll_job = self._win.after(FOREGROUND_POLL_MS, self._poll_foreground)

    def _open_web(self) -> None:
        self.hide()
        self._on_open_web()
