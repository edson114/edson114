"""Generate a soothing parent-style "shhh... shhh..." over a white, pink or brown noise bed.

Each shush is breath-like: soft attack, steady middle, gentle tail, then a short pause,
with small random changes in timing, loudness and tone so it never sounds mechanical.

Usage: python3 make_shush.py <white|pink|brown> <seconds> <out.wav> [seed]
"""
import subprocess, sys
import numpy as np

SR = 48000

def shaped_noise(n, rng, kind):
    """Stereo noise with a given spectral tilt, slightly decorrelated between ears."""
    out = np.empty((n, 2), dtype=np.float32)
    f = np.fft.rfftfreq(n, 1 / SR)
    f[0] = f[1]
    tilt = {"white": np.ones_like(f), "pink": 1 / np.sqrt(f), "brown": 1 / f}[kind]
    tilt = tilt * (f > 25)                         # no sub-bass rumble
    if kind == "white":
        tilt = tilt / (1 + (f / 9000) ** 4)        # soften the harshest hiss
    for ch in range(2):
        spec = np.fft.rfft(rng.standard_normal(n)) * tilt
        x = np.fft.irfft(spec, n)
        out[:, ch] = x / np.sqrt(np.mean(x ** 2))
    return out

def shush_spectrum(f, center):
    """Fricative 'sh' shape: broad peak near `center`, rolling off both sides."""
    lo = 1 / (1 + (center * 0.55 / np.maximum(f, 1)) ** 4)
    hi = 1 / (1 + (f / (center * 2.2)) ** 3)
    peak = np.exp(-0.5 * (np.log(np.maximum(f, 1) / center) / 0.35) ** 2)
    return lo * hi * (0.35 + peak)

def make_shush_track(n, rng):
    track = np.zeros((n, 2), dtype=np.float32)
    t = int(rng.uniform(0.2, 0.6) * SR)
    while t < n:
        dur = rng.uniform(1.25, 1.75)               # length of one "shhh"
        gap = rng.uniform(0.45, 0.8)                # breath between
        m = int(dur * SR)
        center = rng.uniform(2600, 3300)            # tone varies a little each time
        gain = 10 ** (rng.uniform(-1.2, 1.2) / 20)
        seg_len = 1 << int(np.ceil(np.log2(m)))
        f = np.fft.rfftfreq(seg_len, 1 / SR)
        shape = shush_spectrum(f, center)
        env_t = np.linspace(0, 1, m)
        attack, release = 0.12, 0.35
        env = np.minimum(1, env_t / attack) * np.minimum(1, (1 - env_t) / release)
        env = np.sin(np.clip(env, 0, 1) * np.pi / 2) ** 1.5
        env *= 1 - 0.12 * env_t                     # breath fades slightly as it runs out
        for ch in range(2):
            x = np.fft.irfft(np.fft.rfft(rng.standard_normal(seg_len)) * shape, seg_len)[:m]
            x /= np.sqrt(np.mean(x ** 2)) + 1e-9
            end = min(n, t + m)
            track[t:end, ch] += (x * env * gain)[: end - t]
        t += int((dur + gap) * SR)
    # gentle stereo: mostly centred, like someone right beside you
    mid = track.mean(axis=1, keepdims=True)
    return 0.8 * mid + 0.2 * track

def main():
    kind, secs, out = sys.argv[1], float(sys.argv[2]), sys.argv[3]
    rng = np.random.default_rng(int(sys.argv[4]) if len(sys.argv) > 4 else 11)
    n = int(secs * SR)
    bed = shaped_noise(n, rng, kind)
    shush = make_shush_track(n, rng)
    mix = 0.55 * bed + 1.0 * shush                  # shush sits clearly above the bed
    proc = subprocess.run(
        ["ffmpeg", "-v", "error", "-y", "-f", "f32le", "-ar", str(SR), "-ac", "2", "-i", "-",
         "-af", "loudnorm=I=-27:TP=-4:LRA=7", "-ar", str(SR), "-c:a", "pcm_s16le", out],
        input=(mix * 0.05).astype(np.float32).tobytes())
    proc.check_returncode()

if __name__ == "__main__":
    main()
