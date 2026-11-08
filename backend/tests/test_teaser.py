"""Teasers: a ~30 s clip of a few moments, for sharing an adult video without all of it."""
import json
import shutil
import subprocess

import pytest

from app.models import DownloadPath, ShareLink
from app.services.media import store, teaser

from tests.test_organise import lib  # noqa: F401
from tests.test_pipeline import env  # noqa: F401

FFMPEG = shutil.which("ffmpeg") is not None


def _video(path, seconds=60, audio=True):
    path.parent.mkdir(parents=True, exist_ok=True)
    cmd = ["ffmpeg", "-y", "-loglevel", "error",
           "-f", "lavfi", "-i", f"testsrc=size=320x240:rate=25:duration={seconds}"]
    if audio:
        cmd += ["-f", "lavfi", "-i", f"sine=frequency=440:duration={seconds}", "-c:a", "aac"]
    cmd += ["-c:v", "libx264", "-preset", "ultrafast", "-shortest", str(path)]
    subprocess.run(cmd, check=True, timeout=120)
    return store.upsert_file(str(path), path.name, path.stat().st_size, float(seconds), "h264", False)


def _probe(path):
    r = subprocess.run(["ffprobe", "-v", "error", "-show_entries", "format=duration:stream=codec_type",
                        "-of", "json", str(path)], capture_output=True, text=True, check=True)
    d = json.loads(r.stdout)
    return float(d["format"]["duration"]), {s["codec_type"] for s in d["streams"]}


# ── pure logic ───────────────────────────────────────────────────────────────

@pytest.mark.parametrize("duration,count", [(3600, 6), (120, 6), (20, 2), (8, 1)])
def test_default_moments_spread_inside_the_video(duration, count):
    starts = teaser.default_moments(duration)
    assert len(starts) == count
    length = teaser.clip_len(duration, len(starts))
    assert length * len(starts) <= teaser.MAX_TOTAL_S + 0.01
    assert all(0 <= s and s + length <= duration for s in starts)
    if count > 1:
        assert starts[0] >= duration * 0.1 - 0.01 and starts == sorted(starts)


def test_valid_rejects_bad_moments():
    assert teaser.valid([30, 10.5], 60) == [10.5, 30.0]
    for bad in ([], [1] * 7, [-1], [59.5], ["x"], None):
        with pytest.raises(teaser.TeaserError):
            teaser.valid(bad, 60)


def test_command_joins_every_moment_with_fades():
    cmd = teaser.teaser_cmd("/m/a.mp4", [10, 20, 30], 5, "/o.mp4", audio=True, nvenc=False)
    assert cmd.count("-i") == 3 and "libx264" in cmd
    fc = cmd[cmd.index("-filter_complex") + 1]
    assert "concat=n=3:v=1:a=1" in fc and "fade=t=out" in fc and "afade=t=in" in fc
    mute = teaser.teaser_cmd("/m/a.mp4", [10], 5, "/o.mp4", audio=False, nvenc=True)
    assert "anullsrc=r=48000:cl=stereo" in " ".join(mute) and "h264_nvenc" in mute


# ── with ffmpeg ──────────────────────────────────────────────────────────────

@pytest.fixture
def adult(lib, tmp_path, monkeypatch):
    client, Session, media = lib
    monkeypatch.setattr(teaser, "cache_dir", lambda: tmp_path / "teasers")
    bel = media / "Bellesa"
    with Session() as s:
        s.add(DownloadPath(name="Bellesa", path=str(bel), protected=True))
        s.commit()
    return client, Session, media, bel


@pytest.mark.skipif(not FFMPEG, reason="ffmpeg not installed")
def test_teaser_link_plays_about_30_seconds_and_never_the_whole_video(adult):
    client, Session, media, bel = adult
    vid = _video(bel / "Studio - Scene.mp4", 90)
    info = client.get(f"/api/shares/teaser/{vid}").json()
    assert info["adult"] is True and len(info["starts"]) == 6
    out = client.post("/api/shares", json={"kind": "video", "id": vid, "days": 7,
                                           "teaser": {"starts": info["starts"]}})
    assert out.status_code == 201 and out.json()["teaser"] is True
    token = out.json()["url"].rsplit("/", 1)[1]
    meta = client.get(f"/api/public/s/{token}").json()
    assert meta["teaser"] is True and meta["duration_sec"] == 30 and "path" not in json.dumps(meta)
    r = client.get(f"/api/public/s/{token}/video")
    assert r.status_code == 200 and r.headers["content-type"] == "video/mp4"
    clip = media.parent / "teaser.mp4"
    clip.write_bytes(r.content)
    duration, streams = _probe(clip)
    assert 28 <= duration <= 32 and streams == {"video", "audio"}
    assert client.get(f"/api/public/s/{token}/play").status_code == 404      # no way to the full video


@pytest.mark.skipif(not FFMPEG, reason="ffmpeg not installed")
def test_preview_and_silent_video(adult):
    client, Session, media, bel = adult
    vid = _video(bel / "Quiet.mp4", 40, audio=False)
    r = client.get(f"/api/shares/teaser/{vid}/clip", params={"starts": "5,15,25"})
    assert r.status_code == 200
    clip = media.parent / "quiet.mp4"
    clip.write_bytes(r.content)
    duration, streams = _probe(clip)
    assert "audio" in streams and duration == pytest.approx(teaser.clip_len(40, 3) * 3, abs=1.0)
    assert client.get(f"/api/shares/teaser/{vid}/clip", params={"starts": "500"}).status_code == 400
    assert client.get(f"/api/shares/teaser/{vid}/clip", params={"starts": "a"}).status_code == 400


def test_teasers_are_for_adult_videos_only(lib):
    from tests.test_movie_genres import _add
    client, Session, media = lib
    vid = _add(media / "Movies" / "Some Film (2020).mp4")
    r = client.post("/api/shares", json={"kind": "video", "id": vid, "teaser": {"starts": [10]}})
    assert r.status_code == 400
    assert client.get(f"/api/shares/teaser/{vid}").json()["adult"] is False


def test_bad_moments_are_refused(adult):
    from tests.test_movie_genres import _add
    client, Session, media, bel = adult
    vid = _add(bel / "Scene.mp4")                          # 60 s in the store
    r = client.post("/api/shares", json={"kind": "video", "id": vid, "teaser": {"starts": [90]}})
    assert r.status_code == 400
    with Session() as s:
        assert s.query(ShareLink).count() == 0


def test_prune_forgets_old_teasers(tmp_path, monkeypatch):
    import os
    import time
    monkeypatch.setattr(teaser, "cache_dir", lambda: tmp_path)
    old, new = tmp_path / "1-a.mp4", tmp_path / "1-b.mp4"
    old.write_bytes(b"x")
    new.write_bytes(b"x")
    t = time.time() - 60 * 86400
    os.utime(old, (t, t))
    assert teaser.prune() == 1 and new.exists() and not old.exists()
