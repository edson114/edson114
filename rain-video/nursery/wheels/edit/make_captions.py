#!/usr/bin/env python3
"""Sing-along captions for the Wheels on the Bus long-form video (16:9).

Each sung line gets its own page; the word being sung turns gold. Word times
come from a faster-whisper (medium.en) transcription of the Lyria song in
song_words.json; the caption text is the lyric as sung, matched to those
words, and any word the transcription missed gets an interpolated time.

Usage: python3 make_captions.py song_words.json captions.ass
"""
import difflib
import json
import re
import sys

# (caption text, line window start, line window end) in song seconds.
LINES = [
    ("The wheels on the bus go round and round,", 10.5, 13.5),
    ("Rolling, rolling, round and round,", 13.5, 15.7),
    ("Big and yellow, castle-town bound,", 15.7, 17.7),
    ("Gliding through the town!", 17.7, 19.65),
    ("Swish, swish, swish, go the wipers fast,", 19.65, 24.0),
    ("Swish, swish, swish, till the rain has passed,", 24.0, 28.4),
    ("Peekaboo, the sun is out at last,", 28.4, 32.7),
    ("Riding through the town!", 32.7, 36.5),
    ("Beep, beep, beep, says the horn, hooray!", 37.2, 41.6),
    ("Beep, beep, beep, little ducks, this way!", 41.6, 46.5),
    ("Waddle, waddle, cross the road and play,", 46.5, 50.35),
    ("Riding through the town!", 50.35, 54.2),
    ("Open, shut, go the big red doors,", 63.5, 67.8),
    ("Open, shut, there's a mouse on board!", 67.8, 71.9),
    ("Hop on in, there's room for more,", 71.9, 76.4),
    ("Riding through the town!", 76.4, 80.0),
    ("Clip, clip, clop, go the pony's feet,", 80.5, 84.65),
    ("Clip, clip, clop, dancing on her seat,", 84.65, 89.5),
    ("Tap your toes to the happy beat,", 89.5, 93.6),
    ("Riding through the town!", 93.6, 97.2),
    ("Squeak, squeak, squeak, says the mouse with glee,", 97.6, 102.6),
    ("Squeak, squeak, peekaboo, tee-hee!", 102.6, 107.1),
    ("The wheels go round and round,", 107.1, 109.2),
    ("Rolling, rolling, round and round,", 109.2, 111.35),
    ("Standing in the castle town,", 111.35, 113.4),
    ("Riding through the town!", 113.4, 115.3),
    ("Clank, clank, clank, goes the little knight,", 115.3, 117.8),
    ("Clank, clank, clank, hold the wheel tight,", 117.8, 119.95),
    ("Turning left and turning right,", 119.95, 122.0),
    ("Riding through the town!", 122.0, 124.0),
    ("Wobble, wobble, Humpty on the go,", 133.2, 136.6),
    ("Wobble, wobble, rolling to and fro,", 136.6, 141.7),
    ("Catch him, friends! Whoa, whoa, whoa!", 141.7, 146.7),
    ("Ride it, ride it through the town!", 146.7, 150.3),
    ("Clap, clap, clap, everybody now,", 150.3, 154.6),
    ("Clap your hands and show me how,", 154.6, 157.3),
    ("Riding through the town!", 157.3, 158.8),
    ("The wheels on the bus go round and round,", 158.85, 161.5),
    ("Rolling, rolling, round and round,", 161.5, 164.1),
    ("Hip, hip, hooray, what a happy sound,", 168.4, 172.6),
    ("Riding through the town!", 172.6, 175.2),
]
# Word times the transcription squeezed together, set by hand from the
# small.en pass: {line index: {word index: start}}.
OVERRIDES = {32: {3: 144.30, 4: 145.26, 5: 146.10}}
LEAD = 0.25        # page shows this long before its first sung word
MAX_HOLD = 1.2     # a page stays up at most this long after its last word
GOLD = "&H003DC8FF&"
WHITE = "&H00FFFFFF&"

