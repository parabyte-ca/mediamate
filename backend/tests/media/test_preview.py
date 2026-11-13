"""Library previews: thumbnails (cached, with a fail marker) and on-the-fly conversion
to browser-friendly MP4, including seeking and the limit on simultaneous players."""
import shutil
import subprocess
from pathlib import Path

import pytest

from app.services.media import preview, store

needs_ffmpeg = pytest.mark.skipif(not shutil.which("ffmpeg"), reason="ffmpeg not installed")


def _hevc_mkv(path: Path, seconds: int = 6) -> None:
    """An HEVC + AC-3 MKV: the kind of file a browser can't play directly."""
    subprocess.run(
        ["ffmpeg", "-y", "-loglevel", "error",
         "-f", "lavfi", "-i", f"testsrc=size=320x240:rate=25:duration={seconds}",
         "-f", "lavfi", "-i", f"sine=frequency=440:duration={seconds}",
         "-c:v", "libx265", "-preset", "ultrafast", "-x265-params", "log-level=error",
         "-c:a", "ac3", "-shortest", str(path)],
        check=True, timeout=120)


@pytest.fixture
def thumbs(tmp_path, monkeypatch):
    folder = tmp_path / "thumbs"
    monkeypatch.setattr(preview, "cache_dir", lambda: folder)
    return folder


@pytest.fixture
def clip(tmp_media_roots, media_db):
    path = Path(tmp_media_roots[0]) / "Show S01E01.mkv"
    _hevc_mkv(path)
    fid = store.upsert_file(str(path), path.name, path.stat().st_size, 6.0, "hevc", False)
    return fid, path


# ── Pure helpers ──────────────────────────────────────────────────────────────

def test_thumbnail_is_taken_ten_percent_in_and_at_most_five_minutes():
    assert preview.thumb_time(60) == 6.0
    assert preview.thumb_time(7200) == 300.0
    assert preview.thumb_time(None) == 1.0


def test_play_uses_the_gpu_when_there_is_one():
    assert "h264_nvenc" in preview.play_cmd("/m/a.mkv", 0, nvenc=True)
    cpu = preview.play_cmd("/m/a.mkv", 12.5, nvenc=False)
    assert "libx264" in cpu and cpu[cpu.index("-ss") + 1] == "12.5"
    assert "-ss" not in preview.play_cmd("/m/a.mkv", 0, nvenc=False)


# ── Thumbnails ────────────────────────────────────────────────────────────────

@needs_ffmpeg
def test_thumbnail_is_made_in_the_background_then_cached(api, clip, thumbs, monkeypatch):
    from app.services.media import thumbs as worker
    worker.reset()
    fid, _ = clip

    real_run = preview.subprocess.run
    monkeypatch.setattr(preview.subprocess, "run", lambda *a, **k: (_ for _ in ()).throw(AssertionError("ffmpeg in the request")))
    r = api.get(f"/api/media/files/{fid}/thumb")
    assert r.status_code == 202 and r.headers["retry-after"] == "5"   # never waits for ffmpeg
    assert worker.status()["queued"] == 1

    monkeypatch.setattr(preview.subprocess, "run", real_run)
    assert worker.run_once() == 1
    r = api.get(f"/api/media/files/{fid}/thumb")
    assert r.status_code == 200 and r.headers["content-type"] == "image/jpeg"
    assert r.content[:3] == b"\xff\xd8\xff"
    assert len(list(thumbs.glob("*.jpg"))) == 1


@needs_ffmpeg
def test_an_unreadable_file_is_marked_and_not_retried_for_a_while(api, tmp_media_roots, media_db, thumbs):
    import os
    import time
    from app.services.media import thumbs as worker
    worker.reset()
    bad = Path(tmp_media_roots[0]) / "broken.mkv"
    bad.write_bytes(b"not a video")
    fid = store.upsert_file(str(bad), bad.name, 11, 60.0, "h264", False)
    assert api.get(f"/api/media/files/{fid}/thumb").status_code == 202
    worker.run_once()
    [fail] = list(thumbs.glob("*.fail"))
    assert api.get(f"/api/media/files/{fid}/thumb").status_code == 404
    assert worker.missing_ids() == []                        # not queued again yet
    old = time.time() - (preview.FAIL_DAYS + 1) * 86400
    os.utime(fail, (old, old))
    assert worker.missing_ids() == [fid]                     # after a week, tried again


def test_a_timeout_is_retried_not_marked(tmp_media_roots, media_db, thumbs, monkeypatch):
    import subprocess as sp
    from app.services.media import thumbs as worker
    worker.reset()
    f = Path(tmp_media_roots[0]) / "big.mkv"
    f.write_bytes(b"x")
    fid = store.upsert_file(str(f), f.name, 1, 60.0, "hevc", False)
    calls = []

    def slow(cmd, **k):
        calls.append(cmd)
        raise sp.TimeoutExpired(cmd, k.get("timeout"))
    monkeypatch.setattr(preview.subprocess, "run", slow)
    worker.add([fid])
    assert worker.run_once() == worker.MAX_TRIES             # tried a few times, then left for later
    assert not list(thumbs.glob("*.fail")) and worker.status()["queued"] == 0
    assert "-skip_frame" in calls[0] and "nokey" in calls[0]


