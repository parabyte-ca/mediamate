"""Music on the watchlist: following artists (MusicBrainz), album matching, grabbing,
completion and the routes. MusicBrainz, the indexers and qBittorrent are faked."""
from datetime import datetime, timedelta, timezone
from unittest.mock import patch

import pytest
from fastapi.testclient import TestClient

from app.models import Download, DownloadPath, MusicAlbum, Setting, WatchlistItem
from app.services import music, musicbrainz

from tests.test_watchlist_movies import Session, db, grabbing  # noqa: F401

TODAY = datetime.now(timezone.utc).date()


def d(days: int) -> str:
    return (TODAY + timedelta(days=days)).isoformat()


ARTIST = {"mbid": "art-1", "name": "Phoebe Bridgers", "disambiguation": "", "country": "US", "type": "Person"}
GROUPS = [
    {"mbid": "rg-1", "title": "Stranger in the Alps", "type": "Album", "date": "2017-09-22"},
    {"mbid": "rg-2", "title": "Punisher", "type": "Album", "date": "2020-06-18"},
    {"mbid": "rg-3", "title": "Next One", "type": "Album", "date": d(20)},
]


@pytest.fixture
def mb(monkeypatch):
    monkeypatch.setattr(musicbrainz, "artist", lambda mbid: dict(ARTIST))
    monkeypatch.setattr(musicbrainz, "albums", lambda mbid, eps=False: [dict(g) for g in GROUPS])
    monkeypatch.setattr(musicbrainz, "search_artists", lambda q, limit=8: [dict(ARTIST, score=100)])


def _r(title, h="a", seeds=20, size=300):
    return {"title": title, "seeds": seeds, "size_bytes": size, "source": "tpb",
            "magnet": "magnet:?xt=urn:btih:" + h * 40, "info_hash": h * 40}


# ── matching ────────────────────────────────────────────────────────────────


@pytest.mark.parametrize("title", [
    "Phoebe Bridgers - Punisher (2020) [FLAC]", "Phoebe.Bridgers-Punisher-WEB-2020-FLAC",
    "Phoebe Bridgers – Punisher (2020) MP3 320kbps", "Phoebe Bridgers Punisher 24bit Hi-Res",
])
def test_album_matches(title):
    assert music.matches_album(title, "Phoebe Bridgers", "Punisher")


@pytest.mark.parametrize("title", [
    "Phoebe Bridgers - Stranger in the Alps [FLAC]",               # another album
    "Punisher S01E01 1080p WEB x265",                              # a TV episode
    "Phoebe Bridgers - Discography (2014-2022) FLAC",              # everything at once
    "Phoebe Bridgers Punisher Live 1080p BluRay",                  # a concert video
    "The Punisher 2004 1080p",
])
def test_album_rejects(title):
    assert not music.matches_album(title, "Phoebe Bridgers", "Punisher")


def test_quality():
    assert music.quality_of("X - Y [FLAC 24bit]") == (3, "flac")
    assert music.quality_of("X - Y MP3 320") == (2, "mp3-320")
    assert music.quality_of("X - Y (V0)") == (2, "mp3-320")
    assert music.quality_of("X - Y MP3") == (1, "mp3")
    assert music.quality_of("X - Y 128kbps") is None
    assert music.quality_of("X - Y 192 kbps mp3") is None
    assert music.quality_of("X - Y") == (1, "unknown")


# ── following ───────────────────────────────────────────────────────────────


@pytest.mark.parametrize("start,expected", [
    ("new", {"rg-1": "ignored", "rg-2": "ignored", "rg-3": "wanted"}),
    ("latest", {"rg-1": "ignored", "rg-2": "wanted", "rg-3": "wanted"}),
    ("all", {"rg-1": "wanted", "rg-2": "wanted", "rg-3": "wanted"}),
])
def test_follow_marks_albums(db, mb, start, expected):
    item = music.add_artist(db, "art-1", start)
    assert item.kind == "artist" and item.title == "Phoebe Bridgers" and item.mbid == "art-1"
    assert "rg-3" in item.poster_url
    assert {a.mbid: a.status for a in db.query(MusicAlbum)} == expected
    with pytest.raises(music.AlreadyFollowing):
        music.add_artist(db, "art-1")


def test_summary_and_next(db, mb):
    item = music.add_artist(db, "art-1", "latest")
    s = music.summary(db, item)
    assert s["wanted"] == 2 and s["ignored"] == 1 and s["total"] == 3
    assert s["next"] == {"title": "Next One", "date": d(20)}


def test_refresh_adds_new_albums_as_wanted(db, mb, monkeypatch):
    item = music.add_artist(db, "art-1", "new")
    assert music.refresh_albums(db, item) == 0                    # checked today
    extra = GROUPS + [{"mbid": "rg-4", "title": "Surprise", "type": "Album", "date": d(-1)}]
    monkeypatch.setattr(musicbrainz, "albums", lambda mbid, eps=False: [dict(g) for g in extra])
    assert music.refresh_albums(db, item, force=True) == 1
    assert db.query(MusicAlbum).filter_by(mbid="rg-4").one().status == "wanted"


# ── grabbing ────────────────────────────────────────────────────────────────


def _album(db, mb, mbid="rg-2", start="latest"):
    item = music.add_artist(db, "art-1", start)
    return item, db.query(MusicAlbum).filter_by(mbid=mbid).one()