HEADER = """[Script Info]
ScriptType: v4.00+
PlayResX: 1920
PlayResY: 1080
WrapStyle: 0
ScaledBorderAndShadow: yes

[V4+ Styles]
Format: Name, Fontname, Fontsize, PrimaryColour, SecondaryColour, OutlineColour, BackColour, Bold, Italic, Underline, StrikeOut, ScaleX, ScaleY, Spacing, Angle, BorderStyle, Outline, Shadow, Alignment, MarginL, MarginR, MarginV, Encoding
Style: Sing,Lilita One,82,&H00FFFFFF,&H00FFFFFF,&H00301A10,&H80000000,0,0,0,0,100,100,1,0,1,6,3,2,160,160,60,1

[Events]
Format: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text
"""


def norm(w):
    return re.sub(r"[^a-z]", "", w.lower())


def ts(t):
    cs = int(round(t * 100))
    return f"{cs // 360000}:{cs // 6000 % 60:02d}:{cs // 100 % 60:02d}.{cs % 100:02d}"


def word_times(text, t0, t1, heard):
    """Start time for every caption word, matched to the transcription."""
    words = text.split()
    pool = [(s, norm(w)) for s, e, w in heard if t0 - 0.3 <= s < t1]
    times = [None] * len(words)
    sm = difflib.SequenceMatcher(a=[norm(w) for w in words], b=[w for _, w in pool], autojunk=False)
    for a, b, n in sm.get_matching_blocks():
        for k in range(n):
            times[a + k] = pool[b + k][0]
    # first and last anchors, then fill gaps by even spacing
    if times[0] is None:
        times[0] = pool[0][0] if pool else t0 + 0.1
    if times[-1] is None:
        times[-1] = max(times[0] + 0.3 * (len(words) - 1), min(t1 - 0.4, (pool[-1][0] if pool else t1 - 0.4)))
    i = 0
    while i < len(words):
        if times[i] is None:
            j = i
            while times[j] is None:
                j += 1
            a, b = times[i - 1], times[j]
            for k in range(i, j):
                times[k] = a + (b - a) * (k - i + 1) / (j - i + 1)
            i = j
        i += 1
    # keep them increasing
    for k in range(1, len(times)):
        times[k] = max(times[k], times[k - 1] + 0.08)
    return words, times


def page(words, lit):
    out = []
    for i, w in enumerate(words):
        if i == len(words) - 1:
            w = w.rstrip(",")
        out.append(f"{{\\c{GOLD}}}{w}{{\\c{WHITE}}}" if i == lit else w)
    return " ".join(out)


def main(words_path, out_path):
    heard = json.load(open(words_path))
    events = []
    timed = []
    for n, (text, a, b) in enumerate(LINES):
        words, times = word_times(text, a, b, heard)
        for k, t in OVERRIDES.get(n, {}).items():
            times[k] = t
        timed.append((words, times, b))
    for n, (words, times, t_end) in enumerate(timed):
        start = times[0] - LEAD
        if n + 1 < len(timed):
            nxt = timed[n + 1][1][0] - LEAD
        else:
            nxt = t_end + MAX_HOLD
        last_end = min(t_end, times[-1] + 0.9)
        stop = min(nxt, last_end + MAX_HOLD)
        events.append((start, times[0], page(words, -1)))
        for i, t in enumerate(times):
            t_next = times[i + 1] if i + 1 < len(times) else last_end
            events.append((t, t_next, page(words, i)))
        if stop > last_end:
            events.append((last_end, stop, page(words, -1)))
    with open(out_path, "w") as f:
        f.write(HEADER)
        for a, b, text in events:
            if b > a:
                f.write(f"Dialogue: 0,{ts(a)},{ts(b)},Sing,,0,0,0,,{text}\n")


if __name__ == "__main__":
    main(sys.argv[1], sys.argv[2])
