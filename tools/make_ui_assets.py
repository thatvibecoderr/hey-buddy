"""
Regenerates the rounded-corner artwork from the STYLES table in task_reminder.py,
once per theme: ui/ for light, ui/dark/ for dark; the console header pictures; and ui/app.ico.

Only needed after changing a colour or radius in STYLES. Requires Pillow:
    pip install pillow
    python tools/make_ui_assets.py
"""

import os
import sys

from PIL import Image, ImageDraw

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))
import task_reminder as app

SCALE = 8   # draw big, shrink down: smooth edges

for theme in ("light", "dark"):
    app.apply_theme(theme)
    folder = app.theme_ui_dir(theme)
    os.makedirs(folder, exist_ok=True)
    for name, (r, fill, border, bw) in app.STYLES.items():
        size = 2 * r * SCALE
        img = Image.new("RGBA", (size, size), (0, 0, 0, 0))
        draw = ImageDraw.Draw(img)
        draw.ellipse((0, 0, size - 1, size - 1), fill=border)
        if bw:
            inset = bw * SCALE
            draw.ellipse((inset, inset, size - 1 - inset, size - 1 - inset), fill=fill)
        img.resize((2 * r, 2 * r), Image.LANCZOS).save(os.path.join(folder, name + ".png"))
        print(f"wrote {theme}/{name}")

# The small waving character in the console header, for every avatar: head and
# shoulders of its standing / waving pictures, fading out at the bottom.
for key, av in app.AVATARS.items():
    for name in sorted(set(av["header"])):
        img = Image.open(os.path.join(av["dir"], name + ".png")).convert("RGBA")
        left, top, right, bottom = av["header_crop"]
        img = img.crop((round(img.width * left), round(img.height * top),
                        round(img.width * right), round(img.height * bottom)))
        width = round(img.width * app.AVATAR_H / img.height)
        img = img.convert("RGBa").resize((width, app.AVATAR_H), Image.LANCZOS).convert("RGBA")
        alpha = img.getchannel("A")
        fade = 10
        for row in range(fade):
            y = app.AVATAR_H - fade + row
            strip = alpha.crop((0, y, width, y + 1)).point(lambda a, row=row: a * (fade - 1 - row) // fade)
            alpha.paste(strip, (0, y))
        img.putalpha(alpha)
        img.save(os.path.join(app.UI_DIR, f"header_{key}_{name}.png"))
        print(f"wrote header_{key}_{name}")

# The app icon (window, taskbar, tray, installer): Buddy's head and shoulders on a rounded
# accent-coloured tile, so it stays readable on light and dark taskbars.
SIZE = 256
fig = Image.open(os.path.join(app.FIGURE_DIR, "stand.png")).convert("RGBA")
left, top, right, _ = fig.getbbox()
head = fig.crop((left, top, right, top + (right - left)))
tile = Image.new("RGBA", (SIZE * SCALE // 4, SIZE * SCALE // 4), (0, 0, 0, 0))
ImageDraw.Draw(tile).rounded_rectangle((0, 0, tile.width - 1, tile.height - 1),
                                       radius=tile.width // 5, fill=app.THEME_LIGHT["accent"])
tile = tile.resize((SIZE, SIZE), Image.LANCZOS)
inner = round(SIZE * 0.86)
head = head.resize((inner, inner), Image.LANCZOS)
tile.alpha_composite(head, ((SIZE - inner) // 2, SIZE - inner))
tile.save(app.APP_ICON, sizes=[(16, 16), (24, 24), (32, 32), (48, 48), (64, 64), (128, 128), (256, 256)])
print("wrote app.ico")
