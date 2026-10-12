# Build the 8-hour "Rain on a Tent" video (Mac)

Put both files in `~/Documents/Youtube`:
- `tent_loop.mp4` — 13.6 s seamless 1080p loop from inside the tent
- `tent_rain_20min.opus` — 20-minute seamless real-rain mix (loops cleanly)

In Terminal:

```
cd ~/Documents/Youtube
ffmpeg -stream_loop -1 -i tent_loop.mp4 -stream_loop -1 -i tent_rain_20min.opus -t 28800 -map 0:v -map 1:a -c:v copy -af "afade=t=in:d=5,afade=t=out:st=28790:d=10" -c:a aac -b:a 192k -movflags +faststart rain_on_tent_8h.mp4
open .
```

Takes about 15–30 minutes. Result: `rain_on_tent_8h.mp4`, 8 hours, 1080p, about 6 GB.
(Make sure you have ~7 GB free. The audio is already at a calm, even loudness, so no extra loudness pass is needed.)
