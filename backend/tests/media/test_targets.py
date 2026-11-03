"""Target formats per folder: which files are already right, what gets shrunk, what's flagged."""
import os
from unittest.mock import patch

import pytest
from sqlalchemy import create_engine, text
from sqlalchemy.orm import Session

from app.models import DownloadPath, PipelinePlan, QualityProfile, Setting
from app.services.media import store, targets
from app.services.media.codec_detector import ProbeResult


def row(path="/m/tv/Show.S01E01.mkv", codec="hevc", width=1920, height=1080, status="pending", id=1):
    return {"id": id, "path": path, "codec": codec, "width": width, "height": height, "status": status}


FOLDERS = [
    {"id": 1, "root": "/m/bellesa", "kind": "custom", "min": 720, "max": 720, "shrink": True, "name": "bellesa"},
    {"id": 2, "root": "/m/tv", "kind": "tv", "min": None, "max": None, "shrink": False, "name": "TV Shows"},
    {"id": 3, "root": "/m/movies", "kind": "movies", "min": None, "max": None, "shrink": False, "name": "Movies"},
    {"id": 4, "root": "/m/mixed", "kind": "auto", "min": None, "max": None, "shrink": False, "name": "Downloads"},
]


class TestResolution:
    @pytest.mark.parametrize("w,h,res", [
        (1280, 720, 720), (1920, 1080, 1080), (1920, 800, 1080), (1280, 536, 720), (3840, 1600, 2160),
        (3840, 2160, 2160), (720, 480, 480), (0, 0, 0), (None, None, 0), (2560, 1440, 1440),
    ])
    def test_classes(self, w, h, res):
        assert targets.resolution_of(w, h) == res


class TestCheck:
    def test_bellesa_hevc_720p_is_in_target(self):
        c = targets.check(row("/m/bellesa/a.mp4", "hevc", 1280, 720), FOLDERS)
        assert c.meets and not c.above and not c.below and c.shrink_to == "original"
        assert "Already in target format" in c.reason

    def test_bellesa_h264_1080p_encodes_down_to_720(self):
        c = targets.check(row("/m/bellesa/a.mp4", "h264", 1920, 1080), FOLDERS)
        assert not c.meets and c.above and c.shrink_to == "720p"

    def test_bellesa_hevc_1080p_is_left_but_flagged(self):
        c = targets.check(row("/m/bellesa/a.mp4", "hevc", 1920, 1080), FOLDERS)
        assert c.meets and c.above and c.shrink_to == "original"

    def test_tv_4k_h264_is_encoded_without_shrinking(self):
        c = targets.check(row("/m/tv/Show.S01E01.mkv", "h264", 3840, 2160), FOLDERS)
        assert not c.meets and c.above and c.shrink_to == "original"

    def test_movie_720p_hevc_meets_but_below_preferred(self):
        c = targets.check(row("/m/movies/Film.2020.mkv", "hevc", 1280, 720), FOLDERS)
        assert c.meets and c.below and "lower than preferred" in c.reason

    def test_auto_by_name(self):
        ep = targets.check(row("/m/mixed/Show.S02E03.720p.mkv", "hevc", 1280, 720), FOLDERS)
        mv = targets.check(row("/m/mixed/Film.2021.720p.mkv", "hevc", 1280, 720), FOLDERS)
        assert ep.target.kind == "tv" and not ep.below
        assert mv.target.kind == "movies" and mv.below

    def test_deepest_folder_wins_and_outside_is_auto(self):
        nested = FOLDERS + [{"id": 9, "root": "/m/tv/kids", "kind": "custom", "min": 480, "max": 720,
                             "shrink": False, "name": "Kids"}]
        assert targets.check(row("/m/tv/kids/a.S01E01.mkv", "hevc", 1920, 1080), sorted(
            nested, key=lambda f: -len(f["root"]))).above
        assert targets.check(row("/elsewhere/Film.2020.mkv"), FOLDERS).target.kind == "movies"

    def test_custom_range_normalised(self):
        t = targets.folder_target("custom", 1080, 720, None, "x.mkv")
        assert (t.min, t.max, t.shrink) == (720, 1080, False)
        t = targets.folder_target("custom", 999, None, True, "x.mkv")
        assert (t.min, t.max) == (720, 720)


# ── with the database ────────────────────────────────────────────────────────


