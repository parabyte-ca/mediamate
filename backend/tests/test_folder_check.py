"""Settings → Folders → Test: is each folder visible, readable and writable here,
with enough space, and can qBittorrent see it? Plus the likeliest fix when not."""
import os
import shutil
from collections import namedtuple

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app import models  # noqa: F401
from app.database import Base, get_db
from app.models import DownloadPath, PathMapping
from app.services import folders, qbittorrent


@pytest.fixture
def Session(tmp_path):
    engine = create_engine(f"sqlite:///{tmp_path / 'f.db'}", connect_args={"check_same_thread": False})
    Base.metadata.create_all(engine)
    yield sessionmaker(bind=engine)
    engine.dispose()


@pytest.fixture
def client(Session, monkeypatch):
    from app.main import app

    def _db():
        db = Session()
        try:
            yield db
        finally:
            db.close()

    monkeypatch.setattr(qbittorrent, "directory_exists", lambda p: None)
    app.dependency_overrides[get_db] = _db
    try:
        yield TestClient(app)
    finally:
        app.dependency_overrides.pop(get_db, None)


def add(Session, **kw) -> int:
    with Session() as db:
        dp = DownloadPath(**{"name": "F", "downloads": False, "scan": True, **kw})
        db.add(dp)
        db.commit()
        return dp.id


def by_key(result):
    return {c["key"]: c for c in result["checks"]}


def test_a_good_folder_passes_and_counts_its_videos(client, Session, tmp_path):
    tv = tmp_path / "tv"
    (tv / "Show").mkdir(parents=True)
    (tv / "a.mkv").write_bytes(b"x")
    (tv / "Show" / "b.mp4").write_bytes(b"x")
    (tv / "notes.txt").write_text("x")
    fid = add(Session, path=str(tv), local_path=str(tv))

    r = client.post(f"/api/paths/{fid}/test").json()
    c = by_key(r)
    assert r["ok"] and r["hint"] is None
    assert c["visible"]["status"] == c["read"]["status"] == c["write"]["status"] == "ok"
    assert c["read"]["detail"].startswith("2 videos")
    assert "set as Path here" in c["path"]["detail"]
    assert not any(n.startswith(".mediamate-write-test") for n in os.listdir(tv))


def test_a_missing_folder_names_what_does_exist_and_how_to_mount_it(client, Session, tmp_path):
    fid = add(Session, path=str(tmp_path / "nas" / "tv"))
    r = client.post(f"/api/paths/{fid}/test").json()
    c = by_key(r)
    assert not r["ok"] and c["visible"]["status"] == "fail"
    assert f"{tmp_path} does" in c["visible"]["detail"]
    assert c["write"]["status"] == "skip"
    assert r["hint"]["kind"] == "mount" and r["hint"]["steps"][0]["copy"].endswith(f":{tmp_path}/nas/tv")


def test_a_mapped_folder_says_so_and_blames_the_mapping_when_wrong(client, Session, tmp_path):
    with Session() as db:
        db.add(PathMapping(remote_prefix="/downloads", local_prefix=str(tmp_path / "wrong")))
        db.commit()
    fid = add(Session, path="/downloads/tv")
    r = client.post(f"/api/paths/{fid}/test").json()
    assert "from the path mapping /downloads" in by_key(r)["path"]["detail"]
    assert r["hint"]["kind"] == "mapping"


@pytest.mark.skipif(os.geteuid() == 0, reason="root can write anywhere")
@pytest.mark.parametrize("scan,status,ok", [(True, "fail", False), (False, "warn", True)])
def test_a_read_only_folder(client, Session, tmp_path, scan, status, ok):
    lib = tmp_path / "lib"
    lib.mkdir()
    lib.chmod(0o555)
    try:
        fid = add(Session, path=str(lib), local_path=str(lib), scan=scan)
        r = client.post(f"/api/paths/{fid}/test").json()
        assert by_key(r)["write"]["status"] == status and r["ok"] is ok
    finally:
        lib.chmod(0o755)


