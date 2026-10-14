#!/usr/bin/env python3
"""Sing-along captions for the Humpty Dumpty Short.

One sung line per page, the word being sung turns gold. Word start times come
from a faster-whisper transcription of the Lyria song (all words matched the
lyrics); the text is the exact lyric so nothing is misheard.

Usage: python3 make_captions.py captions.ass
"""
import sys

# (word, start seconds) per sung line, plus the time the line's last word ends.
LINES = [
    ([("Humpty", 0.05), ("Dumpty", 0.52), ("sat", 1.00), ("on", 1.32), ("a", 1.56), ("wall,", 1.68)], 2.02),
    ([("Oops!", 2.22), ("Down,", 2.58), ("down,", 2.80), ("down,", 3.04), ("a", 3.22), ("great", 3.27), ("big", 3.42), ("fall!", 3.66)], 4.00),
    ([("Clip-clop", 4.38), ("pony,", 4.72), ("clank-clank", 5.42), ("knight,", 5.82)], 6.08),
    ([("Push", 6.44), ("and", 6.58), ("pull,", 6.86), ("but", 7.08), ("it's", 7.32), ("not", 7.48), ("quite", 7.64), ("right!", 7.88)], 8.16),
    ([("Then", 8.44), ("came", 8.68), ("a", 8.90), ("mouse", 9.04), ("with", 9.24), ("sticky", 9.42), ("tape,", 9.82)], 10.32),
    ([("Wrap,", 10.62), ("wrap,", 10.94), ("wrap,", 11.12), ("he's", 11.46), ("back", 11.62), ("in", 11.86), ("shape!", 12.12)], 12.40),
    ([("Hugs", 12.78), ("from", 12.88), ("the", 13.10), ("pony,", 13.24), ("hugs", 13.72), ("from", 13.90), ("the", 14.14), ("knight,", 14.30)], 14.52),
    ([("Humpty", 14.92), ("Dumpty,", 15.24), ("shiny", 15.82), ("and", 16.02), ("bright!", 16.34)], 16.64),
    ([("Humpty", 17.02), ("Dumpty", 17.36), ("sat", 17.82), ("on", 18.16), ("a", 18.40), ("wall,", 18.54)], 18.90),
    ([("Wobble,", 19.22), ("wobble,", 19.76), ("whoops,", 20.10), ("a", 20.40), ("fall!", 20.54)], 20.86),
    ([("Boing,", 21.28), ("boing,", 21.48), ("boing", 21.70), ("on", 21.92), ("a", 22.08), ("pillow", 22.20), ("pile,", 22.42)], 22.98),
    ([("Humpty", 23.34), ("Dumpty", 23.64), ("has", 24.14), ("a", 24.40), ("big", 24.56), ("smile!", 24.68)], 25.16),
]
LEAD = 0.30          # page appears this long before its first word
LAST_HOLD = 1.6      # the final line stays up this long after it is sung
GOLD = "&H003DC8FF&"   # #FFC83D in ASS BGR order
WHITE = "&H00FFFFFF&"

HEADER = """[Script Info]
ScriptType: v4.00+
PlayResX: 1080
PlayResY: 1920
WrapStyle: 0
ScaledBorderAndShadow: yes

[V4+ Styles]
Format: Name, Fontname, Fontsize, PrimaryColour, SecondaryColour, OutlineColour, BackColour, Bold, Italic, Underline, StrikeOut, ScaleX, ScaleY, Spacing, Angle, BorderStyle, Outline, Shadow, Alignment, MarginL, MarginR, MarginV, Encoding
Style: Sing,Lilita One,100,&H00FFFFFF,&H00FFFFFF,&H00301A10,&H80000000,0,0,0,0,100,100,1,0,1,7,4,2,90,90,430,1

[Events]
Format: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text
"""


def ts(t):
    cs = int(round(t * 100))
    return f"{cs // 360000}:{cs // 6000 % 60:02d}:{cs // 100 % 60:02d}.{cs % 100:02d}"


def page(words, lit):
    out = []
    for i, (w, _) in enumerate(words):
        if i == len(words) - 1:
            w = w.rstrip(",")  # no dangling comma at the end of a page
        out.append(f"{{\\c{GOLD}}}{w}{{\\c{WHITE}}}" if i == lit else w)
    return " ".join(out)


def main(path):
    events = []
    for n, (words, end) in enumerate(LINES):
        start = words[0][1] - LEAD
        stop = LINES[n + 1][0][0][1] - LEAD if n + 1 < len(LINES) else end + LAST_HOLD
        # all white until the first word is sung
        events.append((start, words[0][1], page(words, -1)))
        for i, (_, t) in enumerate(words):
            t_next = words[i + 1][1] if i + 1 < len(words) else end
            events.append((t, t_next, page(words, i)))
        # all white again after the last word, until the next page
        if stop > end:
            events.append((end, stop, page(words, -1)))
    with open(path, "w") as f:
        f.write(HEADER)
        for a, b, text in events:
            f.write(f"Dialogue: 0,{ts(a)},{ts(b)},Sing,,0,0,0,,{text}\n")


if __name__ == "__main__":
    main(sys.argv[1] if len(sys.argv) > 1 else "captions.ass")
