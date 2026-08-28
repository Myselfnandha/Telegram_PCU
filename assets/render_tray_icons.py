#!/usr/bin/env python3
import os
from PIL import Image, ImageDraw

def create_pixel_perfect_tray_icon(size: int = 24, connected: bool = True) -> Image.Image:
    """
    Renders a pixel-perfect, clean Telegram tray icon without any badges or artifacts.
    """
    scale = 4
    s = size * scale
    img = Image.new("RGBA", (s, s), (0, 0, 0, 0))
    draw = ImageDraw.Draw(img)

    pad = 1 * scale
    # Official Telegram Blue circle
    bg = "#24A1DE" if connected else "#5A6578"
    draw.ellipse([pad, pad, s - pad - 1, s - pad - 1], fill=bg)

    cx, cy = s / 2, s / 2
    r = (s - 2 * pad) / 2

    # Centered Telegram Paper Plane
    p_nose = (cx + 0.44 * r, cy - 0.05 * r)
    p_tail = (cx - 0.42 * r, cy + 0.05 * r)
    p_top  = (cx - 0.16 * r, cy - 0.44 * r)
    p_bot  = (cx + 0.02 * r, cy + 0.30 * r)
    p_mid  = (cx - 0.12 * r, cy + 0.08 * r)

    # Top wing (Pure White)
    draw.polygon([p_tail, p_nose, p_top], fill="#FFFFFF")
    # Bottom fold (Subtle shade)
    draw.polygon([p_nose, p_bot, p_mid], fill="#B0DCF2" if connected else "#9CA3AF")
    # Bottom body (Off-white)
    draw.polygon([p_tail, p_nose, p_mid], fill="#E1F3FB" if connected else "#D1D5DB")

    return img.resize((size, size), Image.Resampling.LANCZOS)

def generate_all():
    assets_dir = os.path.join(os.path.dirname(__file__), "assets")
    os.makedirs(assets_dir, exist_ok=True)
    
    for sz in (22, 24, 32, 48, 64, 128, 256):
        img = create_pixel_perfect_tray_icon(sz, connected=True)
        img.save(os.path.join(assets_dir, f"tray_{sz}.png"), "PNG")
    
    # 24px tray icon & 256px app icon
    create_pixel_perfect_tray_icon(24, connected=True).save(os.path.join(assets_dir, "tray_icon.png"), "PNG")
    create_pixel_perfect_tray_icon(256, connected=True).save(os.path.join(assets_dir, "tg-fdm-proxy.png"), "PNG")
    print("Clean tray icons generated successfully.")

if __name__ == "__main__":
    generate_all()