def test_a_read_only_folder_is_reported(Session, tmp_path, monkeypatch):
    # Works as root too: make the write probe fail the way a read-only mount does.
    lib = tmp_path / "lib"
    lib.mkdir()
    real_open = open

    def ro_open(path, mode="r", *a, **k):
        if "w" in mode and ".mediamate-write-test" in str(path):
            raise OSError(30, "Read-only file system")
        return real_open(path, mode, *a, **k)
    monkeypatch.setattr("builtins.open", ro_open)
    with Session() as db:
        dp = DownloadPath(name="L", path=str(lib), local_path=str(lib), downloads=False, scan=True)
        db.add(dp)
        db.commit()
        r = folders.check(db, dp)
    assert by_key(r)["write"]["status"] == "fail" and "Read-only file system" in by_key(r)["write"]["detail"]
    assert r["hint"]["kind"] == "permissions"


def test_low_space_is_a_warning(client, Session, tmp_path, monkeypatch):
    Usage = namedtuple("Usage", "total used free")
    monkeypatch.setattr(shutil, "disk_usage", lambda p: Usage(100e9, 99e9, 1e9))
    fid = add(Session, path=str(tmp_path), local_path=str(tmp_path))
    r = client.post(f"/api/paths/{fid}/test").json()
    assert by_key(r)["space"]["status"] == "warn" and r["ok"]


@pytest.mark.parametrize("seen,status", [(True, "ok"), (False, "fail"), (None, "skip")])
def test_qbittorrents_view_of_download_folders(client, Session, tmp_path, monkeypatch, seen, status):
    monkeypatch.setattr(qbittorrent, "directory_exists", lambda p: seen)
    fid = add(Session, path=str(tmp_path), local_path=str(tmp_path), downloads=True)
    r = client.post(f"/api/paths/{fid}/test").json()
    assert by_key(r)["qbit"]["status"] == status
    assert r["ok"] is (status != "fail")


def test_library_only_folders_do_not_ask_qbittorrent(client, Session, tmp_path):
    fid = add(Session, path=str(tmp_path), local_path=str(tmp_path), downloads=False)
    assert "qbit" not in by_key(client.post(f"/api/paths/{fid}/test").json())


def test_an_unknown_folder_is_404(client):
    assert client.post("/api/paths/999/test").status_code == 404


def test_qbittorrent_4_is_not_asked(monkeypatch):
    class Old:
        def app_web_api_version(self):
            return "2.8.3"
        def app_get_directory_content(self, **k):
            raise AssertionError("not available on 4.x")
    monkeypatch.setattr(qbittorrent, "_get_client", lambda: Old())
    assert qbittorrent.directory_exists("/downloads") is None


def test_qbittorrent_5_reports_missing_folders(monkeypatch):
    class NotFound(Exception):
        http_status_code = 404

    class New:
        def app_web_api_version(self):
            return "2.11.4"
        def app_get_directory_content(self, directory_path):
            if directory_path == "/downloads":
                return ["tv"]
            raise NotFound("Directory does not exist")
    monkeypatch.setattr(qbittorrent, "_get_client", lambda: New())
    assert qbittorrent.directory_exists("/downloads") is True
    assert qbittorrent.directory_exists("/nope") is False


# ── Folders qBittorrent already uses ──────────────────────────────────────────

def test_qbittorrent_folders_not_in_the_list_are_suggested(client, Session, tmp_path, monkeypatch):
    nas = tmp_path / "nas"
    (nas / "tv").mkdir(parents=True)
    (nas / "movies").mkdir()
    add(Session, path=str(nas / "tv"), local_path=str(nas / "tv"), downloads=True)
    monkeypatch.setattr(qbittorrent, "known_folders", lambda: [
        {"path": str(nas / "tv"), "why": ["qBittorrent's default save folder"], "category": None},  # listed already
        {"path": "/downloads/movies", "why": ["category “movies”"], "category": "movies"},           # other container
        {"path": "/elsewhere/nothing", "why": ["3 torrents saved here"], "category": None},
    ])
    r = client.get("/api/paths/suggestions").json()
    assert r["connected"]
    by_path = {s["path"]: s for s in r["suggestions"]}
    assert str(nas / "tv") not in by_path
    movies = by_path["/downloads/movies"]
    assert movies["name"] == "Movies" and movies["visible_here"] and movies["how"] == "found by name"
    assert movies["local"] == str(nas / "movies")
    nothing = by_path["/elsewhere/nothing"]
    assert not nothing["visible_here"] and nothing["name"] == "Nothing"
    assert r["suggestions"][0]["path"] == "/downloads/movies"       # visible ones first


