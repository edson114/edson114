"""Cut the 8 shots + end card into one vertical short with music, ducking and captions.

Edit list: (shot file, in, out) in seconds. Captions are given relative to each shot's own clip time
and converted to timeline time automatically.
"""
import subprocess, os

SRC = os.path.join(os.path.dirname(__file__), "..", "src")
EDIT = [  # file, in, out, captions [(clip_start, clip_end, style, text)]
    ("shot1.mp4", 0.8, 6.0, [(2.0, 5.3, "Line", "They said the evacuation\\Nwould take an hour.")]),
    ("shot2.mp4", 1.0, 6.0, []),
    ("shot3.mp4", 0.0, 4.6, [(0.1, 4.0, "Line", "Eyes on the ridge.\\NNobody fires till I say.")]),
    ("shot4.mp4", 1.0, 5.5, []),
    ("shot5.mp4", 0.5, 6.5, []),
    ("shot6.mp4", 0.5, 6.5, []),
    ("shot7.mp4", 1.5, 8.0, [(5.4, 7.9, "Line", "Hell, it's about time.")]),
    ("shot8.mp4", 0.0, 7.0, []),
]
END = 3.5               # black end card
MUSIC_OFFSET = 11.0     # start the score 11 s in so its battle section lands on the swarm
OUT = os.path.join(os.path.dirname(__file__), "last_stand_mar_sara_short.mp4")

def ts(t):
    h, m, s = int(t // 3600), int(t % 3600 // 60), t % 60
    return f"{h}:{m:02d}:{s:05.2f}"

def main():
    events, t = [], 0.0
    for f, a, b, caps in EDIT:
        for cs, ce, style, text in caps:
            events.append((t + cs - a, t + min(ce, b) - a, style, text))
        t += b - a
    total = t + END
    events += [
        (0.15, 2.6, "Title", "THE LAST STAND"),
        (0.15, 2.6, "Sub", "MAR SARA"),
        (t + 0.3, total, "End", "TO BE\\NCONTINUED"),
        (t + 0.3, total, "Small", "Fan-made tribute · Not affiliated with Blizzard"),
    ]
    ass_src = os.path.join(os.path.dirname(__file__), "captions.ass")
    ass_out = os.path.join(os.path.dirname(__file__), "captions_timed.ass")
    with open(ass_src) as fsrc, open(ass_out, "w") as fo:
        fo.write(fsrc.read())
        for s, e, style, text in events:
            fade = "{\\fad(250,250)}"
            fo.write(f"Dialogue: 0,{ts(s)},{ts(e)},{style},,0,0,0,,{fade}{text}\n")

    cmd = ["ffmpeg", "-v", "error", "-y"]
    for f, a, b, _ in EDIT:
        cmd += ["-ss", str(a), "-t", str(b - a), "-i", os.path.join(SRC, f)]
    cmd += ["-ss", str(MUSIC_OFFSET), "-i", os.path.join(SRC, "score.mp3")]
    n = len(EDIT)
    fc = []
    for i in range(n):
        fc.append(f"[{i}:v]scale=1080:1920:force_original_aspect_ratio=increase,crop=1080:1920,fps=30,setsar=1,format=yuv420p[v{i}]")
        fc.append(f"[{i}:a]aformat=sample_rates=48000:channel_layouts=stereo,afade=t=in:d=0.08,afade=t=out:st={EDIT[i][2]-EDIT[i][1]-0.08:.2f}:d=0.08[a{i}]")
    fc.append(f"color=black:s=1080x1920:r=30:d={END}[ve]")
    fc.append(f"anullsrc=r=48000:cl=stereo,atrim=0:{END}[ae]")
    fc.append("".join(f"[v{i}][a{i}]" for i in range(n)) + f"[ve][ae]concat=n={n+1}:v=1:a=1[vcat][fx]")
    last_start = total - END - (EDIT[-1][2] - EDIT[-1][1])
    fc.append(f"[vcat]fade=t=in:st=0:d=0.4,fade=t=out:st={total-END-0.5:.2f}:d=0.5,ass='{ass_out}'[vout]")
    fc.append("[fx]asplit=2[fx1][fx2]")
    fc.append(f"[{n}:a]aformat=sample_rates=48000:channel_layouts=stereo,atrim=0:{total},volume=-9dB,"
              f"afade=t=in:d=1.5,afade=t=out:st={total-2.5:.2f}:d=2.5[mus]")
    fc.append("[mus][fx1]sidechaincompress=threshold=0.05:ratio=6:attack=20:release=400[musd]")
    fc.append("[fx2][musd]amix=inputs=2:normalize=0,loudnorm=I=-14:TP=-1.5:LRA=11[aout]")
    cmd += ["-filter_complex", ";".join(fc), "-map", "[vout]", "-map", "[aout]",
            "-c:v", "libx264", "-preset", "slow", "-crf", "19", "-pix_fmt", "yuv420p",
            "-c:a", "aac", "-b:a", "192k", "-movflags", "+faststart", "-t", f"{total:.2f}", OUT]
    subprocess.run(cmd, check=True)
    print(f"wrote {OUT}: {total:.1f} s")

if __name__ == "__main__":
    main()
