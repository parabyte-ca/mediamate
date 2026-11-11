"""Journeys: creation through the Download/MediaJob hooks, derived steps, stats, backfill."""
from datetime import datetime, timedelta, timezone

import pytest
from sqlalchemy import create_engine, text
from sqlalchemy.orm import sessionmaker

from app import models  # noqa: F401
from app.database import Base
from app.models import Download, Journey, MediaJob, WatchlistItem
from app.services import events, journeys
from app.services.media import store


@pytest.fixture
def Session(tmp_path, monkeypatch):
    engine = create_engine(f"sqlite:///{tmp_path / 'j.db'}", connect_args={"check_same_thread": False})
    Base.metadata.create_all(engine)
    monkeypatch.setattr(store, "engine", engine)
    journeys.install_download_hooks()
    yield sessionmaker(bind=engine)
    engine.dispose()


@pytest.fixture
def published(monkeypatch):
    seen = []
    events.bus._last_progress.clear()  # throttle state is global; job ids repeat across tests
    real = events.bus.publish
    monkeypatch.setattr(events.bus, "publish", lambda t, d: (seen.append((t, d)), real(t, d))[1])
    monkeypatch.setattr(events, "publish", events.bus.publish)
    return seen


def _dl(db, **kw):
    d = Download(title=kw.pop("title", "Show S01E01"), torrent_hash=kw.pop("torrent_hash", "abc"),
                 status=kw.pop("status", "downloading"), **kw)
    db.add(d)
    db.commit()
    return d


def _file(name="/m/a.mkv"):
    return store.upsert_file(name, name.rsplit("/", 1)[-1], 1000, 60.0, "h264", False)


def test_new_download_creates_exactly_one_journey(Session, published):
    with Session() as db:
        dl = _dl(db)
        dl.status = "completed"
        db.commit()
        rows = db.query(Journey).all()
        assert len(rows) == 1
        j = rows[0]
        assert (j.download_id, j.source, j.title, j.status) == (dl.id, "download", "Show S01E01", "done")
    types = [t for t, _ in published]
    assert types.count("journey.updated") == 2 and "stats" in types


def test_snapshot_steps_follow_download_status(Session):
    with Session() as db:
        item = WatchlistItem(title="Severance", search_query="severance", poster_url="http://p/x.jpg")
        db.add(item)
        db.commit()
        dl = _dl(db, watchlist_id=item.id, size_bytes=2_000_000_000)
        j = db.query(Journey).one()
        snap = journeys.snapshot(db, j)
        assert [s["kind"] for s in snap["steps"]] == ["found", "download"]
        assert snap["steps"][0]["detail"] == "Watchlist · Severance"
        assert snap["steps"][1]["status"] == "running"
        assert snap["poster_url"] == "http://p/x.jpg"
        assert snap["current_step"] == 1 and snap["status"] == "active"

        journeys.set_live(dl.id, {"progress": 0.42, "dlspeed": 1_000_000, "eta": 90})
        step = journeys.snapshot(db, j)["steps"][1]
        assert (step["progress"], step["speed"], step["eta"]) == (42.0, 1_000_000, 90)
        journeys.set_live(dl.id, None)

        dl.status = "failed"
        db.commit()
        db.refresh(j)
        assert j.status == "failed"


def test_stale_flag(Session):
    with Session() as db:
        old = datetime.now(timezone.utc) - timedelta(hours=5)
        _dl(db, last_progress_at=old)
        step = journeys.snapshot(db, db.query(Journey).one())["steps"][1]
        assert step["stale"] is True


def test_deleting_download_removes_its_journey(Session, published):
    with Session() as db:
        dl = _dl(db, status="completed")
        db.delete(dl)
        db.commit()
        assert db.query(Journey).count() == 0
    assert any(t == "journey.removed" for t, _ in published)


def test_rollback_does_not_create_journey(Session):
    with Session() as db:
        db.add(Download(title="x", status="downloading"))
        db.flush()
        db.rollback()
        db.add(Download(title="y", status="completed"))
        db.commit()
        assert [j.title for j in db.query(Journey).all()] == ["y"]


def test_media_jobs_attach_to_their_files_journey(Session, published):
    fid = _file()
    j1 = store.create_job(fid, "encode")
    store.set_job_status(j1, "done")
    j2 = store.create_job(fid, "split")
    with Session() as db:
        jobs = db.query(MediaJob).order_by(MediaJob.id).all()
        assert jobs[0].journey_id == jobs[1].journey_id
        j = db.query(Journey).one()
        assert (j.source, j.title, j.media_file_id) == ("library", "a.mkv", fid)
        snap = journeys.snapshot(db, j)
        assert [(s["kind"], s["status"]) for s in snap["steps"]] == [("encode", "done"), ("split", "pending")]
        assert j.status == "active"


