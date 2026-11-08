"""A Sloan album search once grabbed "BackroomCastingCouch CJ Sloan … (01 12 2025)": the
artist was somewhere in the name and the album "12" was in the date. Names must now lead
with the artist, adult results are filtered harder, and a download that turns out to be
a video is removed and never grabbed again."""
import shutil

import pytest

from app.models import Download, MusicAlbum
from app.services import music, qbittorrent
from app.services.indexers import _is_adult

from tests.test_music_cleanup import _download, qbit  # noqa: F401
from tests.test_music_library import mbz, offline, root  # noqa: F401
from tests.test_music_watchlist import _album, _r, mb  # noqa: F401
from tests.test_organise import lib  # noqa: F401
from tests.test_pipeline import env  # noqa: F401
from tests.test_watchlist_movies import Session, db, grabbing  # noqa: F401

PORN = "BackroomCastingCouch CJ Sloan I Told You I Like Anal (01 12 2025)"


@pytest.mark.parametrize("title,album,ok", [
    (PORN, "12", False),
    ("Sloan Steady (2022) 12 tracks", "12", False),
    ("Sloan 12 (2018) FLAC", "12", True),
    ("Sloan - 12 [FLAC]", "12", True),
    ("[Power Pop Indie Rock] Sloan Based on the Best Seller", "Based on the Best Seller", True),
    ("Sloan Steady (2022) [24Bit 48kHz] FLAC", "Steady", True),
    ("(2018) Sloan - 12 [MP3 320]", "12", True),
    ("Best of Sloan Twice Removed", "Twice Removed", False),        # the artist isn't first
])
def test_names_lead_with_the_artist(title, album, ok):
    assert music.matches_album(title, "Sloan", album) is ok


@pytest.mark.parametrize("title,adult", [
    (PORN, True),
    ("Some Studio Scene Anal Fun 1080p", True),
    ("Rush - Moving Pictures (The Analog Kid) FLAC", False),
    ("Milford Graves - Babi [FLAC]", False),
    ("Sloan - 12 [FLAC]", False),
])
def test_adult_filter(title, adult):
    assert _is_adult({"title": title}) is adult


def test_jackett_xxx_category_is_adult():
    from app.services.indexers.jackett import _is_xxx
    assert _is_xxx({"categories": [{"id": 6010}]}) and _is_xxx({"Category": [6000, 100000]})
    assert not _is_xxx({"categories": [{"id": 3040}]})
    assert _is_adult({"title": "Innocent Name", "_adult_cat": True})


@pytest.mark.parametrize("names,why", [
    (["scene.mp4"], True), (["a/b.mkv", "a/sample.txt"], True),
    (["01.flac", "video.mkv"], False), (["01.mp3"], False), (None, False), ([], False),
])
def test_not_music(names, why):
    assert bool(music.not_music(names)) is why


def test_a_video_torrent_is_refused_at_grab(db, mb, grabbing, monkeypatch):
    removed = []
    monkeypatch.setattr(qbittorrent, "file_names", lambda h: ["scene.mp4"])
    monkeypatch.setattr(qbittorrent, "remove_torrent", lambda h, delete_files=False: removed.append((h, delete_files)))
    item, album = _album(db, mb)
    grabbing["results"] = [_r("Phoebe Bridgers - Punisher [FLAC]")]
    out = music.grab(db, item, album)
    assert out["status"] == "error" and "Not music" in out["message"]
    assert removed == [("a" * 40, True)]
    dl = db.query(Download).one()
    assert dl.status == "error" and dl.qbit_removed
    monkeypatch.setattr(qbittorrent, "file_names", lambda h: ["01.flac"])
    grabbing["results"].append(_r("Phoebe Bridgers - Punisher 24bit FLAC", "b"))
    assert music.grab(db, item, album)["status"] == "started"          # the failed one is never tried again
    assert grabbing["adds"][-1][0].endswith("b" * 40)


def test_a_video_download_is_rejected_at_import(root, qbit):
    client, Session, music_dir = root
    rip = music_dir / PORN
    rip.mkdir()
    (rip / "scene.mp4").write_bytes(b"x" * 100)
    qbit["paths"]["h1"] = str(rip)
    _download(Session, rip)
    assert qbit["removed"] == [("h1", True)]
    with Session() as s:
        dl = s.query(Download).one()
        assert dl.status == "error" and "video" in dl.error_detail
        assert s.query(MusicAlbum).one().status == "wanted"
    assert qbit["plex"] == []


def test_tidy_rejects_a_video_while_it_downloads(root, qbit, monkeypatch):
    from app.services import music_library as ml
    client, Session, music_dir = root
    monkeypatch.setattr(qbittorrent, "file_names", lambda h: ["x/scene.mkv"])
    rip = music_dir / PORN
    _download(Session, rip, status="downloading")
    with Session() as s:
        bind = s.get_bind()
    ml.tidy_finished(bind)
    assert qbit["removed"] == [("h1", True)]
    with Session() as s:
        assert s.query(Download).one().status == "error"


pytestmark = pytest.mark.skipif(not shutil.which("ffmpeg"), reason="ffmpeg not installed")
