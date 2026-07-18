"""
ReelForge — Full-featured reel maker
=====================================
Dark Qt GUI to mix photos/videos/music into an Instagram-ready 9:16 reel.

Features:
  Import (button + drag-drop), thumbnails, reorderable timeline,
  preview, per-clip inspector (length/speed/trim/crop-bias),
  smart ordering (date/visual-flow/shuffle), crossfade & flash transitions,
  colour moods, Ken Burns zoom, blur-background for landscape videos,
  text overlays (title + CTA), music ducking, loop-to-fill,
  smart song start, beat sync, loudness normalise, GPU/CPU encode,
  save/open projects, auto-dated filenames.

Install:
    python -m pip install PySide6 imageio-ffmpeg Pillow
    python -m pip install librosa          # optional, for beat sync

Run:
    python reelforge.py
"""
from __future__ import annotations
import json, logging, os, random, re, shutil, subprocess, sys, tempfile, threading
from dataclasses import dataclass, asdict
from pathlib import Path

from PySide6.QtCore import (Qt, QThread, Signal, QUrl, QSize, QTimer, QMimeData, QPointF,
                             QByteArray, QRectF, QRect, QEvent)
from PySide6.QtGui  import (QPixmap, QIcon, QAction, QFont, QFontDatabase, QDrag, QShortcut,
                             QKeySequence, QPainter, QColor, QPolygonF, QCursor, QPainterPath, QPen)
from PySide6.QtSvg import QSvgRenderer
from PySide6.QtMultimedia import QMediaPlayer, QAudioOutput
from PySide6.QtMultimediaWidgets import QVideoWidget
from PySide6.QtWidgets import (
    QApplication, QMainWindow, QWidget, QListWidget, QListWidgetItem,
    QVBoxLayout, QHBoxLayout, QPushButton, QLabel, QFileDialog, QStackedWidget,
    QSlider, QComboBox, QCheckBox, QDoubleSpinBox, QGroupBox, QFormLayout,
    QProgressBar, QMessageBox, QSplitter, QFrame, QStyle, QScrollArea,
    QLineEdit, QSpinBox, QTabWidget, QStyledItemDelegate, QStyleOptionViewItem,
    QGraphicsDropShadowEffect, QSizePolicy, QMenuBar,
)

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
log = logging.getLogger("reelforge")

# ── constants ────────────────────────────────────────────────────────────────
APP_NAME    = "ReelForge"
PROJECT_EXT = ".rfproj"
PHOTO_EXTS  = {".jpg",".jpeg",".png",".webp",".bmp"}
VIDEO_EXTS  = {".mp4",".mov",".mkv",".avi",".webm",".m4v"}
AUDIO_EXTS  = {".mp3",".wav",".m4a",".aac",".flac",".ogg"}
THUMB_SIZE  = QSize(120,68)
DEFAULT_PHOTO_DUR = 4.0
DEFAULT_VIDEO_MAX = 8.0

# grid-card geometry for the restyled media library / timeline strip, and the
# extra item-data roles the delegates read at paint time
MEDIA_CARD_SIZE    = QSize(128,146)
MEDIA_THUMB_H      = 80
TIMELINE_CARD_SIZE = QSize(112,98)
ROLE_KIND  = Qt.UserRole+1
ROLE_BADGE = Qt.UserRole+2

EXPORT_PRESETS = {
    "Instagram Reel (1080×1920)": (1080,1920),
    "TikTok (1080×1920)":         (1080,1920),
    "YouTube Shorts (1080×1920)": (1080,1920),
    "Facebook Reel (1080×1920)":  (1080,1920),
    "Square (1080×1080)":         (1080,1080),
    "Landscape (1920×1080)":      (1920,1080),
}

TRANSITION_POOL = [
    "fade","dissolve","slideleft","slideright",
    "smoothleft","smoothright","circleopen","wipeleft",
]
TRANSITION_CHOICES = ["random","none","flash_white","flash_black"] + TRANSITION_POOL

QUALITY_PRESETS = {
    "Draft (fast)":          (26,"veryfast"),
    "Balanced":              (21,"medium"),
    "High (recommended)":    (19,"slow"),
    "Max (slow)":            (17,"slow"),
}

COLOUR_MOODS = {
    "None":    "",
    "Warm":    "curves=r='0/0 0.5/0.58 1/1':g='0/0 0.5/0.5 1/0.95':b='0/0 0.5/0.42 1/0.85'",
    "Cool":    "curves=r='0/0 0.5/0.42 1/0.85':g='0/0 0.5/0.5 1/0.95':b='0/0 0.5/0.58 1/1'",
    "Moody":   "curves=all='0/0 0.25/0.18 0.75/0.65 1/0.9',eq=saturation=0.8",
    "Punchy":  "eq=contrast=1.12:saturation=1.3:brightness=0.03",
    "Vintage": "curves=r='0/0.08 1/0.92':g='0/0.04 1/0.88':b='0/0.12 1/0.78',hue=s=0.75",
    "B&W":     "hue=s=0",
}

TEXT_POSITIONS = {
    "Bottom centre": "x=(w-text_w)/2:y=h-text_h-80",
    "Top centre":    "x=(w-text_w)/2:y=80",
    "Centre":        "x=(w-text_w)/2:y=(h-text_h)/2",
    "Bottom left":   "x=60:y=h-text_h-80",
    "Bottom right":  "x=w-text_w-60:y=h-text_h-80",
}

_NO_WINDOW = getattr(subprocess,"CREATE_NO_WINDOW",0) if os.name=="nt" else 0

# ── theme palettes ───────────────────────────────────────────────────────────
THEMES = {
    "dark": {
        "bg":"#211b2e","panel":"#2a2239","inset":"#1b1526","frame":"#3d3350",
        "tx":"#eee8f7","mut":"#a89bc2","bd":"#3d3350","bd2":"#4f4266",
        "ac":"#d4a72c","ac2":"#b88f23",
        "hov":"rgba(255,255,255,14)","hov2":"rgba(255,255,255,23)",
    },
    "light": {
        "bg":"#f4f2ee","panel":"#ecebe5","inset":"#e3e1d9","frame":"#d8d4cc",
        "tx":"#2b2724","mut":"#7d766c","bd":"#dcd8d0","bd2":"#c6c1b7",
        "ac":"#c15f3c","ac2":"#a94e2f",
        "hov":"rgba(0,0,0,10)","hov2":"rgba(0,0,0,18)",
    },
}

# ── SVG icon snippets (paths copied verbatim from ReelForge.dc.html) ────────
# Each value is an inner-<svg> fragment with `{c}` standing in for the colour
# the mockup expressed as currentColor / var(--mut) / var(--ac).
ICON_SVG = {
    "star":       '<path d="M12 1c.4 4.7 1.9 7.6 4.3 8.9C18.2 10.9 20.8 11.4 23 11.6c-4.7.4-7.6 1.9-8.9 4.3-1 1.9-1.5 4.5-1.7 6.7-.4-4.7-1.9-7.6-4.3-8.9C6.2 12.8 3.6 12.3 1.4 12.1c4.7-.4 7.6-1.9 8.9-4.3C11.3 5.9 11.8 3.3 12 1Z" fill="{c}"/>',
    "minimize":   '<line x1="5" y1="12" x2="19" y2="12" stroke="{c}" stroke-width="2" stroke-linecap="round"/>',
    "maximize":   '<rect x="4" y="4" width="16" height="16" rx="2" stroke="{c}" stroke-width="2" fill="none"/>',
    "restore":    '<rect x="6" y="4" width="14" height="14" rx="2" stroke="{c}" stroke-width="2" fill="none"/><path d="M4 8v10a2 2 0 0 0 2 2h10" stroke="{c}" stroke-width="2" fill="none"/>',
    "close":      '<line x1="6" y1="6" x2="18" y2="18" stroke="{c}" stroke-width="2" stroke-linecap="round"/><line x1="18" y1="6" x2="6" y2="18" stroke="{c}" stroke-width="2" stroke-linecap="round"/>',
    "sun":        '<circle cx="12" cy="12" r="4" stroke="{c}" stroke-width="2" fill="none"/><path d="M12 2v2M12 20v2M4.9 4.9l1.4 1.4M17.7 17.7l1.4 1.4M2 12h2M20 12h2M4.9 19.1l1.4-1.4M17.7 6.3l1.4-1.4" stroke="{c}" stroke-width="2" fill="none" stroke-linecap="round"/>',
    "moon":       '<path d="M12 3a6 6 0 0 0 9 9 9 9 0 1 1-9-9Z" stroke="{c}" stroke-width="2" fill="none" stroke-linejoin="round"/>',
    "lightning":  '<path d="M13 2 3 14h7l-1 8 10-12h-7z" fill="{c}"/>',
    "grid3":      '<rect x="3" y="3" width="7" height="7" rx="1.5" stroke="{c}" stroke-width="2" fill="none"/><rect x="14" y="3" width="7" height="7" rx="1.5" stroke="{c}" stroke-width="2" fill="none"/><rect x="3" y="14" width="7" height="7" rx="1.5" stroke="{c}" stroke-width="2" fill="none"/>',
    "media_hdr":  '<rect x="3" y="3" width="18" height="18" rx="2" stroke="{c}" stroke-width="1.8" fill="none"/><circle cx="9" cy="9" r="2" stroke="{c}" stroke-width="1.8" fill="none"/><path d="m21 15-5-5L5 21" stroke="{c}" stroke-width="1.8" fill="none"/>',
    "preview_hdr":'<path d="m22 8-6 4 6 4V8z" stroke="{c}" stroke-width="1.8" fill="none"/><rect x="2" y="6" width="14" height="12" rx="2" stroke="{c}" stroke-width="1.8" fill="none"/>',
    "play":       '<path d="M8 5v14l11-7z" fill="{c}"/>',
    "pause":      '<rect x="6" y="4" width="4" height="16" rx="1" fill="{c}"/><rect x="14" y="4" width="4" height="16" rx="1" fill="{c}"/>',
    "inspector_hdr":'<path d="M4 21v-7M4 10V3M12 21v-9M12 8V3M20 21v-5M20 12V3M1 14h6M9 8h6M17 16h6" stroke="{c}" stroke-width="1.8" fill="none" stroke-linecap="round"/>',
    "music_hdr":  '<path d="M9 18V5l12-2v13" stroke="{c}" stroke-width="2" fill="none"/><circle cx="6" cy="18" r="3" stroke="{c}" stroke-width="2" fill="none"/><circle cx="18" cy="16" r="3" stroke="{c}" stroke-width="2" fill="none"/>',
    "timeline_hdr":'<rect x="2" y="6" width="20" height="12" rx="2" stroke="{c}" stroke-width="1.8" fill="none"/><path d="M6 6v12M12 6v12M18 6v12" stroke="{c}" stroke-width="1.8" fill="none"/>',
    "import":     '<path d="M12 3v12M7 8l5-5 5 5M4 17v2a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2v-2" stroke="{c}" stroke-width="2" fill="none" stroke-linecap="round" stroke-linejoin="round"/>',
    "add":        '<path d="M5 12h14M12 5l7 7-7 7" stroke="{c}" stroke-width="2" fill="none" stroke-linecap="round" stroke-linejoin="round"/>',
    "remove":     '<path d="M3 6h18M8 6V4h8v2M6 6v14a2 2 0 0 0 2 2h8a2 2 0 0 0 2-2V6" stroke="{c}" stroke-width="2" fill="none" stroke-linecap="round" stroke-linejoin="round"/>',
    "undo":       '<path d="M9 14 4 9l5-5M4 9h11a5 5 0 0 1 0 10H9" stroke="{c}" stroke-width="2" fill="none" stroke-linecap="round" stroke-linejoin="round"/>',
    "chevron_up": '<path d="m18 15-6-6-6 6" stroke="{c}" stroke-width="3" fill="none" stroke-linecap="round" stroke-linejoin="round"/>',
    "chevron_down":'<path d="m6 9 6 6 6-6" stroke="{c}" stroke-width="3" fill="none" stroke-linecap="round" stroke-linejoin="round"/>',
    "plus":       '<path d="M12 5v14M5 12h14" stroke="{c}" stroke-width="2" fill="none" stroke-linecap="round"/>',
    "photo_glyph":'<path d="M4 5h16v14H4zM4 15l4-4 4 4M14 13l2-2 4 4" stroke="{c}" stroke-width="1.7" fill="none" stroke-linecap="round" stroke-linejoin="round"/>',
    "video_glyph":'<path d="M8 5v14l11-7z" stroke="{c}" stroke-width="1.7" fill="none" stroke-linejoin="round"/>',
    "chevron_down_combo":'<path d="m6 9 6 6 6-6" stroke="{c}" stroke-width="2" fill="none" stroke-linecap="round" stroke-linejoin="round"/>',
}

def _svg_icon(name, size=14, color="#e6e6e6", viewbox="0 0 24 24"):
    """Rasterize an ICON_SVG snippet (path data copied from ReelForge.dc.html)
    into a QIcon at the given colour — used everywhere instead of hand-drawn
    QPainter primitives so re-tinting on theme toggle is just a re-call."""
    inner=ICON_SVG[name].format(c=color)
    svg=f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="{viewbox}">{inner}</svg>'
    renderer=QSvgRenderer(QByteArray(svg.encode("utf-8")))
    ratio=QApplication.instance().devicePixelRatio() if QApplication.instance() else 1
    px=max(1,round(size*ratio))
    pm=QPixmap(px,px); pm.fill(Qt.transparent)
    p=QPainter(pm); p.setRenderHint(QPainter.Antialiasing)
    renderer.render(p,QRectF(0,0,px,px)); p.end()
    pm.setDevicePixelRatio(ratio)
    return QIcon(pm)

def _svg_pixmap(name, size=14, color="#e6e6e6", viewbox="0 0 24 24"):
    return _svg_icon(name,size,color,viewbox).pixmap(size,size)

def _find_serif_family():
    """Pick the first installed serif family for the mockup's headings —
    'Source Serif 4' is a webfont and isn't installed locally, so fall back
    to whatever serif ships with the OS rather than fetching fonts online."""
    installed=set(QFontDatabase.families())
    for name in ("Source Serif 4","Source Serif Pro","PT Serif","Noto Serif",
                 "Constantia","Cambria","Georgia","Times New Roman","Liberation Serif","DejaVu Serif"):
        if name in installed: return name
    return None

# ── FFmpeg helpers ───────────────────────────────────────────────────────────
def _find_binary(name):
    found = shutil.which(name)
    if found: return found
    if name == "ffmpeg":
        try:
            import imageio_ffmpeg
            return imageio_ffmpeg.get_ffmpeg_exe()
        except Exception: return None
    ff = _find_binary("ffmpeg")
    if ff:
        cand = Path(ff).with_name("ffprobe"+(".exe" if os.name=="nt" else ""))
        if cand.exists(): return str(cand)
    return None

FFMPEG  = _find_binary("ffmpeg")
FFPROBE = _find_binary("ffprobe")

def probe_duration(path):
    if not FFPROBE: return 0.0
    try:
        out = subprocess.run([FFPROBE,"-v","error","-show_entries","format=duration",
            "-of","default=noprint_wrappers=1:nokey=1",path],
            capture_output=True,text=True,creationflags=_NO_WINDOW)
        return float(out.stdout.strip())
    except Exception as e:
        log.warning("probe_duration failed for %s: %s",path,e); return 0.0

def probe_video_size(path):
    """Return (width, height) of a video, or (0,0)."""
    if not FFPROBE: return (0,0)
    try:
        out = subprocess.run([FFPROBE,"-v","error","-select_streams","v:0",
            "-show_entries","stream=width,height",
            "-of","csv=p=0",path],
            capture_output=True,text=True,creationflags=_NO_WINDOW)
        parts = out.stdout.strip().split(",")
        return (int(parts[0]),int(parts[1]))
    except Exception as e:
        log.warning("probe_video_size failed for %s: %s",path,e); return (0,0)

def make_video_thumb(path,dst):
    if not FFMPEG: return False
    try:
        subprocess.run([FFMPEG,"-y","-ss","1","-i",path,"-frames:v","1",
            "-vf","scale=240:-1",dst],capture_output=True,creationflags=_NO_WINDOW)
        return Path(dst).exists()
    except Exception as e:
        log.warning("make_video_thumb failed for %s: %s",path,e); return False

def nvenc_available():
    if not FFMPEG: return False
    try:
        out = subprocess.run([FFMPEG,"-hide_banner","-encoders"],
            capture_output=True,text=True,creationflags=_NO_WINDOW)
        return "h264_nvenc" in out.stdout
    except Exception as e:
        log.warning("nvenc_available check failed: %s",e); return False

# ── Intelligence helpers ─────────────────────────────────────────────────────
def _pil_available():
    try: import PIL; return True
    except Exception: return False

def _librosa_available():
    try: import librosa; return True
    except Exception: return False

def _thumb_image(clip, cache_dir):
    from PIL import Image
    try:
        if clip.kind == "photo":
            img = Image.open(clip.path)
        else:
            dst = cache_dir / (str(abs(hash(clip.path)))+".jpg")
            if not dst.exists(): make_video_thumb(clip.path,str(dst))
            if not dst.exists(): return None
            img = Image.open(str(dst))
        img = img.convert("RGB"); img.thumbnail((32,32)); return img
    except Exception as e:
        log.warning("_thumb_image failed for %s: %s",clip.path,e); return None