def test_worker_style_finish_records_saved_bytes(Session):
    fid = _file()
    job = store.create_job(fid, "encode")
    store.dequeue_next_job()
    store.update_job_progress(job, 50, "frame=100")
    # Worker order: terminal status first, then the final message with saved bytes.
    store.set_job_status(job, "done")
    store.update_job_progress(job, 100.0, "Done — saved 60.0%", saved_bytes=600)
    with Session() as db:
        snap = journeys.snapshot(db, db.query(Journey).one())
        step = snap["steps"][0]
        assert (step["status"], step["saved_bytes"], step["detail"]) == ("done", 600, "Done — saved 60.0%")
        assert snap["saved_bytes"] == 600
        assert journeys.stats(db)["saved_today_bytes"] == 600


def test_running_job_progress_is_throttled_and_not_persisted(Session, published, monkeypatch):
    fid = _file()
    job = store.create_job(fid, "encode")
    store.dequeue_next_job()
    published.clear()
    for pct in (1, 2, 3):
        store.update_job_progress(job, pct, f"p{pct}")
    progress = [d for t, d in published if t == "progress"]
    assert len(progress) == 1 and progress[0]["progress"] == 1 and progress[0]["journey_id"]
    assert not any(t == "journey.updated" for t, _ in published)


def test_stats(Session):
    with Session() as db:
        _dl(db, title="a")
        _dl(db, title="b", status="failed")
        store.set_setting("queue_paused", "1")
        s = journeys.stats(db)
        assert (s["active"], s["failed_24h"], s["queue_paused"]) == (1, 1, True)


def test_backfill_is_idempotent(Session, monkeypatch):
    # Rows written before journeys existed: insert with the hooks bypassed.
    monkeypatch.setattr(journeys, "_after_commit", lambda s: None)
    with Session() as db:
        db.execute(text("INSERT INTO downloads (title, status) VALUES ('old', 'completed')"))
        db.commit()
    fid = _file()
    with store.connect() as conn:
        conn.execute("INSERT INTO media_jobs (file_id, job_type, status) VALUES (:f, 'encode', 'done')",
                     {"f": fid})
    with Session() as db:
        assert journeys.backfill(db) == 2
        assert journeys.backfill(db) == 0
        assert db.query(Journey).count() == 2
        assert db.query(MediaJob).filter(MediaJob.journey_id.is_(None)).count() == 0
        assert {j.status for j in db.query(Journey)} == {"done"}


def test_clearing_jobs_or_deleting_files_prunes_empty_journeys(Session, published):
    fid = _file("/m/a.mkv")
    job = store.create_job(fid, "encode")
    store.set_job_status(job, "done")
    other = _file("/m/b.mkv")
    store.create_job(other, "trim")  # still queued: its journey must survive
    assert store.clear_finished_jobs() == 1
    with Session() as db:
        assert [j.media_file_id for j in db.query(Journey)] == [other]
    assert any(t == "journey.removed" for t, _ in published)
    store.delete_file(other)
    with Session() as db:
        assert db.query(Journey).count() == 0


def test_current_step_of_finished_journey_skips_skipped_tail():
    steps = [{"status": "done"}, {"status": "skipped"}, {"status": "skipped"}]
    assert journeys._current_index(steps) == 0
    assert journeys._current_index([{"status": "done"}, {"status": "failed"}, {"status": "skipped"}]) == 1


def test_a_retried_encode_that_succeeds_is_done(Session, published):
    fid = _file()
    first = store.create_job(fid, "encode")
    store.set_job_status(first, "error")
    retry = store.create_job(fid, "encode")
    with Session() as db:
        snap = journeys.snapshot(db, db.query(Journey).one())
        assert [s["status"] for s in snap["steps"]] == ["retried", "pending"]
        assert db.query(Journey).one().status == "active"
    store.set_job_status(retry, "done")
    with Session() as db:
        j = db.query(Journey).one()
        snap = journeys.snapshot(db, j)
        assert [s["status"] for s in snap["steps"]] == ["retried", "done"]
        assert j.status == "done" and snap["current_step"] == 1


def test_a_failure_without_a_retry_still_fails(Session, published):
    fid = _file()
    enc = store.create_job(fid, "encode")
    store.set_job_status(enc, "done")
    split = store.create_job(fid, "split")
    store.set_job_status(split, "error")
    with Session() as db:
        j = db.query(Journey).one()
        assert [s["status"] for s in journeys.snapshot(db, j)["steps"]] == ["done", "failed"]
        assert j.status == "failed"


def test_repair_rederives_journeys_cached_as_failed(Session, published):
    fid = _file()
    first = store.create_job(fid, "encode")
    store.set_job_status(first, "error")
    retry = store.create_job(fid, "encode")
    store.set_job_status(retry, "done")
    with Session() as db:
        db.query(Journey).update({Journey.status: "failed"})       # as left by older versions
        db.commit()
        assert journeys.repair_retried(db) == 1
        assert db.query(Journey).one().status == "done"
