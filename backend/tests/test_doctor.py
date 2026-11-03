"""Library doctor: what it finds, what it fixes on its own, and the Fix endpoint."""
import os
import shutil
import subprocess

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app import models  # noqa: F401
from app.database import Base, get_db
from app.models import DownloadPath, Setting
from app.services import doctor, folders
from app.services.media import config, store

HAS_FFMPEG = bool(shutil.which("ffmpeg") and shutil.which("ffprobe"))
pytestmark = pytest.mark.skipif(not HAS_FFMPEG, reason="ffmpeg not installed")


def clip(path, seconds=2, size="320x240"):
    path.parent.mkdir(parents=True, exist_ok=True)
    subprocess.run(["ffmpeg", "-y", "-loglevel", "error", "-f", "lavfi", "-i",
                    f"testsrc=size={size}:rate=10:duration={seconds}", "-c:v", "libx264", "-preset", "ultrafast",
                    str(path)], check=True)
    return path


@pytest.fixture
def lib(tmp_path, monkeypatch):
    engine = create_engine(f"sqlite:///{tmp_path / 'd.db'}", connect_args={"check_same_thread": False})
    Base.metadata.create_all(engine)
    monkeypatch.setattr(store, "engine", engine)
    Session = sessionmaker(bind=engine)
    media = tmp_path / "media"
    media.mkdir()
    monkeypatch.setattr(config, "MEDIA_PATHS", [str(media)])
    with Session() as db:
        db.add(DownloadPath(name="Media", path=str(media), local_path=str(media), downloads=False, scan=True))
        db.add(Setting(key=folders.MERGED_KEY, value="1"))
        db.commit()

    def index(p):
        from app.services.media import codec_detector
        info = codec_detector.probe(str(p))
        return store.upsert_file(str(p), p.name, info.size_bytes, info.duration_sec, info.codec, False,
                                 info.width, info.height)
    return Session, media, index


def kinds(report):
    return sorted((i["kind"], os.path.basename(i["path"])) for i in report["issues"])


def test_finds_each_kind_of_problem(lib):
    Session, media, index = lib
    index(clip(media / "TV" / "Show.S01E01.720p.mkv", size="320x240"))
    index(clip(media / "TV" / "Show.S01E01.1080p.mkv", size="640x480"))
    clip(media / "dl" / "Movie.2020" / "sample.mkv")
    (media / "dl" / "Movie.2020" / "RARBG.txt").write_text("x")
    (media / "dl" / "Movie.2020" / "poster.jpg").write_text("keep me")
    (media / "broken.mkv").write_bytes(b"not a video")
    (media / "Empty" / "Season 01").mkdir(parents=True)
    gone = media / "gone.mkv"
    clip(gone)
    index(gone)
    gone.unlink()

    with Session() as db:
        r = doctor.check(db)
    assert kinds(r) == sorted([
        ("sample", "sample.mkv"), ("junk", "RARBG.txt"), ("empty", "Empty"),   # one entry per empty tree
        ("broken", "broken.mkv"), ("missing", "gone.mkv"), ("duplicate", "Show.S01E01.720p.mkv"),
    ])
    assert [i["kind"] for i in r["issues"]][:2] == ["sample", "junk"]       # listed by kind
    dup = next(i for i in r["issues"] if i["kind"] == "duplicate")
    assert dup["keep"].endswith("Show.S01E01.1080p.mkv")           # higher resolution is kept


def test_safe_problems_are_fixed_automatically_into_the_trash(lib):
    Session, media, index = lib
    clip(media / "dl" / "Film.2021" / "Film.2021-sample.mkv")
    (media / "dl" / "Film.2021" / "info.nfo.txt").write_text("x")
    (media / "broken.mkv").write_bytes(b"nope")
    with Session() as db:
        r = doctor.run(db)
    assert sorted(i["kind"] for i in r["fixed"]) == ["empty", "junk", "sample"]
    assert [i["kind"] for i in r["issues"]] == ["broken"]          # waits for a Fix button
    assert not (media / "dl").exists()                             # emptied folders went too
    trashed = [f for _, _, fs in os.walk(media / ".trash") for f in fs]
    assert sorted(trashed) == ["Film.2021-sample.mkv", "info.nfo.txt"]


def test_auto_fix_can_be_turned_off(lib):
    Session, media, index = lib
    (media / "x.url").write_text("x")
    with Session() as db:
        db.add(Setting(key=doctor.AUTO_KEY, value="false"))
        db.commit()
        r = doctor.run(db)
    assert r["fixed"] == [] and [i["kind"] for i in r["issues"]] == ["junk"] and (media / "x.url").exists()


def test_a_real_episode_is_not_a_sample(lib):
    Session, media, index = lib
    index(clip(media / "Samples Of Life S01E01.mkv"))
    with Session() as db:
        assert doctor.check(db)["issues"] == []


def test_names_are_only_checked_when_organise_folders_are_set(lib):
    Session, media, index = lib
    index(clip(media / "Show.S01E02.mkv"))
    with Session() as db:
        assert doctor.check(db)["issues"] == []
        db.add(Setting(key="organise_tv_dir", value=str(media / "TV")))
        db.commit()
        [issue] = doctor.check(db)["issues"]
    assert issue["kind"] == "name" and issue["target"].endswith("TV/Show/Season 01/Show - S01E02.mkv")


@pytest.fixture
def api(lib):
    Session, media, index = lib
    from app.main import app

    def _db():
        db = Session()
        try:
            yield db
        finally:
            db.close()
    app.dependency_overrides[get_db] = _db
    try:
        yield TestClient(app), Session, media, index
    finally:
        app.dependency_overrides.pop(get_db, None)


def test_endpoints_run_fix_and_toggle(api):
    client, Session, media, index = api
    index(clip(media / "A.S01E01.mkv", size="320x240"))
    index(clip(media / "A.S01E01.HEVC.mkv", size="640x480"))
    gone = clip(media / "old.mkv")
    index(gone)
    gone.unlink()
    assert client.get("/api/doctor").json() == {"auto_fix": True, "report": None}

    r = client.post("/api/doctor/run").json()["report"]
    ids = {i["kind"]: i["id"] for i in r["issues"]}
    assert set(ids) == {"missing", "duplicate"}

    out = client.post("/api/doctor/fix", json={"ids": list(ids.values())}).json()
    assert out["done"] == 2 and out["errors"] == [] and out["report"]["issues"] == []
    assert not (media / "A.S01E01.mkv").exists() and (media / "A.S01E01.HEVC.mkv").exists()
    assert [r["filename"] for r in store.get_all_files()] == ["A.S01E01.HEVC.mkv"]

    assert client.put("/api/doctor/settings", json={"auto_fix": False}).json()["auto_fix"] is False


def test_busy_files_are_left_alone(api):
    client, Session, media, index = api
    fid = index(clip(media / "B.S01E01.mkv", size="320x240"))
    index(clip(media / "B.S01E01.1080p.mkv", size="640x480"))
    store.create_job(fid, "encode")
    issue = client.post("/api/doctor/run").json()["report"]["issues"][0]
    out = client.post("/api/doctor/fix", json={"ids": [issue["id"]]}).json()
    assert out["done"] == 0 and "being processed" in out["errors"][0]
    assert (media / "B.S01E01.mkv").exists()
