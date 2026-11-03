"""Media engine configuration — ported from FileSplitter's config.py.

Values start from environment variables (same names and defaults as FileSplitter)
and are overridden at runtime by settings saved from the UI. The media modules read
them as module attributes (config.X265_CRF etc.), so a saved setting takes effect
for the next job without a restart.
"""
import os


def _parse_paths(raw: str) -> list[str]:
    return [p.strip() for p in raw.split(",") if p.strip()]


MEDIA_PATHS: list[str] = _parse_paths(os.environ.get("MEDIA_PATHS", "/media"))


def _parse_category_map(raw: str) -> dict[str, str]:
    """Parse 'folder:category,folder:category' into a lowercased dict."""
    out: dict[str, str] = {}
    for pair in raw.split(","):
        pair = pair.strip()
        if ":" in pair:
            folder, category = pair.split(":", 1)
            folder, category = folder.strip().lower(), category.strip().lower()
            if folder and category:
                out[folder] = category
    return out


# Optional override that groups scan-folder names into friendlier categories, e.g.
# "bellesa:adult,maturemovies:adult,maturetv:adult,movies:movie,tv:tv". Empty by
# default, so each scan folder is its own category (its lowercased basename).
CATEGORY_MAP: dict[str, str] = _parse_category_map(
    os.environ.get("CATEGORY_MAP", "")
)


def categorize(path: str) -> str:
    """
    Classify a file by the scan-path root that contains it. Each scan path *is* a
    category folder (e.g. /media/bellesa), so the category is that root's basename,
    optionally remapped via CATEGORY_MAP. Returns "other" if no root matches.
    """
    best = None
    for root in MEDIA_PATHS:
        root_norm = root.rstrip("/")
        if path == root_norm or path.startswith(root_norm + "/"):
            if best is None or len(root_norm) > len(best):
                best = root_norm  # longest matching root wins
    if best is None:
        return "other"
    name = os.path.basename(best).lower()
    return CATEGORY_MAP.get(name, name or "other")

SCENE_THRESHOLD: float = float(os.environ.get("SCENE_THRESHOLD", "0.4"))
MIN_SCENE_DURATION: int = int(os.environ.get("MIN_SCENE_DURATION", "120"))

# Scene detection method: "auto" (select then blackdetect fallback), "select", or "black"
SCENE_METHOD: str = os.environ.get("SCENE_METHOD", "auto")
BLACK_MIN_DURATION: float = float(os.environ.get("BLACK_MIN_DURATION", "0.5"))
BLACK_PIX_TH: float = float(os.environ.get("BLACK_PIX_TH", "0.10"))

SPLIT_SCENE_METHOD: str = os.environ.get("SPLIT_SCENE_METHOD", "combined")
SPLIT_MIN_EPISODE_GAP: int = int(os.environ.get("SPLIT_MIN_EPISODE_GAP", "2700"))  # 45 min
SPLIT_BLACK_MIN_DURATION: float = float(os.environ.get("SPLIT_BLACK_MIN_DURATION", "2.0"))
SPLIT_FREEZE_MIN_DURATION: float = float(os.environ.get("SPLIT_FREEZE_MIN_DURATION", "2.0"))
SPLIT_EPISODE_COUNT: int = int(os.environ.get("SPLIT_EPISODE_COUNT", "0"))  # 0 = auto

SPLIT_MIN_DURATION: int = int(os.environ.get("SPLIT_MIN_DURATION", "5400"))   # 90 min
SPLIT_MIN_SIZE: int = int(os.environ.get("SPLIT_MIN_SIZE", str(2 * 1024 ** 3)))  # 2 GB
SPLIT_KEYWORDS: list[str] = _parse_paths(
    os.environ.get("SPLIT_KEYWORDS", "Vol,Compilation,Anthology,Collection,Kink")
)

X265_CRF: int = int(os.environ.get("X265_CRF", "28"))
X265_PRESET: str = os.environ.get("X265_PRESET", "medium")

# cpu — software libx265 (default)
# nvenc — NVIDIA hardware hevc_nvenc (requires GPU device passthrough in docker-compose.yml)
ENCODER_BACKEND: str = os.environ.get("ENCODER_BACKEND", "cpu")

# Max seconds to keep retrying the GPU encode probe at startup when a GPU is configured
# but not yet ready (e.g. driver still initializing right after a host reboot).
NVENC_PROBE_MAX_WAIT: int = int(os.environ.get("NVENC_PROBE_MAX_WAIT", "120"))

