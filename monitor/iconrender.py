"""트레이 아이콘 / 작업표시줄 오버레이 이미지 생성 (Pillow).

- render_circle: 오버레이를 표시할 수 없을 때만 쓰는 대체 트레이 아이콘
- render_overlay_strip: 작업표시줄 오버레이 한 줄. 디자인 6종 (config.OVERLAY_LOOKS)
  label 라벨형 / badge 배지형 / gauge 게이지형 / ring 원형 링 / spark 미니 그래프형 / text 두 줄 텍스트

`python -m monitor.iconrender` 실행 시 디자인별 미리보기 PNG를 생성한다.
"""
import functools
import math
import random
import time
from collections import namedtuple

from PIL import Image, ImageDraw, ImageFont

from monitor import display, graph

OVERLAY_BASE = 32                         # overlay_icon_size 기준 크기. 모든 치수는 size/32배
DEFAULT_OVERLAY_FONT_FILE = "seguisb.ttf"  # Segoe UI Semibold (사용자가 12개 후보 미리보기에서 선택)
_SUPERSAMPLE = 4                          # 링/게이지 도형은 4배로 그려 축소 (계단 현상 제거)
_DESIGN_BASE = {"gauge": (34, 40), "ring": (34, 44), "spark": (56, 40)}  # 32 기준 (폭, 높이)

# 글자색: 작업표시줄 테마별 (라벨, 숫자)
_LABEL_MODE_COLORS = {
    True: ((90, 90, 90, 255), (20, 20, 20, 255)),       # 라이트 작업표시줄
    False: ((180, 180, 180, 255), (240, 240, 240, 255)),  # 다크 작업표시줄
}
_TRACK_COLORS = {True: (200, 200, 200, 255), False: (70, 70, 70, 255)}
_WHITE = (255, 255, 255, 255)

OverlayStrip = namedtuple("OverlayStrip", "image hitboxes")
"""image: 투명 배경 RGBA (높이 = 작업표시줄 높이), hitboxes: [(항목, x0, y0, x1, y1)] 이미지 좌표"""