def average_color(clip, cache_dir):
    img = _thumb_image(clip, cache_dir)
    if img is None: return (128,128,128)
    px = list(img.getdata()); n = len(px) or 1
    return (sum(p[0] for p in px)/n, sum(p[1] for p in px)/n, sum(p[2] for p in px)/n)

def exif_datetime(clip):
    if clip.kind == "photo":
        try:
            from PIL import Image
            import datetime as _dt
            exif = Image.open(clip.path).getexif()
            raw  = exif.get(36867) or exif.get(306)
            if raw: return _dt.datetime.strptime(raw,"%Y:%m:%d %H:%M:%S").timestamp()
        except Exception as e:
            log.debug("exif_datetime read failed for %s: %s",clip.path,e)
    try: return os.path.getmtime(clip.path)
    except Exception as e:
        log.warning("could not stat %s: %s",clip.path,e); return 0.0

def exif_rotation(path):
    """Return the FFmpeg transpose filter string needed to fix EXIF orientation,
    or '' if no rotation is needed.  Works without Pillow by using ffprobe."""
    # EXIF orientation tag values → FFmpeg transpose chain
    # 1=normal, 3=180°, 6=90°CW, 8=90°CCW
    orient = 1
    # Try Pillow first (fast)
    if _pil_available():
        try:
            from PIL import Image
            exif = Image.open(path).getexif()
            orient = exif.get(274, 1)  # 274 = Orientation tag
        except Exception as e:
            log.debug("exif_rotation (Pillow) failed for %s: %s",path,e)
    else:
        # Fall back to ffprobe
        if FFPROBE:
            try:
                out = subprocess.run(
                    [FFPROBE,"-v","error","-select_streams","v:0",
                     "-show_entries","stream_tags=rotate",
                     "-of","default=noprint_wrappers=1:nokey=1",path],
                    capture_output=True,text=True,creationflags=_NO_WINDOW)
                deg = out.stdout.strip()
                if deg == "90":   orient = 6
                elif deg == "180": orient = 3
                elif deg == "270": orient = 8
            except Exception as e:
                log.debug("exif_rotation (ffprobe) failed for %s: %s",path,e)
    return {
        1: "",                               # normal
        2: "hflip",                          # mirrored horizontal
        3: "transpose=2,transpose=2",        # 180°
        4: "vflip",                          # mirrored vertical
        5: "transpose=0,hflip",              # 90°CW + mirror
        6: "transpose=1",                    # 90°CW
        7: "transpose=3,hflip",              # 90°CCW + mirror
        8: "transpose=2",                    # 90°CCW
    }.get(orient, "")

def order_by_date(clips, cache_dir=None):
    return sorted(clips, key=exif_datetime)

def order_visual_flow(clips, cache_dir):
    if len(clips) <= 2: return list(clips)
    colors = {id(c): average_color(c,cache_dir) for c in clips}
    remaining = list(clips)
    start = min(remaining, key=lambda c: sum(colors[id(c)]))
    order = [start]; remaining.remove(start)
    while remaining:
        last = colors[id(order[-1])]
        nxt  = min(remaining, key=lambda c: sum((a-b)**2 for a,b in zip(colors[id(c)],last)))
        order.append(nxt); remaining.remove(nxt)
    return order

def analyze_beats(music_path):
    try:
        import librosa
        y,sr = librosa.load(music_path,mono=True)
        tempo,beats = librosa.beat.beat_track(y=y,sr=sr,units="time")
        bpm = float(tempo) if not hasattr(tempo,"__len__") else float(tempo[0])
        return round(bpm,1),[float(t) for t in beats]
    except Exception as e:
        log.warning("analyze_beats failed for %s: %s",music_path,e); return None,[]

def beat_segment_durations(n_clips, beat_times, beats_per_clip):
    if not beat_times or n_clips<=0: return None
    bounds=[0.0]
    for i in range(beats_per_clip-1,len(beat_times),beats_per_clip):
        if beat_times[i]>bounds[-1]+0.05: bounds.append(beat_times[i])
    durs=[bounds[i+1]-bounds[i] for i in range(len(bounds)-1)]
    if not durs: return None
    avg=sum(durs)/len(durs)
    while len(durs)<n_clips: durs.append(avg)
    return durs[:n_clips]

def find_smart_song_start(music_path, reel_duration):
    if not FFMPEG or not music_path: return 0.0
    try:
        result = subprocess.run(
            [FFMPEG,"-i",music_path,"-af",
             "astats=metadata=1:reset=1,ametadata=print:key=lavfi.astats.Overall.RMS_level",
             "-f","null","-"],
            capture_output=True,text=True,creationflags=_NO_WINDOW)
        lines=result.stderr.split("\n")
        times,levels,cur_t=[],[],None
        for line in lines:
            if "pts_time:" in line:
                try: cur_t=float(line.split("pts_time:")[1].split()[0])
                except (ValueError,IndexError): pass
            if "RMS_level=" in line and cur_t is not None:
                try:
                    val=float(line.split("=")[1].strip())
                    if val>-100: times.append(cur_t); levels.append(val); cur_t=None
                except (ValueError,IndexError): pass
        if not levels: return 0.0
        song_dur=probe_duration(music_path)
        peak=max(levels); threshold=peak-6.0
        for t,lvl in zip(times,levels):
            if lvl>=threshold and (song_dur-t)>=reel_duration:
                return max(0.0,t-0.5)
        return 0.0
    except Exception as e:
        log.warning("find_smart_song_start failed for %s: %s",music_path,e); return 0.0

# ── Data model ───────────────────────────────────────────────────────────────
@dataclass
class Clip:
    path:       str
    kind:       str
    duration:   float = 0.0
    photo_dur:  float = DEFAULT_PHOTO_DUR
    video_max:  float = DEFAULT_VIDEO_MAX
    trim_start: float = 0.0
    speed:      float = 1.0
    crop_bias:  str   = "center"   # center | top | bottom | blur_bg
    has_audio:  bool|None = None   # lazily probed — None means "not checked yet"

    @property
    def name(self): return Path(self.path).name

    def render_duration(self):
        if self.kind=="photo": return max(0.5,self.photo_dur)
        raw  = self.duration if self.duration>0 else self.video_max
        avail= max(0.1,raw-self.trim_start)
        clipped=min(avail,self.video_max) if self.video_max>0 else avail
        return round(clipped/max(0.1,self.speed),3)

def classify(path):
    ext=Path(path).suffix.lower()
    if ext in PHOTO_EXTS: return "photo"
    if ext in VIDEO_EXTS: return "video"
    if ext in AUDIO_EXTS: return "audio"
    return None

def probe_has_audio(path):
    """Whether a media file has at least one audio stream."""
    if not FFPROBE: return False
    try:
        out=subprocess.run([FFPROBE,"-v","error","-select_streams","a:0",
            "-show_entries","stream=index","-of","csv=p=0",path],
            capture_output=True,text=True,creationflags=_NO_WINDOW)
        return bool(out.stdout.strip())
    except Exception as e:
        log.warning("probe_has_audio failed for %s: %s",path,e); return False

def ensure_has_audio(clip):
    """Lazily probe and cache whether `clip` carries an audio stream."""
    if clip.has_audio is None:
        clip.has_audio = (clip.kind=="video") and probe_has_audio(clip.path)
    return clip.has_audio

def atempo_chain(speed):
    """FFmpeg's atempo filter only accepts 0.5-2.0 per instance; chain
    several to cover the app's full 0.25x-4x speed range."""
    if abs(speed-1.0)<0.01: return ""
    factors=[]; remaining=speed
    while remaining>2.0: factors.append(2.0); remaining/=2.0
    while remaining<0.5: factors.append(0.5); remaining/=0.5
    factors.append(remaining)
    return "".join(f",atempo={f:.6f}" for f in factors)

# ── FFmpeg command building (pure helpers — no Qt, easy to unit test) ────────
def compute_timeline_durations(clips, settings, rng):
    """Resolve each clip's on-screen duration (beat-sync override, target-
    duration scaling, loop-to-fill), returning the possibly-extended clip
    list alongside a parallel list of durations."""
    n=len(clips)
    beat_durs=settings.get("beat_durations")
    if beat_durs and len(beat_durs)==n:
        durs=[max(0.5,float(d)) for d in beat_durs]
    else:
        durs=[c.render_duration() for c in clips]

    target_dur=settings.get("target_duration")
    if target_dur and target_dur>0:
        raw_total=sum(durs)
        if raw_total>0 and abs(raw_total-target_dur)>0.5:
            ratio=target_dur/raw_total
            durs=[max(0.3,d*ratio) for d in durs]

    fill_target=target_dur if (target_dur and target_dur>0) else None
    if fill_target is None and settings.get("music_duration") is not None:
        fill_target=max(0,settings["music_duration"]-float(settings.get("song_start",0.0)))
    if fill_target and fill_target>0 and settings.get("loop_to_fill",False):
        current_total=sum(durs)
        if current_total<fill_target-0.5 and clips:
            extra_clips=list(clips); extra_durs=list(durs)
            round_clips=list(clips); round_durs=list(durs)
            while sum(extra_durs)<fill_target-0.5:
                sh=list(zip(round_clips,round_durs)); rng.shuffle(sh)
                rc,rd=zip(*sh)
                extra_clips+=list(rc); extra_durs+=list(rd)
            running=0.0; final_clips=[]; final_durs=[]
            for c,d in zip(extra_clips,extra_durs):
                if running>=fill_target: break
                trimmed=min(d,fill_target-running)
                final_clips.append(c); final_durs.append(max(0.3,trimmed))
                running+=trimmed
            clips,durs=final_clips,final_durs
    return clips,durs

def build_clip_video_filter(i, clip, dur, w, h, fps, zoom, mood_filter):
    """Build the [i:v]...[vi] filter chain for one clip, plus the extra
    -i / seek input args ffmpeg needs for it. Returns (input_args, filter_str)."""
    if clip.crop_bias=="top":      cy="0"
    elif clip.crop_bias=="bottom": cy=f"(ih-{h})"
    else:                          cy=f"(ih-{h})/2"
    blur_bg=(clip.crop_bias=="blur_bg")
    rot=exif_rotation(clip.path)
    rot_filter=f"{rot}," if rot else ""

    if clip.kind=="photo":
        input_args=["-loop","1","-t",f"{dur:.3f}","-i",clip.path]
        if zoom:
            frames=max(1,int(round(dur*fps)))
            base=(f"[{i}:v]{rot_filter}scale={w*2}:{h*2}:force_original_aspect_ratio=increase,"
                  f"crop={w*2}:{h*2},"
                  f"zoompan=z='min(zoom+0.0009,1.12)':d={frames}:"
                  f"x='iw/2-(iw/zoom/2)':y='ih/2-(ih/zoom/2)':s={w}x{h}:fps={fps}")
        else:
            base=(f"[{i}:v]{rot_filter}scale={w}:{h}:force_original_aspect_ratio=increase,"
                  f"crop={w}:{h}:x=(iw-{w})/2:y={cy},fps={fps}")
    else:
        ss=max(0.0,clip.trim_start)
        spd=max(0.25,min(4.0,clip.speed))
        src_need=dur*spd
        input_args=["-noautorotate","-ss",f"{ss:.3f}","-t",f"{src_need:.3f}","-i",clip.path]
        speed_filter=f",setpts={1.0/spd:.6f}*PTS" if abs(spd-1.0)>0.01 else ""
        if blur_bg:
            base=(f"[{i}:v]{rot_filter}split[bg{i}][fg{i}];"
                  f"[bg{i}]scale={w}:{h}:force_original_aspect_ratio=increase,"
                  f"crop={w}:{h},gblur=sigma=30[blurred{i}];"
                  f"[fg{i}]scale={w}:{h}:force_original_aspect_ratio=decrease,"
                  f"pad={w}:{h}:(ow-iw)/2:(oh-ih)/2:black[sharp{i}];"
                  f"[blurred{i}][sharp{i}]overlay=(W-w)/2:(H-h)/2{speed_filter}")
        else:
            base=(f"[{i}:v]{rot_filter}scale={w}:{h}:force_original_aspect_ratio=increase,"
                  f"crop={w}:{h}:x=(iw-{w})/2:y={cy},fps={fps}{speed_filter}")

    colour=f",{mood_filter}" if mood_filter else ""
    # settb forces a uniform timebase across all clips — required for xfade
    # when mixing photos (1/30 tb) and videos (1/90000 tb)
    tb_fix=f",settb=1/{fps*1000},fps={fps}"
    return input_args, f"{base}{colour},setsar=1,format=yuv420p{tb_fix},setpts=PTS-STARTPTS[v{i}]"

def combine_clip_streams(n, durs, transition, tdur, forced_t, rng):
    """Concat or crossfade the [v0]..[v{n-1}] streams into one [vN] (or the
    concat label). Returns (filters, final_label, total_duration, clip_starts, transition_t)
    where clip_starts[i] is roughly when clip i becomes the dominant frame."""
    use_flash=transition in ("flash_white","flash_black")
    flash_color="white" if transition=="flash_white" else "black"
    use_xfade=transition!="none" and n>1
    min_dur=min(durs)
    if not use_xfade:           t=0.0
    elif forced_t is not None:  t=max(0.0,float(forced_t))
    elif use_flash:             t=min(0.25,min_dur*0.4)
    else:                       t=max(0.0,min(tdur,min_dur*0.45,1.0))

    filters=[]
    if not use_xfade:
        clip_starts=[0.0]
        for d in durs[:-1]: clip_starts.append(clip_starts[-1]+d)
        if n==1: final_v,total="v0",durs[0]
        else:
            joins="".join(f"[v{i}]" for i in range(n))
            filters.append(f"{joins}concat=n={n}:v=1:a=0[cc]")
            final_v,total="cc",sum(durs)
        return filters,final_v,total,clip_starts,t

    clip_starts=[0.0]
    prev,running="v0",durs[0]
    for k in range(1,n):
        offset=running-t
        clip_starts.append(offset)
        if use_flash:
            filters.append(
                (f"[{prev}][v{k}]xfade=transition=fade:"
                 f"duration={t:.3f}:offset={offset:.3f},"
                 f"curves=all='0/1 {t/2:.3f}/0 {t:.3f}/1'[x{k}]")
                if flash_color=="white" else
                (f"[{prev}][v{k}]xfade=transition=fade:"
                 f"duration={t:.3f}:offset={offset:.3f}[x{k}]"))
        else:
            trans=rng.choice(TRANSITION_POOL) if transition=="random" else transition
            filters.append(
                f"[{prev}][v{k}]xfade=transition={trans}:"
                f"duration={t:.3f}:offset={offset:.3f}[x{k}]")
        prev=f"x{k}"; running+=durs[k]-t
    final_v,total=prev,running
    return filters,final_v,total,clip_starts,t

_FONT_CANDIDATES=[
    r"C:\Windows\Fonts\arial.ttf",
    r"C:\Windows\Fonts\arialbd.ttf",
    r"C:\Windows\Fonts\calibri.ttf",
    r"C:\Windows\Fonts\segoeui.ttf",
    "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",
    "/usr/share/fonts/truetype/liberation/LiberationSans-Regular.ttf",
]

def find_font():
    """Locate a font file directly — avoids Fontconfig entirely (fixes Windows FFmpeg builds)."""
    for c in _FONT_CANDIDATES:
        if Path(c).exists(): return c.replace("\\","/").replace(":","\\:")
    return None

def escape_drawtext(txt):
    return txt.replace("\\","\\\\").replace("'","\\'").replace(":","\\:").replace(",","\\,")

def build_text_filters(settings, total, font_str):
    title_text=settings.get("title_text","").strip()
    cta_text=settings.get("cta_text","").strip()
    txt_size=int(settings.get("text_size",64))
    txt_pos=TEXT_POSITIONS.get(settings.get("text_position","Bottom centre"),
                               "x=(w-text_w)/2:y=h-text_h-80")
    txt_color=settings.get("text_color","white")
    shadow_str=":shadowcolor=black@0.6:shadowx=3:shadowy=3" if settings.get("text_shadow",True) else ""

    text_filters=[]
    if title_text:
        safe=escape_drawtext(title_text)
        text_filters.append(
            f"drawtext=text='{safe}':fontsize={txt_size}{font_str}:{txt_pos}:"
            f"fontcolor={txt_color}{shadow_str}:enable='between(t,0,3)'")
    if cta_text:
        safe=escape_drawtext(cta_text)
        cta_pos="x=(w-text_w)/2:y=h-text_h-80"
        text_filters.append(
            f"drawtext=text='{safe}':fontsize={txt_size}{font_str}:{cta_pos}:"
            f"fontcolor={txt_color}{shadow_str}:"
            f"enable='between(t,{max(0,total-4)},{total})'")
    return text_filters

def build_clip_audio_filters(clips, durs, clip_starts, hold_secs):
    """Extract + delay each audio-bearing clip's own audio so it lands at its
    place in the final timeline. Returns (filters, stream_labels, windows) where
    windows is [(start,end), ...] for ducking the music underneath them."""
    filters=[]; labels=[]; windows=[]
    for i,(clip,dur,start) in enumerate(zip(clips,durs,clip_starts)):
        if clip.kind!="video" or not clip.has_audio: continue
        spd=max(0.25,min(4.0,clip.speed))
        start=start+hold_secs
        delay_ms=max(0,round(start*1000))
        delay_str=f",adelay={delay_ms}:all=1" if delay_ms>0 else ""
        label=f"ca{i}"
        filters.append(f"[{i}:a]asetpts=N/SR/TB{atempo_chain(spd)}{delay_str}[{label}]")
        labels.append(label)
        windows.append((start,start+dur))
    return filters,labels,windows