def test_a_path_mapping_places_a_suggestion(client, Session, tmp_path, monkeypatch):
    (tmp_path / "dl").mkdir()
    with Session() as db:
        db.add(PathMapping(remote_prefix="/downloads", local_prefix=str(tmp_path / "dl")))
        db.commit()
    monkeypatch.setattr(qbittorrent, "known_folders",
                        lambda: [{"path": "/downloads/", "why": ["x"], "category": None}])
    [s] = client.get("/api/paths/suggestions").json()["suggestions"]
    assert (s["path"], s["local"], s["how"], s["visible_here"]) == ("/downloads", str(tmp_path / "dl"), "path mapping", True)


def test_no_suggestions_without_qbittorrent(client, monkeypatch):
    monkeypatch.setattr(qbittorrent, "known_folders", lambda: None)
    assert client.get("/api/paths/suggestions").json() == {"connected": False, "suggestions": []}


def test_known_folders_reads_defaults_categories_and_torrents(monkeypatch):
    class T:
        def __init__(self, sp):
            self.save_path = sp

    class Q:
        def app_preferences(self):
            return {"save_path": "/downloads/"}
        def torrents_categories(self):
            return {"tv": {"savePath": "/downloads/tv"}, "misc": {"savePath": ""}}
        def torrents_info(self):
            return [T("/downloads/tv/"), T("/downloads/tv"), T("/other")]
    monkeypatch.setattr(qbittorrent, "_get_client", lambda: Q())
    got = {f["path"]: f for f in qbittorrent.known_folders()}
    assert set(got) == {"/downloads", "/downloads/tv", "/other"}
    assert got["/downloads/tv"]["category"] == "tv"
    assert got["/downloads/tv"]["why"] == ["category “tv”", "2 torrents saved here"]


def test_a_suggestion_does_not_reuse_a_folders_name(client, Session, tmp_path, monkeypatch):
    add(Session, name="Movies", path=str(tmp_path), local_path=str(tmp_path))
    monkeypatch.setattr(qbittorrent, "known_folders",
                        lambda: [{"path": "/downloads/movies", "why": ["x"], "category": "movies"}])
    [s] = client.get("/api/paths/suggestions").json()["suggestions"]
    assert s["name"] == "Movies (qBittorrent)"


def test_qbittorrent_sees_it_but_screenmate_does_not(client, Session, monkeypatch):
    # Downloads finish inside qBittorrent's mount, which MediaMate doesn't have.
    monkeypatch.setattr(qbittorrent, "directory_exists", lambda p: True)
    fid = add(Session, path="/mnt/video/tv shows", downloads=True)
    r = client.post(f"/api/paths/{fid}/test").json()
    assert not r["ok"] and r["hint"]["kind"] == "same-as-qbit"
    inspect, volume = (s["copy"] for s in r["hint"]["steps"])
    assert inspect.startswith("docker inspect qbittorrent")
    assert volume == "- /host/folder/from/step/1:/mnt/video"
    assert "/mnt/video" in r["hint"]["text"]


def test_volume_lines_with_spaces_are_quoted():
    assert folders._volume_line("/h", "/mnt/video/tv shows") == '- "/h:/mnt/video/tv shows"'
    assert folders._volume_line("/h", "/downloads") == "- /h:/downloads"
    assert folders._mount_root("/downloads/tv") == "/downloads"
    assert folders._mount_root("/mnt/video/tv shows") == "/mnt/video"
