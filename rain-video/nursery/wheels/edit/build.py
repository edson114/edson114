#!/usr/bin/env python3
"""Cut the Wheels on the Bus clips to the song and render the final video.

Every timeline segment names a source clip and an in/out point; a segment
whose source is shorter or longer than its slot is slowed down or sped up
to fit (kept between about 0.8x and 1.1x). Segments are upscaled to 1080p
with Lanczos plus light contrast-adaptive sharpening, joined, then the song,
the sing-along captions and a fade in/out are added in the final pass.

Usage: python3 build.py            (from this folder; needs ffmpeg)
"""
import os
import subprocess

FPS = 30
CLIPS = "../clips"
SONG = "../src/song.mp3"
OUT = "../wheels_on_the_bus_humpty_and_friends.mp4"
SONG_LEN = 180.30
TARGET_MB = 28.5      # stay under the 30 MB file-sharing limit

# (timeline start, timeline end, clip, source in, source out)
EDL = [
    # intro: the bus sets off
    (0.00, 2.70, "A", 0.04, 2.71),
    (2.70, 5.60, "A", 2.75, 5.71),
    (5.60, 8.30, "B", 0.04, 2.92),
    (8.30, 10.55, "A", 5.75, 8.00),
    # verse 1: wheels
    (10.55, 13.35, "C", 0.04, 2.29),
    (13.35, 15.55, "C", 2.33, 4.53),
    (15.55, 17.60, "C", 4.80, 6.85),
    (17.60, 19.50, "A", 8.87, 10.77),
    # verse 2: wipers
    (19.50, 23.85, "D", 0.04, 4.21),
    (23.85, 28.30, "D", 4.25, 8.38),
    (28.30, 32.60, "D", 8.42, 12.72),
    (32.60, 34.90, "B", 6.25, 8.55),
    (34.90, 36.60, "A", 11.75, 13.45),
    # verse 3: horn and ducks
    (36.60, 40.90, "E", 0.04, 3.88),
    (40.90, 45.20, "E", 3.92, 8.21),
    (45.20, 50.20, "E", 8.25, 13.06),
    (50.20, 54.10, "A", 11.75, 15.07),
    # instrumental break 1
    (54.10, 56.90, "B", 0.04, 2.84),
    (56.90, 59.30, "A", 2.90, 5.30),
    (59.30, 60.90, "B", 9.60, 11.20),
    (60.90, 63.60, "A", 5.80, 8.50),
    # verse 4: doors, the mouse gets on
    (63.60, 67.70, "F", 0.04, 3.71),
    (67.70, 71.80, "F", 3.75, 7.75),
    (71.80, 76.30, "F", 7.79, 12.29),
    (76.30, 78.60, "B", 6.25, 8.55),
    (78.60, 80.40, "B", 2.96, 4.76),
    # verse 5: pony
    (80.40, 84.50, "G", 0.04, 3.62),
    (84.50, 89.50, "G", 3.66, 8.08),
    (89.50, 93.50, "G", 8.12, 12.12),
    (93.50, 95.30, "C", 4.80, 6.60),
    (95.30, 97.60, "A", 8.90, 11.20),
    # verse 6: mouse
    (97.60, 101.60, "H", 0.12, 3.79),
    (101.60, 102.60, "F", 12.00, 13.00),
    (102.60, 106.60, "H", 3.83, 7.79),
    # wheels reprise
    (106.60, 109.05, "C", 0.04, 2.29),
    (109.05, 111.25, "C", 2.33, 4.53),
    (111.25, 113.25, "B", 11.83, 13.83),
    (113.25, 115.40, "B", 2.96, 5.11),
    # verse 7: knight
    (115.40, 117.70, "H", 7.83, 9.79),
    (117.70, 119.85, "H", 9.83, 11.71),
    (119.85, 121.95, "C", 7.70, 9.80),
    (121.95, 124.00, "A", 5.80, 7.85),
    # instrumental break 2
    (124.00, 126.90, "B", 0.04, 2.94),
    (126.90, 129.40, "G", 8.30, 10.80),
    (129.40, 131.00, "B", 9.60, 11.20),
    (131.00, 133.10, "A", 2.90, 5.00),
    # verse 8: Humpty wobbles and rolls (the gag)
    (133.10, 136.50, "I", 0.25, 3.65),
    (136.50, 141.60, "I", 4.45, 9.21),
    (141.60, 146.60, "I", 9.25, 14.25),
    (146.60, 150.30, "A", 11.75, 15.07),
    # verse 9: clap
    (150.30, 152.70, "I", 14.75, 17.08),
    (152.70, 154.50, "F", 11.00, 12.80),
    (154.50, 157.20, "I", 17.12, 19.82),
    (157.20, 158.70, "B", 3.00, 4.50),
    # verse 10: wheels, arriving at the castle
    (158.70, 161.40, "C", 0.04, 2.29),
    (161.40, 164.20, "A", 0.04, 2.71),
    (164.20, 168.10, "B", 11.83, 15.07),
    (168.10, 172.60, "C", 10.33, 14.05),
    (172.60, 175.00, "B", 2.96, 5.36),
    # outro: the bus drives off
    (175.00, 177.80, "A", 5.80, 8.60),
    (177.80, 180.30, "A", 12.30, 14.80),
]