def build_duck_filter(in_label, out_label, windows, duck_db):
    """Chain enable-gated volume dips over the music stream, one per window
    where clip audio is playing."""
    if not windows:
        return f"[{in_label}]anull[{out_label}]"
    gain=10**(duck_db/20.0)
    parts=[f"[{in_label}]"]
    chain=[]
    for s,e in windows:
        chain.append(f"volume={gain:.4f}:enable='between(t,{s:.3f},{e:.3f})'")
    return "".join(parts)+",".join(chain)+f"[{out_label}]"

# ── Export worker ────────────────────────────────────────────────────────────
class ExportWorker(QThread):
    progress    = Signal(int)
    status      = Signal(str)
    finished_ok = Signal(str)
    failed      = Signal(str)

    def __init__(self, clips, music_path, settings, out_path):
        super().__init__()
        self.clips=clips; self.music_path=music_path
        self.settings=settings; self.out_path=out_path
        self._proc=None; self._cancel=False

    def cancel(self):
        self._cancel=True
        if self._proc and self._proc.poll() is None:
            try: self._proc.terminate()
            except Exception as e: log.warning("failed to terminate ffmpeg process: %s",e)

    def _build_command(self):
        s=self.settings
        w,h=s["size"]; fps=s["fps"]
        zoom=s["zoom"]; use_gpu=s["gpu"]
        transition=s.get("transition","random")
        tdur=float(s.get("transition_dur",0.6))
        crf=int(s.get("crf",20))
        loudnorm=s.get("loudnorm",False)
        m_fin=float(s.get("music_fade_in",1.0))
        m_fout=float(s.get("music_fade_out",2.5))
        mood_filter=COLOUR_MOODS.get(s.get("colour_mood","None"),"")
        hold_secs=float(s.get("intro_outro_hold",0.0))
        song_start=float(s.get("song_start",0.0))
        include_clip_audio=bool(s.get("include_clip_audio",True))
        duck_music=bool(s.get("duck_music",True))
        duck_db=float(s.get("duck_db",-12.0))

        rng=random.Random(s.get("seed"))
        duration_settings=s
        if self.music_path:
            duration_settings=dict(s); duration_settings["music_duration"]=probe_duration(self.music_path)
        clips,durs=compute_timeline_durations(self.clips,duration_settings,rng)
        n=len(clips)

        if include_clip_audio:
            for c in clips:
                if c.kind=="video": ensure_has_audio(c)

        # per-clip chains → [v0]..[v{n-1}], plus the matching ffmpeg -i args
        cmd=[FFMPEG,"-y"]; filters=[]
        for i,clip in enumerate(clips):
            input_args,filt=build_clip_video_filter(i,clip,durs[i],w,h,fps,zoom,mood_filter)
            cmd+=input_args; filters.append(filt)

        # combine clips (concat or crossfade), tracking each clip's start time
        # in the final timeline so its audio (if any) can be aligned/ducked
        combine_filters,final_v,total,clip_starts,_t=combine_clip_streams(
            n,durs,transition,tdur,s.get("forced_t"),rng)
        filters+=combine_filters

        # intro/outro hold
        if hold_secs>0:
            hold=round(hold_secs,3)
            filters.append(f"[{final_v}]tpad=start_duration={hold}:stop_duration={hold}:"
                           f"start_mode=clone:stop_mode=clone[ph]")
            final_v="ph"; total+=hold*2

        # overall video fade + text overlays (applied after fade so they render on top)
        vfade=min(0.5,total/6)
        font_path=find_font()
        font_str=f":fontfile='{font_path}'" if font_path else ""
        text_filters=build_text_filters(s,total,font_str)
        fade_chain=(f"fade=t=in:st=0:d={vfade:.3f},"
                    f"fade=t=out:st={max(0.0,total-vfade):.3f}:d={vfade:.3f}")
        txt_chain=(","+",".join(text_filters)) if text_filters else ""
        filters.append(f"[{final_v}]{fade_chain}{txt_chain},format=yuv420p[vout]")

        # each clip's own audio (if present), delayed to its place in the timeline
        clip_audio_filters=[]; clip_audio_labels=[]; duck_windows=[]
        if include_clip_audio:
            clip_audio_filters,clip_audio_labels,duck_windows=build_clip_audio_filters(
                clips,durs,clip_starts,hold_secs if hold_secs>0 else 0.0)
        filters+=clip_audio_filters

        # music — index must equal the number of media inputs already added to cmd
        # (n clips each added one -i, so music is input number n)
        has_audio_out=False
        if self.music_path:
            music_index=n
            cmd+=["-i",self.music_path]
            fin=min(m_fin,total/4); fout=min(m_fout,total/3)
            music_chain=(f"[{music_index}:a]"
                 f"atrim=start={song_start:.3f}:duration={total:.3f},"
                 f"asetpts=N/SR/TB,"
                 f"afade=t=in:st=0:d={fin:.3f},"
                 f"afade=t=out:st={max(0.0,total-fout):.3f}:d={fout:.3f}")
            if loudnorm: music_chain+=",loudnorm=I=-14:TP=-1.5:LRA=11"
            if clip_audio_labels and duck_music:
                filters.append(music_chain+"[music_pre]")
                filters.append(build_duck_filter("music_pre","music_ducked",duck_windows,duck_db))
                music_label="music_ducked"
            else:
                filters.append(music_chain+"[music_final]")
                music_label="music_final"

            if clip_audio_labels:
                mix_in="".join(f"[{l}]" for l in [music_label]+clip_audio_labels)
                filters.append(
                    f"{mix_in}amix=inputs={1+len(clip_audio_labels)}:"
                    f"duration=first:dropout_transition=0:normalize=0,alimiter=limit=0.95[aout]")
            else:
                filters.append(f"[{music_label}]anull[aout]")
            has_audio_out=True
        elif clip_audio_labels:
            mix_in="".join(f"[{l}]" for l in clip_audio_labels)
            if len(clip_audio_labels)==1:
                filters.append(f"{mix_in}anull[aout]")
            else:
                filters.append(f"{mix_in}amix=inputs={len(clip_audio_labels)}:"
                               f"duration=longest:dropout_transition=0:normalize=0[aout]")
            has_audio_out=True

        cmd+=["-filter_complex",";".join(filters),"-map","[vout]"]
        if has_audio_out:
            cmd+=["-map","[aout]","-c:a","aac","-q:a","2","-ar","48000"]

        if use_gpu:
            cmd+=["-c:v","h264_nvenc","-preset","p6","-rc","vbr",
                  "-cq",str(crf),"-b:v","12M","-maxrate","16M","-bufsize","24M"]
        else:
            cmd+=["-c:v","libx264","-preset",s.get("x264_preset","medium"),
                  "-crf",str(crf),"-profile:v","high"]

        cmd+=["-pix_fmt","yuv420p","-r",str(fps),"-t",f"{total:.3f}",
              "-colorspace","bt709","-color_primaries","bt709","-color_trc","bt709",
              "-movflags","+faststart","-progress","pipe:1","-nostats",self.out_path]
        return cmd,total

    def _prepare_audio(self):
        if not self.music_path or not FFMPEG: return None
        fd,tmp_path=tempfile.mkstemp(suffix="_rf_audio.wav"); os.close(fd)
        tmp=Path(tmp_path)
        cmd=[FFMPEG,"-y","-i",self.music_path,"-ar","48000","-ac","2","-c:a","pcm_s16le",str(tmp)]
        try:
            r=subprocess.run(cmd,capture_output=True,creationflags=_NO_WINDOW)
            if r.returncode==0 and tmp.exists(): return str(tmp)
            log.warning("_prepare_audio: ffmpeg exited %s: %s",
                       r.returncode,r.stderr.decode(errors="replace")[-500:])
        except Exception as e:
            log.warning("_prepare_audio failed: %s",e)
        return None

    def _run_ffmpeg(self, cmd, total):
        """Run an ffmpeg command, emit progress, return (returncode, stderr_text)."""
        try:
            proc=subprocess.Popen(cmd,stdout=subprocess.PIPE,stderr=subprocess.PIPE,
                                  text=True,bufsize=1,creationflags=_NO_WINDOW)
        except Exception as e:
            return -1, str(e)
        self._proc=proc
        time_re=re.compile(r"out_time_ms=(\d+)")
        stderr_lines=[]; stdout_lines=[]
        def read_err():
            for line in proc.stderr: stderr_lines.append(line)
        t=threading.Thread(target=read_err,daemon=True); t.start()
        for line in proc.stdout:
            stdout_lines.append(line)
            m=time_re.search(line)
            if m:
                done=int(m.group(1))/1_000_000.0
                self.progress.emit(max(0,min(99,int(done/total*100))))
        proc.wait(); t.join(timeout=2)
        return proc.returncode,"".join(stderr_lines)

    def run(self):
        self._tmp_audio=None
        if self.music_path:
            self.status.emit("Preparing audio…")
            self._tmp_audio=self._prepare_audio()
            if self._tmp_audio is None:
                self.failed.emit("Could not read the audio file. Check it's a valid MP3/WAV/M4A.")
                return
            self._orig_music=self.music_path
            self.music_path=self._tmp_audio

        try: cmd,total=self._build_command()
        except Exception as e:
            self._cleanup(); self.failed.emit(f"Build error: {e}"); return

        if total<=0:
            self._cleanup(); self.failed.emit("Timeline is empty."); return

        self.status.emit("Rendering…")
        rc,stderr=self._run_ffmpeg(cmd,total)

        # auto-retry with CPU if GPU failed
        gpu_failed=(rc!=0 and self.settings.get("gpu") and
                    any(x in stderr for x in ["nvcuda","nvenc","Cannot load"]))
        if gpu_failed:
            self.status.emit("GPU failed — retrying with CPU…")
            self.settings["gpu"]=False
            try: cmd2,total2=self._build_command()
            except Exception as e:
                self._cleanup(); self.failed.emit(f"CPU fallback error: {e}"); return
            rc,stderr=self._run_ffmpeg(cmd2,total2)
            total=total2

        self._cleanup()
        if self._cancel: self.failed.emit("Export cancelled."); return
        if rc==0 and Path(self.out_path).exists():
            self.progress.emit(100)
            self.finished_ok.emit(self.out_path)
        else:
            self.failed.emit("FFmpeg error:\n"+stderr[-2000:])

    def _cleanup(self):
        try:
            if getattr(self,"_tmp_audio",None):
                Path(self._tmp_audio).unlink(missing_ok=True)
                self._tmp_audio=None
            if getattr(self,"_orig_music",None):
                self.music_path=self._orig_music
        except Exception as e:
            log.warning("ExportWorker cleanup failed: %s",e)

class FuncWorker(QThread):
    """Runs one blocking call off the GUI thread and reports back its result.
    Used for the slow one-shot scans (song-start energy scan, beat analysis)
    that would otherwise freeze the UI for seconds."""
    result = Signal(object)
    failed = Signal(str)
    def __init__(self, fn, *args, **kwargs):
        super().__init__()
        self._fn=fn; self._args=args; self._kwargs=kwargs
    def run(self):
        try:
            self.result.emit(self._fn(*self._args,**self._kwargs))
        except Exception as e:
            log.warning("FuncWorker(%s) failed: %s",getattr(self._fn,"__name__",self._fn),e)
            self.failed.emit(str(e))

class ImportWorker(QThread):
    """Probes duration and generates thumbnails off the GUI thread so
    dropping/importing many files doesn't freeze the window."""
    clip_found   = Signal(object)  # a fully-populated Clip
    music_found  = Signal(str)     # path to an audio file
    finished_all = Signal(int)     # number of media clips added

    def __init__(self, paths, existing_paths, cache_dir):
        super().__init__()
        self.paths=list(paths)
        self.existing_paths=set(existing_paths)
        self.cache_dir=cache_dir

    def run(self):
        added=0
        for p in self.paths:
            k=classify(p)
            if k is None: continue
            if k=="audio":
                self.music_found.emit(p); continue
            if p in self.existing_paths: continue
            self.existing_paths.add(p)   # so a path repeated in this batch isn't added twice
            dur=probe_duration(p) if k=="video" else 0.0
            if k=="video":
                dst=self.cache_dir/(str(abs(hash(p)))+".jpg")
                if not dst.exists(): make_video_thumb(p,str(dst))
            self.clip_found.emit(Clip(path=p,kind=k,duration=dur)); added+=1
        self.finished_all.emit(added)

# ── Draggable lists ───────────────────────────────────────────────────────────
class _FileDropListWidget(QListWidget):
    """Common drag-drop-files-from-Explorer behavior shared by the media
    library and timeline lists."""
    files_dropped=Signal(list)
    def dragEnterEvent(self,e):
        e.acceptProposedAction() if e.mimeData().hasUrls() else super().dragEnterEvent(e)
    def dragMoveEvent(self,e):
        e.acceptProposedAction() if e.mimeData().hasUrls() else super().dragMoveEvent(e)
    def dropEvent(self,e):
        if e.mimeData().hasUrls():
            self.files_dropped.emit([u.toLocalFile() for u in e.mimeData().urls() if u.toLocalFile()])
            e.acceptProposedAction()
        else:
            self._on_internal_drop(e)
    def _on_internal_drop(self,e):
        super().dropEvent(e)

def _pixmap_from_decoration(deco, size):
    """QListWidgetItem stores its constructor icon under Qt.DecorationRole as
    a QIcon — normalize that (or a bare QPixmap) into a QPixmap for painting."""
    if isinstance(deco,QIcon):
        return deco.pixmap(size)
    if isinstance(deco,QPixmap):
        return deco
    return None

class MediaItemDelegate(QStyledItemDelegate):
    """Paints each media-library item as the mockup's card: rounded thumbnail
    (or a centred kind glyph when there's no real thumbnail yet), a bottom-
    right duration/PHOTO badge, and the filename below."""
    def __init__(self, window):
        super().__init__(window); self._win=window

    def sizeHint(self, option, index):
        return MEDIA_CARD_SIZE

    def paint(self, painter, option, index):
        colors=self._win._colors
        painter.save(); painter.setRenderHint(QPainter.Antialiasing)
        selected=bool(option.state & QStyle.State_Selected)
        pad=4
        card=option.rect.adjusted(pad,pad,-pad,-pad)
        thumb=QRect(card.left(),card.top(),card.width(),MEDIA_THUMB_H)
        path=QPainterPath(); path.addRoundedRect(QRectF(thumb),10,10)

        painter.fillPath(path,QColor(colors["inset"]))
        pm=_pixmap_from_decoration(index.data(Qt.DecorationRole),thumb.size())
        if pm and not pm.isNull():
            painter.save(); painter.setClipPath(path)
            scaled=pm.scaled(thumb.size(),Qt.KeepAspectRatioByExpanding,Qt.SmoothTransformation)
            x=thumb.left()+(thumb.width()-scaled.width())//2
            y=thumb.top()+(thumb.height()-scaled.height())//2
            painter.drawPixmap(x,y,scaled); painter.restore()
        else:
            glyph="video_glyph" if index.data(ROLE_KIND)=="video" else "photo_glyph"
            gp=_svg_pixmap(glyph,20,"#e8e4dc")
            painter.drawPixmap(thumb.center().x()-10,thumb.center().y()-10,gp)

        pen=QPen(QColor(colors["bd2"] if selected else colors["bd"])); pen.setWidthF(1.4)
        painter.setPen(pen); painter.drawPath(path)

        badge=index.data(ROLE_BADGE)
        if badge:
            f=painter.font(); f.setPointSizeF(7.0); painter.setFont(f)
            fm=painter.fontMetrics(); bw=fm.horizontalAdvance(badge)+14; bh=15
            brect=QRectF(thumb.right()-bw-5,thumb.bottom()-bh-5,bw,bh)
            bpath=QPainterPath(); bpath.addRoundedRect(brect,7,7)
            painter.fillPath(bpath,QColor(20,18,16,204))
            painter.setPen(QColor("#ffffff")); painter.drawText(brect,Qt.AlignCenter,badge)

        name=index.data(Qt.DisplayRole) or ""
        text_rect=QRect(card.left(),thumb.bottom()+6,card.width(),card.height()-MEDIA_THUMB_H-6)
        painter.setPen(QColor(colors["mut"]))
        f=painter.font(); f.setPointSizeF(8.3); painter.setFont(f)
        elided=painter.fontMetrics().elidedText(name,Qt.ElideRight,text_rect.width())
        painter.drawText(text_rect,Qt.AlignLeft|Qt.AlignVCenter,elided)
        painter.restore()

