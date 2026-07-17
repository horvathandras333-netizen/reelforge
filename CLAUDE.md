# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## What this is

ReelForge is a single-file PySide6 desktop app (`reelforge.py`) that mixes photos/videos/music into a 9:16 Instagram/TikTok-style reel via FFmpeg. There is no package structure, build step, or test suite — everything lives in this one file.

## Commands

Install dependencies:
```
python -m pip install PySide6 imageio-ffmpeg Pillow
python -m pip install librosa   # optional — required only for beat sync
```
(`install_requirements.bat` / `run_reelforge.bat` wrap the install and run steps for Windows double-click use.)

Run the app:
```
python reelforge.py
```

There is no linter or test framework configured. To sanity-check a change:
- `python -m py_compile reelforge.py` — catches syntax errors.
- The pure command-building helpers (see below) take no Qt objects, so they can be exercised directly from a throwaway script: construct `Clip` objects and a settings dict, instantiate `ExportWorker(clips, music_path, settings, out_path)`, and call `worker._build_command()` to inspect the generated FFmpeg command/filter graph without needing a display or running ffmpeg.
- For a full end-to-end check (including the background QThread workers), run under `QT_QPA_PLATFORM=offscreen`, drive a `QApplication`/`ReelForge` instance from a script, and pump a `QEventLoop` with a `QTimer` while waiting on state (e.g. `len(win.media)`, a `finished_ok`/`failed` signal result) — cross-thread Qt signals only deliver while the event loop is spinning, so plain synchronous polling won't observe worker results.

`reelforge.py.bak` is a stale backup from a previous editing session, not part of the app — don't read it for current behavior.

## Architecture

### FFmpeg is invoked once, as a single `filter_complex` graph

There's no multi-pass editing pipeline — one `ExportWorker._build_command()` call builds one big FFmpeg command string per export, using `xfade`/`concat` to chain clips, `zoompan` for Ken Burns, `drawtext` for overlays, and `amix`/`sidechain`-style `volume` gating for audio ducking. `FFMPEG`/`FFPROBE` are resolved once at import time via `_find_binary()` (falls back to `imageio_ffmpeg`'s bundled binary if not on PATH).

`_build_command()` orchestrates a set of **module-level, Qt-free helper functions** — these are the right place to change filter-graph logic, and the right place to unit-test it in isolation:
- `compute_timeline_durations` — resolves per-clip on-screen duration (beat-sync override → target-duration scaling → loop-to-fill).
- `build_clip_video_filter` — per-clip scale/crop/zoom/blur-background filter chain + its FFmpeg input args.
- `combine_clip_streams` — concat or crossfade the per-clip streams into one timeline, returning each clip's start time (`clip_starts`) for downstream audio alignment.
- `build_text_filters` / `find_font` / `escape_drawtext` — title/CTA `drawtext` overlays (fonts are located directly by file path to dodge Fontconfig on Windows FFmpeg builds).
- `build_clip_audio_filters` / `build_duck_filter` / `atempo_chain` — extract each audio-bearing clip's own audio, delay it to its timeline position, and duck the music underneath it.

`ExportWorker._build_command()` wires these together: build per-clip filters → combine into one video stream → intro/outro hold → fade + text → clip audio extraction → music (fade/loudnorm/duck) → final `amix`. Input index `i` in the filter graph always equals clip index (each clip contributes exactly one `-i`), so video and audio for the same clip always share the same stream index — don't break that invariant when touching input ordering.

### Background work: three QThread subclasses

The GUI thread must never block on FFmpeg/ffprobe/librosa subprocess calls, so:
- **`ExportWorker`** — runs a full export; auto-retries once on CPU if GPU (NVENC) encoding fails.
- **`ImportWorker`** — probes duration and pre-builds thumbnails for dropped/imported files; emits `clip_found`/`music_found` per item so the media list fills in incrementally instead of freezing on a big batch.
- **`FuncWorker`** — generic "run this one blocking call off-thread" wrapper, used for the song-energy scan (`find_smart_song_start`) and beat detection (`analyze_beats`).

Because those two scans are now async, `export_reel` / `auto_reel` / `auto_reel_multi` / `preview_draft` don't run them inline — they call `_ensure_song_start(cb)` and `_ensure_beats(cb)`, which either resolve synchronously (already cached) or kick off a `FuncWorker` and continue in its callback. `_start_render(settings, out, preview)` → `_render_finalize()` is the shared tail end that actually builds and starts the `ExportWorker` for both real exports and draft previews (`preview_draft` just shrinks `size`/`fps` and swaps encode preset before calling it).

### Data model and project files

`Clip` (dataclass) is the only persisted unit: path, kind (`photo`/`video`), timing/trim/speed/crop settings, plus a lazily-probed `has_audio`. `classify()` sorts an incoming path into photo/video/audio by extension. Projects (`.rfproj`) are just `asdict()` of the media/timeline `Clip` lists plus the UI settings, filtered back through `Clip.__dataclass_fields__` on load — so adding a new `Clip` field is backward/forward compatible as long as it has a default.

### UI shape

`ReelForge(QMainWindow)` builds three panes (media library, preview, tabbed inspector) plus a timeline strip; `_build_inspector()` holds four tabs (Clip / Video / Audio / Text) whose widgets are read directly into a settings dict at export time (`_gather_settings()`). `MediaList`/`TimelineList` both subclass `_FileDropListWidget` for shared Explorer drag-and-drop handling. Timeline list items are always built through `_make_timeline_item()` — don't construct `QListWidgetItem`s for the timeline ad hoc, several call sites (append, rebuild, auto-populate, project load) rely on that single helper staying in sync.
