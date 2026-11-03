"""Track clean-up: choose which audio and subtitle tracks an encode keeps.

Kept by default: English audio plus the original-language audio (the file's
first/default audio track, which is the original for foreign films), and English
and forced subtitles. Everything else (dubs, extra subtitle languages) is dropped,
which often saves another 10–20 %. Fonts attached for styled subtitles are kept.

Safety: a track with no language tag is kept (we can't tell), and if no audio
would survive, all audio is kept.

Without clean-up, ffmpeg's defaults would keep just one audio and one subtitle
track; with clean-up off, every track is kept instead.
"""
import json
import logging
import os
import subprocess
from dataclasses import dataclass, field

from . import config

logger = logging.getLogger(__name__)

# ISO 639-2 codes (and common variants) per language, so "en", "eng" and "english" all match.
_ALIASES = {
    "eng": {"eng", "en", "english"}, "fre": {"fre", "fra", "fr", "french"}, "ger": {"ger", "deu", "de", "german"},
    "spa": {"spa", "es", "spanish"}, "ita": {"ita", "it", "italian"}, "jpn": {"jpn", "ja", "japanese"},
    "kor": {"kor", "ko", "korean"}, "chi": {"chi", "zho", "zh", "chinese"}, "por": {"por", "pt", "portuguese"},
    "rus": {"rus", "ru", "russian"}, "dut": {"dut", "nld", "nl", "dutch"},
}
UNKNOWN = {"", "und", "unk", "unknown", "mis", "zxx"}
_BITMAP_SUBS = {"hdmv_pgs_subtitle", "dvd_subtitle", "dvb_subtitle", "xsub"}
_MP4_TEXT_SUBS = {"mov_text", "subrip", "ass", "ssa", "webvtt", "text"}


def _norm(lang: str | None) -> str:
    lang = (lang or "").strip().lower()
    for key, names in _ALIASES.items():
        if lang in names:
            return key
    return lang


def _wanted(langs: list[str]) -> set[str]:
    return {_norm(x) for x in langs if x.strip()}


@dataclass
class Stream:
    index: int
    kind: str           # audio | subtitle | video | attachment | data
    codec: str
    lang: str
    title: str = ""
    default: bool = False
    forced: bool = False


@dataclass
class Selection:
    maps: list[str]                         # ffmpeg -map arguments
    sub_codec: list[str] = field(default_factory=list)
    kept_audio: int = 0
    total_audio: int = 0
    kept_subs: int = 0
    total_subs: int = 0

    def summary(self) -> str:
        return (f"Kept {self.kept_audio} of {self.total_audio} audio and "
                f"{self.kept_subs} of {self.total_subs} subtitle tracks")


def probe_streams(path: str) -> list[Stream]:
    out = subprocess.run(
        ["ffprobe", "-v", "error", "-show_entries",
         "stream=index,codec_type,codec_name:stream_tags=language,title:stream_disposition=default,forced,attached_pic",
         "-of", "json", path],
        capture_output=True, text=True, timeout=config.FFPROBE_TIMEOUT, check=True).stdout
    streams = []
    for s in json.loads(out or "{}").get("streams", []):
        disp, tags = s.get("disposition") or {}, s.get("tags") or {}
        kind = s.get("codec_type") or ""
        if kind == "video" and disp.get("attached_pic"):
            kind = "cover"
        streams.append(Stream(int(s["index"]), kind, s.get("codec_name") or "", _norm(tags.get("language")),
                              tags.get("title") or "", bool(disp.get("default")), bool(disp.get("forced"))))
    return streams


def enabled() -> bool:
    return str(getattr(config, "TRACK_CLEANUP", "true")).lower() == "true"


def select(streams: list[Stream], out_ext: str, cleanup: bool | None = None,
           audio_langs: list[str] | None = None, sub_langs: list[str] | None = None) -> Selection:
    """Which streams to map into the encode."""
    cleanup = enabled() if cleanup is None else cleanup
    keep_audio = _wanted(audio_langs if audio_langs is not None else config.KEEP_AUDIO_LANGS)
    keep_subs = _wanted(sub_langs if sub_langs is not None else config.KEEP_SUB_LANGS)
    audio = [s for s in streams if s.kind == "audio"]
    subs = [s for s in streams if s.kind == "subtitle"]
    mp4 = out_ext.lower() in (".mp4", ".m4v", ".mov")

    if cleanup:
        original = next((s for s in audio if s.default), audio[0] if audio else None)
        kept_a = [s for s in audio if s.lang in UNKNOWN or s.lang in keep_audio or s is original]
        if not kept_a:
            kept_a = audio
        kept_s = [s for s in subs if s.forced or s.lang in UNKNOWN or s.lang in keep_subs]
    else:
        kept_a, kept_s = audio, subs
    if mp4:   # MP4 holds only text subtitles (as mov_text); picture-based ones can't go in
        kept_s = [s for s in kept_s if s.codec in _MP4_TEXT_SUBS]

    maps = ["-map", "0:v:0"]
    for s in kept_a + kept_s:
        maps += ["-map", f"0:{s.index}"]
    if not mp4:
        maps += ["-map", "0:t?"]           # fonts for styled (ASS) subtitles
    sub_codec = ["-c:s", "mov_text" if mp4 else "copy"] if kept_s else []
    return Selection(maps, sub_codec, len(kept_a), len(audio), len(kept_s), len(subs))


def selection_for(path: str) -> Selection | None:
    """The selection for this file, or None to fall back to ffmpeg's defaults (probe failed)."""
    try:
        return select(probe_streams(path), os.path.splitext(path)[1])
    except (subprocess.SubprocessError, OSError, ValueError, KeyError) as exc:
        logger.warning("Track clean-up: couldn't read the tracks of %s (%s); using ffmpeg's defaults", path, exc)
        return None
