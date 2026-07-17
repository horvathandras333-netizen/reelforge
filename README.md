# ReelForge — GUI reel maker

## Install (once)
Double-click **install_requirements.bat**
OPTIONAL for beat sync: `python -m pip install librosa`

## Run
Double-click **run_reelforge.bat**

---

## What's in the inspector (right panel)

### Selected clip
- **Photo length** — how long each photo holds
- **Video max** — cap how many seconds to use from a video
- **Start at** — skip into a video (e.g. "5s in" starts from 5 seconds)
- **Speed** — 0.25× slow-mo to 4× fast-forward
- **Crop bias** — where to anchor the 9:16 crop (center / top / bottom)
  Useful for portraits: "top" keeps faces in frame on landscape photos

### Smart arrange
- **By date taken** — EXIF timestamp order (trips/days flow naturally)
- **Visual flow** — nearest-colour ordering, no jarring colour jumps
- **Shuffle** — random

### Transitions
- Style: random / fade / dissolve / slides / wipes / circle, or none
- Length: crossfade duration in seconds

### Export settings
- **Format** — Reel/TikTok/Shorts/FB/Square/Landscape
- **FPS** — 30 or 60
- **Quality** — Draft (fast) → Max (slow, best)
- **Colour mood** — None / Warm / Cool / Moody / Punchy / Vintage / B&W
- **Intro/outro hold** — freeze first and last frame for N seconds
- **Target length** — set an exact output duration (e.g. 30s for a Reel);
  clip durations scale proportionally to fill it
- **Ken Burns zoom** — gentle zoom on photos
- **GPU (NVENC)** — faster encode on your NVIDIA card
- **Include date in filename** — auto-names output reel_2025-07-01.mp4

### Audio
- Music fade in/out, loudness normalise to -14 LUFS (Instagram target)

### Beat sync
1. Analyze song → see detected BPM
2. Tick "Cut on the beat", pick how often
3. Export — cuts snap to the music automatically
Needs: `python -m pip install librosa`

---

## Output spec
H.264 High, yuv420p, BT.709, +faststart, AAC 192k @48kHz — Instagram-ready.
