"""Build the KDP full-wrap paperback cover (back + spine + front) at 300 dpi.

usage: python3 build_cover.py <raw_art_dir> <fonts_dir> <interior.pdf> <out_dir>
"""
import math
import os
import subprocess
import sys
import tempfile

from PIL import Image, ImageDraw, ImageFilter, ImageFont
from pypdf import PdfReader
from reportlab.pdfgen import canvas

RAW, FONTS, INTERIOR, OUT = sys.argv[1:5]
os.makedirs(OUT, exist_ok=True)

DPI = 300
TRIM_W, TRIM_H, BLEED = 8.5, 11.0, 0.125
PAGES = len(PdfReader(INTERIOR).pages)
SPINE = PAGES * 0.002252                      # KDP white paper, black & white interior
COVER_W_IN = BLEED + TRIM_W + SPINE + TRIM_W + BLEED
COVER_H_IN = TRIM_H + 2 * BLEED
W, H = round(COVER_W_IN * DPI), round(COVER_H_IN * DPI)
px = lambda inches: round(inches * DPI)
BACK_R = px(BLEED + TRIM_W)                    # back panel right edge (= spine left)
FRONT_L = px(BLEED + TRIM_W + SPINE)           # front panel left edge (= spine right)

NAVY = (29, 43, 100)
WHITE = (255, 255, 255)
PINK = (255, 79, 160)
RAINBOW = [(255, 64, 64), (255, 140, 30), (255, 205, 40), (60, 200, 90), (40, 140, 255), (150, 90, 255), (255, 79, 160)]


def font(name, size):
    return ImageFont.truetype(os.path.join(FONTS, name), size)


LUCKY = lambda s: font("LuckiestGuy.ttf", s)
FRED = lambda s: font("Fredoka-700.ttf", s)
FRED6 = lambda s: font("Fredoka-600.ttf", s)
NUN = lambda s: font("Nunito-800.ttf", s)


def cover_fit(im, w, h, anchor_y=0.5):
    s = max(w / im.width, h / im.height)
    im = im.resize((round(im.width * s), round(im.height * s)), Image.LANCZOS)
    x = (im.width - w) // 2
    y = round((im.height - h) * anchor_y)
    return im.crop((x, y, x + w, y + h))


