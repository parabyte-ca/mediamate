import os

import pytest

from app.routers import media as media_router
from app.services.media import config, encoder, store, worker


def _put_trash(media_root, day, rel_name, content=b"x"):
    day_dir = os.path.join(media_root, ".trash", day)
    os.makedirs(day_dir, exist_ok=True)
    with open(os.path.join(day_dir, rel_name), "wb") as f:
        f.write(content)


def _media(root, name="Show.S01E01.mkv", content=b"0123456789"):
    path = os.path.join(root, name)
    with open(path, "wb") as f:
        f.write(content)
    fid = store.upsert_file(path, name, len(content), 60.0, "h264", False)
    return fid, path


@pytest.fixture(autouse=True)
def _keep_runtime_state(monkeypatch):
    for attr, _ in config.SETTINGS_MAP.values():
        monkeypatch.setattr(config, attr, getattr(config, attr))
    monkeypatch.setattr(worker, "_paused", worker._paused)
    yield
    worker.resume()


# --- files ---

def test_list_files_includes_category(api, tmp_media_roots):
    _media(tmp_media_roots[0])
    rows = api.get("/api/media/files").json()
    assert len(rows) == 1 and rows[0]["category"] == "media1"


def test_stream_serves_ranges(api, tmp_media_roots):
    fid, _ = _media(tmp_media_roots[0])
    r = api.get(f"/api/media/files/{fid}/stream", headers={"Range": "bytes=2-4"})
    assert r.status_code == 206 and r.content == b"234"
    d = api.get(f"/api/media/files/{fid}/stream?download=1")
    assert d.headers["content-disposition"].startswith("attachment")


def test_stream_refuses_files_outside_media_roots(api, tmp_path):
    outside = tmp_path / "secret.mkv"
    outside.write_bytes(b"x")
    fid = store.upsert_file(str(outside), "secret.mkv", 1, 1.0, "h264", False)
    assert api.get(f"/api/media/files/{fid}/stream").status_code == 404


def test_rename_keeps_extension_and_blocks_collisions(api, tmp_media_roots):
    fid, path = _media(tmp_media_roots[0])
    _media(tmp_media_roots[0], "Taken.mkv")
    r = api.post(f"/api/media/files/{fid}/rename", json={"new_name": "New Name.mp4"})
    assert r.json() == {"ok": True, "filename": "New Name.mkv"}
    assert os.path.exists(os.path.join(tmp_media_roots[0], "New Name.mkv"))
    assert api.post(f"/api/media/files/{fid}/rename", json={"new_name": "Taken"}).status_code == 409
    assert api.post(f"/api/media/files/{fid}/rename", json={"new_name": "  "}).status_code == 400


def test_delete_moves_to_trash(api, tmp_media_roots):
    fid, path = _media(tmp_media_roots[0])
    assert api.post(f"/api/media/files/{fid}/delete").json() == {"ok": True}
    assert not os.path.exists(path)
    assert api.get("/api/media/trash").json()["file_count"] == 1
    assert store.get_file(fid) is None


def test_active_job_blocks_rename_and_delete(api, tmp_media_roots):
    fid, _ = _media(tmp_media_roots[0])
    api.post("/api/media/queue", json={"file_id": fid, "job_type": "encode"})
    assert api.post(f"/api/media/files/{fid}/delete").status_code == 409
    assert api.post(f"/api/media/files/{fid}/rename", json={"new_name": "x"}).status_code == 409


def test_anthology_override_and_skip(api, tmp_media_roots):
    fid, _ = _media(tmp_media_roots[0])
    api.post(f"/api/media/files/{fid}/anthology", json={"value": True})
    assert store.get_file(fid)["is_anthology"] == 1
    assert api.post(f"/api/media/files/{fid}/skip").json() == {"ok": True}
    assert store.get_file(fid)["status"] == "skipped"
    assert api.post("/api/media/files/999/skip").status_code == 404


def test_purge_missing(api, tmp_media_roots):
    fid, path = _media(tmp_media_roots[0])
    os.remove(path)
    assert api.post("/api/media/files/purge").json() == {"ok": True, "removed": 1}


# --- queue & jobs ---

def test_queue_validation_and_duplicate_guard(api, tmp_media_roots):
    fid, _ = _media(tmp_media_roots[0])
    assert api.post("/api/media/queue", json={"file_id": fid, "job_type": "nope"}).status_code == 400
    assert api.post("/api/media/queue", json={"file_id": fid, "job_type": "encode",
                                              "target_resolution": "999p"}).status_code == 400
    assert api.post("/api/media/queue", json={"file_id": 999, "job_type": "encode"}).status_code == 404
    r = api.post("/api/media/queue", json={"file_id": fid, "job_type": "encode"})
    assert r.json()["ok"] and store.get_file(fid)["status"] == "queued"
    assert api.post("/api/media/queue", json={"file_id": fid, "job_type": "split"}).status_code == 409


def test_cancel_queued_job_and_clear(api, tmp_media_roots):
    fid, _ = _media(tmp_media_roots[0])
    job_id = api.post("/api/media/queue", json={"file_id": fid, "job_type": "trim"}).json()["job_id"]
    assert api.post(f"/api/media/files/{fid}/cancel").json()["ok"]
    assert store.get_job(job_id)["status"] == "cancelled"
    assert store.get_file(fid)["status"] == "pending"
    assert api.post(f"/api/media/jobs/{job_id}/cancel").status_code == 409
    assert api.get("/api/media/jobs").json()[0]["id"] == job_id
    assert api.post("/api/media/jobs/clear").json() == {"ok": True, "cleared": 1}


