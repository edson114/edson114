"""Generate a mother-like "SHHhhh... SHHhhh..." over a white, pink or brown noise bed.

Phrasing follows how a parent actually shushes: a few short shushes, one long one,
then a soft breath in. Each shush starts with a gentle accent and fades as the breath
runs out, the tone drops slightly as the lips relax, and there is a faint tremble and
warm breath under the hiss. A small, soft room echo places the voice beside the crib.

Usage: python3 make_shush.py <white|pink|brown> <seconds> <out.wav> [seed]
"""
import subprocess, sys
import numpy as np

SR = 48000

def shaped_noise(n, rng, kind):
    """Stereo noise bed with a spectral tilt, slightly decorrelated between ears."""
    out = np.empty((n, 2), dtype=np.float32)
    f = np.fft.rfftfreq(n, 1 / SR)
    f[0] = f[1]
    tilt = {"white": np.ones_like(f), "pink": 1 / np.sqrt(f), "brown": 1 / f}[kind]
    tilt = tilt * (f > 25)
    if kind == "white":
        tilt = tilt / (1 + (f / 9000) ** 4)
    for ch in range(2):
        x = np.fft.irfft(np.fft.rfft(rng.standard_normal(n)) * tilt, n)
        out[:, ch] = x / np.sqrt(np.mean(x ** 2))
    return out

def band(f, lo, hi, order=3):
    return 1 / (1 + (lo / np.maximum(f, 1)) ** (2 * order)) / (1 + (f / hi) ** (2 * order))

def fricative(m, rng, center_start, center_end):
    """Noise whose 'sh' peak glides from center_start to center_end (lips relaxing)."""
    blk = 2048
    hop = blk // 2
    win = np.hanning(blk)
    out = np.zeros(m + blk)
    f = np.fft.rfftfreq(blk, 1 / SR)
    nblk = max(1, (m + hop - 1) // hop)
    for i in range(nblk):
        c = center_start + (center_end - center_start) * i / max(1, nblk - 1)
        peak = np.exp(-0.5 * (np.log(np.maximum(f, 1) / c) / 0.32) ** 2)
        shape = band(f, c * 0.5, c * 2.4) * (0.3 + peak)
        shape += 0.06 * band(f, 300, 900, 2)        # warm breath under the hiss
        seg = np.fft.irfft(np.fft.rfft(rng.standard_normal(blk)) * shape, blk) * win
        out[i * hop:i * hop + blk] += seg
    out = out[:m]
    return out / (np.sqrt(np.mean(out ** 2)) + 1e-9)

def envelope(m, rng, accent):
    t = np.linspace(0, 1, m)
    dur = m / SR
    a = min(0.18, 0.25 * dur) / dur                 # quick, soft onset
    rise = np.sin(np.clip(t / a, 0, 1) * np.pi / 2)
    decay = np.exp(-t * rng.uniform(0.9, 1.6))      # breath running out
    tail = np.sin(np.clip((1 - t) / 0.22, 0, 1) * np.pi / 2)
    bump = 1 + accent * np.exp(-((t - a * 1.3) / 0.12) ** 2)   # the "SH" accent
    tremble = 1 + 0.03 * np.sin(2 * np.pi * rng.uniform(5, 7) * t * dur + rng.uniform(0, 6))
    return rise * decay * tail * bump * tremble

def inhale(m, rng):
    f = np.fft.rfftfreq(m, 1 / SR)
    x = np.fft.irfft(np.fft.rfft(rng.standard_normal(m)) * band(f, 600, 2200, 2), m)
    x /= np.sqrt(np.mean(x ** 2)) + 1e-9
    t = np.linspace(0, 1, m)
    return x * np.sin(np.pi * t) ** 2

def make_voice(n, rng):
    v = np.zeros(n + 5 * SR)
    t = int(0.4 * SR)
    while t < n:
        phrase = rng.integers(3, 6)                 # 3–5 short shushes...
        for k in range(phrase + 1):
            last = k == phrase                      # ...then one long one
            dur = rng.uniform(1.9, 2.6) if last else rng.uniform(0.75, 1.15)
            gap = rng.uniform(0.18, 0.32)
            m = int(dur * SR)
            c0 = rng.uniform(3500, 4100)
            sh = fricative(m, rng, c0, c0 * rng.uniform(0.82, 0.9))
            g = 10 ** (rng.uniform(-1.0, 1.0) / 20) * (0.92 if last else 1.0)
            v[t:t + m] += sh * envelope(m, rng, accent=rng.uniform(0.25, 0.45)) * g
            t += m + int(gap * SR)
        im = int(rng.uniform(0.55, 0.8) * SR)       # soft breath in
        v[t:t + im] += inhale(im, rng) * 0.10
        t += im + int(rng.uniform(0.15, 0.3) * SR)
    v = v[:n]
    # small soft nursery: short diffuse echo, a touch different in each ear
    ir_len = int(0.28 * SR)
    out = np.empty((n, 2), dtype=np.float32)
    for ch in range(2):
        ir = rng.standard_normal(ir_len) * np.exp(-np.linspace(0, 9, ir_len))
        ir[0] = 0
        ir *= 0.12 / np.sqrt(np.sum(ir ** 2))
        L = 1 << int(np.ceil(np.log2(n + ir_len)))
        wet = np.fft.irfft(np.fft.rfft(v, L) * np.fft.rfft(ir, L), L)[:n]
        out[:, ch] = v + wet
    return out

def main():
    kind, secs, out = sys.argv[1], float(sys.argv[2]), sys.argv[3]
    rng = np.random.default_rng(int(sys.argv[4]) if len(sys.argv) > 4 else 11)
    n = int(secs * SR)
    mix = 0.42 * shaped_noise(n, rng, kind) + make_voice(n, rng)
    proc = subprocess.run(
        ["ffmpeg", "-v", "error", "-y", "-f", "f32le", "-ar", str(SR), "-ac", "2", "-i", "-",
         "-af", "loudnorm=I=-27:TP=-4:LRA=7", "-ar", str(SR), "-c:a", "pcm_s16le", out],
        input=(mix * 0.05).astype(np.float32).tobytes())
    proc.check_returncode()

if __name__ == "__main__":
    main()
