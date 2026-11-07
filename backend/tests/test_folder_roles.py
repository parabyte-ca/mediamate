"""One Folders list: library roles and the music download folder live on folders, old
typed settings move over once, and organise / music read from the folders."""
import pytest

from app.models import DownloadPath, Setting
from app.services import folder_roles, music_library, organise

from tests.test_pipeline import env  # noqa: F401
from tests.test_organise import lib  # noqa: F401


def _set(Session, **kv):
    with Session() as db:
        for k, v in kv.items():
            db.merge(Setting(key=k, value=v))
        db.commit()


def test_old_settings_move_onto_folders(lib, tmp_path):
    client, Session, media = lib
    tv = media / "tv shows"
    tv.mkdir()
    (media / "TV Shows").mkdir()
    music = tmp_path / "music"
    music.mkdir()
    with Session() as db:
        db.add(DownloadPath(name="TV Shows", path=str(tv), local_path=str(tv), downloads=True, scan=True))
        dl = DownloadPath(name="Music downloads", path=str(music / ".downloads"), downloads=True, scan=False)
        db.add(dl)
        db.commit()
        dl_id = dl.id
    _set(Session, organise_tv_dir=str(media / "TV Shows"),          # differs only by capitals
         organise_music_dir=str(music), music_download_path_id=str(dl_id))
    with Session() as db:
        done = folder_roles.migrate_once(db)
        assert set(done) == {"tv", "movies", "music", "music_downloads"}   # movies: set by the fixture
        by = {d.name: d for d in db.query(DownloadPath)}
        tv_lib = next(d for d in by.values() if d.role == "tv")
        assert tv_lib.path == str(media / "TV Shows") and not tv_lib.downloads
        assert tv_lib.scan is True                                 # not inside a scanned folder, so scanned
        assert next(d for d in by.values() if d.role == "music").path == str(music)
        assert by["Music downloads"].music_downloads
        assert db.get(Setting, "organise_tv_dir").value == ""      # the folder is the only place now
        assert folder_roles.migrate_once(db) == {}                 # once
        assert organise.get_settings(db)["organise_tv_dir"] == str(media / "TV Shows")
        assert music_library.music_dir(db) == str(music)
    rows = {r["name"]: r for r in client.get("/api/paths").json()}
    assert any("capital letters" in n for n in rows["TV Shows"]["notes"])


def test_roles_are_unique_and_never_on_protected_folders(lib):
    client, Session, media = lib
    a = client.post("/api/paths", json={"name": "Films", "path": str(media / "a"), "downloads": False,
                                        "role": "movies"}).json()
    b = client.post("/api/paths", json={"name": "Films 2", "path": str(media / "b"), "downloads": False,
                                        "role": "movies"}).json()
    rows = {r["id"]: r for r in client.get("/api/paths").json()}
    assert rows[a["id"]]["role"] is None and rows[b["id"]]["role"] == "movies"
    r = client.post("/api/paths", json={"name": "Bellesa", "path": str(media / "bellesa"), "downloads": False,
                                        "role": "movies"})
    assert r.status_code == 400 and "protected" in r.json()["detail"]
    r = client.put(f"/api/paths/{b['id']}", json={"protected": True})
    assert r.status_code == 400
    assert client.put(f"/api/paths/{b['id']}", json={"role": ""}).json()["role"] is None


def test_one_music_download_folder_and_it_must_take_downloads(lib):
    client, Session, media = lib
    x = client.post("/api/paths", json={"name": "X", "path": "/dl/x", "music_downloads": True}).json()
    y = client.post("/api/paths", json={"name": "Y", "path": "/dl/y", "music_downloads": True}).json()
    rows = {r["id"]: r for r in client.get("/api/paths").json()}
    assert not rows[x["id"]]["music_downloads"] and rows[y["id"]]["music_downloads"]
    r = client.post("/api/paths", json={"name": "Z", "path": "/z", "downloads": False, "music_downloads": True})
    assert r.status_code == 400
    with Session() as db:
        from app.models import WatchlistItem
        from app.services import music
        item = WatchlistItem(kind="artist", title="A", search_query="A", season=1, episode=1)
        assert music.music_folder(db, item).name == "Y"


def test_organise_ignores_typed_folders_now(lib):
    client, Session, media = lib
    client.put("/api/pipeline/organise", json={"organise_movie_dir": "/somewhere/else"})
    with Session() as db:
        row = db.get(Setting, "organise_movie_dir")
        assert row is None or row.value != "/somewhere/else"


def test_nested_scanned_folders_are_noted(lib):
    client, Session, media = lib
    client.post("/api/paths", json={"name": "Video", "path": str(media), "downloads": False, "scan": True})
    client.post("/api/paths", json={"name": "Movies", "path": str(media / "Movies"), "downloads": False, "scan": True})
    rows = {r["name"]: r for r in client.get("/api/paths").json()}
    assert any("Inside Video" in n for n in rows["Movies"]["notes"])
