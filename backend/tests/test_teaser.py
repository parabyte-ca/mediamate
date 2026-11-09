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


def _wait(client, vid, starts, timeout=120):
    import time
    k = client.post(f"/api/shares/teaser/{vid}/build", json={"starts": starts}).json()["key"]
    end = time.monotonic() + timeout
    while time.monotonic() < end:
        st = client.get(f"/api/shares/teaser/build/{k}").json()
        if st["state"] in ("done", "error"):
            return st
        time.sleep(0.2)
    raise AssertionError("teaser never finished")


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
    cmd = teaser.teaser_cmd("/m/a.mp4", [10, 20, 30], 5, "/o.mp4", audio=True, nvenc=False, video_index=1)
    assert cmd.count("-i") == 3 and "libx264" in cmd and "-progress" in cmd
    fc = cmd[cmd.index("-filter_complex") + 1]
    assert "[0:v:1]" in fc and "concat=n=3:v=1:a=1" in fc and "fade=t=out" in fc and "afade=t=in" in fc
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
    assert _wait(client, vid, info["starts"])["state"] == "done"
    out = client.post("/api/shares", json={"kind": "video", "id": vid, "days": 7,
                                           "teaser": {"starts": info["starts"]}})
    assert out.status_code == 201 and out.json()["teaser"] is True
    token = out.json()["url"].rsplit("/", 1)[1]
    meta = client.get(f"/api/public/s/{token}").json()
    assert meta["ready"] is True and meta["teaser"] is True and meta["duration_sec"] == 30 and "path" not in json.dumps(meta)
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
    assert client.get(f"/api/shares/teaser/{vid}/clip", params={"starts": "5,15,25"}).status_code == 409
    assert _wait(client, vid, [5, 15, 25])["state"] == "done"
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


# ── background builds ────────────────────────────────────────────────────────

class _FakeProc:
    def __init__(self, cmd, ok=True, err=""):
        self.dest, self.ok = cmd[-1], ok
        self.stdout = iter(["out_time_ms=15000000\n", "progress=end\n"])
        self.stderr = __import__("io").StringIO(err)
        self.returncode = None

    def wait(self):
        if self.ok:
            open(self.dest, "wb").write(b"mp4")
        self.returncode = 0 if self.ok else 1
        return self.returncode

    def kill(self):
        pass


def _fake(monkeypatch, tmp_path, ok=True, err=""):
    calls = []
    monkeypatch.setattr(teaser, "cache_dir", lambda: tmp_path / "teasers")
    monkeypatch.setattr(teaser, "probe", lambda p: (0, True))
    monkeypatch.setattr(teaser, "_jobs", {})

    def popen(cmd, **kw):
        calls.append(cmd)
        return _FakeProc(cmd, ok, err)
    monkeypatch.setattr(teaser.subprocess, "Popen", popen)
    src = tmp_path / "v.mp4"
    src.write_bytes(b"x")
    return calls, str(src)


def test_same_moments_build_once(monkeypatch, tmp_path):
    calls, src = _fake(monkeypatch, tmp_path)
    p1 = teaser.build(1, src, [10.0, 20.0], 60)
    k = teaser.start(1, src, [10.0, 20.0], 60)
    assert p1.read_bytes() == b"mp4" and teaser.status(k)["state"] == "done" and len(calls) == 1


def test_failure_is_reported_and_retried(monkeypatch, tmp_path):
    calls, src = _fake(monkeypatch, tmp_path, ok=False, err="noise\nStream map '0:a:0' matches no streams\n")
    with pytest.raises(teaser.TeaserError, match="matches no streams"):
        teaser.build(1, src, [10.0], 60)
    with pytest.raises(teaser.TeaserError):
        teaser.build(1, src, [10.0], 60)
    assert len(calls) == 2                      # an error isn't cached: the next try runs again


def test_teasers_never_wait_for_preview_slots(monkeypatch, tmp_path):
    from app.services.media import preview
    calls, src = _fake(monkeypatch, tmp_path)
    for _ in range(preview.MAX_PLAYERS):
        assert preview._player_slots.acquire(blocking=False)
    try:
        assert teaser.build(1, src, [5.0], 60, wait=5).is_file()
    finally:
        for _ in range(preview.MAX_PLAYERS):
            preview._player_slots.release()


@pytest.mark.skipif(not FFMPEG, reason="ffmpeg not installed")
def test_cover_art_stream_is_skipped(tmp_path, monkeypatch):
    monkeypatch.setattr(teaser, "cache_dir", lambda: tmp_path / "teasers")
    src, art, out = tmp_path / "plain.mp4", tmp_path / "art.jpg", tmp_path / "with-art.mp4"
    subprocess.run(["ffmpeg", "-y", "-loglevel", "error", "-f", "lavfi", "-i", "testsrc=size=320x240:rate=25:duration=20",
                    "-f", "lavfi", "-i", "sine=duration=20", "-c:v", "libx264", "-preset", "ultrafast", "-c:a", "aac",
                    "-shortest", str(src)], check=True)
    subprocess.run(["ffmpeg", "-y", "-loglevel", "error", "-f", "lavfi", "-i", "color=red:size=64x64", "-frames:v", "1",
                    str(art)], check=True)
    subprocess.run(["ffmpeg", "-y", "-loglevel", "error", "-i", str(art), "-i", str(src), "-map", "0", "-map", "1",
                    "-c", "copy", "-disposition:v:0", "attached_pic", str(out)], check=True)
    vi, audio = teaser.probe(str(out))
    r = subprocess.run(["ffprobe", "-v", "error", "-select_streams", f"v:{vi}", "-show_entries",
                        "stream_disposition=attached_pic", "-of", "csv=p=0", str(out)], capture_output=True, text=True)
    assert r.stdout.strip() == "0" and audio is True          # the real picture, not the cover
    assert teaser.build(9, str(out), [2.0, 8.0], 20).stat().st_size > 0


def test_probe_skips_a_leading_cover(monkeypatch):
    import types
    js = '{"streams":[{"codec_type":"video","disposition":{"attached_pic":1}},{"codec_type":"video","disposition":{"attached_pic":0}}]}'
    monkeypatch.setattr(teaser.subprocess, "run", lambda *a, **k: types.SimpleNamespace(stdout=js))
    assert teaser.probe("/x.mp4") == (1, False)


def test_public_link_waits_for_its_teaser(adult, monkeypatch):
    from tests.test_movie_genres import _add
    client, Session, media, bel = adult
    vid = _add(bel / "Scene.mp4")
    started = []
    monkeypatch.setattr(teaser, "start", lambda *a: started.append(a) or "k")
    out = client.post("/api/shares", json={"kind": "video", "id": vid, "teaser": {"starts": [10]}})
    assert out.status_code == 201 and started
    token = out.json()["url"].rsplit("/", 1)[1]
    assert client.get(f"/api/public/s/{token}").json()["ready"] is False
    r = client.get(f"/api/public/s/{token}/video")
    assert r.status_code == 503 and r.headers.get("retry-after") == "10"