def test_force_endpoints(api, tmp_media_roots, media_db, thumbs, monkeypatch):
    from app.services.media import thumbs as worker
    worker.reset()
    ids = []
    for n in range(3):
        f = Path(tmp_media_roots[0]) / f"v{n}.mkv"
        f.write_bytes(b"x")
        ids.append(store.upsert_file(str(f), f.name, 1, 60.0, "h264", False))
    thumbs.mkdir(parents=True, exist_ok=True)
    st = (Path(tmp_media_roots[0]) / "v0.mkv").stat()
    (thumbs / f"{ids[0]}-{st.st_size}-{int(st.st_mtime)}.fail").touch()
    assert api.post("/api/media/thumbs/missing").json()["queued"] == 2           # the failed one waits
    assert api.post("/api/media/thumbs/missing?retry_failed=true").json()["queued"] == 1
    assert not list(thumbs.glob("*.fail"))
    s = api.get("/api/media/thumbs/status").json()
    assert s["queued"] == 3 and s["running"]
    worker.reset()
    assert api.post("/api/media/thumbs/files", json={"ids": ids[:2]}).json()["queued"] == 2


def test_the_worker_steps_aside_for_encodes_and_previews(monkeypatch):
    from app.services.media import thumbs as worker
    monkeypatch.setattr(preview, "players_busy", lambda: True)
    assert worker._busy()
    monkeypatch.setattr(preview, "players_busy", lambda: False)
    monkeypatch.setattr(worker.store, "get_active_jobs", lambda: [{"status": "running"}])
    assert worker._busy()
    monkeypatch.setattr(worker.store, "get_active_jobs", lambda: [{"status": "queued"}])
    assert not worker._busy()


def test_files_outside_the_media_folders_are_not_served(api, tmp_path, media_db, thumbs):
    outside = tmp_path / "secret.mkv"
    outside.write_bytes(b"x")
    fid = store.upsert_file(str(outside), outside.name, 1, 1.0, "h264", False)
    assert api.get(f"/api/media/files/{fid}/thumb").status_code == 404
    assert api.get(f"/api/media/files/{fid}/play").status_code == 404
    assert api.get("/api/media/files/9999/play").status_code == 404


# ── Conversion ────────────────────────────────────────────────────────────────

@needs_ffmpeg
def test_play_streams_browser_friendly_mp4(api, clip, tmp_path):
    fid, _ = clip
    r = api.get(f"/api/media/files/{fid}/play")
    assert r.status_code == 200 and r.headers["content-type"] == "video/mp4"
    assert r.content[4:8] == b"ftyp"
    out = tmp_path / "out.mp4"
    out.write_bytes(r.content)
    probe = subprocess.run(
        ["ffprobe", "-v", "error", "-show_entries", "stream=codec_name", "-of", "csv=p=0", str(out)],
        capture_output=True, text=True, check=True).stdout.split()
    assert probe == ["h264", "aac"]


@needs_ffmpeg
def test_play_starts_where_asked(api, clip, tmp_path):
    fid, _ = clip
    whole = api.get(f"/api/media/files/{fid}/play").content
    later = api.get(f"/api/media/files/{fid}/play?start=4").content
    assert 0 < len(later) < len(whole)


def test_too_many_players_is_a_clear_429(api, clip_stub, monkeypatch):
    fid = clip_stub

    def busy(*a, **k):
        raise preview.Busy("2 previews are already converting. Close one and try again.")
    monkeypatch.setattr(preview, "open_player", busy)
    r = api.get(f"/api/media/files/{fid}/play")
    assert r.status_code == 429 and "already converting" in r.json()["msg"]


def test_the_player_slot_is_freed_when_the_stream_ends(monkeypatch):
    class FakeProc:
        def __init__(self, *a, **k):
            import io
            self.stdout = io.BytesIO(b"abc")
            self.killed = False
        def poll(self):
            return 0
        def kill(self):
            self.killed = True
        def wait(self):
            return 0
    monkeypatch.setattr(preview.subprocess, "Popen", FakeProc)
    for _ in range(preview.MAX_PLAYERS + 2):     # would raise Busy if slots leaked
        p = preview.open_player("/m/a.mkv", 0)
        assert b"".join(p.chunks()) == b"abc"
        p.stop()                                  # a second stop is harmless


@pytest.fixture
def clip_stub(tmp_media_roots, media_db):
    path = Path(tmp_media_roots[0]) / "a.mkv"
    path.write_bytes(b"x")
    return store.upsert_file(str(path), path.name, 1, 1.0, "h264", False)
