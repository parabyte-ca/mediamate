"""The music doctor: checks, safe auto-fixes, the trash, fixes, and its routes."""
import datetime as dt
import os
import shutil

import pytest

from app.models import MusicAlbum, MusicLibAlbum, Setting, WatchlistItem
from app.services import doctor, music_batch, music_doctor as md
from app.services import music_library as ml

from tests.test_pipeline import env  # noqa: F401
from tests.test_organise import lib  # noqa: F401
from tests.test_music_library import album, clip, offline, root  # noqa: F401

pytestmark = pytest.mark.skipif(not shutil.which("ffmpeg"), reason="ffmpeg not installed")


def kinds(report, kind):
    return [i for i in report["issues"] if i["kind"] == kind]


def check(Session):
    with Session() as db:
        return md.check(db)


def test_a_tidy_library_is_healthy(root):
    client, Session, music = root
    a = album(music / "Phoebe Bridgers" / "Punisher (2020)")
    for i, f in enumerate(sorted(a.iterdir()), 1):
        f.rename(a / f"{i:02d} - Song {i}.flac")
    from tests.test_music_library import _jpeg            # a real image, so it can go in the files
    (a / "cover.jpg").write_bytes(_jpeg())
    ml.embed_cover([str(p) for p in a.glob("*.flac")], _jpeg())
    (a / "notes.txt").write_text("setlist")               # kept: bootleg notes belong to the album
    (a / "rip.log").write_text("eac")
    ml.scan()
    report = check(Session)
    assert report["issues"] == [], report["issues"]


def test_finds_problems(root):
    client, Session, music = root
    album(music / "Phoebe Bridgers" / "Punisher (2020)")                        # loose names, no cover
    clip(music / "Unknown" / "rip" / "track.flac")                              # no tags
    (music / "Phoebe Bridgers" / "Punisher (2020)" / "get more.url").write_text("x")
    (music / "Phoebe Bridgers" / "Punisher (2020)" / "Thumbs.db").write_bytes(b"x")
    (music / "Old" / "Nothing").mkdir(parents=True)
    (music / ".downloads" / "Some.Torrent").mkdir(parents=True)                  # hidden: left alone
    (music / ".downloads" / "Some.Torrent" / "x.url").write_text("x")
    ml.scan()
    r = check(Session)
    assert {os.path.basename(i["path"]) for i in kinds(r, "junk")} == {"get more.url", "Thumbs.db"}
    assert [os.path.relpath(i["path"], music) for i in kinds(r, "empty")] == ["Old"]
    assert [os.path.basename(i["path"]) for i in kinds(r, "untagged")] == ["rip"]
    assert {os.path.basename(i["path"]) for i in kinds(r, "no_cover")} == {"rip", "Punisher (2020)"}
    place = kinds(r, "placement")
    assert len(place) == 1 and "3 of 3 tracks not named" in place[0]["detail"]   # untagged ones aren't offered


def test_safe_problems_fixed_automatically_into_the_music_trash(root):
    client, Session, music = root
    a = album(music / "Band" / "Record (2001)")
    (a / "x.url").write_text("x")
    (music / "Empty" / "Deeper").mkdir(parents=True)
    ml.scan()
    shutil.rmtree(music / "Band" / "Record (2001)")
    (music / "Band" / "Other").mkdir()
    (music / "Band" / "Other" / "y.sfv").write_text("x")
    with Session() as db:
        r = md.run(db)
    fixed = {(i["kind"], os.path.relpath(i["path"], music)) for i in r["fixed"]}
    assert ("missing", "Band/Record (2001)") in fixed
    assert ("junk", "Band/Other/y.sfv") in fixed
    assert ("empty", "Empty") in fixed and ("empty", "Band") in fixed           # emptied by the fixes
    assert not os.path.exists(music / "Band") and not os.path.exists(music / "Empty")
    day = dt.date.today().isoformat()
    assert (music / ".trash" / day / "Band" / "Other" / "y.sfv").exists()
    with Session() as db:
        assert db.query(MusicLibAlbum).count() == 0
        assert md.last_report(db)["total"] == 0


def test_auto_fix_off_leaves_them(root):
    client, Session, music = root
    (music / "Empty").mkdir()
    with Session() as db:
        db.merge(Setting(key=doctor.AUTO_KEY, value="false"))
        db.commit()
        r = md.run(db)
    assert r["fixed"] == [] and kinds(r, "empty") and (music / "Empty").exists()


def test_duplicate_keeps_the_lossless_copy(root):
    client, Session, music = root
    album(music / "Band" / "Record (2001)", artist="Band", title="Record", year="2001")
    album(music / "Band" / "Record mp3", artist="Band", title="Record", year="2001", ext=".mp3")
    ml.scan()
    r = check(Session)
    dups = kinds(r, "duplicate")
    assert [os.path.basename(i["path"]) for i in dups] == ["Record mp3"]
    assert dups[0]["keep"].endswith("Record (2001)") and "FLAC" in dups[0]["detail"]
    # The kept copy isn't reported for its own small problems while it has a twin.
    with Session() as db:
        db.merge(Setting(key=doctor.AUTO_KEY, value="false"))
        db.commit()
        md.run(db)
        res = md.fix_ids(db, [dups[0]["id"]])
    assert res["done"] == 1 and not res["errors"]
    assert not (music / "Band" / "Record mp3").exists()
    with Session() as db:
        left = db.query(MusicLibAlbum).one()
        assert left.folder.endswith("Record (2001)") and left.issues is None