class TimelineItemDelegate(QStyledItemDelegate):
    """Paints each timeline item as the mockup's clip card: thumbnail, a
    VIDEO/PHOTO kind badge top-left, and a footer strip with name+duration
    that inverts to the accent colour when the clip is selected."""
    def __init__(self, window):
        super().__init__(window); self._win=window

    def sizeHint(self, option, index):
        return TIMELINE_CARD_SIZE

    def paint(self, painter, option, index):
        colors=self._win._colors
        painter.save(); painter.setRenderHint(QPainter.Antialiasing)
        selected=bool(option.state & QStyle.State_Selected)
        pad=3
        card=option.rect.adjusted(pad,pad,-pad,-pad)
        foot_h=26
        thumb=QRect(card.left(),card.top(),card.width(),card.height()-foot_h)
        foot=QRect(card.left(),thumb.bottom(),card.width(),foot_h)
        path=QPainterPath(); path.addRoundedRect(QRectF(card),9,9)

        painter.save(); painter.setClipPath(path)
        painter.fillRect(thumb,QColor(colors["inset"]))
        pm=_pixmap_from_decoration(index.data(Qt.DecorationRole),thumb.size())
        if pm and not pm.isNull():
            scaled=pm.scaled(thumb.size(),Qt.KeepAspectRatioByExpanding,Qt.SmoothTransformation)
            x=thumb.left()+(thumb.width()-scaled.width())//2
            y=thumb.top()+(thumb.height()-scaled.height())//2
            painter.drawPixmap(x,y,scaled)
        painter.fillRect(foot,QColor(colors["ac"] if selected else colors["panel"]))
        painter.restore()

        pen=QPen(QColor(colors["ac"] if selected else "transparent")); pen.setWidthF(2)
        painter.setPen(pen); painter.drawPath(path)

        kind=index.data(ROLE_KIND) or ""
        if kind:
            f=painter.font(); f.setPointSizeF(6.3); painter.setFont(f)
            fm=painter.fontMetrics(); bw=fm.horizontalAdvance(kind)+9; bh=13
            brect=QRectF(thumb.left()+5,thumb.top()+5,bw,bh)
            bpath=QPainterPath(); bpath.addRoundedRect(brect,7,7)
            if kind=="VIDEO":
                painter.fillPath(bpath,QColor(20,18,16,204)); painter.setPen(QColor("#ffffff"))
            else:
                painter.fillPath(bpath,QColor(255,255,255,224)); painter.setPen(QColor("#2b2724"))
            painter.drawText(brect,Qt.AlignCenter,kind)

        name=index.data(Qt.DisplayRole) or ""; dur=index.data(ROLE_BADGE) or ""
        painter.setPen(QColor("#ffffff") if selected else QColor(colors["mut"]))
        f=painter.font(); f.setPointSizeF(7.3); painter.setFont(f)
        fm=painter.fontMetrics(); dw=fm.horizontalAdvance(dur)+4
        name_rect=QRect(foot.left()+6,foot.top(),max(0,foot.width()-12-dw),foot.height())
        dur_rect=QRect(foot.right()-dw-6,foot.top(),dw,foot.height())
        painter.drawText(name_rect,Qt.AlignLeft|Qt.AlignVCenter,
                          fm.elidedText(name,Qt.ElideRight,name_rect.width()))
        painter.drawText(dur_rect,Qt.AlignRight|Qt.AlignVCenter,dur)
        painter.restore()

class TimelinePlayhead(QWidget):
    """Transparent overlay drawn over the timeline viewport: an accent
    vertical line + diamond marker at the preview scrubber's fraction."""
    def __init__(self, window, parent=None):
        super().__init__(parent)
        self._win=window; self._frac=0.0
        self.setAttribute(Qt.WA_TransparentForMouseEvents)

    def set_fraction(self, frac):
        frac=max(0.0,min(1.0,frac))
        if frac==self._frac: return
        self._frac=frac; self.update()

    def paintEvent(self, e):
        if self._frac<=0.0: return
        colors=self._win._colors
        p=QPainter(self); p.setRenderHint(QPainter.Antialiasing)
        x=self._frac*self.width()
        p.setPen(QPen(QColor(colors["ac"]),2)); p.drawLine(QPointF(x,0),QPointF(x,self.height()))
        p.setPen(Qt.NoPen); p.setBrush(QColor(colors["ac"]))
        p.save(); p.translate(x,7); p.rotate(45); p.drawRoundedRect(QRectF(-5,-5,10,10),2,2)
        p.restore(); p.end()

class WaveformBar(QWidget):
    """Decorative music-waveform bar. Matches the mockup's own approach — a
    fixed bar-height sequence, not real audio-amplitude analysis."""
    _HEIGHTS=[8,14,20,12,24,18,10,22,26,16,12,20,28,14,8,18,24,20,12,26,
              16,10,22,14,20,28,18,12,24,16,8,20,14,26,18,12,22,16,10,24,14,8]
    def __init__(self, window, parent=None):
        super().__init__(parent)
        self._win=window
        self.setFixedHeight(28); self.setMinimumWidth(40)
        self.setSizePolicy(QSizePolicy.Expanding,QSizePolicy.Fixed)

    def paintEvent(self, e):
        colors=self._win._colors
        p=QPainter(self); p.setRenderHint(QPainter.Antialiasing)
        n=len(self._HEIGHTS); gap=2.0
        bw=max(1.0,(self.width()-gap*(n-1))/n)
        accent_n=round(n*0.38); x=0.0
        for i,h in enumerate(self._HEIGHTS):
            rect=QRectF(x,self.height()-h,bw,h)
            path=QPainterPath(); path.addRoundedRect(rect,1.4,1.4)
            p.fillPath(path,QColor(colors["ac"] if i<accent_n else colors["bd2"]))
            x+=bw+gap
        p.end()

class MediaList(_FileDropListWidget):
    def __init__(self, window):
        super().__init__()
        self._win=window
        self.setAcceptDrops(True)
        self.setViewMode(QListWidget.IconMode)
        self.setMovement(QListWidget.Static)
        self.setResizeMode(QListWidget.Adjust)
        self.setWrapping(True); self.setFlow(QListWidget.LeftToRight)
        self.setGridSize(MEDIA_CARD_SIZE); self.setSpacing(6)
        self.setSelectionMode(QListWidget.SingleSelection)
        self.setItemDelegate(MediaItemDelegate(window))

class TimelineList(_FileDropListWidget):
    reordered=Signal()
    def __init__(self, window):
        super().__init__()
        self._win=window
        self.setViewMode(QListWidget.IconMode); self.setFlow(QListWidget.LeftToRight)
        self.setWrapping(False); self.setDragDropMode(QListWidget.DragDrop)
        self.setAcceptDrops(True); self.setSelectionMode(QListWidget.SingleSelection)
        self.setGridSize(TIMELINE_CARD_SIZE); self.setSpacing(5)
        self.setFixedHeight(TIMELINE_CARD_SIZE.height()+16)
        self.setItemDelegate(TimelineItemDelegate(window))
        self._playhead=TimelinePlayhead(window,self.viewport())
        self._playhead.setGeometry(self.viewport().rect())
        self._playhead.raise_()
    def _on_internal_drop(self,e):
        super()._on_internal_drop(e); self.reordered.emit()
    def resizeEvent(self,e):
        super().resizeEvent(e)
        self._playhead.setGeometry(self.viewport().rect())
    def set_playhead_fraction(self, frac):
        self._playhead.set_fraction(frac)

class _FramelessRoot(QWidget):
    """Central widget for the frameless main window: the thin transparent
    margin around the visible rounded card doubles as the resize-grab area
    (native title bars normally provide this) — hovering near the outer
    edge shows a resize cursor and a press there kicks off an OS-native
    resize via QWindow.startSystemResize()."""
    MARGIN=6
    def __init__(self, window):
        super().__init__()
        self._win=window
        self.setMouseTracking(True)
        self.setAttribute(Qt.WA_Hover)

    def _edges_at(self, pos):
        m=self.MARGIN; w=self.width(); h=self.height(); edges=Qt.Edges()
        if pos.x()<=m: edges|=Qt.Edge.LeftEdge
        elif pos.x()>=w-m: edges|=Qt.Edge.RightEdge
        if pos.y()<=m: edges|=Qt.Edge.TopEdge
        elif pos.y()>=h-m: edges|=Qt.Edge.BottomEdge
        return edges

    _CURSORS={
        Qt.Edge.LeftEdge:Qt.SizeHorCursor, Qt.Edge.RightEdge:Qt.SizeHorCursor,
        Qt.Edge.TopEdge:Qt.SizeVerCursor, Qt.Edge.BottomEdge:Qt.SizeVerCursor,
    }
    def mouseMoveEvent(self,e):
        if self._win.isMaximized():
            super().mouseMoveEvent(e); return
        edges=self._edges_at(e.position().toPoint())
        if edges in (Qt.Edge.LeftEdge|Qt.Edge.TopEdge, Qt.Edge.RightEdge|Qt.Edge.BottomEdge):
            self.setCursor(Qt.SizeFDiagCursor)
        elif edges in (Qt.Edge.RightEdge|Qt.Edge.TopEdge, Qt.Edge.LeftEdge|Qt.Edge.BottomEdge):
            self.setCursor(Qt.SizeBDiagCursor)
        elif edges & (Qt.Edge.LeftEdge|Qt.Edge.RightEdge):
            self.setCursor(Qt.SizeHorCursor)
        elif edges & (Qt.Edge.TopEdge|Qt.Edge.BottomEdge):
            self.setCursor(Qt.SizeVerCursor)
        else:
            self.unsetCursor()
        super().mouseMoveEvent(e)

    def mousePressEvent(self,e):
        if e.button()==Qt.LeftButton and not self._win.isMaximized():
            edges=self._edges_at(e.position().toPoint())
            if edges:
                wh=self._win.windowHandle()
                if wh: wh.startSystemResize(edges); return
        super().mousePressEvent(e)

class _TitleBar(QFrame):
    """Custom title bar replacing the native one: dragging any blank area
    moves the window (QWindow.startSystemMove()); double-click toggles
    maximize, matching common native title-bar behaviour."""
    def __init__(self, window, parent=None):
        super().__init__(parent)
        self._win=window
    def mousePressEvent(self,e):
        if e.button()==Qt.LeftButton:
            wh=self._win.windowHandle()
            if wh: wh.startSystemMove()
        super().mousePressEvent(e)
    def mouseDoubleClickEvent(self,e):
        if e.button()==Qt.LeftButton:
            self._win._toggle_maximize()
        super().mouseDoubleClickEvent(e)

