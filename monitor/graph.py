"""미니 그래프 (tk.Canvas). 최근 60초 히스토리를 영역 채우기 차트로 그린다.

tkinter Canvas는 알파(반투명)를 지원하지 않으므로 채우기 색은 선 색과 배경색을 미리 섞어서 쓴다.
X축은 샘플 개수가 아니라 시간(60초) 기준이라 수집 주기가 바뀌어도 폭이 같다.
"""
import tkinter as tk

from monitor import display

WINDOW_SEC = 60
FILL_RATIO = 0.3          # 채우기 = 선 색 30% + 배경 70%
NET_MIN_SCALE_KB = 64     # 네트워크 자동 스케일 최소값 (유휴 상태에서 잡음이 크게 보이지 않도록)
NET_SCALE_HEADROOM = 1.15


def blend(color: str, background: str, ratio: float) -> str:
    """#RRGGBB 두 색을 섞는다. ratio=1이면 color, 0이면 background."""
    c = [int(color[i:i + 2], 16) for i in (1, 3, 5)]
    b = [int(background[i:i + 2], 16) for i in (1, 3, 5)]
    mixed = [round(cv * ratio + bv * (1 - ratio)) for cv, bv in zip(c, b)]
    return "#{:02X}{:02X}{:02X}".format(*mixed)


def sample_value(sample: dict, key: str) -> float | None:
    """샘플 하나에서 key의 값. key: cpu, ram, gpu, gpu_temp, net_down, net_up, disk:C:\\ (없으면 None)."""
    if key in ("net_down", "net_up"):
        net = sample.get("network")
        return (net["download_kb_s"] if key == "net_down" else net["upload_kb_s"]) if net else None
    if key == "gpu_temp":
        gpu = sample.get("gpu")
        return gpu["temp"] if gpu else None
    if key.startswith("disk:"):
        disk = display.find_disk(sample, key[5:])
        return disk["percent"] if disk else None
    section = sample.get(key)
    return section["percent"] if section else None


def series_from_history(history: list[dict], key: str) -> list[tuple[float, float | None]]:
    """히스토리 → [(timestamp, 값 | None)]."""
    return [(sample["timestamp"], sample_value(sample, key)) for sample in history]


def auto_scale(values: list[float]) -> float:
    return max(max(values, default=0) * NET_SCALE_HEADROOM, NET_MIN_SCALE_KB)


def to_segments(series, now: float, width: int, height: int, vmax: float,
                window: float = WINDOW_SEC) -> list[list[tuple[float, float]]]:
    """캔버스 좌표 선분 목록. 값이 없는 샘플에서 선이 끊기고, window보다 오래된 샘플은 버린다.

    y는 0일 때 맨 아래 선(height-1), vmax일 때 맨 위(0)가 되도록 매핑한다.
    """
    segments, current = [], []
    for timestamp, value in series:
        age = now - timestamp
        if age > window or age < -1:
            continue
        if value is None:
            if current:
                segments.append(current)
            current = []
            continue
        x = width - 1 - max(0.0, age) / window * (width - 1)
        ratio = min(max(value, 0) / vmax, 1.0) if vmax > 0 else 0.0
        y = (height - 1) - ratio * (height - 1)
        current.append((x, y))
    if current:
        segments.append(current)
    return segments


class MiniGraph:
    """lines: [(히스토리 key, 선 색)]. auto_scale=False면 Y축 0~100."""

    def __init__(self, parent, width: int, height: int, background: str,
                 lines: list[tuple[str, str]], auto: bool = False, label_color: str = "#888888", label_font=None):
        self.width, self.height = width, height
        self.background = background
        self.lines = lines
        self.auto = auto
        self.label_color = label_color
        self.label_font = label_font
        self.canvas = tk.Canvas(parent, width=width, height=height, bg=background, highlightthickness=0, bd=0)

    def draw(self, history: list[dict], now: float) -> None:
        c = self.canvas
        c.delete("all")
        series = {key: series_from_history(history, key) for key, _ in self.lines}
        if self.auto:
            vmax = auto_scale([v for s in series.values() for _, v in s if v is not None])
        else:
            vmax = 100.0

        all_segments = [(color, to_segments(series[key], now, self.width, self.height, vmax))
                        for key, color in self.lines]
        for color, segments in all_segments:  # 채우기를 먼저 모두 그리고 선을 위에 그린다
            fill = blend(color, self.background, FILL_RATIO)
            for seg in segments:
                if len(seg) >= 2:
                    polygon = seg + [(seg[-1][0], self.height), (seg[0][0], self.height)]
                    c.create_polygon(*[v for point in polygon for v in point], fill=fill, outline="")
        for color, segments in all_segments:
            for seg in segments:
                if len(seg) >= 2:
                    c.create_line(*[v for point in seg for v in point], fill=color, width=1)
                elif seg:
                    x, y = seg[0]
                    c.create_rectangle(x - 1, y - 1, x + 1, y + 1, fill=color, outline="")

        if self.auto:
            c.create_text(3, 1, anchor="nw", text=display.format_rate(vmax), fill=self.label_color,
                          font=self.label_font)
