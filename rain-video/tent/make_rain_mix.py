"""Build a long, non-repeating tent-rain soundscape from a few real recordings.

Takes are cut into random chunks, shuffled, and joined with equal-power crossfades.
Intensity drifts slowly between light / medium / heavy takes like real rain.
A quiet forest-rain bed is layered underneath for depth.

Usage: python3 make_rain_mix.py <tent.mp3> <forest.mp3> <minutes> <out.wav> [seed]
"""
import subprocess, sys
import numpy as np

SR = 48000

def load(path, start=None, end=None):
    cmd = ["ffmpeg", "-v", "error"]
    if start is not None:
        cmd += ["-ss", str(start), "-to", str(end)]
    cmd += ["-i", path, "-ac", "2", "-ar", str(SR), "-f", "f32le", "-"]
    raw = subprocess.run(cmd, capture_output=True, check=True).stdout
    return np.frombuffer(raw, dtype=np.float32).reshape(-1, 2).copy()

def rms_db(x):
    return 20 * np.log10(np.sqrt(np.mean(x ** 2)) + 1e-12)

def main():
    tent, forest, minutes, out = sys.argv[1], sys.argv[2], float(sys.argv[3]), sys.argv[4]
    rng = np.random.default_rng(int(sys.argv[5]) if len(sys.argv) > 5 else 7)

    # The trp recording has three takes separated by short silences.
    takes = {"light": load(tent, 0.5, 62.7), "medium": load(tent, 67.5, 191.5), "heavy": load(tent, 196.8, 259.9)}
    # Narrow the loudness gap between takes so changes feel gentle, not jumpy.
    target = {"light": -29.0, "medium": -27.0, "heavy": -25.5}
    for k in takes:
        takes[k] *= 10 ** ((target[k] - rms_db(takes[k])) / 20)

    # Slow intensity drift: mostly stay, sometimes step to a neighbour.
    order = ["light", "medium", "heavy"]
    stay = {"light": 0.75, "medium": 0.8, "heavy": 0.7}

    total = int(minutes * 60 * SR)
    xf = int(4 * SR)
    fade_in = np.sin(np.linspace(0, np.pi / 2, xf))[:, None] ** 2
    fade_out = 1 - fade_in
    fade_in, fade_out = np.sqrt(fade_in), np.sqrt(fade_out)  # equal power

    outbuf = np.zeros((total + xf, 2), dtype=np.float32)
    pos, state, last = 0, "medium", None
    while pos < total:
        if rng.random() > stay[state]:
            i = order.index(state)
            state = order[min(2, max(0, i + rng.choice([-1, 1])))]
        src = takes[state]
        length = int(rng.uniform(18, 40) * SR)
        length = min(length, len(src) - 1)
        start = int(rng.integers(0, len(src) - length))
        if last == (state, start // SR):
            continue
        last = (state, start // SR)
        chunk = src[start:start + length].copy()
        if rng.random() < 0.5:
            chunk = chunk[:, ::-1]  # swap left/right for extra variety
        if pos == 0:
            outbuf[:length] = chunk
            pos = length
        else:
            p = pos - xf
            outbuf[p:p + xf] = outbuf[p:p + xf] * fade_out + chunk[:xf] * fade_in
            end = min(p + length, len(outbuf))
            outbuf[p + xf:end] = chunk[xf:end - p]
            pos = p + length
    mix = outbuf[:total]

    # Forest bed: quiet, low-passed slightly, chained with long crossfades and random offsets.
    bed_src = load(forest)
    bed_src = bed_src[int(3 * SR):-int(2 * SR)]  # drop the fade-in/out
    bed_src *= 10 ** ((-41.0 - rms_db(bed_src)) / 20)
    bed = np.zeros_like(mix)
    bxf = int(8 * SR)
    bfi = np.sqrt(np.sin(np.linspace(0, np.pi / 2, bxf))[:, None] ** 2)
    bfo = np.sqrt(1 - bfi ** 2)
    bpos = 0
    while bpos < total:
        off = int(rng.integers(0, len(bed_src) // 3))
        seg = bed_src[off:]
        if bpos == 0:
            n = min(len(seg), total)
            bed[:n] = seg[:n]
            bpos = n
        else:
            p = bpos - bxf
            bed[p:p + bxf] = bed[p:p + bxf] * bfo + seg[:bxf] * bfi
            n = min(len(seg), total - p)
            bed[p + bxf:p + n] = seg[bxf:n]
            bpos = p + n
    mix = mix + bed

    # Seamless wrap: crossfade the tail into the head so the file can loop.
    wrap = int(6 * SR)
    wi = np.sqrt(np.linspace(0, 1, wrap))[:, None]
    mix[:wrap] = mix[:wrap] * wi + mix[-wrap:] * np.sqrt(1 - wi ** 2)
    mix = mix[:-wrap]

    # Write 32-bit float, then tame the few very loud drop "plops" with a gentle
    # limiter and set a calm, consistent loudness for sleep listening.
    proc = subprocess.run(
        ["ffmpeg", "-v", "error", "-y", "-f", "f32le", "-ar", str(SR), "-ac", "2", "-i", "-",
         "-af", "alimiter=limit=0.35:attack=2:release=80:level=false,"
                "loudnorm=I=-24:TP=-3:LRA=7",
         "-ar", str(SR), "-c:a", "pcm_s16le", out],
        input=mix.astype(np.float32).tobytes())
    proc.check_returncode()
    print(f"wrote {out}: {len(mix)/SR/60:.1f} min")

if __name__ == "__main__":
    main()
