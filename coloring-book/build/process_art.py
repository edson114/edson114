"""Clean AI line art into crisp 1-bit print images.

- Strips any stray colour fills (keeps black outlines only)
- Removes paper-texture / light-gray noise
- Upscales to print resolution and thresholds to pure black & white
"""
import sys, os, glob
import numpy as np
from PIL import Image, ImageFilter

SRC, DST = sys.argv[1], sys.argv[2]
TARGET_W = 3600  # ~500 dpi across a 7.2in art box
os.makedirs(DST, exist_ok=True)

def clean(path):
    im = np.asarray(Image.open(path).convert('RGB')).astype(np.float32)
    chroma = im.max(axis=2) - im.min(axis=2)
    gray = 0.299 * im[..., 0] + 0.587 * im[..., 1] + 0.114 * im[..., 2]
    gray[chroma > 45] = 255          # coloured fills -> white, outlines stay
    g = Image.fromarray(gray.clip(0, 255).astype(np.uint8), 'L')
    h = round(g.height * TARGET_W / g.width)
    g = g.resize((TARGET_W, h), Image.LANCZOS).filter(ImageFilter.GaussianBlur(1.1))
    bw = g.point(lambda v: 255 if v > 145 else 0).convert('1')
    # despeckle: drop isolated noise pixels
    bw = bw.convert('L').filter(ImageFilter.MedianFilter(3)).point(lambda v: 255 if v > 127 else 0).convert('1')
    return bw

for p in sorted(sys.argv[3:]) if len(sys.argv) > 3 else sorted(glob.glob(os.path.join(SRC, 'p*.jpg'))):
    name = os.path.splitext(os.path.basename(p))[0]
    if name in ('pcover', 'pback'):
        continue
    out = os.path.join(DST, name + '.png')
    clean(p).save(out, optimize=True)
    print(name, os.path.getsize(out) // 1024, 'KB')
