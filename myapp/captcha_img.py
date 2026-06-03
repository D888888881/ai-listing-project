"""
图形验证码：答案仅存于服务端 session，图片通过独立 URL 输出（不在 HTML 中明文传输答案）。
依赖 Pillow；若未安装请执行: pip install Pillow
"""
from __future__ import annotations

import io
import os
import random
import string
from pathlib import Path
from typing import Tuple

CAPTCHA_SESSION_KEY = "captcha_code"

# 排除易混淆字符
_CHARSET = "ABCDEFGHJKLMNPQRSTUVWXYZ23456789"
_LENGTH = 5


def _pick_font(size: int):
    from PIL import ImageFont

    candidates = [
        Path(__file__).resolve().parent / "static_fonts" / "DejaVuSans-Bold.ttf",
        Path(os.environ.get("WINDIR", "C:/Windows")) / "Fonts" / "arial.ttf",
        Path("/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf"),
        Path("/usr/share/fonts/truetype/liberation/LiberationSans-Bold.ttf"),
    ]
    for p in candidates:
        try:
            if p.is_file():
                return ImageFont.truetype(str(p), size)
        except OSError:
            continue
    return ImageFont.load_default()


def generate_captcha_png() -> Tuple[str, io.BytesIO]:
    """生成 PNG，返回 (session 中应保存的小写答案, BytesIO 缓冲区)。"""
    from PIL import Image, ImageDraw, ImageFilter

    code = "".join(random.choices(_CHARSET, k=_LENGTH))
    answer = code.lower()

    w, h = 140, 48
    bg = (
        random.randint(235, 255),
        random.randint(235, 255),
        random.randint(235, 255),
    )
    image = Image.new("RGB", (w, h), bg)
    draw = ImageDraw.Draw(image)

    font_large = _pick_font(28)

    # 干扰曲线
    for _ in range(30):
        x1, y1 = random.randint(0, w), random.randint(0, h)
        x2, y2 = random.randint(0, w), random.randint(0, h)
        draw.line([(x1, y1), (x2, y2)], fill=(random.randint(80, 180),) * 3, width=1)

    # 干扰点
    for _ in range(600):
        x, y = random.randint(0, w - 1), random.randint(0, h - 1)
        draw.point((x, y), fill=(random.randint(100, 200), random.randint(100, 200), random.randint(100, 200)))

    # 字符（颜色干扰 + 轻微错位）
    try:
        bbox = draw.textbbox((0, 0), code[0], font=font_large)
        char_w = max(1, (bbox[2] - bbox[0]))
    except Exception:
        char_w = 22
    gap = (w - 31) // _LENGTH
    for i, ch in enumerate(code):
        fill = (random.randint(20, 120), random.randint(20, 120), random.randint(20, 120))
        ox = 12 + i * gap + random.randint(-4, 4)
        oy = random.randint(4, 12)
        draw.text((ox, oy), ch, font=font_large, fill=fill)

    # 轻微扭曲（机器更难识别）
    image = image.filter(ImageFilter.GaussianBlur(1.5))
    image = image.filter(ImageFilter.SMOOTH_MORE)  # 额外平滑

    buf = io.BytesIO()
    image.save(buf, format="PNG", optimize=True)
    buf.seek(0)
    return answer, buf


def verify_captcha_post(request) -> bool:
    """校验 POST 中的 captcha 是否与 session 一致（大小写不敏感）。"""
    user_val = (request.POST.get("captcha") or "").strip().lower()
    sess_val = (request.session.get(CAPTCHA_SESSION_KEY) or "").strip().lower()
    return bool(sess_val) and bool(user_val) and user_val == sess_val
