"""Build the KDP interior PDF: 8.5 x 11 in, no bleed, black & white, single-sided coloring pages.

usage: python3 build_interior.py <clean_art_dir> <fonts_dir> <out.pdf>
"""
import math
import sys
from datetime import date

from PIL import Image
from reportlab.lib.utils import ImageReader
from reportlab.pdfbase import pdfmetrics
from reportlab.pdfbase.ttfonts import TTFont
from reportlab.pdfgen import canvas

from pages import PAGES

ART, FONTS, OUT = sys.argv[1], sys.argv[2], sys.argv[3]

AUTHOR = "Edson Aguilar Salazar"
YEAR = date.today().year

IN = 72.0
PW, PH = 8.5 * IN, 11 * IN
SIDE = 0.65 * IN
TOP = 0.5 * IN
ART_W = PW - 2 * SIDE

for name, f in [("Fredoka", "Fredoka-600.ttf"), ("FredokaBold", "Fredoka-700.ttf"),
                ("Lucky", "LuckiestGuy.ttf"), ("Nunito", "Nunito-600.ttf"),
                ("NunitoBold", "Nunito-800.ttf")]:
    pdfmetrics.registerFont(TTFont(name, f"{FONTS}/{f}"))


def art(name):
    return Image.open(f"{ART}/{name}.png").convert("L")


# ---------- vector helpers ----------

def star_path(c, cx, cy, r, points=5, inner=0.45):
    p = c.beginPath()
    for i in range(points * 2):
        rad = r if i % 2 == 0 else r * inner
        a = math.pi / 2 + i * math.pi / points
        x, y = cx + rad * math.cos(a), cy + rad * math.sin(a)
        (p.moveTo if i == 0 else p.lineTo)(x, y)
    p.close()
    return p


def star(c, cx, cy, r, lw=1.6, fill=0):
    c.setLineWidth(lw)
    c.setLineJoin(1)
    c.drawPath(star_path(c, cx, cy, r), stroke=1, fill=fill)


def heart(c, cx, cy, s, lw=1.8):
    p = c.beginPath()
    p.moveTo(cx, cy - s * 0.9)
    p.curveTo(cx - s * 1.25, cy - s * 0.05, cx - s * 0.75, cy + s * 0.95, cx, cy + s * 0.4)
    p.curveTo(cx + s * 0.75, cy + s * 0.95, cx + s * 1.25, cy - s * 0.05, cx, cy - s * 0.9)
    p.close()
    c.setLineWidth(lw)
    c.drawPath(p, stroke=1, fill=0)


def paw(c, cx, cy, s, lw=1.8, fill=0):
    c.setLineWidth(lw)
    c.ellipse(cx - s * 0.55, cy - s * 0.55, cx + s * 0.55, cy + s * 0.25, stroke=1, fill=fill)
    for dx, dy in [(-0.62, 0.42), (-0.22, 0.72), (0.22, 0.72), (0.62, 0.42)]:
        r = s * 0.2
        c.ellipse(cx + dx * s - r, cy + dy * s - r * 1.2, cx + dx * s + r, cy + dy * s + r * 1.2, stroke=1, fill=fill)


def bone(c, cx, cy, s, lw=1.8):
    # shaft with two round knobs at each end, traced as one outline
    L, r = s * 0.5, s * 0.22
    h, d = r * 0.6, r * 0.85
    j = math.degrees(math.asin((d - h) / r))      # where shaft meets a knob
    k = math.degrees(math.asin(d / r))            # where two knobs meet
    sweep = -((180 + j) + k)                      # clockwise around the outside of each knob
    p = c.beginPath()
    p.moveTo(cx - L + r * math.cos(math.radians(-j)), cy + h)
    p.lineTo(cx + L - r * math.cos(math.radians(j)), cy + h)
    p.arcTo(cx + L - r, cy + d - r, cx + L + r, cy + d + r, startAng=180 + j, extent=sweep)
    p.arcTo(cx + L - r, cy - d - r, cx + L + r, cy - d + r, startAng=k, extent=sweep)
    p.lineTo(cx - L + r * math.cos(math.radians(j)), cy - h)
    p.arcTo(cx - L - r, cy - d - r, cx - L + r, cy - d + r, startAng=j, extent=sweep)
    p.arcTo(cx - L - r, cy + d - r, cx - L + r, cy + d + r, startAng=180 + k, extent=sweep)
    p.close()
    c.setLineWidth(lw)
    c.setLineJoin(1)
    c.drawPath(p, stroke=1, fill=0)


