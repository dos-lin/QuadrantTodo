"""生成应用图标 assets/app.ico（多尺寸 Windows 图标）。"""
from __future__ import annotations

from pathlib import Path

from PIL import Image, ImageDraw

SIZE = 256
PAD = 16
GAP = 8

# 左上 Q1（重要紧急-红）、右上 Q2（重要不紧急-蓝）、
# 左下 Q3（不重要紧急-黄）、右下 Q4（不重要不紧急-绿）
COLORS = [
    ("#ea4335", "#c5221f"),  # Q1
    ("#4285f4", "#1a73e8"),  # Q2
    ("#fbbc04", "#f9ab00"),  # Q3
    ("#34a853", "#137333"),  # Q4
]


def draw_icon(size: int) -> Image.Image:
    img = Image.new("RGBA", (size, size), (255, 255, 255, 0))
    draw = ImageDraw.Draw(img)

    pad = max(1, size // 16)
    gap = max(1, size // 32)
    inner = size - 2 * pad
    half = (inner - gap) // 2

    positions = [
        (pad, pad),  # Q1 左上
        (pad + half + gap, pad),  # Q2 右上
        (pad, pad + half + gap),  # Q3 左下
        (pad + half + gap, pad + half + gap),  # Q4 右下
    ]

    radius = max(2, size // 16)
    for (x, y), (fill, outline) in zip(positions, COLORS):
        # 主体圆角矩形
        draw.rounded_rectangle(
            [x, y, x + half - 1, y + half - 1],
            radius=radius,
            fill=fill,
            outline=outline,
            width=max(1, size // 64),
        )
        # 顶部高光条，增加立体感
        highlight_h = max(1, half // 5)
        draw.rounded_rectangle(
            [x + size // 32, y + size // 32, x + half - size // 32, y + highlight_h],
            radius=radius // 2,
            fill=(255, 255, 255, 80),
        )
    return img


def main() -> None:
    """生成多尺寸 ICO。

    坑：Pillow 保存 ICO 时以「基础图」为准，sizes 只是声明目录条目；
    若基础图本身很小（如 16x16），无法放大，最终 ICO 只会有一个尺寸，
    导致任务栏/资源管理器拿不到 32/48 尺寸而显示异常（托盘 16x16 却正常）。
    因此必须以最大尺寸（256）为基础图，让 Pillow 自动缩放到各尺寸。
    """
    sizes = [16, 20, 24, 32, 40, 48, 64, 128, 256]
    base = draw_icon(256)
    ico_path = Path(__file__).parent / "app.ico"
    base.save(ico_path, format="ICO", sizes=[(s, s) for s in sizes])

    # 自检：确认 ICO 内含多个尺寸
    with Image.open(ico_path) as check:
        frames = sorted(check.info.get("sizes", set()))
    print(f"generated {ico_path} sizes={frames}")

    # 另存 PNG 便于人工预览
    base.save(Path(__file__).parent / "app_preview.png")


if __name__ == "__main__":
    main()
