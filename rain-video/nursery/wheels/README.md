# Wheels on the Bus with Humpty & Friends (long-form, 16:9, 3:00)

- `src/song.mp3` — Lyria 3.5 song, 180.3 s. The classic verses were blocked by the music
  model's copyright filter twice, so only the hook line "The wheels on the bus go round
  and round" is traditional; the other verses are new words in the same style.
- `src/sheet.jpg` — character and bus sheet; `src/frameA..I.jpg` — start frames.
- `prompts/clipA..I.txt` — Seedance 2.5 prompts (480p, 16:9, multi-shot). A and B are
  driving shots reused for intro, breaks and every "riding through the town" line.
- `clips/A..I.mp4` — rendered clips.
- `edit/build.py` — cuts the clips to the song (EDL inside), upscales to 1080p
  (Lanczos + CAS sharpening), adds the song, captions and fades, 2-pass encode under 30 MB.
- `edit/make_captions.py` — sing-along captions from `edit/song_words.json`
  (faster-whisper medium.en word times), sung word in gold, Lilita One font (OFL).
- `thumbnail/thumbnail_1280x720.jpg` — custom thumbnail; `youtube-metadata.md` — upload text.

Rebuild: `cd edit && python3 make_captions.py song_words.json captions.ass && python3 build.py`
