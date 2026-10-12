# Build the 10-hour "Cozy Christmas Fireplace" video (Mac)

Put both files in `~/Documents/Youtube`:
- `fireplace_loop.mp4` — 13 s seamless 1080p loop of the decorated fireplace
- `fire_crackle_20min.opus` — 20-minute seamless crackling-fire mix

In Terminal (type these, don't drag files in):

```
cd ~/Documents/Youtube
ffmpeg -stream_loop -1 -i fireplace_loop.mp4 -stream_loop -1 -i fire_crackle_20min.opus -t 36000 -map 0:v -map 1:a -c:v copy -af "afade=t=in:d=5,afade=t=out:st=35990:d=10" -c:a aac -b:a 192k -movflags +faststart cozy_christmas_fireplace_10h.mp4
open .
```

Takes about 15–30 minutes. Result: `cozy_christmas_fireplace_10h.mp4`, 10 hours, 1080p, about 11 GB.
(Make sure you have ~12 GB free.)