def shadow_layer(size, draw_fn, blur=14, offset=(10, 14), alpha=110):
    layer = Image.new("L", size, 0)
    draw_fn(ImageDraw.Draw(layer))
    layer = layer.filter(ImageFilter.GaussianBlur(blur))
    sh = Image.new("RGBA", size, NAVY + (0,))
    sh.putalpha(layer.point(lambda v: v * alpha // 255))
    out = Image.new("RGBA", size, (0, 0, 0, 0))
    out.paste(sh, offset, sh)
    return out


def candy_text(img, text, cx, y, f, colors, outer=26, inner=12, spacing=0):
    """Multi-colored letters with white + navy outline and soft drop shadow, centred on cx."""
    widths = [f.getlength(text[:i]) for i in range(len(text) + 1)]
    total = widths[-1] + spacing * (len(text) - 1)
    x0 = cx - total / 2
    pos = [(x0 + widths[i] + spacing * i, y) for i in range(len(text))]

    def sil(d):
        for (x, yy), ch in zip(pos, text):
            d.text((x, yy), ch, font=f, fill=255, stroke_width=outer, stroke_fill=255)

    img.alpha_composite(shadow_layer(img.size, sil))
    d = ImageDraw.Draw(img)
    for (x, yy), ch in zip(pos, text):
        d.text((x, yy), ch, font=f, fill=NAVY, stroke_width=outer, stroke_fill=NAVY)
    ci = 0
    for (x, yy), ch in zip(pos, text):
        col = colors[ci % len(colors)] if ch.strip() else WHITE
        if ch.strip():
            ci += 1
        d.text((x, yy), ch, font=f, fill=col, stroke_width=inner, stroke_fill=WHITE)
        # glossy highlight
    return total


def outlined(d, xy, text, f, fill=WHITE, stroke=NAVY, sw=8, anchor="mm"):
    d.text(xy, text, font=f, fill=fill, stroke_width=sw, stroke_fill=stroke, anchor=anchor)


def ribbon(img, cx, cy, w, h, color=(255, 59, 92), dark=(196, 30, 70)):
    d = ImageDraw.Draw(img)
    tail = h * 0.95
    for sgn in (-1, 1):
        x0 = cx + sgn * (w / 2 - h * 0.35)
        x1 = cx + sgn * (w / 2 + tail)
        pts = [(x0, cy - h * 0.2), (x1, cy - h * 0.2), (x1 - sgn * h * 0.45, cy + h * 0.35),
               (x1, cy + h * 0.9), (x0, cy + h * 0.9)]
        d.polygon(pts, fill=dark, outline=NAVY, width=9)
    box = (cx - w / 2, cy - h / 2, cx + w / 2, cy + h / 2)

    def sil(dd):
        dd.rounded_rectangle(box, radius=18, fill=255)
    img.alpha_composite(shadow_layer(img.size, sil, blur=10, offset=(6, 10), alpha=90))
    d = ImageDraw.Draw(img)
    d.rounded_rectangle(box, radius=18, fill=color, outline=NAVY, width=10)


def starburst(img, cx, cy, r, points=18, color=(255, 212, 59)):
    pts = []
    for i in range(points * 2):
        rad = r if i % 2 == 0 else r * 0.84
        a = i * math.pi / points
        pts.append((cx + rad * math.cos(a), cy + rad * math.sin(a)))

    def sil(d):
        d.polygon(pts, fill=255)
    img.alpha_composite(shadow_layer(img.size, sil, blur=12, offset=(8, 12), alpha=110))
    d = ImageDraw.Draw(img)
    d.polygon(pts, fill=color, outline=NAVY, width=12)
    d.ellipse((cx - r * 0.72, cy - r * 0.72, cx + r * 0.72, cy + r * 0.72), outline=WHITE, width=8)


def paw_icon(d, cx, cy, s, fill=PINK):
    d.ellipse((cx - s * 0.55, cy - s * 0.2, cx + s * 0.55, cy + s * 0.6), fill=fill)
    for dx, dy in [(-0.62, -0.42), (-0.22, -0.72), (0.22, -0.72), (0.62, -0.42)]:
        r = s * 0.2
        d.ellipse((cx + dx * s - r, cy + dy * s - r * 1.2, cx + dx * s + r, cy + dy * s + r * 1.2), fill=fill)


def front(img):
    x0, w = FRONT_L, W - FRONT_L
    art = cover_fit(Image.open(os.path.join(RAW, "pcover.jpg")).convert("RGB"), w, H, anchor_y=0.3)
    img.paste(art, (x0, 0))
    cx = x0 + (w - px(BLEED)) / 2                # centre of the trimmed front panel

    candy_text(img, "PUDDING &", cx, px(0.42), LUCKY(300), RAINBOW, outer=30, inner=13, spacing=6)
    candy_text(img, "PICKLE'S", cx, px(1.42), LUCKY(300), RAINBOW[3:] + RAINBOW[:3], outer=30, inner=13, spacing=6)
    rw, rh, ry = px(5.6), px(0.56), px(2.72)
    ribbon(img, cx, ry, rw, rh)
    d = ImageDraw.Draw(img)
    outlined(d, (cx, ry + 4), "A YEAR OF ADVENTURES", FRED(108), WHITE, NAVY, 7)

    # 60 pages burst (left, over the snowflakes)
    bx, by, br = x0 + px(1.2), px(4.0), px(0.9)
    starburst(img, bx, by, br)
    d = ImageDraw.Draw(img)
    outlined(d, (bx, by - px(0.18)), "60", LUCKY(190), PINK, NAVY, 9)
    outlined(d, (bx, by + px(0.28)), "BIG & EASY", FRED(60), NAVY, WHITE, 0)
    outlined(d, (bx, by + px(0.48)), "PAGES", FRED(60), NAVY, WHITE, 0)

    # COLORING BOOK label (bottom)
    lw_, lh = px(6.9), px(1.18)
    box = (cx - lw_ / 2, H - px(BLEED + 0.32) - lh, cx + lw_ / 2, H - px(BLEED + 0.32))

    def sil(dd):
        dd.rounded_rectangle(box, radius=70, fill=255)
    img.alpha_composite(shadow_layer(img.size, sil, blur=14, offset=(8, 12), alpha=120))
    d = ImageDraw.Draw(img)
    d.rounded_rectangle(box, radius=70, fill=WHITE, outline=NAVY, width=12)
    d.rounded_rectangle((box[0] + 16, box[1] + 16, box[2] - 16, box[3] - 16), radius=56, outline=PINK, width=7)
    mid = (box[1] + box[3]) / 2
    outlined(d, (cx, mid - px(0.16)), "COLORING BOOK", LUCKY(196), PINK, NAVY, 8)
    outlined(d, (cx, mid + px(0.33)), "FOR KIDS AGES 2-6", FRED(70), NAVY, WHITE, 0)


def back(img):
    w = BACK_R
    art = cover_fit(Image.open(os.path.join(RAW, "pback.jpg")).convert("RGB"), w, H, anchor_y=0.5)
    img.paste(art, (0, 0))
    cx = px(BLEED) + px(TRIM_W) / 2
    d = ImageDraw.Draw(img)

    candy_text(img, "A WHOLE YEAR", cx, px(0.55), LUCKY(170), RAINBOW, outer=20, inner=9, spacing=4)
    candy_text(img, "OF PUPPY FUN!", cx, px(1.15), LUCKY(170), RAINBOW[2:] + RAINBOW[:2], outer=20, inner=9, spacing=4)

    # blurb card
    box = (px(0.6), px(1.95), BACK_R - px(0.55), px(5.15))
    panel = Image.new("RGBA", img.size, (0, 0, 0, 0))
    pd = ImageDraw.Draw(panel)
    pd.rounded_rectangle(box, radius=50, fill=(255, 255, 255, 236), outline=NAVY + (255,), width=8)
    img.alpha_composite(shadow_layer(img.size, lambda dd: dd.rounded_rectangle(box, radius=50, fill=255),
                                     blur=14, offset=(8, 12), alpha=90))
    img.alpha_composite(panel)
    d = ImageDraw.Draw(img)
    intro = [
        "Color your way through 12 magical months with Pudding & Pickle,",
        "two lovable corgi pups who never miss an adventure! Build a",
        "snow-pup in January, fly a kite in March, dig up pirate treasure",
        "in July and find the biggest pumpkin in October.",
    ]
    y = box[1] + px(0.32)
    for ln in intro:
        d.text((box[0] + px(0.35), y), ln, font=FRED6(60), fill=NAVY)
        y += px(0.27)
    y += px(0.12)
    bullets = [
        "60 original pictures, each on its own page (no bleed-through)",
        "Big, bold, easy lines made for little hands",
        "A read-aloud story line + color challenge on every page",
        "Bonus: color tester, draw-yourself page & Super Colorer award",
        "Large 8.5 x 11 inch pages",
    ]
    for b in bullets:
        paw_icon(d, box[0] + px(0.5), y + px(0.1), px(0.13))
        d.text((box[0] + px(0.75), y), b, font=NUN(54), fill=NAVY)
        y += px(0.27)

    # sample page thumbnails
    with tempfile.TemporaryDirectory() as tmp:
        thumbs = []
        for n in (11, 33, 47):
            page = 7 + 2 * (n - 1)
            subprocess.run(["pdftoppm", "-r", "110", "-png", "-f", str(page), "-l", str(page),
                            "-singlefile", INTERIOR, f"{tmp}/t{n}"], check=True)
            thumbs.append(Image.open(f"{tmp}/t{n}.png").convert("RGB"))
    # fanned stack of sample pages on the right (clear of the pups at bottom-left)
    tw = px(1.72)
    th = round(tw * thumbs[0].height / thumbs[0].width)
    order = [(0, px(5.12), -8, px(5.38)), (2, px(7.38), 8, px(5.38)), (1, px(6.25), 0, px(5.25))]  # middle on top
    for k, tx, ang, top in order:
        t = thumbs[k].resize((tw, th), Image.LANCZOS)
        card = Image.new("RGBA", (tw + 26, th + 26), WHITE + (255,))
        card.paste(t, (13, 13))
        ImageDraw.Draw(card).rectangle((0, 0, card.width - 1, card.height - 1), outline=NAVY + (255,), width=5)
        card = card.rotate(ang, expand=True, resample=Image.BICUBIC)
        pos = (round(tx - card.width / 2), top)
        sh = Image.new("RGBA", img.size, (0, 0, 0, 0))
        mask = Image.new("L", img.size, 0)
        mask.paste(card.split()[3], (pos[0] + 12, pos[1] + 16))
        mask = mask.filter(ImageFilter.GaussianBlur(12)).point(lambda v: v * 100 // 255)
        sh.paste(Image.new("RGBA", img.size, NAVY + (255,)), (0, 0), mask)
        img.alpha_composite(sh)
        img.alpha_composite(card, pos)
    d = ImageDraw.Draw(img)
    outlined(d, (px(6.25), px(5.38) + th + px(0.42)), "Peek inside!", FRED(72), WHITE, NAVY, 6)

    # series tag (kept clear of the barcode box at bottom-right)
    sx0, sy0, sx1, sy1 = px(4.8), px(8.75), BACK_R - px(0.4), px(9.45)
    d.rounded_rectangle((sx0, sy0, sx1, sy1), radius=40, fill=PINK, outline=NAVY, width=7)
    outlined(d, ((sx0 + sx1) / 2, (sy0 + sy1) / 2 - px(0.08)), "COLLECT THE SERIES!", FRED(62), WHITE, NAVY, 5)
    outlined(d, ((sx0 + sx1) / 2, (sy0 + sy1) / 2 + px(0.15)), "Pudding & Pickle Coloring Adventures  •  Book 1",
             FRED6(42), WHITE, NAVY, 0)


def spine(img):
    d = ImageDraw.Draw(img)
    d.rectangle((BACK_R, 0, FRONT_L, H), fill=PINK)
    sw = FRONT_L - BACK_R
    text = "PUDDING & PICKLE'S  •  A YEAR OF ADVENTURES"
    size = 60
    f = LUCKY(size)
    # keep text within spine minus 0.0625in each side
    while f.getbbox("A")[3] - f.getbbox("A")[1] > sw - px(0.2) or f.getlength(text) > px(9.3):
        size -= 1
        f = LUCKY(size)
    layer = Image.new("RGBA", (round(f.getlength(text)) + 40, size * 2), (0, 0, 0, 0))
    ld = ImageDraw.Draw(layer)
    ld.text((layer.width / 2, layer.height / 2), text, font=f, fill=WHITE, anchor="mm",
            stroke_width=3, stroke_fill=NAVY)
    layer = layer.rotate(-90, expand=True)
    img.alpha_composite(layer, (round(BACK_R + sw / 2 - layer.width / 2), round(H / 2 - layer.height / 2)))
    return size


def main():
    img = Image.new("RGBA", (W, H), WHITE + (255,))
    back(img)
    front(img)
    s = spine(img)
    rgb = img.convert("RGB")
    full_png = os.path.join(OUT, "cover_full_preview.jpg")
    rgb.save(full_png, quality=90)
    front_only = rgb.crop((FRONT_L, px(BLEED), W - px(BLEED), H - px(BLEED)))
    front_only.save(os.path.join(OUT, "cover_front.jpg"), quality=94)

    tmp_jpg = os.path.join(OUT, "_cover_tmp.jpg")
    rgb.save(tmp_jpg, quality=96, dpi=(DPI, DPI))
    pdf = os.path.join(OUT, "cover.pdf")
    c = canvas.Canvas(pdf, pagesize=(COVER_W_IN * 72, COVER_H_IN * 72))
    c.setTitle("Pudding & Pickle's A Year of Adventures - Cover")
    c.drawImage(tmp_jpg, 0, 0, COVER_W_IN * 72, COVER_H_IN * 72)
    c.showPage()
    c.save()
    os.remove(tmp_jpg)
    print(f"interior pages={PAGES} spine={SPINE:.4f}in cover={COVER_W_IN:.4f}x{COVER_H_IN:.4f}in "
          f"({W}x{H}px) spine_font={s}px")


if __name__ == "__main__":
    main()
