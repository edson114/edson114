# Build the 10-hour "Christmas Jazz by the Fireplace" video (Mac)

Put these three files in `~/Documents/Youtube`:
- `fireplace_loop.mp4` — the fireplace video loop (same as the fireplace video)
- `jazz_fire_part1.opus` and `jazz_fire_part2.opus` — the 1-hour jazz + fire soundtrack, in two halves

In Terminal, type these one at a time (don't drag files in):

```
cd ~/Documents/Youtube
printf "file 'jazz_fire_part1.opus'\nfile 'jazz_fire_part2.opus'\n" > playlist.txt
ffmpeg -stream_loop -1 -i fireplace_loop.mp4 -stream_loop -1 -f concat -safe 0 -i playlist.txt -t 36000 -map 0:v -map 1:a -c:v copy -af "afade=t=in:d=5,afade=t=out:st=35990:d=10" -c:a aac -b:a 192k -movflags +faststart christmas_jazz_fireplace_10h.mp4
open .
```

The second line just creates a tiny list telling ffmpeg to play part 1, then part 2.
Takes about 15–30 minutes. Result: `christmas_jazz_fireplace_10h.mp4`, 10 hours, 1080p, about 11 GB.