def outline_text(c, txt, x, y, font, size, lw=1.4, align="center", fill=False):
    """Hollow lettering so kids can color the letters too."""
    w = pdfmetrics.stringWidth(txt, font, size)
    if align == "center":
        x -= w / 2
    t = c.beginText(x, y)
    t.setFont(font, size)
    t.setTextRenderMode(2 if fill else 1)
    c.setLineWidth(lw)
    c.setLineJoin(1)
    t.textOut(txt)
    t.setTextRenderMode(0)  # render mode persists in PDF text state; reset it
    c.drawText(t)


def fit_size(txt, font, size, max_w):
    w = pdfmetrics.stringWidth(txt, font, size)
    return size if w <= max_w else size * max_w / w


def text_c(c, txt, y, font, size, max_w=ART_W):
    size = fit_size(txt, font, size, max_w)
    c.setFont(font, size)
    c.drawCentredString(PW / 2, y, txt)


def banner(c, cx, cy, w, h, lw=2):
    """Ribbon banner with folded tails (outline only)."""
    c.setLineWidth(lw)
    c.setLineJoin(1)
    tail = h * 0.9
    for sgn in (-1, 1):
        x0 = cx + sgn * (w / 2 - h * 0.25)
        x1 = cx + sgn * (w / 2 + tail)
        p = c.beginPath()
        p.moveTo(x0, cy - h / 2 - h * 0.25)
        p.lineTo(x1, cy - h / 2 - h * 0.25)
        p.lineTo(x1 - sgn * h * 0.4, cy - h * 0.25)
        p.lineTo(x1, cy + h / 2 - h * 0.25)
        p.lineTo(x0, cy + h / 2 - h * 0.25)
        p.close()
        c.drawPath(p, stroke=1, fill=0)
    c.setFillGray(1)
    c.rect(cx - w / 2, cy - h / 2, w, h, stroke=1, fill=1)
    c.setFillGray(0)


def framed_art(c, img, x, y, w, h, radius=18, lw=2.6):
    c.saveState()
    p = c.beginPath()
    p.roundRect(x, y, w, h, radius)
    c.clipPath(p, stroke=0, fill=0)
    c.drawImage(ImageReader(img), x, y, w, h)
    c.restoreState()
    c.setLineWidth(lw)
    c.roundRect(x, y, w, h, radius, stroke=1, fill=0)


# ---------- pages ----------

def blank_back(c):
    c.setFont("Nunito", 8)
    c.drawCentredString(PW / 2, 0.45 * IN,
                        "This side is blank on purpose, so markers won't bleed through to the next picture.")
    c.showPage()