# ── Main window ───────────────────────────────────────────────────────────────
class ReelForge(QMainWindow):
    def __init__(self):
        super().__init__()
        self.setWindowTitle(APP_NAME)
        self.setWindowFlag(Qt.FramelessWindowHint)
        self.setAttribute(Qt.WA_TranslucentBackground)
        self.resize(1560,960)
        self.theme="dark"; self._colors=THEMES[self.theme]
        self._serif_family=_find_serif_family()
        self._icon_registry=[]
        self.media:list[Clip]=[]; self.timeline:list[Clip]=[]
        self.music_path:str|None=None; self.project_path:str|None=None
        self.worker:ExportWorker|None=None
        self._thumb_cache:dict[str,QIcon]={}
        self._tmpdir=Path(tempfile.mkdtemp(prefix="reelforge_"))
        self._beat_times:list[float]=[]; self._bpm=None
        self._beats_for:str|None=None; self._song_start:float=0.0
        self._undo_stack:list=[]
        self._build_ui()
        self._apply_theme()
        if not FFMPEG: QTimer.singleShot(300,self._warn_no_ffmpeg)

    # ── theming / icon helpers ───────────────────────────────────────────────
    def _serif_font(self, size, bold=True):
        f=QFont(self._serif_family) if self._serif_family else QFont()
        f.setPointSizeF(size); f.setWeight(QFont.DemiBold if bold else QFont.Normal)
        return f

    def _themed_icon(self, widget, name, size=14, color="mut", kind="icon"):
        """Set (and remember) an icon so `_refresh_themed_icons` can re-tint
        it after a theme toggle. `color` is a THEMES palette key, or a
        literal '#rrggbb' for icons that don't follow the palette (e.g. a
        white glyph on a fixed-accent button)."""
        self._icon_registry.append((widget,name,size,color,kind))
        self._apply_icon(widget,name,size,color,kind)

    def _apply_icon(self, widget, name, size, color, kind):
        rgb=color if color.startswith("#") else self._colors[color]
        if kind=="pixmap":
            widget.setPixmap(_svg_pixmap(name,size,rgb))
        else:
            widget.setIcon(_svg_icon(name,size,rgb)); widget.setIconSize(QSize(size,size))

    def _refresh_themed_icons(self):
        for widget,name,size,color,kind in self._icon_registry:
            self._apply_icon(widget,name,size,color,kind)

    def _card(self, object_name=None):
        f=QFrame(); f.setProperty("card",True)
        if object_name: f.setObjectName(object_name)
        return f

    def _panel_header(self, icon_name, title, trailing=None):
        row=QHBoxLayout(); row.setContentsMargins(15,14,15,11); row.setSpacing(9)
        icon=QLabel(); row.addWidget(icon)
        self._themed_icon(icon,icon_name,14,"mut","pixmap")
        lbl=QLabel(title); lbl.setFont(self._serif_font(10.5))
        row.addWidget(lbl); row.addStretch(1)
        if trailing is not None: row.addWidget(trailing)
        return row

    # ── UI build ──────────────────────────────────────────────────────────────
    def _build_ui(self):
        outer=_FramelessRoot(self)
        self._outer_layout=QVBoxLayout(outer)
        self._outer_layout.setContentsMargins(14,14,14,14)

        root_card=self._card("rfRoot")
        self._root_card=root_card
        shadow=QGraphicsDropShadowEffect(root_card)
        shadow.setBlurRadius(60); shadow.setOffset(0,14); shadow.setColor(QColor(0,0,0,150))
        root_card.setGraphicsEffect(shadow)
        card=QVBoxLayout(root_card); card.setContentsMargins(0,0,0,0); card.setSpacing(0)

        card.addWidget(self._build_titlebar())
        card.addWidget(self._build_menu())
        card.addWidget(self._build_auto_band())

        body=QSplitter(Qt.Horizontal)
        body.addWidget(self._build_media_panel())
        body.addWidget(self._build_preview_panel())
        body.addWidget(self._build_inspector_panel())
        body.setStretchFactor(0,0); body.setStretchFactor(1,1); body.setStretchFactor(2,0)
        body_wrap=QWidget(); bwl=QVBoxLayout(body_wrap)
        bwl.setContentsMargins(16,10,16,14); bwl.addWidget(body)
        card.addWidget(body_wrap,1)

        card.addWidget(self._build_timeline_panel())
        self._outer_layout.addWidget(root_card)
        self.setCentralWidget(outer)

        QShortcut(QKeySequence("Ctrl+Z"),self,activated=self.undo)
        del_shortcut=QShortcut(QKeySequence("Delete"),self.timeline_list,activated=self.remove_from_timeline)
        del_shortcut.setContext(Qt.WidgetShortcut)
        for btn in self.findChildren(QPushButton):
            btn.setCursor(Qt.PointingHandCursor)

    def _chrome_btn(self, icon_name):
        b=QPushButton(); b.setObjectName("chromeBtn"); b.setFixedSize(38,32)
        b.setProperty("iconName",icon_name)
        return b

    def _build_titlebar(self):
        bar=_TitleBar(self); bar.setObjectName("titlebar"); bar.setFixedHeight(48)
        lay=QHBoxLayout(bar); lay.setContentsMargins(18,0,8,0); lay.setSpacing(0)

        star=QLabel(); lay.addWidget(star)
        self._themed_icon(star,"star",18,"ac","pixmap")
        lay.addSpacing(11)

        title=QLabel("ReelForge"); title.setObjectName("titleText"); title.setFont(self._serif_font(11.5))
        lay.addWidget(title); lay.addSpacing(12)

        pill=QLabel("reelforge.py"); pill.setObjectName("pathPill")
        lay.addWidget(pill); lay.addStretch(1)

        self.theme_btn=QPushButton(); self.theme_btn.setObjectName("modeBtn")
        self.theme_btn.setToolTip("Switch theme"); self.theme_btn.clicked.connect(self._toggle_theme)
        lay.addWidget(self.theme_btn)

        sep=QFrame(); sep.setObjectName("titleSep"); sep.setFixedSize(1,20)
        lay.addSpacing(6); lay.addWidget(sep); lay.addSpacing(6)

        self.min_btn=self._chrome_btn("minimize"); self.min_btn.setToolTip("Minimize")
        self.min_btn.clicked.connect(self.showMinimized)
        self.max_btn=self._chrome_btn("maximize"); self.max_btn.setToolTip("Maximize")
        self.max_btn.clicked.connect(self._toggle_maximize)
        self.close_btn=self._chrome_btn("close"); self.close_btn.setObjectName("closeBtn")
        self.close_btn.setToolTip("Close"); self.close_btn.clicked.connect(self.close)
        for b in (self.min_btn,self.max_btn,self.close_btn): lay.addWidget(b)

        self._update_mode_button(); self._update_max_icon()
        return bar

    def _update_mode_button(self):
        dark=self.theme=="dark"
        self.theme_btn.setIcon(_svg_icon("sun" if dark else "moon",14,self._colors["mut"]))
        self.theme_btn.setIconSize(QSize(14,14))
        self.theme_btn.setText(" Light" if dark else " Dark")

    def _update_max_icon(self):
        name="restore" if self.isMaximized() else "maximize"
        self.max_btn.setProperty("iconName",name)
        self.max_btn.setIcon(_svg_icon(name,14,self._colors["mut"]))
        self.max_btn.setIconSize(QSize(14,14))

    def _toggle_maximize(self):
        self.showNormal() if self.isMaximized() else self.showMaximized()

    def changeEvent(self, e):
        super().changeEvent(e)
        if e.type()==QEvent.Type.WindowStateChange and hasattr(self,"_outer_layout"):
            maxed=self.isMaximized()
            if maxed: self._outer_layout.setContentsMargins(0,0,0,0)
            else:     self._outer_layout.setContentsMargins(14,14,14,14)
            self._root_card.setProperty("maximized",maxed)
            self._root_card.style().unpolish(self._root_card); self._root_card.style().polish(self._root_card)
            self._update_max_icon()

    def _toggle_theme(self):
        self.theme="light" if self.theme=="dark" else "dark"
        self._colors=THEMES[self.theme]
        self._apply_theme()

    def _build_auto_band(self):
        band=self._card("autoBand")
        bl=QHBoxLayout(band); bl.setContentsMargins(16,14,16,14); bl.setSpacing(16)

        lead=QHBoxLayout(); lead.setSpacing(8)
        lico=QLabel(); self._themed_icon(lico,"lightning",15,"ac","pixmap"); lead.addWidget(lico)
        ltxt=QLabel("Auto Reel"); ltxt.setObjectName("autoLabel"); lead.addWidget(ltxt)
        bl.addLayout(lead)

        vsep=QFrame(); vsep.setObjectName("titleSep"); vsep.setFixedSize(1,22); bl.addWidget(vsep)

        desc=QLabel("Import your media and a song, then hit generate — ReelForge cuts the reel for you.")
        desc.setObjectName("mutedText"); desc.setWordWrap(True)
        bl.addWidget(desc,1)

        self.auto_btn=QPushButton(" Generate reel"); self.auto_btn.setObjectName("primaryBtn")
        self.auto_btn.setMinimumHeight(38)
        self.auto_btn.setToolTip("Auto-order clips, auto-pick mood/transitions, and export in one step")
        self.auto_btn.clicked.connect(self.auto_reel)
        self._themed_icon(self.auto_btn,"lightning",15,"#ffffff","icon")

        self.multi_btn=QPushButton(" 3 versions"); self.multi_btn.setObjectName("ghostBtn")
        self.multi_btn.setMinimumHeight(38)
        self.multi_btn.setToolTip("Render 3 quick variations (different mood + transitions) to compare")
        self.multi_btn.clicked.connect(self.auto_reel_multi)
        self._themed_icon(self.multi_btn,"grid3",14,"tx","icon")

        bl.addWidget(self.auto_btn); bl.addWidget(self.multi_btn)
        return band

    def _build_media_panel(self):
        card=self._card()
        cl=QVBoxLayout(card); cl.setContentsMargins(0,0,0,0); cl.setSpacing(0)
        self.media_count_label=QLabel("0 items"); self.media_count_label.setObjectName("mutedText")
        cl.addLayout(self._panel_header("media_hdr","Media Library",self.media_count_label))

        self.media_list=MediaList(self)
        self.media_list.files_dropped.connect(self.add_files)
        self.media_list.currentRowChanged.connect(self._on_media_selected)
        self.media_list.itemDoubleClicked.connect(lambda _i: self.add_selected_to_timeline())
        body=QWidget(); blay=QVBoxLayout(body); blay.setContentsMargins(13,2,13,14)
        blay.addWidget(self.media_list)
        cl.addWidget(body,1)

        footer=QWidget(); footer.setObjectName("mediaFooter")
        fl=QVBoxLayout(footer); fl.setContentsMargins(13,12,13,13); fl.setSpacing(8)
        row=QHBoxLayout()
        bi=QPushButton(" Import"); bi.setObjectName("ghostBtn")
        bi.setToolTip("Add photos, videos or a song from disk")
        bi.clicked.connect(self.import_dialog); self._themed_icon(bi,"import",14,"tx","icon")
        ba=QPushButton("Add "); ba.setObjectName("ghostBtn")
        ba.setToolTip("Add the selected clip to the timeline (or double-click it)")
        ba.clicked.connect(self.add_selected_to_timeline); self._themed_icon(ba,"add",14,"tx","icon")
        row.addWidget(bi); row.addWidget(ba); fl.addLayout(row)
        ball=QPushButton("Add all to timeline"); ball.setObjectName("ghostBtn")
        ball.setToolTip("Add every clip in the media library to the timeline, in order")
        ball.clicked.connect(self.add_all_to_timeline)
        fl.addWidget(ball)
        cl.addWidget(footer)

        card.setMinimumWidth(280); card.setMaximumWidth(320)
        return card

    def _build_preview_panel(self):
        card=self._card()
        cl=QVBoxLayout(card); cl.setContentsMargins(0,0,0,0); cl.setSpacing(0)
        self.preview_meta_label=QLabel(""); self.preview_meta_label.setObjectName("mutedText")
        cl.addLayout(self._panel_header("preview_hdr","Preview",self.preview_meta_label))

        stage=QWidget(); stage.setObjectName("previewStage")
        sl=QVBoxLayout(stage); sl.setContentsMargins(22,10,22,10)
        self.preview_stack=QStackedWidget(); self.preview_stack.setObjectName("previewBox")
        self.image_label=QLabel("Drop photos, videos and a song to start.\n\nAdd clips to the timeline then Export.")
        self.image_label.setAlignment(Qt.AlignCenter); self.image_label.setWordWrap(True)
        self.image_label.setObjectName("previewPlaceholder")
        self.video_widget=QVideoWidget()
        self.preview_stack.addWidget(self.image_label)
        self.preview_stack.addWidget(self.video_widget)
        sl.addWidget(self.preview_stack,1)
        cl.addWidget(stage,1)

        self.player=QMediaPlayer(); self.audio_out=QAudioOutput()
        self.player.setAudioOutput(self.audio_out)
        self.player.setVideoOutput(self.video_widget)
        self.player.positionChanged.connect(self._on_player_position)
        self.player.durationChanged.connect(self._on_player_duration)

        ctl=QWidget(); ctl.setObjectName("previewControls")
        cbl=QHBoxLayout(ctl); cbl.setContentsMargins(16,13,16,13); cbl.setSpacing(14)
        self.play_btn=QPushButton(); self.play_btn.setObjectName("playBtn"); self.play_btn.setFixedSize(40,40)
        self.play_btn.setIcon(_svg_icon("play",15,"#ffffff")); self.play_btn.setIconSize(QSize(15,15))
        self.play_btn.clicked.connect(self._toggle_play)
        cbl.addWidget(self.play_btn)
        self.seek_start_label=QLabel("00:00"); self.seek_start_label.setObjectName("mutedText")
        self.seek=QSlider(Qt.Horizontal); self.seek.sliderMoved.connect(self.player.setPosition)
        self.seek_end_label=QLabel("00:00"); self.seek_end_label.setObjectName("mutedText")
        cbl.addWidget(self.seek_start_label); cbl.addWidget(self.seek,1); cbl.addWidget(self.seek_end_label)
        cl.addWidget(ctl)
        return card

    @staticmethod
    def _fmt_time(ms):
        s=int(max(0,ms)/1000); m,s=divmod(s,60)
        return f"{m:02d}:{s:02d}"

    def _on_player_position(self, p):
        self.seek.setValue(p)
        self.seek_start_label.setText(self._fmt_time(p))
        dur=self.player.duration()
        self.timeline_list.set_playhead_fraction((p/dur) if dur>0 else 0.0)

    def _on_player_duration(self, d):
        self.seek.setRange(0,d)
        self.seek_end_label.setText(self._fmt_time(d))

    def _build_timeline_panel(self):
        card=self._card()
        cl=QVBoxLayout(card); cl.setContentsMargins(0,0,0,0); cl.setSpacing(0)

        header=QHBoxLayout(); header.setContentsMargins(16,12,16,11); header.setSpacing(10)
        hico=QLabel(); self._themed_icon(hico,"timeline_hdr",14,"mut","pixmap"); header.addWidget(hico)
        htxt=QLabel("Timeline"); htxt.setFont(self._serif_font(10.5)); header.addWidget(htxt)
        hint=QLabel("Drag clips to reorder — or drop from Explorer"); hint.setObjectName("mutedText")
        header.addWidget(hint); header.addStretch(1)
        brem=QPushButton(" Remove"); brem.setObjectName("ghostBtn")
        brem.setToolTip("Remove the selected timeline clip (Del)"); brem.clicked.connect(self.remove_from_timeline)
        self._themed_icon(brem,"remove",13,"tx","icon")
        bclr=QPushButton("Clear"); bclr.setObjectName("ghostBtn")
        bclr.setToolTip("Remove every clip from the timeline"); bclr.clicked.connect(self.clear_timeline)
        bund=QPushButton(" Undo"); bund.setObjectName("ghostBtn")
        bund.setToolTip("Undo the last timeline change (Ctrl+Z)"); bund.clicked.connect(self.undo)
        self._themed_icon(bund,"undo",13,"tx","icon")
        for b in (brem,bclr,bund): header.addWidget(b)
        cl.addLayout(header)

        self.timeline_list=TimelineList(self)
        self.timeline_list.reordered.connect(self._sync_timeline_order)
        self.timeline_list.currentRowChanged.connect(self._on_timeline_selected)
        self.timeline_list.files_dropped.connect(self._timeline_files_dropped)
        body=QWidget(); body.setObjectName("timelineTrack")
        blay=QVBoxLayout(body); blay.setContentsMargins(16,0,16,0); blay.addWidget(self.timeline_list)
        cl.addWidget(body,1)

        footer=QWidget(); footer.setObjectName("timelineFooter")
        fl=QHBoxLayout(footer); fl.setContentsMargins(16,9,16,9)
        self.clip_count_label=QLabel("0 clips · 0 audio tracks"); self.clip_count_label.setObjectName("mutedText")
        fl.addWidget(self.clip_count_label); fl.addStretch(1)
        totlbl=QLabel("Total"); totlbl.setObjectName("mutedText")
        self.total_label=QLabel("0:00.0"); self.total_label.setFont(self._serif_font(12.5))
        fl.addWidget(totlbl); fl.addWidget(self.total_label)
        cl.addWidget(footer)
        return card

    def _build_inspector_panel(self):
        card=self._card()
        cl=QVBoxLayout(card); cl.setContentsMargins(0,0,0,0); cl.setSpacing(0)
        cl.addLayout(self._panel_header("inspector_hdr","Inspector"))

        tabs=QTabWidget(); tabs.setObjectName("pillTabs"); tabs.setDocumentMode(True)

        # ── TAB 1: Clip ──────────────────────────────────────────────────────
        clip_tab=QWidget(); ctv=QVBoxLayout(clip_tab); ctv.setContentsMargins(0,0,0,0); ctv.setSpacing(12)

        info_row=QWidget(); info_row.setObjectName("clipInfoRow")
        irl=QHBoxLayout(info_row); irl.setContentsMargins(11,10,11,10); irl.setSpacing(10)
        avatar=QLabel(); avatar.setObjectName("clipAvatar"); avatar.setFixedSize(30,30)
        avatar.setAlignment(Qt.AlignCenter)
        self._themed_icon(avatar,"preview_hdr",14,"#ffffff","pixmap")
        irl.addWidget(avatar)
        info_col=QVBoxLayout(); info_col.setSpacing(0)
        cap=QLabel("Selected clip"); cap.setObjectName("mutedText")
        self.insp_name=QLabel("—")
        self.insp_name.setTextInteractionFlags(Qt.NoTextInteraction)
        self.insp_name.setSizePolicy(QSizePolicy.Ignored,QSizePolicy.Fixed)
        info_col.addWidget(cap); info_col.addWidget(self.insp_name)
        irl.addLayout(info_col,1)
        ctv.addWidget(info_row)

        form_host=QWidget(); cf=QFormLayout(form_host)
        self.photo_dur_spin=self._dspin(0.5,60,0.5,DEFAULT_PHOTO_DUR," s",self._update_selected_clip)
        self.video_max_spin=self._dspin(1,120,1,DEFAULT_VIDEO_MAX," s",self._update_selected_clip)
        self.trim_start_spin=self._dspin(0,3600,1,0," s in",self._update_selected_clip)
        self.speed_spin=self._dspin(0.25,4,0.25,1,"×",self._update_selected_clip)
        self.crop_bias_combo=QComboBox()
        self.crop_bias_combo.addItems(["center","top","bottom","blur_bg"])
        self.crop_bias_combo.currentTextChanged.connect(self._update_selected_clip)
        cf.addRow("Photo length:",self.photo_dur_spin)
        cf.addRow("Video max:",self.video_max_spin)
        cf.addRow("Start at:",self.trim_start_spin)
        cf.addRow("Speed:",self.speed_spin)
        cf.addRow("Crop / fill:",self.crop_bias_combo)

        # Music inline
        mbox=QGroupBox("Music")
        mf=QVBoxLayout(mbox)
        self.music_label=QLabel("No song selected"); self.music_label.setWordWrap(True)
        self.waveform_bar=WaveformBar(self)
        bm=QPushButton(" Choose song…"); bm.setObjectName("ghostBtn"); bm.clicked.connect(self.choose_music)
        self._themed_icon(bm,"music_hdr",13,"tx","icon")
        mf.addWidget(self.music_label); mf.addWidget(self.waveform_bar); mf.addWidget(bm)
        cf.addRow(mbox)
        ctv.addWidget(form_host)
        ctv.addStretch(1)
        tabs.addTab(clip_tab,"Clip")

        # ── TAB 2: Video ────────────────────────────────────────────────────
        vid_tab=QWidget(); vf=QFormLayout(vid_tab)

        arr_box=QGroupBox("Smart arrange")
        af2=QFormLayout(arr_box)
        self.arrange_combo=QComboBox()
        self.arrange_combo.addItems(["Manual (my order)","By date taken","Visual flow","Shuffle"])
        b_arr=QPushButton("Apply order"); b_arr.clicked.connect(self.apply_arrange)
        af2.addRow("Order:",self.arrange_combo); af2.addRow(b_arr)
        vf.addRow(arr_box)

        tr_box=QGroupBox("Transitions")
        tf=QFormLayout(tr_box)
        self.trans_combo=QComboBox(); self.trans_combo.addItems(TRANSITION_CHOICES)
        self.trans_combo.setCurrentText("random")
        self.trans_dur_spin=self._dspin(0.1,2,0.1,0.6," s")
        tf.addRow("Style:",self.trans_combo); tf.addRow("Length:",self.trans_dur_spin)
        vf.addRow(tr_box)

        ex_box=QGroupBox("Export")
        ef=QFormLayout(ex_box)
        self.preset_combo=QComboBox(); self.preset_combo.addItems(EXPORT_PRESETS.keys())
        self.fps_combo=QComboBox(); self.fps_combo.addItems(["30","60"])
        self.quality_combo=QComboBox(); self.quality_combo.addItems(QUALITY_PRESETS.keys())
        self.quality_combo.setCurrentText("High (recommended)")
        self.mood_combo=QComboBox(); self.mood_combo.addItems(COLOUR_MOODS.keys())
        self.hold_spin=self._dspin(0,3,0.25,0," s")
        self.target_dur_check=QCheckBox("Target length")
        self.target_dur_spin=self._dspin(5,600,5,30," s")
        self.target_dur_spin.setEnabled(False)
        self.target_dur_check.toggled.connect(self.target_dur_spin.setEnabled)
        trow=QHBoxLayout(); trow.addWidget(self.target_dur_check); trow.addWidget(self.target_dur_spin)
        self.zoom_check=QCheckBox("Ken Burns zoom on photos")
        self.gpu_check=QCheckBox("Use GPU (NVENC) — faster")
        self.gpu_check.setChecked(nvenc_available()); self.gpu_check.setEnabled(nvenc_available())
        self.dated_name_check=QCheckBox("Include date in filename"); self.dated_name_check.setChecked(True)
        ef.addRow("Format:",self.preset_combo); ef.addRow("FPS:",self.fps_combo)
        ef.addRow("Quality:",self.quality_combo); ef.addRow("Colour mood:",self.mood_combo)
        ef.addRow("Intro/outro hold:",self.hold_spin)
        ef.addRow(trow); ef.addRow(self.zoom_check)
        ef.addRow(self.gpu_check); ef.addRow(self.dated_name_check)
        vf.addRow(ex_box)
        tabs.addTab(vid_tab,"Video")

        # ── TAB 3: Audio ────────────────────────────────────────────────────
        aud_tab=QWidget(); auf=QFormLayout(aud_tab)

        aud_box=QGroupBox("Music")
        aff=QFormLayout(aud_box)
        self.fadein_spin=self._dspin(0,10,0.5,1," s")
        self.fadeout_spin=self._dspin(0,10,0.5,2.5," s")
        self.loudnorm_check=QCheckBox("Normalise loudness (~-14 LUFS)")
        aff.addRow("Fade in:",self.fadein_spin); aff.addRow("Fade out:",self.fadeout_spin)
        aff.addRow(self.loudnorm_check)
        self.clip_audio_check=QCheckBox("Include original clip audio")
        self.clip_audio_check.setChecked(True)
        self.clip_audio_check.setToolTip("Mix each video clip's own audio in alongside the music")
        self.duck_check=QCheckBox("Duck music under clip audio")
        self.duck_check.setChecked(True)
        self.duck_check.toggled.connect(lambda on: self.duck_db_spin.setEnabled(on))
        self.clip_audio_check.toggled.connect(self.duck_check.setEnabled)
        self.duck_db_spin=self._dspin(-40,0,1,-12," dB")
        aff.addRow(self.clip_audio_check)
        aff.addRow(self.duck_check)
        aff.addRow("Duck amount:",self.duck_db_spin)
        auf.addRow(aud_box)

        beat_box=QGroupBox("Beat sync  (needs librosa)")
        bf=QFormLayout(beat_box)
        self.beatsync_check=QCheckBox("Cut on the beat")
        self.cut_combo=QComboBox()
        self.cut_combo.addItems(["Every beat","Every 2 beats","Every bar (4)","Every 2 bars (8)"])
        self.cut_combo.setCurrentText("Every bar (4)")
        self.bpm_label=QLabel("BPM: —")
        b_beats=QPushButton("Analyze song"); b_beats.clicked.connect(self.analyze_song)
        bf.addRow(self.beatsync_check); bf.addRow("Cut:",self.cut_combo)
        bf.addRow(self.bpm_label); bf.addRow(b_beats)
        auf.addRow(beat_box)

        song_box=QGroupBox("Song intelligence")
        sf=QFormLayout(song_box)
        self.smart_start_check=QCheckBox("Auto-find best start point")
        self.smart_start_check.setChecked(True)
        self.song_start_label=QLabel("Start: 0.0s  (not analysed yet)")
        self.song_start_label.setWordWrap(True)
        b_start=QPushButton("Find best start"); b_start.clicked.connect(self.find_song_start)
        self.loop_to_fill_check=QCheckBox("Loop clips to fill song")
        self.loop_to_fill_check.setChecked(True)
        sf.addRow(self.smart_start_check); sf.addRow(self.song_start_label)
        sf.addRow(b_start); sf.addRow(self.loop_to_fill_check)
        auf.addRow(song_box)
        tabs.addTab(aud_tab,"Audio")

        # ── TAB 4: Text overlays ────────────────────────────────────────────
        txt_tab=QWidget(); ttf=QFormLayout(txt_tab)

        title_box=QGroupBox("Title  (shown at start)")
        tlf=QFormLayout(title_box)
        self.title_edit=QLineEdit(); self.title_edit.setPlaceholderText("e.g.  Summer 2025")
        tlf.addRow("Text:",self.title_edit)
        ttf.addRow(title_box)

        cta_box=QGroupBox("Call to action  (shown at end)")
        clf=QFormLayout(cta_box)
        self.cta_edit=QLineEdit(); self.cta_edit.setPlaceholderText("e.g.  Follow @yourhandle")
        clf.addRow("Text:",self.cta_edit)
        ttf.addRow(cta_box)

        style_box=QGroupBox("Text style")
        stf=QFormLayout(style_box)
        self.text_size_spin=QSpinBox(); self.text_size_spin.setRange(24,200); self.text_size_spin.setValue(64)
        self.text_pos_combo=QComboBox(); self.text_pos_combo.addItems(TEXT_POSITIONS.keys())
        self.text_color_combo=QComboBox()
        self.text_color_combo.addItems(["white","yellow","black","cyan","red"])
        self.text_shadow_check=QCheckBox("Drop shadow"); self.text_shadow_check.setChecked(True)
        stf.addRow("Font size:",self.text_size_spin)
        stf.addRow("Title position:",self.text_pos_combo)
        stf.addRow("Colour:",self.text_color_combo)
        stf.addRow(self.text_shadow_check)
        ttf.addRow(style_box)
        tabs.addTab(txt_tab,"Text")

        scroll=QScrollArea(); scroll.setWidgetResizable(True); scroll.setWidget(tabs)
        scroll.setFrameShape(QFrame.NoFrame)
        body=QWidget(); bodyl=QVBoxLayout(body); bodyl.setContentsMargins(15,4,15,4); bodyl.addWidget(scroll)
        cl.addWidget(body,1)

        # export controls — always visible, pinned below the scrollable tabs
        footer=QWidget(); fl=QVBoxLayout(footer); fl.setContentsMargins(15,10,15,14); fl.setSpacing(8)
        self.preview_btn=QPushButton(" Preview draft")
        self.preview_btn.setObjectName("ghostBtn")
        self.preview_btn.setMinimumHeight(36)
        self.preview_btn.setToolTip("Quick low-res render (360p, ultrafast) played in the preview pane —\n"
                                    "see transitions, mood, text and timing without a full export")
        self.preview_btn.clicked.connect(self.preview_draft)
        self._themed_icon(self.preview_btn,"play",12,"tx","icon")
        fl.addWidget(self.preview_btn)
        self.export_btn=QPushButton("Export Reel")
        self.export_btn.setObjectName("primaryBtn")
        self.export_btn.setMinimumHeight(44)
        self.export_btn.setToolTip("Render the timeline with the settings from these tabs (Ctrl+E)")
        self.export_btn.clicked.connect(self.export_reel)
        fl.addWidget(self.export_btn)
        self.progress=QProgressBar(); self.progress.setValue(0); fl.addWidget(self.progress)
        self.status_label=QLabel("Ready"+(""if FFMPEG else"  —  FFmpeg not found"))
        self.status_label.setObjectName("mutedText")
        fl.addWidget(self.status_label)
        cl.addWidget(footer)

        card.setMinimumWidth(300); card.setMaximumWidth(360)
        return card

    def _dspin(self,mn,mx,step,val,suffix="",slot=None):
        s=QDoubleSpinBox(); s.setRange(mn,mx); s.setSingleStep(step)
        s.setValue(val); s.setSuffix(suffix)
        if slot: s.valueChanged.connect(slot)
        return s

    def _build_menu(self):
        bar=QMenuBar(); bar.setObjectName("menuBar")
        fm=bar.addMenu("File")
        for txt,slot,shortcut in [
            ("Import media…",self.import_dialog,None),("Choose song…",self.choose_music,None),(None,None,None),
            ("Open project…",self.open_project,"Ctrl+O"),("Save project",self.save_project,"Ctrl+S"),
            ("Save project as…",self.save_project_as,"Ctrl+Shift+S"),(None,None,None),
            ("Export reel…",self.export_reel,"Ctrl+E"),(None,None,None),("Quit",self.close,"Ctrl+Q")]:
            if txt is None: fm.addSeparator(); continue
            a=QAction(txt,self); a.triggered.connect(slot)
            if shortcut: a.setShortcut(QKeySequence(shortcut))
            fm.addAction(a)
        hm=bar.addMenu("Help")
        ab=QAction("About",self); ab.triggered.connect(self._about); hm.addAction(ab)
        return bar

    def _make_arrow_icon(self,direction):
        """Render a small chevron PNG for the spin-box up/down buttons — Qt
        stops drawing its own arrow once the button subcontrol is stylesheet-
        customized, so we supply one, coloured for the current theme (the
        filename bakes in the theme name so QSS doesn't cache a stale colour
        under the same url() after a toggle)."""
        pm=_svg_pixmap("chevron_up" if direction=="up" else "chevron_down",10,self._colors["mut"])
        path=self._tmpdir/f"arrow_{direction}_{self.theme}.png"
        pm.save(str(path))
        return str(path).replace("\\","/")

    def _apply_theme(self):
        c=self._colors
        up_arrow=self._make_arrow_icon("up"); down_arrow=self._make_arrow_icon("down")
        radius=0 if self.isMaximized() else 16
        qss=f"""
            QWidget{{color:{c['tx']};font-family:'Segoe UI',Arial;font-size:13px}}
            QMainWindow{{background:transparent}}
            QFrame[card="true"]{{background:{c['panel']};border:1px solid {c['bd']};border-radius:13px}}
            QFrame#rfRoot{{background:{c['bg']};border:1px solid {c['frame']};border-radius:{radius}px}}
            QFrame#rfRoot[maximized="true"]{{border-radius:0px;border:none}}
            QFrame#titlebar{{background:transparent;border:none}}
            QLabel#titleText{{font-weight:600}}
            QLabel#pathPill{{color:{c['mut']};font-size:10px;border:1px solid {c['bd']};
                              border-radius:10px;padding:3px 9px}}
            QLabel#mutedText{{color:{c['mut']};font-size:11px}}
            QLabel#autoLabel{{color:{c['ac']};font-weight:600;font-size:13px}}
            QFrame#titleSep{{background:{c['bd']};border:none}}
            QPushButton#modeBtn,QPushButton#chromeBtn{{background:transparent;border:none;
                              border-radius:8px;color:{c['mut']};padding:0 10px;min-height:0}}
            QPushButton#modeBtn:hover,QPushButton#chromeBtn:hover{{background:{c['hov']}}}
            QPushButton#closeBtn:hover{{background:{c['ac']};color:#ffffff}}
            QMenuBar#menuBar{{background:transparent;padding:2px 14px;border:none;font-size:12px}}
            QMenuBar#menuBar::item{{background:transparent;padding:4px 10px;border-radius:6px}}
            QMenuBar#menuBar::item:selected{{background:{c['hov']}}}
            QMenu{{background:{c['panel']};color:{c['tx']};border:1px solid {c['bd']}}}
            QMenu::item:selected{{background:{c['ac']};color:#ffffff}}

            QPushButton{{background:{c['inset']};border:1px solid {c['bd']};border-radius:9px;
                         padding:8px 14px;min-height:18px}}
            QPushButton:hover{{background:{c['hov2']};border-color:{c['bd2']}}}
            QPushButton:pressed{{background:{c['inset']}}}
            QPushButton:disabled{{color:{c['mut']};border-color:{c['bd']}}}
            QPushButton#ghostBtn{{background:{c['inset']};border:1px solid {c['bd']}}}
            QPushButton#ghostBtn:hover{{background:{c['hov2']};border-color:{c['bd2']}}}
            QPushButton#primaryBtn{{background:{c['ac']};color:#ffffff;border:none;font-weight:600}}
            QPushButton#primaryBtn:hover{{background:{c['ac2']}}}
            QPushButton#primaryBtn:disabled{{background:{c['bd']};color:{c['mut']}}}
            QPushButton#playBtn{{background:{c['ac']};border:none;border-radius:20px}}
            QPushButton#playBtn:hover{{background:{c['ac2']}}}

            QLabel#clipAvatar{{background:{c['ac']};border-radius:7px}}
            QWidget#clipInfoRow{{background:{c['inset']};border-radius:9px}}
            QWidget#mediaFooter,QWidget#timelineFooter{{border-top:1px solid {c['bd']}}}
            QWidget#previewStage{{background:{c['inset']}}}
            QStackedWidget#previewBox{{background:#131211;border-radius:12px}}
            QLabel#previewPlaceholder{{color:{c['mut']}}}

            QTabWidget#pillTabs::pane{{border:none;margin-top:6px}}
            QTabWidget#pillTabs::tab-bar{{alignment:left}}
            QTabBar::tab{{background:transparent;color:{c['mut']};padding:9px 6px;margin-right:4px;
                          border:none;border-radius:8px;min-width:56px}}
            QTabBar::tab:selected{{background:{c['ac']};color:#ffffff;font-weight:600}}
            QTabBar::tab:hover:!selected{{background:{c['hov']}}}

            QListWidget{{background:transparent;border:none}}
            QListWidget::item{{border:none}}
            QListWidget::item:selected{{background:transparent}}

            QGroupBox{{border:1px solid {c['bd']};border-radius:9px;margin-top:12px;padding:10px;
                       background:{c['inset']}}}
            QGroupBox::title{{subcontrol-origin:margin;left:8px;padding:0 4px;color:{c['mut']}}}

            QProgressBar{{border:1px solid {c['bd']};border-radius:6px;text-align:center;
                          background:{c['inset']};height:16px;color:{c['tx']}}}
            QProgressBar::chunk{{background:{c['ac']};border-radius:5px}}

            QComboBox,QDoubleSpinBox,QSpinBox,QLineEdit{{background:{c['inset']};
                          border:1px solid {c['bd']};border-radius:8px;padding:4px 8px}}
            QComboBox:hover,QDoubleSpinBox:hover,QSpinBox:hover,QLineEdit:hover{{border-color:{c['bd2']}}}
            QComboBox::drop-down{{border:none;width:22px}}
            QComboBox QAbstractItemView{{background:{c['panel']};border:1px solid {c['bd']};
                          selection-background-color:{c['ac']};selection-color:#ffffff}}
            QDoubleSpinBox,QSpinBox{{min-height:26px;padding-right:22px}}
            QDoubleSpinBox::up-button,QSpinBox::up-button{{
                subcontrol-origin:border;subcontrol-position:top right;
                width:20px;height:14px;border:none;background:transparent}}
            QDoubleSpinBox::down-button,QSpinBox::down-button{{
                subcontrol-origin:border;subcontrol-position:bottom right;
                width:20px;height:14px;border:none;background:transparent}}
            QDoubleSpinBox::up-button:hover,QSpinBox::up-button:hover,
            QDoubleSpinBox::down-button:hover,QSpinBox::down-button:hover{{background:{c['hov']}}}

            QSlider::groove:horizontal{{height:5px;background:{c['bd']};border-radius:2px}}
            QSlider::sub-page:horizontal{{background:{c['ac']};border-radius:2px}}
            QSlider::handle:horizontal{{background:{c['ac']};width:13px;margin:-4px 0;border-radius:6px}}

            QScrollArea{{background:transparent;border:none}}
            QScrollBar:vertical{{background:transparent;width:10px;margin:2px}}
            QScrollBar::handle:vertical{{background:{c['bd2']};border-radius:5px;min-height:24px}}
            QScrollBar::add-line:vertical,QScrollBar::sub-line:vertical{{height:0px}}
            QScrollBar:horizontal{{background:transparent;height:10px;margin:2px}}
            QScrollBar::handle:horizontal{{background:{c['bd2']};border-radius:5px;min-width:24px}}
            QScrollBar::add-line:horizontal,QScrollBar::sub-line:horizontal{{width:0px}}
        """ + (
            f"QDoubleSpinBox::up-arrow,QSpinBox::up-arrow{{image:url({up_arrow});width:8px;height:8px}}"
            f"QDoubleSpinBox::down-arrow,QSpinBox::down-arrow{{image:url({down_arrow});width:8px;height:8px}}"
        )
        self.setStyleSheet(qss)
        self._refresh_themed_icons()
        self._update_mode_button(); self._update_max_icon()
        for name in ("media_list","timeline_list","waveform_bar"):
            w=getattr(self,name,None)
            if w is None: continue
            (w.viewport() if hasattr(w,"viewport") else w).update()
        if hasattr(self,"timeline_list"): self.timeline_list._playhead.update()

    # ── thumbnails ────────────────────────────────────────────────────────────
    def _icon_for(self,clip):
        if clip.path in self._thumb_cache: return self._thumb_cache[clip.path]
        icon=None
        if clip.kind=="photo":
            if _pil_available():
                # Use Pillow so EXIF rotation is honoured automatically
                try:
                    from PIL import Image, ImageOps, ImageQt
                    img=Image.open(clip.path)
                    img=ImageOps.exif_transpose(img)  # auto-rotate per EXIF
                    img.thumbnail((THUMB_SIZE.width()*2, THUMB_SIZE.height()*2))
                    # Convert PIL → QPixmap via bytes
                    from PySide6.QtGui import QImage
                    img=img.convert("RGB")
                    data=img.tobytes("raw","RGB")
                    qimg=QImage(data,img.width,img.height,img.width*3,QImage.Format_RGB888)
                    pm=QPixmap.fromImage(qimg)
                    if not pm.isNull():
                        icon=QIcon(pm.scaled(THUMB_SIZE,Qt.KeepAspectRatio,Qt.SmoothTransformation))
                except Exception:
                    pass
            if icon is None:
                pm=QPixmap(clip.path)
                if not pm.isNull():
                    icon=QIcon(pm.scaled(THUMB_SIZE,Qt.KeepAspectRatio,Qt.SmoothTransformation))
        else:
            dst=self._tmpdir/(str(abs(hash(clip.path)))+".jpg")
            # ImportWorker usually pre-builds this thumb in the background;
            # only shell out to ffmpeg if it isn't there yet
            if dst.exists() or make_video_thumb(clip.path,str(dst)):
                pm=QPixmap(str(dst))
                if not pm.isNull(): icon=QIcon(pm.scaled(THUMB_SIZE,Qt.KeepAspectRatio,Qt.SmoothTransformation))
        if icon is None:
            icon=self.style().standardIcon(QStyle.SP_FileIcon if clip.kind=="photo" else QStyle.SP_MediaPlay)
        self._thumb_cache[clip.path]=icon; return icon

    # ── import ────────────────────────────────────────────────────────────────
    def import_dialog(self):
        exts=" ".join("*"+e for e in (PHOTO_EXTS|VIDEO_EXTS|AUDIO_EXTS))
        files,_=QFileDialog.getOpenFileNames(self,"Import media","",f"Media files ({exts});;All files (*)")
        if files: self.add_files(files)

    def add_files(self,paths):
        """Probe durations and build thumbnails in the background so importing
        many files doesn't freeze the window; new files dropped while an import
        is already running are queued and picked up when it finishes."""
        if getattr(self,"_import_worker",None) and self._import_worker.isRunning():
            self._pending_import_paths=getattr(self,"_pending_import_paths",[])+list(paths)
            return
        self.status_label.setText("Importing…")
        existing=[c.path for c in self.media]
        self._import_worker=ImportWorker(paths,existing,self._tmpdir)
        self._import_worker.clip_found.connect(self._on_clip_imported)
        self._import_worker.music_found.connect(self._set_music)
        self._import_worker.finished_all.connect(self._on_import_finished)
        self._import_worker.start()

    def _on_clip_imported(self,clip):
        self.media.append(clip); self._add_media_row(clip)
        pending=getattr(self,"_add_to_timeline_after_import",None)
        if pending and clip.path in pending:
            pending.discard(clip.path); self._append_timeline(clip)

    def _on_import_finished(self,added):
        self.status_label.setText(f"Imported {added} file(s)" if added else "Ready")
        pending=getattr(self,"_pending_import_paths",None)
        if pending:
            self._pending_import_paths=[]
            self.add_files(pending)

    @staticmethod
    def _media_badge(clip):
        if clip.kind=="photo": return "PHOTO"
        m,s=divmod(int(clip.duration or 0),60)
        return f"{m}:{s:02d}"

    def _add_media_row(self,clip):
        item=QListWidgetItem(self._icon_for(clip),clip.name)
        item.setData(Qt.UserRole,id(clip))
        item.setData(ROLE_KIND,clip.kind)
        item.setData(ROLE_BADGE,self._media_badge(clip))
        self.media_list.addItem(item)
        item.setToolTip(clip.path)
        self.media_count_label.setText(f"{len(self.media)} item"+("s" if len(self.media)!=1 else ""))

    def _make_timeline_item(self,clip):
        item=QListWidgetItem(self._icon_for(clip),clip.name)
        item.setData(Qt.UserRole,id(clip))
        item.setData(ROLE_KIND,"VIDEO" if clip.kind=="video" else "PHOTO")
        item.setData(ROLE_BADGE,f"{clip.render_duration():.1f}s")
        item.setToolTip(clip.path)
        return item

    def _set_music(self,path):
        self.music_path=path
        self.music_label.setText("♪ "+Path(path).name)
        if self._beats_for!=path:
            self._beat_times=[]; self._bpm=None; self._beats_for=None; self._song_start=0.0
            if hasattr(self,"bpm_label"): self.bpm_label.setText("BPM: —")
            if hasattr(self,"song_start_label"): self.song_start_label.setText("Start: 0.0s  (not analysed yet)")
        self._update_total()

    def choose_music(self):
        exts=" ".join("*"+e for e in AUDIO_EXTS)
        f,_=QFileDialog.getOpenFileName(self,"Choose song","",f"Audio ({exts});;All files (*)")
        if f: self._set_music(f)

    # ── timeline ──────────────────────────────────────────────────────────────
    def add_selected_to_timeline(self):
        row=self.media_list.currentRow()
        if 0<=row<len(self.media): self._append_timeline(self.media[row])

    def add_all_to_timeline(self):
        for c in self.media: self._append_timeline(c)

    def _timeline_files_dropped(self,paths):
        """Files dragged directly from Explorer onto the timeline — import
        them (async) and, as each one lands, add it to the timeline too."""
        already=[c.path for c in self.media]
        pending=getattr(self,"_add_to_timeline_after_import",set())
        for p in paths:
            if classify(p) in ("photo","video"):
                if p in already:
                    matches=[c for c in self.media if c.path==p]
                    if matches: self._append_timeline(matches[0])
                else:
                    pending.add(p)
        self._add_to_timeline_after_import=pending
        self.add_files(paths)

    def _append_timeline(self,src):
        self._push_undo()
        clip=Clip(path=src.path,kind=src.kind,duration=src.duration,
                  photo_dur=src.photo_dur,video_max=src.video_max)
        self.timeline.append(clip)
        self.timeline_list.addItem(self._make_timeline_item(clip)); self._update_total()

    def remove_from_timeline(self):
        row=self.timeline_list.currentRow()
        if 0<=row<len(self.timeline):
            self._push_undo()
            self.timeline_list.takeItem(row); del self.timeline[row]; self._update_total()

    def clear_timeline(self):
        self._push_undo(); self.timeline_list.clear(); self.timeline.clear(); self._update_total()

    def _rebuild_timeline_widget(self):
        self.timeline_list.clear()
        for clip in self.timeline:
            self.timeline_list.addItem(self._make_timeline_item(clip))
        self._update_total()

    def _sync_timeline_order(self):
        by_id={id(c):c for c in self.timeline}; new=[]
        for i in range(self.timeline_list.count()):
            cid=self.timeline_list.item(i).data(Qt.UserRole)
            if cid in by_id: new.append(by_id[cid])
        self.timeline=new; self._update_total()

    def _update_total(self):
        total=sum(c.render_duration() for c in self.timeline)
        m,s=divmod(total,60)
        self.total_label.setText(f"{int(m)}:{s:04.1f}")
        n=len(self.timeline)
        tracks=1 if self.music_path else 0
        self.clip_count_label.setText(
            f"{n} clip{'s' if n!=1 else ''} · {tracks} audio track{'s' if tracks!=1 else ''}")

    def _refresh_timeline_label(self,row):
        if 0<=row<self.timeline_list.count():
            c=self.timeline[row]
            item=self.timeline_list.item(row)
            item.setData(ROLE_BADGE,f"{c.render_duration():.1f}s")
            self.timeline_list.viewport().update()

    # ── undo ──────────────────────────────────────────────────────────────────
    def _push_undo(self):
        self._undo_stack.append([asdict(c) for c in self.timeline])
        if len(self._undo_stack)>30: self._undo_stack.pop(0)

    def undo(self):
        if not self._undo_stack: return
        state=self._undo_stack.pop()
        self.timeline=[Clip(**{k:d[k] for k in d if k in Clip.__dataclass_fields__}) for d in state]
        self._rebuild_timeline_widget()

    # ── arrange ───────────────────────────────────────────────────────────────
    def apply_arrange(self):
        if len(self.timeline)<2: return
        mode=self.arrange_combo.currentText()
        if mode.startswith("Manual"): return
        if (mode.startswith("By date") or mode.startswith("Visual")) and not _pil_available():
            QMessageBox.information(self,APP_NAME,"Smart ordering needs Pillow:\n    python -m pip install pillow"); return
        self.status_label.setText("Arranging…"); QApplication.processEvents()
        self._push_undo()
        try:
            if mode.startswith("By date"):   self.timeline=order_by_date(self.timeline)
            elif mode.startswith("Visual"):  self.timeline=order_visual_flow(self.timeline,self._tmpdir)
            elif mode.startswith("Shuffle"): random.shuffle(self.timeline)
        except Exception as e:
            QMessageBox.warning(self,APP_NAME,f"Could not arrange: {e}"); return
        self._rebuild_timeline_widget(); self.status_label.setText(f"Arranged: {mode}")

    # ── selection / inspector ─────────────────────────────────────────────────
    def _on_media_selected(self,row):
        if 0<=row<len(self.media): self._show_clip(self.media[row]); self._load_inspector(self.media[row],row,False)

    def _on_timeline_selected(self,row):
        if 0<=row<len(self.timeline): self._show_clip(self.timeline[row]); self._load_inspector(self.timeline[row],row,True)

    def _load_inspector(self,clip,row,is_tl):
        self._insp_clip=clip; self._insp_row=row; self._insp_is_tl=is_tl
        fm=self.insp_name.fontMetrics()
        self.insp_name.setText(fm.elidedText(clip.name,Qt.ElideMiddle,220))
        for w in [self.photo_dur_spin,self.video_max_spin,self.trim_start_spin,self.speed_spin,self.crop_bias_combo]:
            w.blockSignals(True)
        self.photo_dur_spin.setValue(clip.photo_dur); self.video_max_spin.setValue(clip.video_max)
        self.trim_start_spin.setValue(clip.trim_start); self.speed_spin.setValue(clip.speed)
        self.crop_bias_combo.setCurrentText(clip.crop_bias)
        self.photo_dur_spin.setEnabled(clip.kind=="photo")
        self.video_max_spin.setEnabled(clip.kind=="video")
        self.trim_start_spin.setEnabled(clip.kind=="video")
        for w in [self.photo_dur_spin,self.video_max_spin,self.trim_start_spin,self.speed_spin,self.crop_bias_combo]:
            w.blockSignals(False)

    def _update_selected_clip(self):
        clip=getattr(self,"_insp_clip",None)
        if not clip: return
        clip.photo_dur=self.photo_dur_spin.value(); clip.video_max=self.video_max_spin.value()
        clip.trim_start=self.trim_start_spin.value(); clip.speed=self.speed_spin.value()
        clip.crop_bias=self.crop_bias_combo.currentText()
        if getattr(self,"_insp_is_tl",False):
            self._refresh_timeline_label(self._insp_row); self._update_total()

    # ── preview ───────────────────────────────────────────────────────────────
    def _show_clip(self,clip):
        self.player.stop()
        if clip.kind=="photo":
            loaded=False
            if _pil_available():
                try:
                    from PIL import Image, ImageOps
                    from PySide6.QtGui import QImage
                    img=Image.open(clip.path)
                    img=ImageOps.exif_transpose(img).convert("RGB")
                    data=img.tobytes("raw","RGB")
                    qimg=QImage(data,img.width,img.height,img.width*3,QImage.Format_RGB888)
                    pm=QPixmap.fromImage(qimg)
                    if not pm.isNull():
                        self.image_label.setPixmap(pm.scaled(
                            self.preview_stack.size(),Qt.KeepAspectRatio,Qt.SmoothTransformation))
                        loaded=True
                except Exception:
                    pass
            if not loaded:
                pm=QPixmap(clip.path)
                if not pm.isNull():
                    self.image_label.setPixmap(pm.scaled(
                        self.preview_stack.size(),Qt.KeepAspectRatio,Qt.SmoothTransformation))
            self.preview_stack.setCurrentIndex(0)
        else:
            self.player.setSource(QUrl.fromLocalFile(clip.path))
            self.preview_stack.setCurrentIndex(1)

    def _toggle_play(self):
        if self.player.playbackState()==QMediaPlayer.PlayingState:
            self.player.pause(); self.play_btn.setIcon(_svg_icon("play",15,"#ffffff"))
        else:
            self.player.play(); self.play_btn.setIcon(_svg_icon("pause",15,"#ffffff"))

    # ── audio intelligence ────────────────────────────────────────────────────
    def _beats_per_clip(self):
        return {"Every beat":1,"Every 2 beats":2,"Every bar (4)":4,"Every 2 bars (8)":8}.get(
            self.cut_combo.currentText(),4)

    def analyze_song(self, on_done=None):
        """Beat-detect the current song in the background. `on_done` (if given)
        fires once analysis finishes either way — used to chain into export."""
        if not self.music_path:
            if on_done is None: QMessageBox.information(self,APP_NAME,"Choose a song first.")
            if on_done: on_done()
            return
        if not _librosa_available():
            QMessageBox.information(self,APP_NAME,"Beat detection needs librosa:\n    python -m pip install librosa")
            if on_done: on_done()
            return
        self.status_label.setText("Analyzing song…")
        worker=FuncWorker(analyze_beats,self.music_path)
        self._analyze_worker=worker
        def done(res):
            bpm,beats=res
            if not beats:
                self.bpm_label.setText("BPM: (couldn't detect)")
            else:
                self._bpm,self._beat_times,self._beats_for=bpm,beats,self.music_path
                self.bpm_label.setText(f"BPM: {bpm}  ({len(beats)} beats)")
                self.status_label.setText("Beats detected")
            if on_done: on_done()
        def failed(msg):
            self.bpm_label.setText("BPM: (couldn't detect)")
            if on_done: on_done()
        worker.result.connect(done); worker.failed.connect(failed); worker.start()

    def find_song_start(self, on_done=None):
        """Scan the song's energy in the background to find the best start
        point. `on_done` (if given) fires once the scan finishes either way."""
        if not self.music_path:
            if on_done is None: QMessageBox.information(self,APP_NAME,"Choose a song first.")
            if on_done: on_done()
            return
        reel_dur=sum(c.render_duration() for c in self.timeline) or 30.0
        self.status_label.setText("Scanning song energy…")
        worker=FuncWorker(find_smart_song_start,self.music_path,reel_dur)
        self._songstart_worker=worker
        def done(offset):
            self._song_start=offset
            mins,secs=divmod(int(offset),60)
            label=f"{mins}:{secs:02d}" if mins else f"{offset:.1f}s"
            self.song_start_label.setText(
                f"Start: {label}  ✓ skipping intro" if offset>0.5 else "Start: 0.0s  (already starts strong)")
            self.status_label.setText(f"Best start: {label}")
            if on_done: on_done()
        def failed(msg):
            if on_done: on_done()
        worker.result.connect(done); worker.failed.connect(failed); worker.start()

    def _ensure_song_start(self, cb):
        """Run the smart-start scan first if it's enabled and not already cached."""
        if self.music_path and self.smart_start_check.isChecked() and self._song_start==0.0:
            self.find_song_start(on_done=cb)
        else:
            cb()

    def _ensure_beats(self, cb):
        """Run beat analysis first if beat-sync is on and the current song hasn't been analyzed."""
        if self.beatsync_check.isChecked() and self.music_path and \
           (self._beats_for!=self.music_path or not self._beat_times):
            self.analyze_song(on_done=cb)
        else:
            cb()

    # ── Auto Reel ─────────────────────────────────────────────────────────────
    def _auto_pick_mood(self):
        """Pick a colour mood by sampling the average warmth of media colours."""
        if not _pil_available() or not self.media:
            return "None"
        try:
            rs,gs,bs,n=[],[],[],0
            for clip in self.media[:12]:  # sample up to 12 clips
                img=_thumb_image(clip,self._tmpdir)
                if img is None: continue
                px=list(img.getdata())
                rs.append(sum(p[0] for p in px)/len(px))
                gs.append(sum(p[1] for p in px)/len(px))
                bs.append(sum(p[2] for p in px)/len(px))
                n+=1
            if not n: return "None"
            r,g,b=sum(rs)/n, sum(gs)/n, sum(bs)/n
            warmth=r-b   # positive = warm, negative = cool
            if warmth>20:  return "Warm"
            if warmth<-15: return "Cool"
            brightness=(r+g+b)/3
            if brightness<80: return "Moody"
            return "Punchy"
        except Exception as e:
            log.warning("_auto_pick_mood failed: %s",e)
            return "None"

    def _build_auto_settings(self, seed=None, force_mood=None, force_transition=None):
        """Build a fully-automatic settings dict — no user intervention needed."""
        import datetime as _dt
        rng=random.Random(seed)
        crf,x264=QUALITY_PRESETS["High (recommended)"]

        mood = force_mood or self._auto_pick_mood()
        transition = force_transition or rng.choice(["random","flash_white","fade","dissolve","slideleft"])

        return {
            "size":EXPORT_PRESETS["Instagram Reel (1080×1920)"],
            "fps":30,
            "zoom":True,
            "gpu":self.gpu_check.isChecked(),
            "transition":transition,
            "transition_dur":0.5,
            "crf":crf, "x264_preset":x264,
            "music_fade_in":1.5, "music_fade_out":3.0,
            "loudnorm":True,
            "include_clip_audio":True,
            "duck_music":True,
            "duck_db":-12.0,
            "colour_mood":mood,
            "intro_outro_hold":0.5,
            "target_duration":None,
            "loop_to_fill":True,
            "song_start":0.0,
            "seed":seed or rng.randint(0,9999),
            "title_text":self.title_edit.text(),
            "cta_text":self.cta_edit.text(),
            "text_size":self.text_size_spin.value(),
            "text_position":self.text_pos_combo.currentText(),
            "text_color":self.text_color_combo.currentText(),
            "text_shadow":True,
        }

    def _auto_prepare_timeline(self, seed=None):
        """Populate timeline automatically from the media library."""
        rng=random.Random(seed)
        if not self.media:
            QMessageBox.warning(self,APP_NAME,"Import some photos or videos first.")
            return False
        if not self.music_path:
            QMessageBox.warning(self,APP_NAME,"Choose a song first.")
            return False
        self._push_undo()
        self.timeline.clear(); self.timeline_list.clear()

        # order by date if Pillow available, otherwise shuffle for variety
        if _pil_available():
            ordered=order_by_date(self.media)
        else:
            ordered=list(self.media); rng.shuffle(ordered)

        for clip in ordered:
            c=Clip(path=clip.path,kind=clip.kind,duration=clip.duration,
                   photo_dur=clip.photo_dur,video_max=clip.video_max)
            # auto-set blur_bg for landscape videos
            if c.kind=="video":
                vw,vh=probe_video_size(c.path)
                if vw>0 and vw>vh:   c.crop_bias="blur_bg"
            self.timeline.append(c)
            self.timeline_list.addItem(self._make_timeline_item(c))
        self._update_total()
        return True

    def auto_reel(self):
        """One-click: auto-populate timeline, auto-settings, auto-export."""
        if not self._auto_prepare_timeline(): return
        self.status_label.setText("Scanning song…")
        self.auto_btn.setEnabled(False); self.multi_btn.setEnabled(False)
        reel_dur=sum(c.render_duration() for c in self.timeline) or 30.0
        worker=FuncWorker(find_smart_song_start,self.music_path,reel_dur)
        self._auto_songstart_worker=worker
        worker.result.connect(lambda song_start: self._auto_reel_continue(song_start))
        worker.failed.connect(lambda msg: self._auto_reel_continue(0.0))
        worker.start()

    def _auto_reel_continue(self, song_start):
        self._song_start=song_start
        settings=self._build_auto_settings()
        settings["song_start"]=song_start

        import datetime as _dt
        date_str=_dt.datetime.now().strftime("%Y-%m-%d")
        out,_=QFileDialog.getSaveFileName(self,"Save auto reel",
                                          f"reel_{date_str}.mp4","MP4 video (*.mp4)")
        if not out:
            self.auto_btn.setEnabled(True); self.multi_btn.setEnabled(True); return

        self.status_label.setText(f"Auto reel — mood: {settings['colour_mood']}, "
                                  f"transition: {settings['transition']}")
        self.export_btn.setEnabled(False); self.preview_btn.setEnabled(False)
        self.progress.setValue(0)
        self.worker=ExportWorker(list(self.timeline),self.music_path,settings,out)
        self.worker.progress.connect(self.progress.setValue)
        self.worker.status.connect(self.status_label.setText)
        self.worker.finished_ok.connect(self._auto_done)
        self.worker.failed.connect(self._export_failed)
        self.worker.start()

    def auto_reel_multi(self):
        """Generate 3 different versions with different moods and transition styles."""
        if not self._auto_prepare_timeline(): return
        self.status_label.setText("Scanning song…")
        self.auto_btn.setEnabled(False); self.multi_btn.setEnabled(False)
        reel_dur=sum(c.render_duration() for c in self.timeline) or 30.0
        worker=FuncWorker(find_smart_song_start,self.music_path,reel_dur)
        self._auto_songstart_worker=worker
        worker.result.connect(lambda song_start: self._auto_reel_multi_continue(song_start))
        worker.failed.connect(lambda msg: self._auto_reel_multi_continue(0.0))
        worker.start()

    def _auto_reel_multi_continue(self, song_start):
        self._song_start=song_start
        import datetime as _dt
        date_str=_dt.datetime.now().strftime("%Y-%m-%d")
        out_dir=QFileDialog.getExistingDirectory(self,"Choose folder for 3 reels")
        if not out_dir:
            self.auto_btn.setEnabled(True); self.multi_btn.setEnabled(True); return

        moods   =["Warm","Moody","Punchy"]
        transitions=["random","flash_white","dissolve"]
        labels  =["warm","moody","punchy"]

        self._multi_queue=[]
        for i,(mood,trans,label) in enumerate(zip(moods,transitions,labels)):
            s=self._build_auto_settings(seed=i*7, force_mood=mood, force_transition=trans)
            s["song_start"]=song_start
            out_path=str(Path(out_dir)/f"reel_{date_str}_{label}.mp4")
            self._multi_queue.append((s,out_path,label))

        self._multi_results=[]
        self.export_btn.setEnabled(False); self.preview_btn.setEnabled(False)
        self._run_next_multi()

    def _run_next_multi(self):
        if not self._multi_queue:
            self.auto_btn.setEnabled(True); self.multi_btn.setEnabled(True)
            self.export_btn.setEnabled(True); self.preview_btn.setEnabled(True)
            self.progress.setValue(100)
            done="\n".join(self._multi_results)
            QMessageBox.information(self,APP_NAME,
                f"3 reels generated!\n\n{done}\n\nPick your favourite.")
            self.status_label.setText("Done — 3 versions generated ✓")
            return
        s,out_path,label=self._multi_queue.pop(0)
        remaining=len(self._multi_queue)
        self.status_label.setText(f"Rendering '{label}' version… ({remaining} after this)")
        self.progress.setValue(0)
        self.worker=ExportWorker(list(self.timeline),self.music_path,s,out_path)
        self.worker.progress.connect(self.progress.setValue)
        self.worker.status.connect(self.status_label.setText)
        self.worker.finished_ok.connect(self._multi_version_done)
        self.worker.failed.connect(self._multi_version_failed)
        self.worker.start()

    def _multi_version_done(self,path):
        self._multi_results.append(path)
        self._run_next_multi()

    def _multi_version_failed(self,msg):
        self._multi_results.append(f"(failed)")
        QMessageBox.warning(self,APP_NAME,f"One version failed:\n{msg}\nContinuing…")
        self._run_next_multi()

    def _auto_done(self,path):
        self.auto_btn.setEnabled(True); self.multi_btn.setEnabled(True)
        self.export_btn.setEnabled(True); self.preview_btn.setEnabled(True)
        self._export_done(path)

    # ── export ────────────────────────────────────────────────────────────────
    def export_reel(self):
        if not FFMPEG: self._warn_no_ffmpeg(); return
        if not self.timeline: QMessageBox.warning(self,APP_NAME,"Add clips to the timeline first."); return
        import datetime as _dt
        date_str=_dt.datetime.now().strftime("%Y-%m-%d") if self.dated_name_check.isChecked() else ""
        if self.beatsync_check.isChecked() and not self.music_path:
            QMessageBox.information(self,APP_NAME,"Beat sync needs a song."); return

        default=f"reel_{date_str}.mp4" if date_str else "reel.mp4"
        out,_=QFileDialog.getSaveFileName(self,"Export reel",default,"MP4 video (*.mp4)")
        if not out: return
        self._start_render(self._gather_settings(),out,preview=False)

    def preview_draft(self):
        """Render a fast low-res draft of the current timeline + settings into
        the temp dir and play it in the preview pane — no full export needed."""
        if not FFMPEG: self._warn_no_ffmpeg(); return
        if not self.timeline: QMessageBox.warning(self,APP_NAME,"Add clips to the timeline first."); return
        if self.beatsync_check.isChecked() and not self.music_path:
            QMessageBox.information(self,APP_NAME,"Beat sync needs a song."); return
        settings=self._gather_settings()
        w,h=settings["size"]
        scale=360/w
        settings["size"]=(360, max(2,round(h*scale/2)*2))
        settings["text_size"]=max(12,round(settings["text_size"]*scale))
        settings["crf"]=30; settings["x264_preset"]="ultrafast"
        settings["gpu"]=False; settings["fps"]=24
        # unique name per draft — QMediaPlayer may still hold the previous
        # draft open, and ffmpeg can't overwrite a locked file on Windows
        self._draft_counter=getattr(self,"_draft_counter",0)+1
        out=str(self._tmpdir/f"draft_preview_{self._draft_counter}.mp4")
        self._start_render(settings,out,preview=True)

    def _gather_settings(self):
        """Collect the full export settings dict from the current UI state."""
        crf,x264_preset=QUALITY_PRESETS[self.quality_combo.currentText()]
        return {
            "size":EXPORT_PRESETS[self.preset_combo.currentText()],
            "fps":int(self.fps_combo.currentText()),
            "zoom":self.zoom_check.isChecked(), "gpu":self.gpu_check.isChecked(),
            "transition":self.trans_combo.currentText(),
            "transition_dur":self.trans_dur_spin.value(),
            "crf":crf, "x264_preset":x264_preset,
            "music_fade_in":self.fadein_spin.value(), "music_fade_out":self.fadeout_spin.value(),
            "loudnorm":self.loudnorm_check.isChecked(),
            "include_clip_audio":self.clip_audio_check.isChecked(),
            "duck_music":self.duck_check.isChecked(),
            "duck_db":self.duck_db_spin.value(),
            "colour_mood":self.mood_combo.currentText(),
            "intro_outro_hold":self.hold_spin.value(),
            "target_duration":(self.target_dur_spin.value() if self.target_dur_check.isChecked() else None),
            "loop_to_fill":self.loop_to_fill_check.isChecked(),
            "song_start":0.0, "seed":None,
            "title_text":self.title_edit.text(),
            "cta_text":self.cta_edit.text(),
            "text_size":self.text_size_spin.value(),
            "text_position":self.text_pos_combo.currentText(),
            "text_color":self.text_color_combo.currentText(),
            "text_shadow":self.text_shadow_check.isChecked(),
        }

    def _start_render(self, settings, out, preview):
        """Kick off a render, first running the smart-start scan and beat
        analysis in the background if they're needed; _render_finalize
        continues once both are ready."""
        self.export_btn.setEnabled(False); self.preview_btn.setEnabled(False)
        self._render_ctx=(settings,out,preview)
        self._ensure_song_start(lambda: self._ensure_beats(self._render_finalize))

    def _render_finalize(self):
        settings,out,preview=self._render_ctx
        if self.music_path and self.smart_start_check.isChecked():
            settings["song_start"]=self._song_start

        if self.beatsync_check.isChecked() and self._beat_times:
            n=len(self.timeline)
            seg=beat_segment_durations(n,self._beat_times,self._beats_per_clip())
            if seg:
                transition=settings["transition"]; tdur=settings["transition_dur"]
                t=0.0 if transition=="none" else min(tdur,min(seg)*0.5,1.0)
                settings["forced_t"]=t; settings["beat_durations"]=[d+t for d in seg]

        self.progress.setValue(0)
        self.status_label.setText("Rendering draft preview…" if preview else "Rendering…")
        self.worker=ExportWorker(list(self.timeline),self.music_path,settings,out)
        self.worker.progress.connect(self.progress.setValue)
        self.worker.status.connect(self.status_label.setText)
        self.worker.finished_ok.connect(self._preview_done if preview else self._export_done)
        self.worker.failed.connect(self._export_failed)
        self.worker.start()

    def _preview_done(self,path):
        self.export_btn.setEnabled(True); self.preview_btn.setEnabled(True)
        self.progress.setValue(100)
        self.status_label.setText("Draft ready — playing preview ✓")
        self.player.stop()
        self.player.setSource(QUrl())              # release any previous draft file
        self.player.setSource(QUrl.fromLocalFile(path))
        self.preview_stack.setCurrentIndex(1)
        self.player.play()
        self.play_btn.setIcon(_svg_icon("pause",15,"#ffffff"))

    def _export_done(self,path):
        self.export_btn.setEnabled(True); self.preview_btn.setEnabled(True)
        self.status_label.setText("Done ✓")
        QMessageBox.information(self,APP_NAME,f"Reel exported:\n{path}")

    def _export_failed(self,msg):
        self.export_btn.setEnabled(True); self.preview_btn.setEnabled(True)
        self.auto_btn.setEnabled(True); self.multi_btn.setEnabled(True)
        self.progress.setValue(0)
        self.status_label.setText("Export failed"); QMessageBox.critical(self,APP_NAME,msg)

    # ── project save / load ───────────────────────────────────────────────────
    def _project_dict(self):
        return {
            "version":3,"music":self.music_path,
            "media":[asdict(c) for c in self.media],
            "timeline":[asdict(c) for c in self.timeline],
            "preset":self.preset_combo.currentText(),"fps":self.fps_combo.currentText(),
            "zoom":self.zoom_check.isChecked(),"quality":self.quality_combo.currentText(),
            "transition":self.trans_combo.currentText(),"transition_dur":self.trans_dur_spin.value(),
            "music_fade_in":self.fadein_spin.value(),"music_fade_out":self.fadeout_spin.value(),
            "loudnorm":self.loudnorm_check.isChecked(),
            "include_clip_audio":self.clip_audio_check.isChecked(),
            "duck_music":self.duck_check.isChecked(),
            "duck_db":self.duck_db_spin.value(),
            "colour_mood":self.mood_combo.currentText(),
            "intro_outro_hold":self.hold_spin.value(),
            "target_duration_on":self.target_dur_check.isChecked(),
            "target_duration":self.target_dur_spin.value(),
            "dated_name":self.dated_name_check.isChecked(),
            "loop_to_fill":self.loop_to_fill_check.isChecked(),
            "smart_start":self.smart_start_check.isChecked(),
            "title_text":self.title_edit.text(),"cta_text":self.cta_edit.text(),
            "text_size":self.text_size_spin.value(),
            "text_position":self.text_pos_combo.currentText(),
            "text_color":self.text_color_combo.currentText(),
            "text_shadow":self.text_shadow_check.isChecked(),
        }

    def save_project(self):
        if not self.project_path: return self.save_project_as()
        try:
            Path(self.project_path).write_text(json.dumps(self._project_dict(),indent=2),encoding="utf-8")
            self.status_label.setText("Project saved")
        except Exception as e: QMessageBox.critical(self,APP_NAME,f"Save failed: {e}")

    def save_project_as(self):
        f,_=QFileDialog.getSaveFileName(self,"Save project","project"+PROJECT_EXT,
                                        f"{APP_NAME} project (*{PROJECT_EXT})")
        if f: self.project_path=f; self.save_project()

    def open_project(self):
        f,_=QFileDialog.getOpenFileName(self,"Open project","",f"{APP_NAME} project (*{PROJECT_EXT})")
        if not f: return
        try: data=json.loads(Path(f).read_text(encoding="utf-8"))
        except Exception as e: QMessageBox.critical(self,APP_NAME,f"Open failed: {e}"); return
        self.project_path=f
        self.media_list.clear(); self.timeline_list.clear(); self.media.clear(); self.timeline.clear()
        fields=set(Clip.__dataclass_fields__)
        for d in data.get("media",[]):
            clip=Clip(**{k:d[k] for k in d if k in fields})
            self.media.append(clip); self._add_media_row(clip)
        for d in data.get("timeline",[]):
            clip=Clip(**{k:d[k] for k in d if k in fields})
            self.timeline.append(clip)
            self.timeline_list.addItem(self._make_timeline_item(clip))
        if data.get("music"): self._set_music(data["music"])
        if data.get("preset") in EXPORT_PRESETS: self.preset_combo.setCurrentText(data["preset"])
        if data.get("fps"): self.fps_combo.setCurrentText(str(data["fps"]))
        if data.get("quality") in QUALITY_PRESETS: self.quality_combo.setCurrentText(data["quality"])
        if data.get("transition") in TRANSITION_CHOICES: self.trans_combo.setCurrentText(data["transition"])
        if data.get("transition_dur"): self.trans_dur_spin.setValue(float(data["transition_dur"]))
        if data.get("music_fade_in"): self.fadein_spin.setValue(float(data["music_fade_in"]))
        if data.get("music_fade_out"): self.fadeout_spin.setValue(float(data["music_fade_out"]))
        self.loudnorm_check.setChecked(bool(data.get("loudnorm")))
        self.clip_audio_check.setChecked(bool(data.get("include_clip_audio",True)))
        self.duck_check.setChecked(bool(data.get("duck_music",True)))
        if data.get("duck_db") is not None: self.duck_db_spin.setValue(float(data["duck_db"]))
        if data.get("colour_mood") in COLOUR_MOODS: self.mood_combo.setCurrentText(data["colour_mood"])
        if data.get("intro_outro_hold") is not None: self.hold_spin.setValue(float(data["intro_outro_hold"]))
        self.target_dur_check.setChecked(bool(data.get("target_duration_on")))
        if data.get("target_duration"): self.target_dur_spin.setValue(float(data["target_duration"]))
        self.dated_name_check.setChecked(bool(data.get("dated_name",True)))
        self.loop_to_fill_check.setChecked(bool(data.get("loop_to_fill",True)))
        self.smart_start_check.setChecked(bool(data.get("smart_start",True)))
        self.title_edit.setText(data.get("title_text",""))
        self.cta_edit.setText(data.get("cta_text",""))
        if data.get("text_size"): self.text_size_spin.setValue(int(data["text_size"]))
        if data.get("text_position") in TEXT_POSITIONS: self.text_pos_combo.setCurrentText(data["text_position"])
        if data.get("text_color"): self.text_color_combo.setCurrentText(data["text_color"])
        self.text_shadow_check.setChecked(bool(data.get("text_shadow",True)))
        self.zoom_check.setChecked(bool(data.get("zoom")))
        self._update_total(); self.status_label.setText("Project loaded")

    # ── misc ──────────────────────────────────────────────────────────────────
    def _warn_no_ffmpeg(self):
        QMessageBox.warning(self,APP_NAME,
            "FFmpeg not found.\n\nRun:\n    python -m pip install imageio-ffmpeg\nthen restart.")

    def _about(self):
        QMessageBox.information(self,f"About {APP_NAME}",
            f"{APP_NAME} — full-featured reel maker\n\nFFmpeg: {FFMPEG or 'not found'}")

    def closeEvent(self,e):
        if self.worker and self.worker.isRunning():
            self.worker.cancel(); self.worker.wait(2000)
        # background scan/import threads must finish (or be given a chance to)
        # before teardown — destroying a running QThread aborts the process
        for attr in ("_import_worker","_analyze_worker","_songstart_worker","_auto_songstart_worker"):
            w=getattr(self,attr,None)
            if w and w.isRunning(): w.wait(3000)
        try: shutil.rmtree(self._tmpdir,ignore_errors=True)
        except Exception as ex:
            log.warning("failed to remove temp dir %s: %s",self._tmpdir,ex)
        super().closeEvent(e)

# ── entry point ───────────────────────────────────────────────────────────────
def main():
    app=QApplication(sys.argv); app.setApplicationName(APP_NAME)
    win=ReelForge(); win.show(); sys.exit(app.exec())

if __name__=="__main__":
    main()
