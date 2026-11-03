"""Track clean-up: which audio and subtitle tracks an encode keeps."""
import json
import shutil
import subprocess

import pytest

from app.services.media import config, encoder, tracks
from app.services.media.tracks import Stream

HAS_FFMPEG = bool(shutil.which("ffmpeg") and shutil.which("ffprobe"))


def A(i, lang, default=False):
    return Stream(i, "audio", "aac", tracks._norm(lang), default=default)


def T(i, lang, forced=False, codec="subrip"):
    return Stream(i, "subtitle", codec, tracks._norm(lang), forced=forced)


def kept(sel):
    return [m for m in sel.maps[1::2] if m not in ("0:v:0", "0:t?")]


def test_english_plus_original_audio_and_english_forced_subs():
    # A French film: French original (default), English dub, Spanish dub; subs in four languages.
    streams = [Stream(0, "video", "h264", ""), A(1, "fre", default=True), A(2, "eng"), A(3, "spa"),
               T(4, "eng"), T(5, "fre"), T(6, "ger", forced=True), T(7, "ita")]
    sel = tracks.select(streams, ".mkv", cleanup=True, audio_langs=["eng"], sub_langs=["eng"])
    assert kept(sel) == ["0:1", "0:2", "0:4", "0:6"]
    assert sel.summary() == "Kept 2 of 3 audio and 2 of 4 subtitle tracks"
    assert "0:t?" in sel.maps and sel.sub_codec == ["-c:s", "copy"]


def test_language_names_and_untagged_tracks():
    streams = [A(1, "en"), A(2, "und"), A(3, "de"), T(4, "English"), T(5, "")]
    sel = tracks.select(streams, ".mkv", cleanup=True, audio_langs=["english"], sub_langs=["eng"])
    assert kept(sel) == ["0:1", "0:2", "0:4", "0:5"]          # untagged kept: can't tell what it is


def test_never_drops_all_audio_and_off_keeps_everything():
    streams = [A(1, "jpn"), A(2, "kor"), T(3, "jpn")]
    # jpn is first, so it counts as the original; nothing else matches.
    assert kept(tracks.select(streams, ".mkv", cleanup=True, audio_langs=["eng"], sub_langs=["eng"])) == ["0:1"]
    assert kept(tracks.select(streams, ".mkv", cleanup=False)) == ["0:1", "0:2", "0:3"]


def test_mp4_output_keeps_only_text_subtitles_as_mov_text():
    streams = [A(1, "eng"), T(2, "eng"), T(3, "eng", codec="hdmv_pgs_subtitle")]
    sel = tracks.select(streams, ".mp4", cleanup=True, audio_langs=["eng"], sub_langs=["eng"])
    assert kept(sel) == ["0:1", "0:2"] and sel.sub_codec == ["-c:s", "mov_text"] and "0:t?" not in sel.maps


def test_settings_are_runtime_editable():
    assert config.apply("keep_audio_langs", "eng, jpn") and config.KEEP_AUDIO_LANGS == ["eng", "jpn"]
    assert config.apply("track_cleanup", "false") and not tracks.enabled()
    config.apply("track_cleanup", "true")
    config.apply("keep_audio_langs", "eng")


def _multi_track(path):
    """French original + English + Spanish audio; English, French, forced German subtitles."""
    srt = path.parent / "s.srt"
    srt.write_text("1\n00:00:00,000 --> 00:00:01,000\nhi\n")
    cmd = ["ffmpeg", "-y", "-loglevel", "error", "-f", "lavfi", "-i", "testsrc=size=160x120:rate=10:duration=2"]
    for f in (300, 400, 500):
        cmd += ["-f", "lavfi", "-i", f"sine=frequency={f}:duration=2"]
    cmd += ["-i", str(srt), "-i", str(srt), "-i", str(srt), "-map", "0"]
    for i in (1, 2, 3):
        cmd += ["-map", str(i)]
    for i in (4, 5, 6):
        cmd += ["-map", str(i)]
    cmd += ["-c:v", "libx264", "-preset", "ultrafast", "-c:a", "aac", "-c:s", "srt",
            "-metadata:s:a:0", "language=fre", "-disposition:a:0", "default",
            "-metadata:s:a:1", "language=eng", "-metadata:s:a:2", "language=spa",
            "-metadata:s:s:0", "language=eng", "-metadata:s:s:1", "language=fre",
            "-metadata:s:s:2", "language=ger", "-disposition:s:2", "forced", str(path)]
    subprocess.run(cmd, check=True, timeout=120)


@pytest.mark.skipif(not HAS_FFMPEG, reason="ffmpeg not installed")
def test_encode_drops_unwanted_tracks(tmp_path, monkeypatch):
    src = tmp_path / "film.mkv"
    _multi_track(src)
    monkeypatch.setattr(config, "ENCODER_BACKEND", "cpu")
    monkeypatch.setattr(config, "X265_PRESET", "ultrafast")
    monkeypatch.setattr(config, "TRACK_CLEANUP", "true")
    monkeypatch.setattr(config, "KEEP_AUDIO_LANGS", ["eng"])
    monkeypatch.setattr(config, "KEEP_SUB_LANGS", ["eng"])
    assert encoder.encode_to_x265(str(src)) is not None
    out = json.loads(subprocess.run(
        ["ffprobe", "-v", "error", "-show_entries", "stream=codec_type,codec_name:stream_tags=language",
         "-of", "json", str(src)], capture_output=True, text=True, check=True).stdout)["streams"]
    got = [(s["codec_type"], s.get("tags", {}).get("language")) for s in out]
    assert got[0] == ("video", None) and out[0]["codec_name"] == "hevc"
    assert sorted(got[1:]) == [("audio", "eng"), ("audio", "fre"), ("subtitle", "eng"), ("subtitle", "ger")]