def run(cmd):
    subprocess.run(cmd, check=True)


def frame(t):
    return int(round(t * FPS))


def main():
    os.makedirs("segs", exist_ok=True)
    assert abs(EDL[-1][1] - SONG_LEN) < 0.05
    names = []
    for i, (t0, t1, clip, s0, s1) in enumerate(EDL):
        if i:
            assert abs(EDL[i - 1][1] - t0) < 1e-6, f"gap before segment {i}"
        n = frame(t1) - frame(t0)
        speed = (s1 - s0) / (t1 - t0)
        assert 0.75 <= speed <= 1.15, f"segment {i} speed {speed:.2f}"
        out = f"segs/{i:03d}.mp4"
        vf = (f"setpts=(PTS-STARTPTS)/{speed:.5f},fps={FPS},"
              "scale=1920:1080:flags=lanczos,cas=0.45,"
              "tpad=stop_mode=clone:stop_duration=1,format=yuv420p")
        run(["ffmpeg", "-v", "error", "-y", "-ss", f"{s0:.3f}", "-t", f"{s1 - s0 + 0.2:.3f}",
             "-i", f"{CLIPS}/{clip}.mp4", "-an", "-vf", vf, "-frames:v", str(n),
             "-c:v", "libx264", "-preset", "veryfast", "-crf", "12", out])
        names.append(out)
    with open("segs/list.txt", "w") as f:
        f.writelines(f"file '{os.path.basename(p)}'\n" for p in names)
    run(["ffmpeg", "-v", "error", "-y", "-f", "concat", "-safe", "0", "-i", "segs/list.txt",
         "-c", "copy", "joined.mp4"])

    vf = ("ass=captions.ass:fontsdir=fonts,"
          f"fade=t=in:st=0:d=0.4,fade=t=out:st={SONG_LEN - 1.2:.2f}:d=1.2")
    af = f"afade=t=out:st={SONG_LEN - 1.5:.2f}:d=1.5"
    abr = 160
    vbr = int(TARGET_MB * 8 * 1024 / SONG_LEN) - abr   # kbit/s
    common = ["-i", "joined.mp4", "-i", SONG, "-map", "0:v", "-map", "1:a",
              "-vf", vf, "-c:v", "libx264", "-preset", "slow", "-b:v", f"{vbr}k",
              "-pix_fmt", "yuv420p", "-t", f"{SONG_LEN:.2f}"]
    run(["ffmpeg", "-v", "error", "-y", *common, "-pass", "1", "-passlogfile", "segs/x264",
         "-an", "-f", "mp4", os.devnull])
    run(["ffmpeg", "-v", "error", "-y", *common, "-pass", "2", "-passlogfile", "segs/x264",
         "-af", af, "-c:a", "aac", "-b:a", f"{abr}k", "-movflags", "+faststart", OUT])
    print("wrote", OUT, f"video {vbr} kbit/s")


if __name__ == "__main__":
    main()