# original | 480p | 720p | 1080p | 1440p | 2160p
TARGET_RESOLUTION: str = os.environ.get("TARGET_RESOLUTION", "original")

RESOLUTION_MAP: dict[str, int] = {
    "480p": 480,
    "720p": 720,
    "1080p": 1080,
    "1440p": 1440,
    "2160p": 2160,
}

MAX_WORKERS: int = int(os.environ.get("MAX_WORKERS", "2"))

VIDEO_EXTENSIONS: set[str] = set(
    os.environ.get(
        "VIDEO_EXTENSIONS", ".mp4,.mkv,.avi,.mov,.wmv,.m4v,.ts,.flv"
    ).split(",")
)

FFPROBE_TIMEOUT: int = int(os.environ.get("FFPROBE_TIMEOUT", "60"))

# ThePornDB API key for content-based scene naming (free registration at theporndb.net/register)
TPDB_API_KEY: str = os.environ.get("TPDB_API_KEY", "")


# ── Runtime settings ──────────────────────────────────────────────────────────
# Settings that can be changed from the UI and are persisted in ScreenMate's
# `settings` table. Keys match FileSplitter's, so imported databases carry over.
# Track clean-up (services/media/tracks.py): which audio/subtitle languages an encode keeps.
TRACK_CLEANUP: str = os.environ.get("TRACK_CLEANUP", "true").lower()
KEEP_AUDIO_LANGS: list[str] = _parse_paths(os.environ.get("KEEP_AUDIO_LANGS", "eng"))
KEEP_SUB_LANGS: list[str] = _parse_paths(os.environ.get("KEEP_SUB_LANGS", "eng"))

SETTINGS_MAP: dict[str, tuple[str, type]] = {
    "media_paths":               ("MEDIA_PATHS",               list),
    "scene_threshold":           ("SCENE_THRESHOLD",           float),
    "min_scene_duration":        ("MIN_SCENE_DURATION",        int),
    "scene_method":              ("SCENE_METHOD",              str),
    "x265_crf":                  ("X265_CRF",                  int),
    "x265_preset":               ("X265_PRESET",               str),
    "target_resolution":         ("TARGET_RESOLUTION",         str),
    "encoder_backend":           ("ENCODER_BACKEND",           str),
    "max_workers":               ("MAX_WORKERS",               int),
    "ffprobe_timeout":           ("FFPROBE_TIMEOUT",           int),
    "black_min_duration":        ("BLACK_MIN_DURATION",        float),
    "black_pix_th":              ("BLACK_PIX_TH",              float),
    "split_min_duration":        ("SPLIT_MIN_DURATION",        int),
    "split_min_size":            ("SPLIT_MIN_SIZE",            int),
    "split_keywords":            ("SPLIT_KEYWORDS",            list),
    "split_scene_method":        ("SPLIT_SCENE_METHOD",        str),
    "split_min_episode_gap":     ("SPLIT_MIN_EPISODE_GAP",     int),
    "split_black_min_duration":  ("SPLIT_BLACK_MIN_DURATION",  float),
    "split_freeze_min_duration": ("SPLIT_FREEZE_MIN_DURATION", float),
    "split_episode_count":       ("SPLIT_EPISODE_COUNT",       int),
    "tpdb_api_key":              ("TPDB_API_KEY",              str),
    "track_cleanup":             ("TRACK_CLEANUP",             str),
    "keep_audio_langs":          ("KEEP_AUDIO_LANGS",          list),
    "keep_sub_langs":            ("KEEP_SUB_LANGS",            list),
}


def apply(key: str, value) -> bool:
    """Set one runtime setting on this module. Returns False if the value can't be cast."""
    attr, cast = SETTINGS_MAP[key]
    try:
        if cast is list:
            if isinstance(value, (list, tuple)):
                parsed = [str(v).strip() for v in value if str(v).strip()]
            else:
                parsed = _parse_paths(str(value))
        elif cast is int:
            # int("28.0") raises; accept whole-number floats from JSON/forms.
            parsed = int(float(value))
        else:
            parsed = cast(value)
    except (TypeError, ValueError):
        return False
    globals()[attr] = parsed
    return True


def serialize(key: str, value) -> str:
    """The string form a setting is stored as in the settings table."""
    if SETTINGS_MAP[key][1] is list and isinstance(value, (list, tuple)):
        return ",".join(str(v).strip() for v in value if str(v).strip())
    return str(value)


def load_from_db() -> None:
    """Apply persisted UI settings over the environment defaults (call at startup)."""
    from . import store

    for key, val in store.get_settings(SETTINGS_MAP).items():
        if val != "":
            apply(key, val)