def test_grabs_flac_first(db, mb, grabbing):
    item, album = _album(db, mb)
    grabbing["results"] = [_r("Phoebe Bridgers - Punisher MP3 320", "b", seeds=99),
                           _r("Phoebe Bridgers - Punisher [FLAC]", "c", seeds=5),
                           _r("Phoebe Bridgers - Punisher 128kbps", "e", seeds=500)]
    out = music.grab(db, item, album)
    assert out["status"] == "started" and "FLAC" in out["title"]
    q, kw = grabbing["queries"][0]
    assert q == "Phoebe Bridgers Punisher" and kw["codec"] == "any"
    dl = db.query(Download).one()
    assert dl.music_album_id == album.id and dl.watchlist_id == item.id
    assert album.status == "grabbed" and album.quality == "flac"
    assert music.grab(db, item, album)["status"] == "already"


def test_only_flac_in_the_first_48_hours(db, mb, grabbing, monkeypatch):
    fresh = GROUPS[:2] + [{"mbid": "rg-5", "title": "Fresh", "type": "Album", "date": d(0)}]
    monkeypatch.setattr(musicbrainz, "albums", lambda mbid, eps=False: [dict(g) for g in fresh])
    item, album = _album(db, mb, "rg-5", "new")
    grabbing["results"] = [_r("Phoebe Bridgers - Fresh MP3 320", "b")]
    assert music.grab(db, item, album)["status"] == "not_found"
    album.release_date = d(-3)
    db.commit()
    assert music.grab(db, item, album)["status"] == "started"


def test_waits_for_release_unless_forced(db, mb, grabbing):
    item, album = _album(db, mb, "rg-3")
    grabbing["results"] = [_r("Phoebe Bridgers - Next One [FLAC]")]
    assert music.grab(db, item, album)["status"] == "waiting" and grabbing["queries"] == []
    assert music.grab(db, item, album, force=True)["status"] == "started"


def test_goes_to_the_music_folder_with_music_category(db, mb, grabbing):
    db.add(DownloadPath(name="Movies", path="/dl/movies", is_default=True))
    db.add(DownloadPath(name="Music", path="/dl/music"))
    db.commit()
    item, album = _album(db, mb)
    grabbing["results"] = [_r("Phoebe Bridgers - Punisher [FLAC]")]
    with patch("app.services.qbittorrent.add_torrent", return_value="a" * 40) as add:
        music.grab(db, item, album)
    assert add.call_args[0][1] == "/dl/music" and add.call_args[0][2] == "music"


def test_scan_grabs_released_wanted_albums_and_retries_failed(db, mb, grabbing):
    item, album = _album(db, mb)
    grabbing["results"] = [_r("Phoebe Bridgers - Punisher [FLAC]")]
    out = music.scan_artist(db, item, {})
    assert [r["status"] for r in out] == ["started"]                 # Next One isn't out yet
    db.query(Download).one().status = "failed"
    db.commit()
    grabbing["results"] = [_r("Phoebe Bridgers - Punisher [FLAC]"),          # the failed one: never again
                           _r("Phoebe Bridgers - Punisher (2020) FLAC 16bit", "f")]
    out = music.scan_artist(db, item, {})
    assert out[0]["status"] == "started" and db.query(Download).count() == 2
    assert "16bit" in db.query(Download).order_by(Download.id.desc()).first().title


def test_completed_download_marks_the_album(db, mb, grabbing):
    item, album = _album(db, mb)
    grabbing["results"] = [_r("Phoebe Bridgers - Punisher [FLAC]")]
    music.grab(db, item, album)
    db.query(Download).one().status = "completed"
    db.commit()
    db.refresh(album)
    assert album.status == "have" and album.completed_at is not None
    assert music.summary(db, item)["have"] == 1


# ── routes ──────────────────────────────────────────────────────────────────


@pytest.fixture
def api(Session, mb):
    from app.database import get_db
    from app.main import app

    def _db():
        s = Session()
        try:
            yield s
        finally:
            s.close()
    app.dependency_overrides[get_db] = _db
    yield TestClient(app)
    app.dependency_overrides.pop(get_db, None)


def test_routes(api, grabbing):
    assert api.get("/api/music/artists/search", params={"q": "phoebe"}).json()[0]["mbid"] == "art-1"
    r = api.post("/api/watchlist/from-musicbrainz", json={"mbid": "art-1", "start": "latest"})
    assert r.status_code == 201 and r.json()["albums"]["wanted"] == 2
    item_id = r.json()["id"]
    assert api.post("/api/watchlist/from-musicbrainz", json={"mbid": "art-1"}).status_code == 409
    assert api.post("/api/watchlist/from-musicbrainz", json={"mbid": "x", "start": "bad"}).status_code == 422
    wl = api.get("/api/watchlist").json()
    assert wl[0]["kind"] == "artist" and wl[0]["albums"]["total"] == 3
    albums = api.get(f"/api/watchlist/{item_id}/albums").json()
    assert [a["title"] for a in albums] == ["Next One", "Punisher", "Stranger in the Alps"]
    alps = albums[2]
    assert api.patch(f"/api/music/albums/{alps['id']}", json={"status": "wanted"}).json()["status"] == "wanted"
    assert api.patch(f"/api/music/albums/{alps['id']}", json={"status": "nope"}).status_code == 422
    grabbing["results"] = [_r("Phoebe Bridgers - Stranger in the Alps [FLAC]")]
    assert api.post(f"/api/music/albums/{alps['id']}/search").json()["status"] == "started"
    assert api.patch(f"/api/music/albums/{alps['id']}", json={"status": "ignored"}).status_code == 409
    assert api.delete(f"/api/watchlist/{item_id}").json()["ok"]
    assert api.get(f"/api/watchlist/{item_id}/albums").status_code == 404


def test_unavailable(api, monkeypatch):
    def boom(*a, **k):
        raise musicbrainz.MusicBrainzUnavailable("down")
    monkeypatch.setattr(musicbrainz, "search_artists", boom)
    assert api.get("/api/music/artists/search", params={"q": "x"}).status_code == 503