def render_circle(color: str, size: int = 64) -> Image.Image:
    """대체 트레이 아이콘: 부하 색상의 원. Windows가 트레이 크기로 축소한다."""
    image = Image.new("RGBA", (size, size), (0, 0, 0, 0))
    margin = max(1, size // 16)
    ImageDraw.Draw(image).ellipse((margin, margin, size - 1 - margin, size - 1 - margin), fill=color)
    return image


# ---------------------------------------------------------------------------
# 글자 / 도형 도우미
# ---------------------------------------------------------------------------

@functools.lru_cache(maxsize=128)
def _truetype(font_file: str | None, px: int):
    for candidate in (font_file, DEFAULT_OVERLAY_FONT_FILE):
        if candidate:
            try:
                return ImageFont.truetype(candidate, px)
            except OSError:
                continue
    return ImageFont.load_default(size=px)


# 글꼴에 따라(일부 한글 글꼴) 'G°C'가 'G° C'처럼 벌어져 보인다 (사용자 지적).
# 원인은 글꼴마다 다르다: '°' 글자 폭이 넓거나, 뒤 글자('C')의 왼쪽 여백이 넓다.
# 그래서 '°' 잉크 오른쪽 끝과 뒤 글자 잉크 왼쪽 끝 사이의 실제 간격을 재서, 최소 간격만 남기고 당긴다.
# 고정 비율로 당기면 Segoe UI처럼 원래 붙어 있는 글꼴에서 겹친다 (비교 렌더링으로 확인).
DEGREE_MIN_GAP_RATIO = 0.08
_degree_kern_cache: dict[tuple, float] = {}


def _ink_range(font, text: str) -> tuple[int, int] | None:
    """text를 원점에 그렸을 때 실제 잉크가 있는 x 구간 (left, right). 잉크가 없으면 None.

    getbbox()는 글꼴에 따라 잉크가 아니라 글자 칸 전체를 돌려준다 (어떤 글꼴은 '°' 잉크가 3px인데 bbox 9px).
    그래서 빈 이미지에 실제로 그려 픽셀로 잰다.
    """
    size = max(8, int(getattr(font, "size", 10)))
    image = Image.new("L", (int(font.getlength(text)) + size * 3, size * 3), 0)
    ImageDraw.Draw(image).text((size, size // 2), text, font=font, fill=255)
    box = image.getbbox()
    if box is None:
        return None
    return box[0] - size, box[2] - size


def _degree_kern_px(font, next_char: str = "C") -> float:
    """'°' 바로 뒤에 next_char를 그릴 때 왼쪽으로 당길 거리 (px, 0 이상)."""
    key = (getattr(font, "path", None) or id(font), getattr(font, "size", None), next_char)
    if key not in _degree_kern_cache:
        try:
            degree = _ink_range(font, "°")
            following = _ink_range(font, next_char)
            if degree is None or following is None:
                kern = 0.0
            else:
                gap = font.getlength("°") + following[0] - degree[1]
                kern = max(0.0, gap - DEGREE_MIN_GAP_RATIO * getattr(font, "size", 10))
        except Exception:
            kern = 0.0
        _degree_kern_cache[key] = kern
    return _degree_kern_cache[key]


def _degree_kern(text: str, font) -> float:
    """문자열 안의 모든 '°'+뒤 글자 쌍에 대해 당길 거리 합 (px)."""
    return sum(_degree_kern_px(font, text[i + 1]) for i, ch in enumerate(text[:-1]) if ch == "°")


def _text_length(text: str, font) -> float:
    return font.getlength(text) - _degree_kern(text, font)


def _draw_text(draw, xy, text: str, font, fill, anchor: str = "la") -> None:
    """draw.text와 같지만 '°' 뒤 글자를 당겨 붙인다. 조각마다 같은 anchor를 쓰므로 세로 위치가 일치한다."""
    if "°" not in text[:-1]:
        draw.text(xy, text, font=font, fill=fill, anchor=anchor)
        return
    x, y = xy
    pieces = text.split("°")
    for i, piece in enumerate(pieces):
        segment = piece + ("°" if i < len(pieces) - 1 else "")
        if segment:
            draw.text((x, y), segment, font=font, fill=fill, anchor=anchor)
            x += font.getlength(segment)
        if i < len(pieces) - 1 and pieces[i + 1]:
            x -= _degree_kern_px(font, pieces[i + 1][0])


def _fit_truetype(font_file: str | None, px: int, text: str, max_width: float):
    """max_width에 들어갈 때까지 글꼴 크기를 줄인다."""
    px = max(6, px)
    while px > 6:
        font = _truetype(font_file, px)
        if _text_length(text, font) <= max_width:
            return font
        px -= 1
    return _truetype(font_file, px)


def _center_text(draw, text, font, fill, left, right, top, bottom) -> None:
    l, t, r, b = draw.textbbox((0, 0), text, font=font)
    width = (r - l) - _degree_kern(text, font)
    x = left + (right - left - width) / 2 - l
    y = top + (bottom - top - (b - t)) / 2 - t
    _draw_text(draw, (x, y), text, font, fill)


def _draw_arrow(draw, x, top, bottom, up: bool, fill, scale: float) -> None:
    """↑/↓를 도형으로 그린다. 글꼴마다 화살표 글자 유무/모양이 달라서 직접 그림."""
    head = max(2, round(3 * scale))
    cx = x + 2 * scale
    half = 2.5 * scale
    shaft = max(0.5, 0.5 * scale)
    if up:
        draw.polygon([(cx, top), (cx - half, top + head), (cx + half, top + head)], fill=fill)
        draw.rectangle((cx - shaft, top + head, cx + shaft, bottom), fill=fill)
    else:
        draw.rectangle((cx - shaft, top, cx + shaft, bottom - head), fill=fill)
        draw.polygon([(cx, bottom), (cx - half, bottom - head), (cx + half, bottom - head)], fill=fill)


def _draw_label(draw, label: str, font_file, px: int, fill, box, scale: float, align: str = "center") -> None:
    """라벨을 box 안에 그린다. 앞에 ↓/↑가 있으면 도형 화살표 + 나머지 글자."""
    left, top, right, bottom = box
    arrow = label[:1] if label[:1] in ("↓", "↑") else None
    body = label[1:] if arrow else label
    arrow_width = round(6 * scale) if arrow else 0
    font = _fit_truetype(font_file, px, body, max(1, right - left - 2 - arrow_width))
    l, _, r, _ = draw.textbbox((0, 0), body, font=font)
    total = arrow_width + (r - l) - _degree_kern(body, font)
    x0 = left + (right - left - total) / 2 if align == "center" else left
    if arrow:
        _draw_arrow(draw, x0, top + round(2 * scale), bottom - round(1 * scale), arrow == "↑", fill, scale)
    _center_text(draw, body, font, fill, x0 + arrow_width, x0 + total, top, bottom)


def _rounded_mask(size: tuple[int, int], radius: int) -> Image.Image:
    mask = Image.new("L", size, 0)
    ImageDraw.Draw(mask).rounded_rectangle((0, 0, size[0] - 1, size[1] - 1), radius=radius, fill=255)
    return mask


def _rgb(hex_color: str) -> tuple[int, int, int]:
    return tuple(int(hex_color[i:i + 2], 16) for i in (1, 3, 5))


# ---------------------------------------------------------------------------
# 디자인별 항목 이미지
# ---------------------------------------------------------------------------

def render_overlay_icon(look: str, label: str, text: str, color: str, size: int = 32,
                        light_taskbar: bool = True, font_file: str | None = None) -> Image.Image:
    """라벨형(label) / 배지형(badge) 정사각형 아이콘. 치수는 32px 기준값에 size/32를 곱한다."""
    s = size / 32
    image = Image.new("RGBA", (size, size), (0, 0, 0, 0))
    draw = ImageDraw.Draw(image)

    if look == "badge":
        draw.rounded_rectangle((0, 0, size - 1, size - 1), radius=max(2, round(4 * s)), fill=color)
        label_fill = number_fill = _WHITE
        label_band = (round(1 * s), round(12 * s))
        number_band = (round(12 * s), size - 1)
    else:
        label_fill, number_fill = _LABEL_MODE_COLORS[bool(light_taskbar)]
        bar = max(1, round(2 * s))
        draw.rectangle((round(2 * s), size - bar, size - 1 - round(2 * s), size - 1), fill=color)
        label_band = (0, round(11 * s))
        number_band = (round(11 * s), size - bar - 1)

    _draw_label(draw, label, font_file, round(12 * s), label_fill, (0, label_band[0], size, label_band[1]), s)
    number_font = _fit_truetype(font_file, round(20 * s), text, size - 2)
    _center_text(draw, text, number_font, number_fill, 0, size, *number_band)
    return image


def render_gauge_item(face: display.IconFace, ratio: float, width: int, height: int,
                      light_taskbar: bool = True, font_file: str | None = None) -> Image.Image:
    """게이지형: 둥근 사각형이 값만큼 아래에서 차오르고, 위에 라벨·숫자."""
    k = height / 40
    ss = _SUPERSAMPLE
    big_size = (width * ss, height * ss)
    base = Image.new("RGBA", big_size, _TRACK_COLORS[bool(light_taskbar)])
    fill_height = round(height * ss * min(max(ratio, 0.0), 1.0))
    layer = Image.new("RGBA", big_size, (0, 0, 0, 0))
    ImageDraw.Draw(layer).rectangle((0, big_size[1] - fill_height, big_size[0], big_size[1]),
                                    fill=_rgb(face.color) + (150,))
    base.alpha_composite(layer)
    big = Image.new("RGBA", big_size, (0, 0, 0, 0))
    big.paste(base, (0, 0), _rounded_mask(big_size, max(2, round(5 * k)) * ss))
    image = big.resize((width, height), Image.LANCZOS)

    label_fill, number_fill = _LABEL_MODE_COLORS[bool(light_taskbar)]
    draw = ImageDraw.Draw(image)
    _draw_label(draw, face.label, font_file, round(10 * k), label_fill, (0, round(3 * k), width, round(14 * k)), k)
    number_font = _fit_truetype(font_file, round(17 * k), face.text, width - 2)
    _center_text(draw, face.text, number_font, number_fill, 0, width, round(14 * k), height - round(2 * k))
    return image


def render_ring_item(face: display.IconFace, ratio: float, width: int, height: int,
                     light_taskbar: bool = True, font_file: str | None = None) -> Image.Image:
    """원형 링: 값만큼 호가 채워지고 가운데 숫자, 아래 라벨."""
    k = height / 44
    diameter = min(width, round(32 * k))
    ss = _SUPERSAMPLE
    ring = Image.new("RGBA", (diameter * ss, diameter * ss), (0, 0, 0, 0))
    draw = ImageDraw.Draw(ring)
    inset = round(2 * k * ss)
    bbox = (inset, inset, diameter * ss - inset, diameter * ss - inset)
    stroke = max(ss, round(3 * k * ss))
    draw.ellipse(bbox, outline=_TRACK_COLORS[bool(light_taskbar)], width=stroke)
    sweep = 360 * min(max(ratio, 0.01), 1.0)
    draw.arc(bbox, start=-90, end=-90 + sweep, fill=_rgb(face.color) + (255,), width=stroke)
    ring = ring.resize((diameter, diameter), Image.LANCZOS)

    image = Image.new("RGBA", (width, height), (0, 0, 0, 0))
    image.alpha_composite(ring, ((width - diameter) // 2, 0))
    label_fill, number_fill = _LABEL_MODE_COLORS[bool(light_taskbar)]
    draw = ImageDraw.Draw(image)
    number_font = _fit_truetype(font_file, round(13 * k), face.text, diameter - round(8 * k))
    _center_text(draw, face.text, number_font, number_fill, 0, width, 0, diameter)
    _draw_label(draw, face.label, font_file, round(9 * k), label_fill, (0, diameter, width, height), k * 0.8)
    return image


def render_spark_item(item: str, face: display.IconFace, history: list[dict], now: float, vmax: float,
                      width: int, height: int, light_taskbar: bool = True,
                      font_file: str | None = None) -> Image.Image:
    """미니 그래프형: 최근 60초 추이 영역 그래프 위에 라벨(왼쪽 위)과 숫자(오른쪽 위)."""
    k = height / 40
    track = _TRACK_COLORS[bool(light_taskbar)]
    image = Image.new("RGBA", (width, height), track)
    draw = ImageDraw.Draw(image)

    top = round(15 * k)
    graph_height = height - top
    background_hex = "#{:02X}{:02X}{:02X}".format(*track[:3])
    fill = graph.blend(face.color, background_hex, 0.35)
    series = graph.series_from_history(history, item)
    for segment in graph.to_segments(series, now, width, graph_height, vmax):
        points = [(x, y + top) for x, y in segment]
        if len(points) >= 2:
            draw.polygon(points + [(points[-1][0], height), (points[0][0], height)], fill=fill)
            draw.line(points, fill=face.color, width=1)
    image.putalpha(_rounded_mask((width, height), max(2, round(4 * k))))

    label_fill, number_fill = _LABEL_MODE_COLORS[bool(light_taskbar)]
    draw = ImageDraw.Draw(image)
    pad = round(4 * k)
    _draw_label(draw, face.label, font_file, round(10 * k), label_fill, (pad, 0, round(width * 0.55), top), k,
                align="left")
    number_font = _fit_truetype(font_file, round(13 * k), face.text, width * 0.45 - pad)
    l, t, r, b = draw.textbbox((0, 0), face.text, font=number_font)
    draw.text((width - pad - (r - l) - l, (top - (b - t)) / 2 - t), face.text, font=number_font, fill=number_fill)
    return image


def _text_value_sample(item: str) -> str:
    """두 줄 텍스트에서 칸 폭을 고정하기 위한 가장 긴 값 (값이 바뀔 때마다 창 폭이 흔들리지 않도록)."""
    if item in ("net_down", "net_up"):
        return "1023K"
    return "100°" if item == "gpu_temp" else "100%"


def _render_text_strip(items, data, k: float, bar_height: int, light_taskbar: bool, font_file):
    """두 줄 텍스트: 항목을 위/아래 두 줄로 채워 열 단위로 배치 (1열: 1·2번째 항목, 2열: 3·4번째 …)."""
    label_fill, number_fill = _LABEL_MODE_COLORS[bool(light_taskbar)]
    label_font = _truetype(font_file, max(7, round(11 * k)))
    value_font = _truetype(font_file, max(8, round(15 * k)))
    label_gap, column_gap, pad = round(5 * k), round(16 * k), max(2, round(4 * k))
    columns = [items[i:i + 2] for i in range(0, len(items), 2)]

    widths = []
    for column in columns:
        widths.append(math.ceil(max(
            _text_length(display.text_design_parts(item, None)[0], label_font) + label_gap
            + _text_length(_text_value_sample(item), value_font)
            for item in column)))

    width = 2 * pad + sum(widths) + column_gap * max(0, len(columns) - 1)
    image = Image.new("RGBA", (max(width, 1), bar_height), (0, 0, 0, 0))
    draw = ImageDraw.Draw(image)
    row_height = bar_height / 2
    hitboxes = []
    x = pad
    for column, column_width in zip(columns, widths):
        for row, item in enumerate(column):
            label, value, color = display.text_design_parts(item, data)
            y0, y1 = round(row * row_height), round((row + 1) * row_height)
            # 두 줄을 가운데 쪽으로 약간 모아 작업표시줄 위아래 여백과 균형을 맞춘다
            cy = (y0 + y1) / 2 + (row_height * 0.1 if row == 0 else -row_height * 0.1)
            _draw_text(draw, (x, cy), label, label_font, label_fill, anchor="lm")
            label_width = _text_length(label, label_font)
            _draw_text(draw, (x + label_width + label_gap, cy), value, value_font, color or number_fill, anchor="lm")
            hitboxes.append((item, x, y0, x + column_width, y1))
        x += column_width + column_gap
    return image, hitboxes


def _scale_info(item: str, data: dict | None, history: list[dict]) -> tuple[float, float]:
    """(현재 값 비율 0~1, 그래프 Y축 최대값). 네트워크는 최근 최대값 기준 자동 스케일, 나머지는 0~100."""
    if item in ("net_down", "net_up"):
        values = [v for _, v in graph.series_from_history(history, item) if v is not None]
        current = graph.sample_value(data, item) if data else None
        if current is not None:
            values.append(current)
        vmax = graph.auto_scale(values)
        return (current / vmax if current is not None else 0.0), vmax
    value = display.item_value(item, data) if data else None
    return (min(max(value, 0), 100) / 100 if value is not None else 0.0), 100.0


# ---------------------------------------------------------------------------
# 오버레이 한 줄
# ---------------------------------------------------------------------------

def render_overlay_strip(look: str, items: list[str], data: dict | None, history: list[dict], theme: dict,
                         size: int, bar_height: int, light_taskbar: bool = True, font_file: str | None = None,
                         net_units: dict | None = None, now: float | None = None):
    """작업표시줄 높이의 투명 이미지 한 줄. 반환: (OverlayStrip, 갱신된 net_units).

    size는 DPI 배율을 적용한 overlay_icon_size. 디자인 높이가 작업표시줄보다 커지면 작업표시줄에 맞춰 줄인다.
    net_units는 네트워크 단위 전환 히스테리시스 상태로, 다음 호출에 그대로 넘긴다.
    """
    net_units = dict(net_units or {})
    now = time.time() if now is None else now
    k = size / OVERLAY_BASE

    if look == "text":
        image, hitboxes = _render_text_strip(list(items), data, k, bar_height, light_taskbar, font_file)
        return OverlayStrip(image, hitboxes), net_units

    parts = []
    for item in items:
        face, unit = display.icon_face(item, data, theme, net_units.get(item))
        net_units[item] = unit
        if look in _DESIGN_BASE:
            base_width, base_height = _DESIGN_BASE[look]
            scale = min(k, (bar_height - 4) / base_height)
            width, height = max(8, round(base_width * scale)), max(8, round(base_height * scale))
            ratio, vmax = _scale_info(item, data, history)
            if look == "gauge":
                image = render_gauge_item(face, ratio, width, height, light_taskbar, font_file)
            elif look == "ring":
                image = render_ring_item(face, ratio, width, height, light_taskbar, font_file)
            else:
                image = render_spark_item(item, face, history, now, vmax, width, height, light_taskbar, font_file)
        else:
            px = max(12, min(round(size), bar_height - 4))
            image = render_overlay_icon("badge" if look == "badge" else "label", face.label, face.text,
                                        face.color, px, light_taskbar, font_file)
        parts.append((item, image))

    gap, pad = max(4, round(8 * k)), max(2, round(4 * k))
    width = 2 * pad + sum(image.width for _, image in parts) + gap * max(0, len(parts) - 1)
    strip = Image.new("RGBA", (max(width, 1), bar_height), (0, 0, 0, 0))
    hitboxes = []
    x = pad
    for item, image in parts:
        y = (bar_height - image.height) // 2
        strip.alpha_composite(image, (x, y))
        hitboxes.append((item, x, y, x + image.width, y + image.height))
        x += image.width + gap
    return OverlayStrip(strip, hitboxes), net_units


def render_overlay_preview(look: str, items: list[str], data: dict | None, history: list[dict], theme: dict,
                           size: int = 32, light_taskbar: bool = True, font_file: str | None = None,
                           bar_height: int = 48) -> Image.Image:
    """대시보드 설정 화면용: 작업표시줄 배경색 위에 그린 오버레이 모습 (실제 크기)."""
    background = (238, 238, 238, 255) if light_taskbar else (32, 32, 32, 255)
    if not items:
        canvas = Image.new("RGBA", (bar_height, bar_height), background)
        canvas.alpha_composite(render_circle(display.GRAY).resize((16, 16), Image.LANCZOS),
                               ((bar_height - 16) // 2, (bar_height - 16) // 2))
        return canvas
    strip, _ = render_overlay_strip(look, items, data, history, theme, size, bar_height, light_taskbar, font_file)
    canvas = Image.new("RGBA", (strip.image.width + 16, bar_height), background)
    canvas.alpha_composite(strip.image, (8, 0))
    return canvas


# ---------------------------------------------------------------------------
# 미리보기 CLI
# ---------------------------------------------------------------------------

_PREVIEW_DATA = {
    "cpu": {"percent": 45.2, "per_core": []},
    "ram": {"used_gb": 27.7, "total_gb": 31.8, "percent": 87.1},
    "gpu": {"name": "RTX 2060", "percent": 23, "temp": 62, "vram_used_gb": 2.1, "vram_total_gb": 6.0},
    "disks": [{"mount": "C:\\", "used_gb": 120.0, "total_gb": 256.0, "percent": 58.0,
               "read_mb_s": 1.0, "write_mb_s": 0.0}],
    "network": {"upload_kb_s": 125.3, "download_kb_s": 2048.7, "today_upload_mb": 85.0,
                "today_download_mb": 1228.8},
    "timestamp": 60.0,
}
_PREVIEW_ITEMS = ["cpu", "ram", "gpu", "gpu_temp", "net_down", "net_up"]


def _preview_history() -> list[dict]:
    rng = random.Random(7)
    history, cpu, down = [], 45.0, 2000.0
    for t in range(0, 61, 2):
        cpu = min(98.0, max(2.0, cpu + rng.uniform(-9, 9)))
        down = max(0.0, down + rng.uniform(-600, 600))
        sample = dict(_PREVIEW_DATA, timestamp=float(t))
        sample["cpu"] = {"percent": cpu, "per_core": []}
        sample["network"] = dict(_PREVIEW_DATA["network"], download_kb_s=down)
        history.append(sample)
    return history


def build_preview(size: int = 32) -> Image.Image:
    from monitor.config import DEFAULTS, OVERLAY_LOOKS

    theme = DEFAULTS["ui_theme"]
    history = _preview_history()
    rows = []
    for light in (True, False):
        for look in OVERLAY_LOOKS:
            rows.append(render_overlay_preview(look, _PREVIEW_ITEMS, _PREVIEW_DATA, history, theme, size, light,
                                               DEFAULT_OVERLAY_FONT_FILE))
    width = max(r.width for r in rows) + 20
    canvas = Image.new("RGBA", (width, sum(r.height + 8 for r in rows) + 8), (255, 255, 255, 255))
    y = 8
    for row in rows:
        canvas.alpha_composite(row, (10, y))
        y += row.height + 8
    return canvas


def main(argv=None):
    import argparse
    import os

    parser = argparse.ArgumentParser(description="오버레이 디자인 6종 미리보기 PNG 생성 (라이트/다크 작업표시줄)")
    parser.add_argument("--out", default="overlay_preview.png")
    parser.add_argument("--size", type=int, default=32)
    args = parser.parse_args(argv)
    build_preview(args.size).convert("RGB").save(args.out)
    print(os.path.abspath(args.out))


if __name__ == "__main__":
    main()
