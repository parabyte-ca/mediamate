"""Season packs: one download, several episodes. Each episode's steps carry its name,
Plex refreshes and the notification go out once after the last episode, and Cancel all
stops what's left."""
from unittest.mock import patch

import pytest

from app.models import Journey, MediaJob, PipelinePlan, Setting
from app.services import journeys
from app.services.media import store

from tests.test_pipeline import HAS_FFMPEG, _clip, _complete, _download, _rule, _run_queued_jobs, env  # noqa: F401

pytestmark = pytest.mark.skipif(not HAS_FFMPEG, reason="ffmpeg not installed")
EPS = ["The.Body.S01E01.mkv", "The.Body.S01E02.mkv", "The.Body.S01E03.mkv"]


def _pack(Session, tmp):
    _rule(Session)
    dl_id, dl_dir = _download(Session, tmp, name="The Body S01 1080p")
    pack = dl_dir / "The Body S01 1080p"
    pack.mkdir()
    for ep in EPS:
        _clip(pack / ep, seconds=2)
    with Session() as db:
        db.add_all([Setting(key="apprise_url", value="json://x"), Setting(key="plex_enabled", value="true"),
                    Setting(key="plex_url", value="http://plex"), Setting(key="plex_token", value="t")])
        db.commit()
    return dl_id


def test_one_refresh_and_one_summary_after_the_last_episode(env):  # noqa: F811
    _, Session, tmp = env
    dl_id = _pack(Session, tmp)
    with patch("app.services.media_servers.trigger_media_refresh") as refresh, \
         patch("app.services.apprise_notify.notify") as notify:
        _complete(Session, dl_id, content="/remote/dl/The Body S01 1080p")
        with Session() as db:
            snap = journeys.snapshot(db, db.query(Journey).one())
            labels = [s["label"] for s in snap["steps"] if s["kind"] == "encode"]
            assert labels == ["Encode · S01E01", "Encode · S01E02", "Encode · S01E03"]
            assert [s["kind"] for s in snap["steps"]].count("library") == 1
        job = store.dequeue_next_job()                     # first episode only
        from app.services.media import worker
        import threading
        worker._run_job(dict(job), threading.Event())
        assert refresh.call_count == 0 and notify.call_count == 0   # waits for the others
        with Session() as db:
            first = db.query(PipelinePlan).order_by(PipelinePlan.id).first()
            assert first.library_status == "deferred"
            snap = journeys.snapshot(db, db.query(Journey).one())
            tail = [s for s in snap["steps"] if s["kind"] in ("library", "notify")]
            assert [(s["kind"], s["status"]) for s in tail] == [("library", "pending"), ("notify", "pending")]
        _run_queued_jobs()
    refresh.assert_called_once()
    notify.assert_called_once()
    assert "3 of 3 files" in notify.call_args.args[2] and "The Body S01" in notify.call_args.args[2]
    with Session() as db:
        assert {(p.library_status, p.notify_status) for p in db.query(PipelinePlan)} == {("done", "done")}
        snap = journeys.snapshot(db, db.query(Journey).one())
        assert [s["kind"] for s in snap["steps"]].count("notify") == 1 and snap["steps"][-1]["status"] == "done"


def test_cancel_all_stops_the_rest_and_still_sends_one_summary(env):  # noqa: F811
    from fastapi.testclient import TestClient
    from app.database import get_db
    from app.main import app
    _, Session, tmp = env
    dl_id = _pack(Session, tmp)

    def _db():
        db = Session()
        try:
            yield db
        finally:
            db.close()
    app.dependency_overrides[get_db] = _db
    try:
        with patch("app.services.media_servers.trigger_media_refresh") as refresh, \
             patch("app.services.apprise_notify.notify") as notify:
            _complete(Session, dl_id, content="/remote/dl/The Body S01 1080p")
            import threading
            from app.services.media import worker
            worker._run_job(dict(store.dequeue_next_job()), threading.Event())   # E01 done
            with Session() as db:
                jid = db.query(Journey).one().id
            r = TestClient(app).post(f"/api/activity/{jid}/cancel-all").json()
            assert r["ok"] and r["waiting"] == 2 and "2 waiting" in r["msg"]
            assert store.dequeue_next_job() is None
        refresh.assert_called_once()
        assert "1 of 3 files" in notify.call_args.args[2] and "2 stopped or failed" in notify.call_args.args[2]
        with Session() as db:
            assert {j.status for j in db.query(MediaJob)} == {"done", "cancelled"}
        assert TestClient(app).post(f"/api/activity/{jid}/cancel-all").status_code == 409
    finally:
        app.dependency_overrides.pop(get_db, None)