def test_cancel_running_job_signals_worker(api, tmp_media_roots, monkeypatch):
    fid, _ = _media(tmp_media_roots[0])
    api.post("/api/media/queue", json={"file_id": fid, "job_type": "encode"})
    job = store.dequeue_next_job()
    sent = []
    monkeypatch.setattr(worker, "cancel_job", lambda jid: sent.append(jid) or True)
    assert api.post(f"/api/media/jobs/{job['id']}/cancel").json()["ok"]
    assert sent == [job["id"]]


def test_pause_resume_persists(api):
    assert api.post("/api/media/queue/pause").json()["paused"] is True
    assert store.get_setting("queue_paused") == "1"
    assert api.get("/api/media/stats").json()["paused"] is True
    api.post("/api/media/queue/resume")
    assert store.get_setting("queue_paused") == "0"


def test_scan_runs_and_rejects_overlap(api, monkeypatch):
    media_router._scan_status["running"] = True
    try:
        assert api.post("/api/media/scan").status_code == 409
    finally:
        media_router._scan_status["running"] = False
    ran = []
    monkeypatch.setattr(media_router.scanner, "scan_all", lambda: ran.append(1))
    assert api.post("/api/media/scan").json()["ok"]


# --- trash (FileSplitter's test_trash_routes.py) ---

def _total(api):
    t = api.get("/api/media/trash").json()
    return {"size_bytes": t["size_bytes"], "file_count": t["file_count"]}


def test_get_trash_empty(api):
    t = api.get("/api/media/trash").json()
    assert _total(api) == {"size_bytes": 0, "file_count": 0}
    assert t["video"] == {"size_bytes": 0, "file_count": 0} and t["music"] == {"size_bytes": 0, "file_count": 0}


def test_empty_trash_route_clears_and_reports_freed(api, tmp_media_roots):
    root1, root2 = tmp_media_roots
    _put_trash(root1, "2026-01-01", "a.mp4", b"12345")
    _put_trash(root2, "2026-10-21", "b.mp4", b"12")
    assert _total(api) == {"size_bytes": 7, "file_count": 2}
    assert api.get("/api/media/trash").json()["video"]["size_bytes"] == 7
    assert api.post("/api/media/trash/empty?kind=bogus").status_code == 422
    body = api.post("/api/media/trash/empty?kind=video").json()
    assert body == {"ok": True, "freed_bytes": 7, "removed_files": 2}
    assert _total(api) == {"size_bytes": 0, "file_count": 0}


# --- settings ---

def test_settings_get_hides_secret(api, monkeypatch):
    monkeypatch.setattr(config, "TPDB_API_KEY", "sekrit")
    body = api.get("/api/media/settings").json()
    assert "tpdb_api_key" not in body and body["tpdb_api_key_set"] is True
    assert body["resolutions"][0] == "original" and "nvenc_available" in body


def test_settings_post_applies_and_persists(api):
    r = api.post("/api/media/settings", json={"x265_crf": 21, "split_keywords": "Box,Set", "bogus": 1})
    assert r.json() == {"ok": True}
    assert config.X265_CRF == 21 and config.SPLIT_KEYWORDS == ["Box", "Set"]
    assert store.get_setting("x265_crf") == "21"
    bad = api.post("/api/media/settings", json={"x265_crf": "high"})
    assert bad.status_code == 400 and bad.json()["rejected"] == ["x265_crf"]


def test_saved_bytes_and_gpu_recheck(api, monkeypatch):
    assert api.post("/api/media/settings/saved-bytes", json={"value": 42}).json()["total_saved_bytes"] == 42
    assert api.get("/api/media/stats").json()["total_saved_bytes"] == 42
    monkeypatch.setattr(encoder, "_nvenc_probe_once", lambda: False)
    assert api.post("/api/media/encoder/recheck-gpu").json()["nvenc_available"] is False


def test_bulk_queue_skips_busy_and_validates(api, tmp_media_roots):
    a, _ = _media(tmp_media_roots[0], "a.mkv")
    b, _ = _media(tmp_media_roots[0], "b.mkv")
    api.post("/api/media/queue", json={"file_id": b, "job_type": "encode"})
    r = api.post("/api/media/queue/bulk", json={"file_ids": [a, b, 999], "job_type": "encode",
                                                "target_resolution": "720p"}).json()
    assert (r["queued"], r["skipped"]) == (1, 2)
    assert store.get_job(r["job_ids"][0])["target_resolution"] == "720p"
    assert store.get_file(a)["status"] == "queued"
    assert api.post("/api/media/queue/bulk", json={"file_ids": [a], "job_type": "nope"}).status_code == 400
    assert api.post("/api/media/queue/bulk", json={"file_ids": [], "job_type": "encode"}).status_code == 400


def test_bulk_skip_and_delete(api, tmp_media_roots):
    a, pa = _media(tmp_media_roots[0], "a.mkv")
    b, pb = _media(tmp_media_roots[0], "b.mkv")
    c, _ = _media(tmp_media_roots[0], "c.mkv")
    api.post("/api/media/queue", json={"file_id": c, "job_type": "trim"})
    assert api.post("/api/media/files/bulk", json={"action": "skip", "file_ids": [a, c]}).json() == \
        {"ok": True, "done": 1, "skipped": 1, "errors": []}
    assert store.get_file(a)["status"] == "skipped"
    r = api.post("/api/media/files/bulk", json={"action": "delete", "file_ids": [a, b]}).json()
    assert r["done"] == 2 and not os.path.exists(pa) and not os.path.exists(pb)
    assert api.get("/api/media/trash").json()["file_count"] == 2
    assert api.post("/api/media/files/bulk", json={"action": "explode", "file_ids": [c]}).status_code == 400
