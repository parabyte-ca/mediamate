"""One Folders list for downloads and the media engine: the upgrade merge, the
derived media folders, .env folders, the API, and where torrents are sent."""
import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app import models  # noqa: F401
from app.database import Base, get_db
from app.models import DownloadPath, PathMapping, Setting
from app.services import folders
from app.services.media import config


@pytest.fixture
def Session(tmp_path, monkeypatch):
    engine = create_engine(f"sqlite:///{tmp_path / 'f.db'}", connect_args={"check_same_thread": False})
    Base.metadata.create_all(engine)
    monkeypatch.setattr(config, "MEDIA_PATHS", list(config.MEDIA_PATHS))  # restored after the test
    monkeypatch.delenv("MEDIA_PATHS", raising=False)
    yield sessionmaker(bind=engine)
    engine.dispose()


@pytest.fixture
def disk(tmp_path):
    """What this container sees: /…/nas/{tv,movies,library}."""
    root = tmp_path / "nas"
    for d in ("tv", "movies", "library"):
        (root / d).mkdir(parents=True)
    return root


def rows(db):
    return {dp.name: (dp.downloads, dp.scan, folders.local_of(db, dp)) for dp in db.query(DownloadPath)}


# ── Upgrade: the old media folders fold into the list ─────────────────────────

def test_upgrade_merges_both_lists_without_losing_anything(Session, disk, monkeypatch):
    with Session() as db:
        db.add(PathMapping(remote_prefix="/downloads", local_prefix=str(disk)))
        db.add_all([DownloadPath(name="TV", path="/downloads/tv", is_default=True),
                    DownloadPath(name="Gone", path="/downloads/gone")])
        db.add(Setting(key="media_paths", value=f"{disk}/tv,{disk}/library"))
        db.commit()
        monkeypatch.setattr(config, "MEDIA_PATHS", [f"{disk}/tv", f"{disk}/library"])

        roots = folders.startup(db)
        r = rows(db)
    assert r["TV"] == (True, True, f"{disk}/tv")              # download folder, already a media folder
    assert r["Gone"][:2] == (True, False)                      # not here: keeps downloading, not scanned
    assert r["library"] == (False, True, f"{disk}/library")    # old media-only folder → library folder
    assert roots == [f"{disk}/library", f"{disk}/tv"] and config.MEDIA_PATHS == roots


def test_the_merge_runs_once(Session, disk, monkeypatch):
    with Session() as db:
        monkeypatch.setattr(config, "MEDIA_PATHS", [f"{disk}/library"])
        db.add(Setting(key="media_paths", value=f"{disk}/library"))
        db.commit()
        folders.startup(db)
        db.query(DownloadPath).delete()            # you removed it afterwards
        db.commit()
        folders.startup(db)
        assert db.query(DownloadPath).count() == 0


def test_the_unset_default_media_folder_is_not_invented(Session, monkeypatch, tmp_path):
    with Session() as db:
        # The built-in default when nobody set MEDIA_PATHS, on a host where it doesn't exist.
        monkeypatch.setattr(config, "MEDIA_PATHS", [str(tmp_path / "media")])
        assert folders.startup(db) == [] and db.query(DownloadPath).count() == 0


def test_nested_folders_are_scanned_once(Session, disk):
    with Session() as db:
        db.add_all([DownloadPath(name="All", path=str(disk), local_path=str(disk), downloads=False),
                    DownloadPath(name="TV", path=f"{disk}/tv")])
        db.add(Setting(key=folders.MERGED_KEY, value="1"))
        db.commit()
        assert folders.refresh(db) == [str(disk)]
        assert db.query(Setting).filter(Setting.key == "media_paths").one().value == str(disk)


# ── .env (setup.sh --media) ──────────────────────────────────────────────────

def test_env_folders_appear_and_stay_deleted_once_removed(Session, disk, monkeypatch):
    monkeypatch.setenv("MEDIA_PATHS", f"{disk}/movies")
    with Session() as db:
        db.add(Setting(key=folders.MERGED_KEY, value="1"))
        db.commit()
        folders.startup(db)
        [dp] = db.query(DownloadPath).all()
        assert (dp.downloads, dp.scan, dp.local_path) == (False, True, f"{disk}/movies")
        folders.forget_env(db, folders.local_of(db, dp))
        db.delete(dp)
        db.commit()
        folders.startup(db)
        assert db.query(DownloadPath).count() == 0


# ── Where torrents go ─────────────────────────────────────────────────────────

def test_a_folder_that_no_longer_takes_downloads_falls_back_to_the_default(Session):
    with Session() as db:
        default = DownloadPath(name="Default", path="/downloads", is_default=True)
        lib = DownloadPath(name="Lib", path="/lib", local_path="/lib", downloads=False)
        db.add_all([default, lib])
        db.commit()
        assert folders.download_folder(db, lib.id).name == "Default"
        assert folders.download_folder(db, default.id).name == "Default"
        assert folders.download_folder(db, None).name == "Default"
        assert folders.download_folder(db, 999).name == "Default"


# ── API ───────────────────────────────────────────────────────────────────────

@pytest.fixture
def client(Session):
    from app.main import app

    def _db():
        db = Session()
        try:
            yield db
        finally:
            db.close()

    app.dependency_overrides[get_db] = _db
    try:
        yield TestClient(app)
    finally:
        app.dependency_overrides.pop(get_db, None)


def test_api_adds_folders_and_keeps_the_media_engine_in_step(client, disk):
    tv = client.post("/api/paths", json={"name": "TV", "path": f"{disk}/tv", "is_default": True}).json()
    assert (tv["downloads"], tv["scan"], tv["local"], tv["exists_here"]) == (True, True, f"{disk}/tv", True)
    assert tv["total_bytes"]                                         # measured where MediaMate sees it

    lib = client.post("/api/paths", json={"name": "Library", "path": f"{disk}/library",
                                          "downloads": False}).json()
    assert (lib["local_path"], lib["is_default"]) == (f"{disk}/library", False)
    assert config.MEDIA_PATHS == [f"{disk}/library", f"{disk}/tv"]

    client.put(f"/api/paths/{tv['id']}", json={"scan": False})
    assert config.MEDIA_PATHS == [f"{disk}/library"]
    client.delete(f"/api/paths/{lib['id']}")
    assert config.MEDIA_PATHS == []


def test_only_a_download_folder_can_be_the_default(client):
    r = client.post("/api/paths", json={"name": "Lib", "path": "/lib", "downloads": False, "is_default": True})
    assert r.status_code == 400


def test_a_path_mapping_fills_in_the_folder_here(client, disk, Session):
    with Session() as db:
        db.add(PathMapping(remote_prefix="/downloads", local_prefix=str(disk)))
        db.commit()
    r = client.post("/api/paths", json={"name": "TV", "path": "/downloads/tv"}).json()
    assert (r["path"], r["local"], r["exists_here"]) == ("/downloads/tv", f"{disk}/tv", True)


def test_the_old_media_folders_setting_still_works(client, disk, Session, monkeypatch):
    from app.services.media import store
    with Session() as db:
        monkeypatch.setattr(store, "engine", db.get_bind())
    assert client.post("/api/media/settings", json={"media_paths": [f"{disk}/movies"]}).json()["ok"]
    assert config.MEDIA_PATHS == [f"{disk}/movies"]
    names = [p["name"] for p in client.get("/api/paths").json()]
    assert names == ["movies"]
    assert client.get("/api/media/settings").json()["media_paths"] == [f"{disk}/movies"]