def test_a_duplicate_that_holds_other_albums_is_not_trashed(root):
    client, Session, music = root
    album(music / "Band", artist="Band", title="Record", ext=".mp3")             # loose tracks in the artist folder
    album(music / "Band" / "Record (2001)", artist="Band", title="Record")
    ml.scan()
    dup = kinds(check(Session), "duplicate")[0]
    assert dup["path"] == str(music / "Band") and dup["fixable"] is False
    with Session() as db:
        with pytest.raises(ValueError):
            md.fix(db, {**dup, "fixable": True}, [str(music)])


def test_placement_fix_organises_and_keeps_sidecars(root):
    client, Session, music = root
    src = album(music / "incoming" / "pb-punisher", year="2020")
    (src / "info.txt").write_text("setlist")
    (src / "rip.cue").write_text("cue")
    ml.scan()
    issue = kinds(check(Session), "placement")[0]
    assert "Would move to" in issue["detail"]
    with Session() as db:
        db.merge(Setting(key=doctor.AUTO_KEY, value="false"))
        db.commit()
        md.run(db)
        res = md.fix_ids(db, [issue["id"]])
    assert res["done"] == 1, res
    dest = music / "Phoebe Bridgers" / "Punisher (2020)"
    assert sorted(p.name for p in dest.iterdir()) == ["01 - Song 1.flac", "02 - Song 2.flac", "03 - Song 3.flac",
                                                       "info.txt", "rip.cue"]
    assert not src.exists()
    with Session() as db:
        assert [a.folder for a in db.query(MusicLibAlbum)] == [str(dest)]


def test_loose_tracks_in_the_library_root_go_to_their_own_albums(root):
    client, Session, music = root
    clip(music / "a.flac", artist="A", album="One", title="x", tracknumber="1")
    clip(music / "b.flac", artist="B", album="Two", title="y", tracknumber="1")
    (music / "README.txt").write_text("mine")
    ml.scan()
    issue = kinds(check(Session), "placement")[0]
    assert issue["path"] == str(music)
    with Session() as db:
        r = md.fix(db, issue, [str(music)])
    assert "put in" in r
    assert (music / "A" / "One" / "01 - x.flac").exists() and (music / "B" / "Two" / "01 - y.flac").exists()
    assert (music / "README.txt").exists()                                       # the root is never tidied away


def test_tag_and_artwork_fixes_run_in_the_background(root, monkeypatch):
    client, Session, music = root
    clip(music / "Unknown" / "rip" / "track.flac")
    ml.scan()
    seen = []
    monkeypatch.setattr(music_batch, "start", lambda action, ids, bind=None: seen.append((action, ids)))
    r = check(Session)
    with Session() as db:
        db.merge(Setting(key=doctor.AUTO_KEY, value="false"))
        db.commit()
        md.run(db)
        res = md.fix_ids(db, [i["id"] for i in kinds(r, "untagged")])
        both = md.fix_ids(db, [i["id"] for i in kinds(md.last_report(db), "no_cover")])
    assert seen[0][0] == "fix_tags" and res["started"].startswith("Fixing tags for 1 album")
    assert seen[1][0] == "covers" and both["done"] == 1


def test_low_bitrate_marks_the_album_wanted(root):
    client, Session, music = root
    folder = music / "Lo" / "Fi (2001)"
    for i in (1, 2):
        clip(folder / f"{i:02d} - t{i}.mp3", kbps=128, artist="Lo", albumartist="Lo", album="Fi", date="2001",
             title=f"t{i}", tracknumber=f"{i}/2")
    ml.scan()
    r = check(Session)
    low = kinds(r, "low_bitrate")[0]
    assert low["fixable"] is False and "Follow Lo" in low["detail"]
    with Session() as db:
        item = WatchlistItem(kind="artist", mbid="art-1", title="Lo", search_query="Lo")
        db.add(item)
        db.flush()
        db.add(MusicAlbum(watchlist_id=item.id, mbid="rg-fi", title="Fi", status="have"))
        db.commit()
    low = kinds(check(Session), "low_bitrate")[0]
    assert low.get("fixable", True) and low["watch_album_id"]
    with Session() as db:
        assert "Wanted" in md.fix(db, low, [str(music)])
        assert db.query(MusicAlbum).one().status == "wanted"
    assert "already wanted" in kinds(check(Session), "low_bitrate")[0]["detail"]


def test_old_trash_is_purged(tmp_path):
    old = tmp_path / ".trash" / "2000-01-01"
    new = tmp_path / ".trash" / dt.date.today().isoformat()
    old.mkdir(parents=True)
    new.mkdir(parents=True)
    assert md.cleanup_trash([str(tmp_path)]) == 1
    assert not old.exists() and new.exists()


def test_routes(root):
    client, Session, music = root
    (music / "Empty").mkdir()
    assert client.get("/api/music/doctor").json()["report"] is None
    r = client.post("/api/music/doctor/run").json()
    assert r["report"]["fixed"][0]["kind"] == "empty" and "batch" in r
    client.put("/api/doctor/settings", json={"auto_fix": False})
    (music / "Again").mkdir()
    r = client.post("/api/music/doctor/run").json()
    ids = [i["id"] for i in r["report"]["issues"]]
    r = client.post("/api/music/doctor/fix", json={"ids": ids}).json()
    assert r["done"] == 1 and r["report"]["total"] == 0


def test_video_doctor_leaves_music_folders_alone(root):
    client, Session, music = root
    (music / "Band").mkdir()
    (music / "Band" / "setlist.txt").write_text("x")
    (music / "Empty").mkdir()
    from app.models import DownloadPath
    with Session() as db:                                  # a scanned folder that holds the music library
        db.add(DownloadPath(name="All", path=str(music.parent), local_path=str(music.parent),
                            downloads=False, scan=True))
        db.commit()
        r = doctor.check(db)
        assert str(music.parent) in r["roots"]
    assert not [i for i in r["issues"] if i["path"].startswith(str(music))]
