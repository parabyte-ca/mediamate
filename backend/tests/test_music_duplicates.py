"""Album downloads check the music library first: an album you already have is never
downloaded again, unless your copy is worth replacing (lossy, low bitrate, missing
tracks); then a lossless copy replaces it."""
import datetime as dt
import shutil

import pytest

from app.models import Download, MusicAlbum, MusicLibAlbum, WatchlistItem
from app.services import music
from app.services import music_library as ml

from tests.test_music_library import album, clip, mbz, offline, root  # noqa: F401
from tests.test_music_watchlist import _album, _r, mb  # noqa: F401
from tests.test_organise import lib  # noqa: F401
from tests.test_pipeline import env  # noqa: F401
from tests.test_watchlist_movies import Session, db, grabbing  # noqa: F401

pytestmark = pytest.mark.skipif(not shutil.which("ffmpeg"), reason="ffmpeg not installed")


def _lib(db, title="Punisher", fmt="flac", issues=None, mbid=None, artist="Phoebe Bridgers", lossless=True):
    from app.models import MusicTrack
    a = MusicLibAlbum(folder=f"/music/{artist}/{title}", artist=artist, title=title, format=fmt, tracks=11,
                      issues=issues, mb_release_group_id=mbid)
    db.add(a)
    db.flush()
    db.add(MusicTrack(album_id=a.id, path=f"/music/{artist}/{title}/01.{fmt}", lossless=lossless, format=fmt))
    db.commit()
    return a


def test_an_album_in_the_library_is_not_downloaded_again(db, mb, grabbing):
    item, album_ = _album(db, mb)
    _lib(db, "Punisher (Deluxe Edition)")                       # name match, ignoring the edition
    grabbing["results"] = [_r("Phoebe Bridgers - Punisher [FLAC]")]
    out = music.grab(db, item, album_)
    assert out["status"] == "in_library" and "FLAC, 11 tracks" in out["message"]
    assert album_.status == "have" and grabbing["queries"] == [] and db.query(Download).count() == 0
    assert music.grab(db, item, album_, force=True)["status"] == "already"   # have: already there


def test_matched_by_musicbrainz_id(db, mb, grabbing):
    item, album_ = _album(db, mb)
    _lib(db, "Something Else Entirely", mbid="rg-2")
    assert music.grab(db, item, album_)["status"] == "in_library"


def test_a_lossy_copy_is_upgraded_with_flac_only(db, mb, grabbing):
    item, album_ = _album(db, mb)
    old = _lib(db, fmt="mp3", lossless=False)
    grabbing["results"] = [_r("Phoebe Bridgers - Punisher MP3 320", "b", seeds=99)]
    out = music.grab(db, item, album_)
    assert out["status"] == "not_found" and "replace your MP3, 11 tracks copy" in out["message"]
    grabbing["results"].append(_r("Phoebe Bridgers - Punisher [FLAC]", "c"))
    out = music.grab(db, item, album_)
    assert out["status"] == "started" and "FLAC" in out["title"]
    assert db.query(Download).one().replaces_album_id == old.id


def test_low_bitrate_or_missing_tracks_count_as_upgrades(db, mb, grabbing):
    item, album_ = _album(db, mb)
    _lib(db, issues='["missing_tracks"]')
    grabbing["results"] = [_r("Phoebe Bridgers - Punisher [FLAC]")]
    assert music.grab(db, item, album_)["status"] == "started"


def test_following_marks_owned_albums_have(db, mb, grabbing):
    _lib(db, "Stranger in the Alps")
    _lib(db, "Punisher", fmt="mp3", lossless=False)              # an upgrade: stays wanted
    item = music.add_artist(db, "art-1", "all")
    st = {a.title: a.status for a in db.query(MusicAlbum)}
    assert st["Stranger in the Alps"] == "have" and st["Punisher"] == "wanted"


def test_start_none_wants_only_future_releases(db, mb, grabbing):
    item = music.add_artist(db, "art-1", "none")
    st = {a.title: a.status for a in db.query(MusicAlbum)}
    assert st == {"Stranger in the Alps": "ignored", "Punisher": "ignored", "Next One": "wanted"}


def test_an_upgrade_replaces_the_old_files(root, tmp_path, mbz, monkeypatch):
    client, Session, music_dir = root
    monkeypatch.setattr(ml, "IMPORT_SYNC", True)
    monkeypatch.setattr("app.services.pipeline.on_download_complete", lambda bind, dl_id: None)
    old = music_dir / "Phoebe Bridgers" / "Punisher (2020)"
    for i in (1, 2, 3):
        clip(old / f"{i:02d} - Song {i}.mp3", kbps=128, artist="Phoebe Bridgers", albumartist="Phoebe Bridgers",
             album="Punisher", date="2020", title=f"Song {i}", tracknumber=f"{i}/3")
    (old / "setlist.txt").write_text("notes")
    ml.scan()
    dl_dir = tmp_path / "downloads"
    rip = dl_dir / "Phoebe Bridgers - Punisher [FLAC]"
    for i in (1, 2, 3):
        clip(rip / f"{i}.flac")
    with Session() as db:
        old_id = db.query(MusicLibAlbum).one().id
        w = WatchlistItem(kind="artist", mbid="art-1", title="Phoebe Bridgers", search_query="Phoebe Bridgers",
                          season=1, episode=1)
        db.add(w)
        db.flush()
        al = MusicAlbum(watchlist_id=w.id, mbid="rg-2", title="Punisher", release_date="2020-06-18", status="grabbed")
        db.add(al)
        db.flush()
        dl = Download(title=rip.name, download_path=str(dl_dir), status="downloading", watchlist_id=w.id,
                      music_album_id=al.id, qbit_removed=True, replaces_album_id=old_id)
        db.add(dl)
        db.commit()
        dl.status = "completed"
        db.commit()
    assert sorted(p.suffix for p in old.iterdir() if p.suffix in (".mp3", ".flac")) == [".flac"] * 3
    assert (old / "setlist.txt").exists()
    day = dt.date.today().isoformat()
    assert len(list((music_dir / ".trash" / day).rglob("*.mp3"))) == 3
    with Session() as db:
        a = db.query(MusicLibAlbum).one()
        assert a.format == "flac" and a.tracks == 3
