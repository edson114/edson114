"""Mix a playlist of jazz tracks over a seamless fire-crackle bed, as one seamless loop.

Each track is brought to the same calm loudness, faded in/out, and separated by a short
pause where only the fire is heard. The fire bed is tiled under the whole playlist and its
tail is crossfaded into its head so the hour repeats without a seam. Output is split into
two halves at a pause between songs (to keep each file small).

Usage: python3 make_jazz_fire.py <fire_loop.wav> <out_prefix> <track1> [track2 ...]
"""
import subprocess, sys, wave
import numpy as np

SR = 48000

def load(path):
    raw = subprocess.run(["ffmpeg", "-v", "error", "-i", path, "-ac", "2", "-ar", str(SR), "-f", "f32le", "-"],
                         capture_output=True, check=True).stdout
    return np.frombuffer(raw, dtype=np.float32).reshape(-1, 2).copy()

def lufs(x):
    m = subprocess.run(["ffmpeg", "-hide_banner", "-f", "f32le", "-ar", str(SR), "-ac", "2", "-i", "-",
                        "-af", "ebur128", "-f", "null", "-"], input=x.astype(np.float32).tobytes(), capture_output=True)
    return float([l for l in m.stderr.decode().splitlines() if l.strip().startswith("I:")][-1].split()[1])

def write(path, x):
    pcm = (np.clip(x, -1, 1) * 32767).astype(np.int16)
    with wave.open(path, "wb") as w:
        w.setnchannels(2); w.setsampwidth(2); w.setframerate(SR); w.writeframes(pcm.tobytes())

def main():
    fire_path, prefix, tracks = sys.argv[1], sys.argv[2], sys.argv[3:]
    gap, fin, fout = int(3 * SR), int(1.0 * SR), int(4 * SR)
    music, gaps = [], []
    pos = 0
    for t in tracks:
        x = load(t)
        x *= 10 ** ((-24.0 - lufs(x)) / 20)          # every song at the same calm level
        x[:fin] *= np.linspace(0, 1, fin)[:, None]
        x[-fout:] *= (np.cos(np.linspace(0, np.pi / 2, fout)) ** 2)[:, None]
        music.append(x)
        pos += len(x)
        gaps.append(pos + gap // 2)                  # middle of the pause after this song
        music.append(np.zeros((gap, 2), np.float32))
        pos += gap
    music = np.concatenate(music)
    n = len(music)

    fire_src = load(fire_path)
    fire_src *= 10 ** ((-33.0 - lufs(fire_src)) / 20)   # crackle sits gently under the music
    w = int(4 * SR)
    reps = int(np.ceil((n + w) / len(fire_src)))
    fire = np.concatenate([fire_src] * reps)[:n + w]
    wi = np.sqrt(np.linspace(0, 1, w))[:, None]
    fire[:w] = fire[:w] * wi + fire[n:n + w] * np.sqrt(1 - wi ** 2)   # seamless wrap
    fire = fire[:n]

    mix = music + fire
    mix *= 10 ** ((-23.0 - lufs(mix)) / 20)
    peak = np.abs(mix).max()
    if peak > 0.89:
        mix *= 0.89 / peak
    # split at the pause nearest the middle
    cut = min(gaps, key=lambda g: abs(g - n / 2))
    write(prefix + "_part1.wav", mix[:cut])
    write(prefix + "_part2.wav", mix[cut:])
    print(f"total {n / SR / 60:.1f} min, split at {cut / SR / 60:.1f} min, final {lufs(mix):.1f} LUFS, peak {20 * np.log10(np.abs(mix).max()):.1f} dBFS")

if __name__ == "__main__":
    main()
