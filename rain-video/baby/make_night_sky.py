"""Render a dark, nursery-friendly night sky loop: twinkling stars and a softly glowing moon.

Every star twinkles a whole number of times per loop and the moon glow 'breathes' once,
so the clip repeats seamlessly. Kept very dark so it won't light up a baby's room.

Usage: python3 make_night_sky.py <out.mp4> [seconds=20] [fps=15]
"""
import subprocess, sys
import numpy as np

W, H = 1920, 1080

def main():
    out = sys.argv[1]
    secs = float(sys.argv[2]) if len(sys.argv) > 2 else 20
    fps = int(sys.argv[3]) if len(sys.argv) > 3 else 15
    frames = int(secs * fps)
    rng = np.random.default_rng(3)
    yy, xx = np.mgrid[0:H, 0:W].astype(np.float32)

    # deep navy gradient with fine dither so it doesn't band
    top, bottom = np.array([4, 6, 16]), np.array([9, 13, 28])
    sky = top + (bottom - top) * (yy / H)[..., None]
    sky += rng.uniform(-0.6, 0.6, (H, W, 1))

    # crescent moon, upper right
    mx, my, r = 1460, 290, 78
    d = np.hypot(xx - mx, yy - my)
    d2 = np.hypot(xx - (mx + 34), yy - (my - 22))
    disc = np.clip(r - d + 1, 0, 1) * np.clip(d2 - r * 0.92 + 1, 0, 1)
    moon_col = np.array([226, 214, 182]) * 0.62
    glow = np.exp(-(d / 230) ** 2) * 0.55 + np.exp(-(d / 520) ** 2) * 0.25

    # stars: avoid the moon's glow
    n = 230
    sx = rng.uniform(0, W, n); sy = rng.uniform(0, H * 0.92, n)
    keep = np.hypot(sx - mx, sy - my) > 220
    sx, sy = sx[keep], sy[keep]
    base = rng.uniform(0.12, 0.55, len(sx)) ** 1.4
    size = rng.uniform(0.7, 1.8, len(sx))
    cycles = rng.integers(1, 4, len(sx))
    phase = rng.uniform(0, 2 * np.pi, len(sx))
    depth = rng.uniform(0.25, 0.6, len(sx))
    tint = np.where(rng.random(len(sx)) < 0.2, 1, 0)

    proc = subprocess.Popen(
        ["ffmpeg", "-v", "error", "-y", "-f", "rawvideo", "-pix_fmt", "rgb24", "-s", f"{W}x{H}",
         "-r", str(fps), "-i", "-", "-c:v", "libx264", "-preset", "slow", "-crf", "20",
         "-tune", "stillimage", "-g", str(frames), "-pix_fmt", "yuv420p", out],
        stdin=subprocess.PIPE)
    for f in range(frames):
        t = f / frames
        img = sky.copy()
        breathe = 1 + 0.06 * np.sin(2 * np.pi * t)
        img += (glow * breathe)[..., None] * np.array([40, 42, 52])
        img = img * (1 - disc[..., None]) + moon_col * disc[..., None]
        bright = base * (1 - depth + depth * (0.5 + 0.5 * np.sin(2 * np.pi * cycles * t + phase)))
        for x, y, b, s, tt in zip(sx, sy, bright, size, tint):
            x0, x1 = int(max(0, x - 5)), int(min(W, x + 6))
            y0, y1 = int(max(0, y - 5)), int(min(H, y + 6))
            g = np.exp(-((xx[y0:y1, x0:x1] - x) ** 2 + (yy[y0:y1, x0:x1] - y) ** 2) / (2 * (s * 0.8) ** 2))
            col = np.array([255, 236, 200]) if tt else np.array([215, 225, 255])
            img[y0:y1, x0:x1] += (g * b)[..., None] * col
        proc.stdin.write(np.clip(img, 0, 255).astype(np.uint8).tobytes())
    proc.stdin.close()
    proc.wait()

if __name__ == "__main__":
    main()
