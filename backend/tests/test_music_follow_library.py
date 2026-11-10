"""Following an artist from the music library, and getting a better copy of a
low-quality or incomplete album (asked in the album view, the doctor and after scans)."""
import datetime as dt
import shutil

import pytest

from app.models import Download, MusicAlbum, MusicLibAlbum, Setting, WatchlistItem
from app.services import music_follow, musicbrainz

from tests.test_music_duplicates import _lib
from tests.test_music_library import album, clip, mbz, offline, root  # noqa: F401
from tests.test_music_watchlist import _r, mb  # noqa: F401
from tests.test_organise import lib  # noqa: F401
from tests.test_pipeline import env  # noqa: F401
from tests.test_watchlist_movies import Session, db, grabbing  # noqa: F401

pytestmark = pytest.mark.skipif(not shutil.which("ffmpeg"), reason="ffmpeg not installed")


def test_follow_by_release_group_then_by_name(db, mb, monkeypatch):
    seen = []
    monkeypatch.setattr(musicbrainz, "release_group_artist",
                        lambda rg: seen.append(rg) or {"mbid": "art-1", "name": "Phoebe Bridgers"})
    a = _lib(db, mbid="rg-2")
    item, new = music_follow.follow(db, a)
    assert new and item.mbid == "art-1" and seen == ["rg-2"]
    st = {r.title: r.status for r in db.query(MusicAlbum)}
    assert st["Punisher"] == "have" and st["Next One"] == "wanted"          # start "none": future only
    b = _lib(db, "Stranger in the Alps")                                   # same artist by name
    assert music_follow.follow(db, b) == (item, False)


def test_name_match_must_be_exact(db, mb, monkeypatch):
    monkeypatch.setattr(musicbrainz, "search_artists", lambda q, limit=8: [{"mbid": "x", "name": "Phoebe"}])
    with pytest.raises(music_follow.NotFound):
        music_follow.resolve_artist(db, _lib(db))


def test_various_artists_cant_be_followed(db, mb):
    with pytest.raises(music_follow.NotFound, match="Various"):
        music_follow.resolve_artist(db, _lib(db, artist="Various Artists"))


def test_upgrade_follows_wants_and_grabs_flac(db, mb, grabbing):
    old = _lib(db, fmt="mp3", lossless=False, issues='["low_bitrate"]')
    grabbing["results"] = [_r("Phoebe Bridgers - Punisher MP3 320", "b", seeds=99),
                           _r("Phoebe Bridgers - Punisher [FLAC]", "c")]
    out = music_follow.better_copy(db, old, "upgrade")
    assert out["status"] == "started" and "Now following Phoebe Bridgers" in out["message"]
    d = db.query(Download).one()
    assert "FLAC" in d.title and d.replaces_album_id == old.id


def test_missing_tracks_of_an_mp3_album_takes_mp3(db, mb, grabbing):
    old = _lib(db, fmt="mp3", lossless=False, issues='["missing_tracks"]')
    grabbing["results"] = [_r("Phoebe Bridgers - Punisher MP3 320", "b", seeds=99)]
    assert music_follow.better_copy(db, old, "complete")["status"] == "started"


def test_nothing_found_stays_wanted(db, mb, grabbing):
    old = _lib(db, fmt="mp3", lossless=False, issues='["low_bitrate"]')
    grabbing["results"] = []
    out = music_follow.better_copy(db, old, "upgrade")
    assert out["status"] == "wanted" and "stays Wanted" in out["message"]
    assert db.query(MusicAlbum).filter_by(title="Punisher").one().status == "wanted"


def test_not_now_and_the_notice(db, mb, monkeypatch):
    sent = []
    monkeypatch.setattr("app.services.apprise_notify.notify", lambda *a, **k: sent.append(k.get("body") or a[2]))
    a = _lib(db, fmt="mp3", lossless=False, issues='["low_bitrate"]')
    _lib(db, "Stranger in the Alps", issues='["missing_tracks"]')
    assert music_follow.notify_after_scan(db) and "1 album could be upgraded, 1 is missing tracks" in sent[0]
    assert not music_follow.notify_after_scan(db)                          # same count: quiet
    a.upgrade_dismissed_at = dt.datetime.now(dt.timezone.utc)
    db.commit()
    assert [x.title for x in music_follow.candidates(db)] == ["Stranger in the Alps"]
    db.merge(Setting(key="music_upgrade_notified_at", value="2000-01-01T00:00:00+00:00"))
    db.merge(Setting(key="music_upgrade_notified_count", value="0"))
    db.merge(Setting(key="music_upgrade_notify", value="false"))
    db.commit()
    assert not music_follow.notify_after_scan(db)                          # switched off


def test_routes(lib, mb, monkeypatch):
    client, Session_, media = lib
    monkeypatch.setattr(musicbrainz, "release_group_artist", lambda rg: None)
    with Session_() as s:
        a = MusicLibAlbum(folder=str(media / "Music" / "P"), artist="Phoebe Bridgers", title="Punisher",
                          format="mp3", tracks=3, issues='["low_bitrate"]')
        s.add(a)
        s.commit()
        aid = a.id
    detail = client.get(f"/api/music/library/albums/{aid}").json()
    assert detail["followed_artist_id"] is None and detail["upgrade_dismissed"] is False
    r = client.post(f"/api/music/library/albums/{aid}/follow-artist", json={})
    assert r.status_code == 200 and r.json()["msg"] == "Now following Phoebe Bridgers"
    assert client.post(f"/api/music/library/albums/{aid}/follow-artist", json={}).json()["already"] is True
    assert client.get(f"/api/music/library/albums/{aid}").json()["followed_artist_id"] == r.json()["watchlist_id"]
    assert client.post(f"/api/music/library/albums/{aid}/better-copy", json={"mode": "x"}).status_code == 422
    assert client.post(f"/api/music/library/albums/{aid}/not-now").json()["ok"] is True
    assert client.get(f"/api/music/library/albums/{aid}").json()["upgrade_dismissed"] is True
