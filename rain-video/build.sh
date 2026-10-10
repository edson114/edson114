#!/usr/bin/env bash
# Build a long rain-ambience video from a short video loop and a short rain clip.
# Usage: ./build.sh <loop_video> <rain_audio> <hours> <output.mp4>
set -euo pipefail
VID="$1"; AUD="$2"; HOURS="${3:-3}"; OUT="${4:-rain_forest_night_${HOURS}h.mp4}"
SECS=$(awk "BEGIN{print int($HOURS*3600)}")
WORK=$(mktemp -d)
trap 'rm -rf "$WORK"' EXIT

# 1) Seamless video loop: crossfade the clip's tail into its head (1.5s), 1080p, 30fps.
VDUR=$(ffprobe -v error -show_entries format=duration -of csv=p=0 "$VID")
XF=1.5
ffmpeg -y -v error -i "$VID" -i "$VID" -filter_complex "
 [0:v]scale=1920:1080:force_original_aspect_ratio=increase,crop=1920:1080,fps=30,format=yuv420p,setsar=1[a];
 [1:v]scale=1920:1080:force_original_aspect_ratio=increase,crop=1920:1080,fps=30,format=yuv420p,setsar=1[b];
 [a]trim=start=$XF,setpts=PTS-STARTPTS[body];
 [b]trim=0:$XF,setpts=PTS-STARTPTS[head];
 [body][head]xfade=transition=fade:duration=$XF:offset=$(awk "BEGIN{print $VDUR-2*$XF}")[v]" \
 -map "[v]" -an -c:v libx264 -preset slow -crf 20 -g 60 "$WORK/loop.mp4"

# 2) Seamless audio loop: crossfade tail into head (3s), stereo 48kHz.
ADUR=$(ffprobe -v error -show_entries format=duration -of csv=p=0 "$AUD")
AX=3
ffmpeg -y -v error -i "$AUD" -i "$AUD" -filter_complex "
 [0:a]aformat=sample_rates=48000:channel_layouts=stereo,atrim=start=$AX,asetpts=PTS-STARTPTS[body];
 [1:a]aformat=sample_rates=48000:channel_layouts=stereo,atrim=0:$AX,asetpts=PTS-STARTPTS[head];
 [body][head]acrossfade=d=$AX:c1=qsin:c2=qsin[a]" -map "[a]" -c:a pcm_s16le "$WORK/loop.wav"

for f in "$WORK/loop.mp4" "$WORK/loop.wav"; do
  d=$(ffprobe -v error -show_entries format=duration -of csv=p=0 "$f" 2>/dev/null || echo 0)
  awk "BEGIN{exit !(${d:-0}+0 > 1)}" || { echo "Loop step produced an empty file: $f" >&2; exit 1; }
done

# 3) Repeat to full length (video stream-copied, so this is fast), normalize audio loudness.
ffmpeg -y -v error -stream_loop -1 -i "$WORK/loop.mp4" -stream_loop -1 -i "$WORK/loop.wav" \
 -t "$SECS" -map 0:v -map 1:a -c:v copy \
 -af "loudnorm=I=-23:TP=-2:LRA=7,afade=t=in:d=5,afade=t=out:st=$((SECS-10)):d=10" \
 -c:a aac -b:a 192k -movflags +faststart "$OUT"
echo "Done: $OUT ($(du -h "$OUT" | cut -f1))"
