# Make the 3-hour video on your computer

You need the two files in this folder:
- `forest_loop.mp4` — 8.5-second forest clip that loops seamlessly (1080p)
- `rain_loop.flac` — 27-second rain sound that loops seamlessly

## 1. Install ffmpeg (free, one time)

- **Windows:** open *Command Prompt* and run `winget install ffmpeg`, then close and reopen Command Prompt.
- **Mac:** install Homebrew from https://brew.sh, then run `brew install ffmpeg` in *Terminal*.

## 2. Build the video

Put both files in one folder, open Command Prompt (Windows) or Terminal (Mac) **in that folder**, and paste this single line:

```
ffmpeg -stream_loop -1 -i forest_loop.mp4 -stream_loop -1 -i rain_loop.flac -t 10800 -map 0:v -map 1:a -c:v copy -af "loudnorm=I=-23:TP=-2:LRA=7,afade=t=in:d=5,afade=t=out:st=10790:d=10" -c:a aac -b:a 192k -movflags +faststart rain_forest_night_3h.mp4
```

It takes about 10 minutes and creates `rain_forest_night_3h.mp4` (3 hours, 1080p, about 3 GB) in the same folder.

Tip — Windows: in File Explorer, open the folder, click the address bar, type `cmd` and press Enter to open Command Prompt right there.

## 3. Upload

Upload `rain_forest_night_3h.mp4` to YouTube and use the title, description and tags in `../youtube-metadata.md`. Remember to tick **Altered or synthetic content → Yes**.
