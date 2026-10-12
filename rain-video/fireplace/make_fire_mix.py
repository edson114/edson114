"""Stretch a short fire-crackle recording into a long, non-repeating, seamless loop.

Short pieces (3–9 s) are taken from random places, given a slight random change in
pitch/size (±6%) and level, sometimes mirrored left/right, and joined with smooth
crossfades. Rumble below ~70 Hz is removed and loud pops are gently limited.

Usage: python3 make_fire_mix.py <fire.wav> <minutes> <out.wav> [seed]
"""
import subprocess, sys, wave
import numpy as np

SR = 48000

def load(path):
    raw = subprocess.run(["ffmpeg", "-v", "error", "-i", path, "-af", "highpass=f=70:poles=2,highpass=f=70:poles=2",
                          "-ac", "2", "-ar", str(SR), "-f", "f32le", "-"], capture_output=True, check=True).stdout
    return np.frombuffer(raw, dtype=np.float32).reshape(-1, 2).copy()

def resample(x, factor):
    n = int(len(x) / factor)
    pos = np.arange(n) * factor
    i = np.minimum(pos.astype(int), len(x) - 2)
    frac = (pos - i)[:, None]
    return x[i] * (1 - frac) + x[i + 1] * frac

def main():
    src_path, minutes, out = sys.argv[1], float(sys.argv[2]), sys.argv[3]
    rng = np.random.default_rng(int(sys.argv[4]) if len(sys.argv) > 4 else 21)
    src = load(src_path)[int(1.0 * SR):-int(0.3 * SR)]   # skip the very start/end
    total = int(minutes * 60 * SR)
    xf = int(0.7 * SR)
    fi = np.sqrt(np.sin(np.linspace(0, np.pi / 2, xf)) ** 2)[:, None]
    fo = np.sqrt(1 - fi ** 2)
    out_buf = np.zeros((total + 12 * SR, 2), dtype=np.float32)
    pos, last = 0, -99
    while pos < total:
        length = int(rng.uniform(3, 9) * SR)
        start = int(rng.integers(0, len(src) - length))
        if abs(start - last) < 2 * SR:                   # avoid back-to-back same spot
            continue
        last = start
        piece = resample(src[start:start + length], rng.uniform(0.94, 1.06))
        piece *= 10 ** (rng.uniform(-1.5, 1.5) / 20)
        if rng.random() < 0.5:
            piece = piece[:, ::-1]
        if pos == 0:
            out_buf[:len(piece)] = piece
            pos = len(piece)
        else:
            p = pos - xf
            out_buf[p:p + xf] = out_buf[p:p + xf] * fo + piece[:xf] * fi
            out_buf[p + xf:p + len(piece)] = piece[xf:]
            pos = p + len(piece)
    mix = out_buf[:total + 3 * SR]
    # seamless wrap: crossfade the tail into the head
    w = int(3 * SR)
    wi = np.sqrt(np.linspace(0, 1, w))[:, None]
    mix[:w] = mix[:w] * wi + mix[total:total + w] * np.sqrt(1 - wi ** 2)
    mix = mix[:total]
    # fixed gain to a calm loudness, then gently limit loud pops (no time-varying gain, so the loop stays level)
    def lufs_of(x):
        m = subprocess.run(["ffmpeg", "-hide_banner", "-f", "f32le", "-ar", str(SR), "-ac", "2", "-i", "-",
                            "-af", "ebur128", "-f", "null", "-"], input=x.astype(np.float32).tobytes(), capture_output=True)
        return float([l for l in m.stderr.decode().splitlines() if l.strip().startswith("I:")][-1].split()[1])
    # a few gentle rounds: raise to the target loudness, then soften only the sharpest pops
    for _ in range(4):
        lufs = lufs_of(mix)
        mix = mix * 10 ** ((-26.0 - lufs) / 20)
        lim = subprocess.run(["ffmpeg", "-v", "error", "-f", "f32le", "-ar", str(SR), "-ac", "2", "-i", "-",
                              "-af", "alimiter=limit=0.7:attack=1:release=50:level=false", "-f", "f32le", "-"],
                             input=mix.astype(np.float32).tobytes(), capture_output=True, check=True).stdout
        mix = np.frombuffer(lim, dtype=np.float32).reshape(-1, 2).copy()[:total]
    lufs = lufs_of(mix)
    pcm = (np.clip(mix, -1, 1) * 32767).astype(np.int16)
    with wave.open(out, "wb") as wv:
        wv.setnchannels(2); wv.setsampwidth(2); wv.setframerate(SR)
        wv.writeframes(pcm.tobytes())
    print(f"wrote {out}: {total / SR / 60:.1f} min, peak {20 * np.log10(np.abs(mix).max()):.1f} dBFS, from {lufs:.1f} LUFS")

if __name__ == "__main__":
    main()
