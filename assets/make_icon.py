"""assets/icon.ico 생성 (exe 아이콘). 어두운 둥근 사각형 + CPU/RAM/GPU 색 막대 3개.

실행: .venv\\Scripts\\python.exe assets\\make_icon.py
"""
import os

from PIL import Image, ImageDraw

OUT = os.path.join(os.path.dirname(os.path.abspath(__file__)), "icon.ico")
SIZE = 1024  # 크게 그려 축소 (가장자리 부드럽게)
SIZES = [16, 20, 24, 32, 40, 48, 64, 128, 256]

BACKGROUND = (30, 30, 30, 255)
TRACK = (58, 58, 58, 255)
BARS = [  # (색, 높이 비율) — config 기본 테마의 CPU / RAM / GPU 색
    ((79, 195, 247, 255), 0.55),
    ((129, 199, 132, 255), 0.85),
    ((255, 183, 77, 255), 0.40),
]


def build() -> Image.Image:
    image = Image.new("RGBA", (SIZE, SIZE), (0, 0, 0, 0))
    draw = ImageDraw.Draw(image)
    draw.rounded_rectangle((0, 0, SIZE - 1, SIZE - 1), radius=SIZE // 5, fill=BACKGROUND)

    pad = SIZE * 0.2
    gap = SIZE * 0.07
    bar_width = (SIZE - 2 * pad - gap * (len(BARS) - 1)) / len(BARS)
    top, bottom = SIZE * 0.2, SIZE * 0.8
    for i, (color, ratio) in enumerate(BARS):
        left = pad + i * (bar_width + gap)
        radius = bar_width * 0.25
        draw.rounded_rectangle((left, top, left + bar_width, bottom), radius=radius, fill=TRACK)
        fill_top = bottom - (bottom - top) * ratio
        draw.rounded_rectangle((left, fill_top, left + bar_width, bottom), radius=radius, fill=color)
    return image


def main():
    base = build()
    icons = [base.resize((s, s), Image.LANCZOS) for s in SIZES]
    icons[-1].save(OUT, format="ICO", sizes=[(s, s) for s in SIZES], append_images=icons[:-1])
    print(OUT)


if __name__ == "__main__":
    main()
