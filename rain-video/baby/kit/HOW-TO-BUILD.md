# Build the 10-hour "Shhh for Babies" video (Mac)

Put both files in `~/Documents/Youtube`:
- `night_sky_loop.mp4` — 20 s seamless dark night-sky loop (1080p)
- `shush_pink_20min.opus` — 20-minute seamless mother-style shush over pink noise

In Terminal (type these, don't drag files in):

```
cd ~/Documents/Youtube
ffmpeg -stream_loop -1 -i night_sky_loop.mp4 -stream_loop -1 -i shush_pink_20min.opus -t 36000 -map 0:v -map 1:a -c:v copy -af "afade=t=in:d=8,afade=t=out:st=35985:d=15" -c:a aac -b:a 160k -movflags +faststart shhh_baby_10h.mp4
open .
```

Takes about 15–30 minutes. Result: `shhh_baby_10h.mp4`, 10 hours, 1080p, about 5 GB.