@pytest.fixture
def lib(media_db, tmp_path):
    """Two scan folders on disk: bellesa (custom 720) and TV."""
    bell, tv = tmp_path / "bellesa", tmp_path / "tv"
    bell.mkdir()
    tv.mkdir()
    with Session(bind=media_db) as db:
        db.add(DownloadPath(name="bellesa", path=str(bell), local_path=str(bell), downloads=False, scan=True,
                            target="custom", target_min=720, target_max=720, target_shrink=True))
        db.add(DownloadPath(name="TV Shows", path=str(tv), local_path=str(tv), downloads=False, scan=True,
                            target="tv"))
        db.commit()
    return {"bellesa": str(bell), "tv": str(tv), "engine": media_db}


def _add(path, codec, w, h, status="pending"):
    fid = store.upsert_file(path=path, filename=os.path.basename(path), size_bytes=10, duration_sec=60,
                            codec=codec, is_anthology=False, width=w, height=h)
    if status != "pending":
        store.set_file_status(fid, status)
    return fid


def test_scanner_marks_files_already_in_target(lib, monkeypatch):
    from app.services.media import config, scanner
    for name in ("a.mp4", "b.mp4"):
        open(os.path.join(lib["bellesa"], name), "wb").close()
    monkeypatch.setattr(config, "MEDIA_PATHS", [lib["bellesa"]])
    probes = {"a.mp4": ProbeResult("hevc", 60, 10, 1280, 720), "b.mp4": ProbeResult("h264", 60, 10, 1920, 1080)}
    with patch("app.services.media.codec_detector.probe_with_error",
               side_effect=lambda p: (probes[os.path.basename(p)], None)):
        scanner.scan_all()
    by_name = {r["filename"]: r["status"] for r in store.get_all_files()}
    assert by_name == {"a.mp4": "ok", "b.mp4": "pending"}
    assert store.get_stats()["ok"] == 1


def test_encode_job_shrinks_to_the_folder_target(lib):
    fid = _add(os.path.join(lib["bellesa"], "big.mp4"), "h264", 1920, 1080)
    jid = store.create_job(fid, "encode")
    assert store.get_job(jid)["target_resolution"] == "720p"
    tv = _add(os.path.join(lib["tv"], "Show.S01E01.mkv"), "h264", 3840, 2160)
    assert store.get_job(store.create_job(tv, "encode"))["target_resolution"] == "original"
    picked = _add(os.path.join(lib["bellesa"], "c.mp4"), "h264", 1920, 1080)
    assert store.get_job(store.create_job(picked, "encode", "480p"))["target_resolution"] == "480p"


def test_reclassify_after_a_target_change(lib):
    ok = _add(os.path.join(lib["tv"], "A.S01E01.mkv"), "hevc", 1280, 720, "ok")
    done = _add(os.path.join(lib["tv"], "B.S01E01.mkv"), "hevc", 1280, 720, "done")
    h264 = _add(os.path.join(lib["tv"], "C.S01E01.mkv"), "h264", 1280, 720, "ok")   # stale
    other = _add(os.path.join(lib["bellesa"], "x.mp4"), "hevc", 1280, 720)
    assert targets.reclassify(lib["tv"]) == 1
    statuses = {r["id"]: r["status"] for r in store.get_all_files()}
    assert statuses == {ok: "ok", done: "done", h264: "pending", other: "pending"}


def test_folder_api_guesses_and_reclassifies(lib, tmp_path):
    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    from app.database import get_db
    from app.routers import paths

    app = FastAPI()
    app.include_router(paths.router, prefix="/api")

    def _db():
        with Session(bind=lib["engine"]) as s:
            yield s
    app.dependency_overrides[get_db] = _db
    c = TestClient(app)
    mv = tmp_path / "Movies"
    mv.mkdir()
    r = c.post("/api/paths", json={"name": "Movies", "path": str(mv), "downloads": False, "scan": True}).json()
    assert r["target"] == "movies" and "1080p–4K" in r["target_summary"]
    b = c.post("/api/paths", json={"name": "Bellesa 2", "path": str(tmp_path), "downloads": False}).json()
    assert (b["target"], b["target_min"], b["target_max"], b["target_shrink"]) == ("custom", 720, 720, True)
    fid = _add(os.path.join(str(mv), "Film.2020.mkv"), "hevc", 1920, 1080)
    tv_id = [p for p in c.get("/api/paths").json() if p["name"] == "TV Shows"][0]["id"]
    assert c.put(f"/api/paths/{tv_id}", json={"target_min": 999}).status_code == 400
    c.put(f"/api/paths/{r['id']}", json={"target": "movies"})
    assert store.get_file(fid)["status"] == "ok"