def title_page(c):
    for i, x in enumerate([1.0, 2.6, 4.25, 5.9, 7.5]):
        paw(c, x * IN, 10.45 * IN, 11 + (i % 2) * 3)
    outline_text(c, "PUDDING &", PW / 2, 8.85 * IN, "Lucky", 76, lw=2.2)
    outline_text(c, "PICKLE'S", PW / 2, 7.75 * IN, "Lucky", 76, lw=2.2)
    banner(c, PW / 2, 7.05 * IN, 5.2 * IN, 0.62 * IN)
    text_c(c, "A YEAR OF ADVENTURES", 6.95 * IN, "FredokaBold", 26, 4.9 * IN)
    outline_text(c, "Coloring Book", PW / 2, 5.95 * IN, "FredokaBold", 44, lw=1.8)
    for x, y, r in [(1.0, 9.6, 16), (7.5, 9.6, 16), (0.95, 6.2, 12), (7.55, 6.2, 12), (1.6, 7.6, 9), (6.9, 7.6, 9)]:
        star(c, x * IN, y * IN, r)

    # "belongs to" box
    bx, by, bw, bh = 1.15 * IN, 2.0 * IN, 6.2 * IN, 2.7 * IN
    c.setLineWidth(2.4)
    c.roundRect(bx, by, bw, bh, 24)
    c.setDash(5, 4)
    c.setLineWidth(1.2)
    c.roundRect(bx + 9, by + 9, bw - 18, bh - 18, 18)
    c.setDash()
    text_c(c, "This book belongs to:", by + bh - 0.75 * IN, "FredokaBold", 26)
    c.setLineWidth(1.6)
    c.line(bx + 0.6 * IN, by + 0.85 * IN, bx + bw - 0.6 * IN, by + 0.85 * IN)
    for x, y in [(bx + 0.45 * IN, by + bh - 0.45 * IN), (bx + bw - 0.45 * IN, by + bh - 0.45 * IN)]:
        paw(c, x, y, 13)
    heart(c, bx + 0.45 * IN, by + 0.45 * IN, 9)
    heart(c, bx + bw - 0.45 * IN, by + 0.45 * IN, 9)
    text_c(c, "60 adventures  •  12 months  •  1 whole year of fun!", 1.2 * IN, "Fredoka", 15)
    c.showPage()


def copyright_page(c):
    lines = [
        ("NunitoBold", 11, "Pudding & Pickle's A Year of Adventures: Coloring Book"),
        ("Nunito", 9.5, "Pudding & Pickle Coloring Adventures • Book 1"),
        ("Nunito", 9.5, ""),
        ("Nunito", 9.5, f"Copyright © {YEAR} {AUTHOR}. All rights reserved."),
        ("Nunito", 9.5, "Pudding & Pickle and all characters in this book are original creations."),
        ("Nunito", 9.5, "No part of this book may be reproduced, stored or shared in any form"),
        ("Nunito", 9.5, "without written permission from the publisher, except that the buyer"),
        ("Nunito", 9.5, "may copy pages for their own family's personal, non-commercial coloring."),
        ("Nunito", 9.5, ""),
        ("NunitoBold", 9.5, "Coloring tips for grown-ups"),
        ("Nunito", 9.5, "• Each picture is printed on its own page, so slip a sheet of paper"),
        ("Nunito", 9.5, "   behind the page when using markers."),
        ("Nunito", 9.5, "• Every page has a story line to read aloud and a fun color challenge."),
        ("Nunito", 9.5, "• Pages may be carefully cut out and framed or hung on the fridge!"),
    ]
    y = 4.4 * IN
    for font, size, txt in lines:
        c.setFont(font, size)
        c.drawString(1.0 * IN, y, txt)
        y -= size * 1.55
    c.showPage()


def meet_page(c):
    outline_text(c, "MEET PUDDING & PICKLE!", PW / 2, 10.0 * IN, "Lucky", 40, lw=1.8)
    img = art("pchars")
    w = ART_W
    h = w * img.height / img.width
    framed_art(c, img, SIDE, 4.0 * IN, w, h)
    col = [(SIDE + 0.15 * IN, "PUDDING", ["The brave big brother.", "Loves: kites, cookies and", "big adventures.",
                                          "Always wears: his lucky", "star bandana."]),
           (PW / 2 + 0.25 * IN, "PICKLE", ["The giggly little sister.", "Loves: flowers, bubbles and", "big hugs.",
                                           "Always wears: her favorite", "polka-dot bow."])]
    for x, name, lines in col:
        outline_text(c, name, x, 3.35 * IN, "Lucky", 30, lw=1.5, align="left")
        y = 2.9 * IN
        for ln in lines:
            c.setFont("Fredoka", 14)
            c.drawString(x, y, ln)
            y -= 20
    text_c(c, "Color them in! Corgis can be orange, brown, cream... or any color you like!", 0.75 * IN, "Nunito", 11)
    c.showPage()


