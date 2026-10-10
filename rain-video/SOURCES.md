# Generated source files (Everygen)

| What | Link |
|---|---|
| Forest image (2752×1536) | https://media.viewmax.io/users/kf7gtmhX31mLsFXmuUhkFyGbipoOj6Vc/generated/98fd4061-6ee7-4299-9f1f-864cfaeed308.jpg |
| 10s animated loop (Kling 3.0, same first and last frame) | https://media.viewmax.io/users/kf7gtmhX31mLsFXmuUhkFyGbipoOj6Vc/sora-videos/937872796633014309/a56f1e34-ccb0-4008-80e8-151eeb4394f3.mp4 |
| 30s rain sound (Seed Audio) | https://media.viewmax.io/users/kf7gtmhX31mLsFXmuUhkFyGbipoOj6Vc/seed-audio/f968e7d5-5c29-4371-b497-b3613374fa85.mp3 |

## Build the 3-hour video (needs network access to media.viewmax.io)

```bash
cd rain-video
curl -L -o loop.mp4 "<loop link above>"
curl -L -o rain.mp3 "<rain sound link above>"
./build.sh loop.mp4 rain.mp3 3 rain_forest_night_3h.mp4
```