def test_doctor_flags_and_shrinks(lib):
    from app.services import doctor
    big = _add(os.path.join(lib["bellesa"], "big.mp4"), "hevc", 1920, 1080, "ok")
    low = _add(os.path.join(lib["tv"], "Low.S01E01.mkv"), "hevc", 720, 480, "ok")
    for p in ("big.mp4",):
        open(os.path.join(lib["bellesa"], p), "wb").close()
    open(os.path.join(lib["tv"], "Low.S01E01.mkv"), "wb").close()
    with Session(bind=lib["engine"]) as db, \
            patch("app.services.doctor._is_sample", return_value=False):
        report = doctor.check(db)
        kinds = {i["kind"]: i for i in report["issues"] if i["kind"] in ("oversize", "lowres")}
        assert kinds["oversize"]["file_id"] == big and kinds["oversize"]["resolution"] == "720p"
        assert kinds["lowres"]["file_id"] == low and not kinds["lowres"]["safe"]
        assert doctor.fix(db, kinds["oversize"]) == "Queued: shrink to 720p"
        job = store.get_active_job_for_file(big)
        assert job["target_resolution"] == "720p" and store.get_file(big)["status"] == "queued"
        with pytest.raises(ValueError):
            doctor.fix(db, kinds["lowres"])


def test_pipeline_skips_encode_when_in_target(lib, monkeypatch):
    import json
    from app.services import journeys, pipeline
    monkeypatch.setattr(journeys, "PIPELINE_SYNC", True)
    fid = _add(os.path.join(lib["tv"], "S.S01E01.mkv"), "hevc", 1280, 720, "ok")
    with Session(bind=lib["engine"]) as db:
        plan = PipelinePlan(file_id=fid, rule_name="Shrink", status="waiting", next_index=0,
                            actions=json.dumps([{"type": "encode", "resolution": "original"}, {"type": "trim"}]))
        db.add(plan)
        db.commit()
        pid = plan.id
    pipeline.advance(lib["engine"], pid)
    job = store.get_active_job_for_file(fid)
    assert job["job_type"] == "trim"


def test_migration_backfills_targets(tmp_path):
    from app.migrations import run_migrations
    engine = create_engine(f"sqlite:///{tmp_path / 'old.db'}")
    with engine.connect() as c:
        c.execute(text("CREATE TABLE download_paths (id INTEGER PRIMARY KEY, name TEXT NOT NULL, path TEXT NOT NULL, "
                       "is_default INTEGER)"))
        for i, n in enumerate(["TV Shows", "Mature Movies", "bellesa", "Downloads"], 1):
            c.execute(text("INSERT INTO download_paths (id, name, path) VALUES (:i, :n, '/x')"), {"i": i, "n": n})
        c.commit()
    run_migrations(engine)
    with engine.connect() as c:
        rows = c.execute(text("SELECT name, target, target_min, target_max, target_shrink FROM download_paths "
                              "ORDER BY id")).all()
    assert rows == [("TV Shows", "tv", None, None, 0), ("Mature Movies", "movies", None, None, 0),
                    ("bellesa", "custom", 720, 720, 1), ("Downloads", "auto", None, None, 0)]


def test_quality_defaults_created_once(media_db):
    from app.services import watchlist_add
    with Session(bind=media_db) as db:
        db.add(Setting(key="default_profile_id", value=""))
        db.commit()
        watchlist_add.ensure_format_defaults(db)
        watchlist_add.ensure_format_defaults(db)
        profiles = {p.name: p for p in db.query(QualityProfile)}
        assert set(profiles) == {watchlist_add.TV_PROFILE["name"], watchlist_add.MOVIE_PROFILE["name"]}
        assert watchlist_add.default_profile_id(db) == profiles[watchlist_add.TV_PROFILE["name"]].id
        assert watchlist_add.default_profile_id(db, "movie") == profiles[watchlist_add.MOVIE_PROFILE["name"]].id
        assert profiles[watchlist_add.MOVIE_PROFILE["name"]].quality == "4K"


def test_quality_defaults_keep_an_existing_choice(media_db):
    from app.services import watchlist_add
    with Session(bind=media_db) as db:
        mine = QualityProfile(name="Mine", quality="720p")
        db.add(mine)
        db.flush()
        db.add(Setting(key="default_profile_id", value=str(mine.id)))
        db.commit()
        watchlist_add.ensure_format_defaults(db)
        assert watchlist_add.default_profile_id(db) == mine.id
        assert watchlist_add.default_profile_id(db, "movie") != mine.id