def test_colors_page(c):
    outline_text(c, "TEST YOUR COLORS!", PW / 2, 10.0 * IN, "Lucky", 46, lw=2)
    text_c(c, "Try your crayons, markers and pencils here before you start.", 9.5 * IN, "Fredoka", 15)
    shapes = ["circle", "heart", "star", "paw", "bone"]
    cols, rows = 4, 5
    cw, rh = ART_W / cols, 1.62 * IN
    top = 8.75 * IN
    for r in range(rows):
        for k in range(cols):
            cx = SIDE + cw * (k + 0.5)
            cy = top - rh * (r + 0.5)
            s = shapes[(r + k) % len(shapes)]
            if s == "circle":
                c.setLineWidth(1.8)
                c.circle(cx, cy, 0.5 * IN)
            elif s == "heart":
                heart(c, cx, cy + 4, 0.55 * IN)
            elif s == "star":
                star(c, cx, cy, 0.62 * IN, lw=1.8)
            elif s == "paw":
                paw(c, cx, cy - 4, 0.62 * IN)
            else:
                bone(c, cx, cy, 0.9 * IN)
    c.showPage()


def coloring_page(c, idx):
    month, story, challenge = PAGES[idx]
    img = art(f"p{idx + 1:02d}")
    h = ART_W * img.height / img.width
    y = PH - TOP - h
    framed_art(c, img, SIDE, y, ART_W, h)

    # caption block
    my = y - 0.43 * IN
    outline_text(c, month.upper(), PW / 2, my, "FredokaBold", 22, lw=1.1)
    mw = pdfmetrics.stringWidth(month.upper(), "FredokaBold", 22)
    star(c, PW / 2 - mw / 2 - 16, my + 7, 8, lw=1.1)
    star(c, PW / 2 + mw / 2 + 16, my + 7, 8, lw=1.1)

    c.setFont("Nunito", 9)
    c.drawString(SIDE + 2, my + 3, f"Adventure {idx + 1} of 60")
    c.setLineWidth(1.2)
    c.roundRect(PW - SIDE - 13, my, 12, 12, 2)
    c.drawRightString(PW - SIDE - 18, my + 3, "I colored it!")

    text_c(c, story, my - 0.3 * IN, "Fredoka", 15)
    ch = f"Color challenge: {challenge}"
    lab = "Color challenge: "
    size = fit_size(ch, "NunitoBold", 10.5, ART_W)
    lw_ = pdfmetrics.stringWidth(lab, "NunitoBold", size)
    x0 = PW / 2 - (lw_ + pdfmetrics.stringWidth(challenge, "Nunito", size)) / 2
    c.setFont("NunitoBold", size)
    c.drawString(x0, my - 0.55 * IN, lab)
    c.setFont("Nunito", size)
    c.drawString(x0 + lw_, my - 0.55 * IN, challenge)
    c.showPage()


def draw_yourself_page(c):
    img = art("pdy")
    h = ART_W * img.height / img.width
    y = PH - TOP - h
    framed_art(c, img, SIDE, y, ART_W, h)
    my = y - 0.43 * IN
    outline_text(c, "BONUS PAGE", PW / 2, my, "FredokaBold", 22, lw=1.1)
    text_c(c, "Draw yourself in the frame and join Pudding & Pickle!", my - 0.3 * IN, "Fredoka", 15)
    text_c(c, "What adventure will the three of you go on next?", my - 0.55 * IN, "Nunito", 10.5)
    c.showPage()


def certificate_page(c):
    c.setLineWidth(3)
    c.roundRect(0.6 * IN, 0.6 * IN, PW - 1.2 * IN, PH - 1.2 * IN, 26)
    c.setLineWidth(1.2)
    c.roundRect(0.75 * IN, 0.75 * IN, PW - 1.5 * IN, PH - 1.5 * IN, 20)
    for x, y in [(1.15, 9.85), (7.35, 9.85), (1.15, 1.15), (7.35, 1.15)]:
        star(c, x * IN, y * IN, 17, lw=1.6)
    outline_text(c, "SUPER COLORER", PW / 2, 9.0 * IN, "Lucky", 54, lw=2)
    banner(c, PW / 2, 8.25 * IN, 3.6 * IN, 0.55 * IN)
    text_c(c, "AWARD", 8.15 * IN, "FredokaBold", 26, 3.3 * IN)
    text_c(c, "This certificate is proudly presented to", 7.35 * IN, "Fredoka", 17)
    c.setLineWidth(1.6)
    c.line(1.6 * IN, 6.5 * IN, PW - 1.6 * IN, 6.5 * IN)
    text_c(c, "for coloring a whole year of adventures", 5.95 * IN, "Fredoka", 17)
    text_c(c, "with Pudding & Pickle!", 5.65 * IN, "Fredoka", 17)
    img = art("pchars")
    w = 3.9 * IN
    h = w * img.height / img.width
    c.drawImage(ImageReader(img), PW / 2 - w / 2, 2.35 * IN, w, h)
    c.setFont("Fredoka", 14)
    c.drawString(1.3 * IN, 1.65 * IN, "Date:")
    c.line(1.85 * IN, 1.62 * IN, 3.7 * IN, 1.62 * IN)
    c.drawString(4.55 * IN, 1.65 * IN, "Signed:")
    c.line(5.3 * IN, 1.62 * IN, 7.2 * IN, 1.62 * IN)
    c.showPage()


def goodbye_page(c):
    outline_text(c, "SEE YOU NEXT YEAR!", PW / 2, 9.85 * IN, "Lucky", 46, lw=2)
    text_c(c, "Pudding & Pickle had the best year ever with you!", 9.35 * IN, "Fredoka", 16)
    text_c(c, "More Pudding & Pickle adventures are on the way.", 9.07 * IN, "Fredoka", 16)

    # drawing box
    bx, by, bw, bh = SIDE, 3.35 * IN, ART_W, 5.3 * IN
    c.setLineWidth(2.4)
    c.roundRect(bx, by, bw, bh, 20)
    c.setFont("FredokaBold", 17)
    c.drawString(bx + 0.3 * IN, by + bh - 0.45 * IN, "Draw your favorite adventure from this book!")
    for k, x in enumerate([bx + bw - 0.9 * IN, bx + bw - 0.45 * IN]):
        paw(c, x, by + bh - 0.36 * IN + (k % 2) * 6, 10)
    c.setLineWidth(1.4)
    c.line(bx + 0.3 * IN, by + 0.5 * IN, bx + bw - 0.3 * IN, by + 0.5 * IN)
    c.setFont("Nunito", 11)
    c.drawString(bx + 0.3 * IN, by + 0.62 * IN, "My favorite adventure was...")

    # note for grown-ups
    nx, ny, nw, nh = 1.0 * IN, 0.7 * IN, PW - 2.0 * IN, 2.15 * IN
    c.setLineWidth(1.8)
    c.roundRect(nx, ny, nw, nh, 18)
    heart(c, nx + 0.4 * IN, ny + nh - 0.42 * IN, 9)
    heart(c, nx + nw - 0.4 * IN, ny + nh - 0.42 * IN, 9)
    text_c(c, "A note for grown-ups", ny + nh - 0.5 * IN, "FredokaBold", 18)
    for k, ln in enumerate([
        "Thank you for choosing Pudding & Pickle! We are a small, independent",
        "creator, and every picture in this book was made with little artists in mind.",
        "If your child enjoyed coloring this year of adventures, we'd be so grateful",
        "if you shared your thoughts in a review. It helps other families find us!",
    ]):
        text_c(c, ln, ny + nh - 0.9 * IN - k * 16, "Nunito", 11, nw - 0.4 * IN)
    c.showPage()


def build():
    c = canvas.Canvas(OUT, pagesize=(PW, PH), initialFontName="Nunito", initialFontSize=10)
    c.setTitle("Pudding & Pickle's A Year of Adventures: Coloring Book")
    c.setAuthor(AUTHOR)
    title_page(c)
    copyright_page(c)
    meet_page(c)
    blank_back(c)
    test_colors_page(c)
    blank_back(c)
    for i in range(60):
        coloring_page(c, i)
        blank_back(c)
    draw_yourself_page(c)
    blank_back(c)
    certificate_page(c)
    blank_back(c)
    goodbye_page(c)
    blank_back(c)
    c.save()


if __name__ == "__main__":
    build()
