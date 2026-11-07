"""After an album download is imported: the torrent leaves qBittorrent, its raw files go to
the music trash (never a second copy in the library), and Plex rescans its music."""
import datetime as dt
import shutil

import pytest

from app.models import Download, MusicAlbum, MusicLibAlbum, Setting, WatchlistItem
from app.services import media_servers, qbittorrent
from app.services import music_library as ml

from tests.test_music_library import clip, mbz, offline, root  # noqa: F401
from tests.test_organise import lib  # noqa: F401
from tests.test_pipeline import env  # noqa: F401

pytestmark = pytest.mark.skipif(not shutil.which("ffmpeg"), reason="ffmpeg not installed")


@pytest.fixture
def qbit(monkeypatch):
    calls = {"removed": [], "plex": [], "paths": {}}
    monkeypatch.setattr(ml, "IMPORT_SYNC", True)
    monkeypatch.setattr("app.services.pipeline.on_download_complete", lambda bind, dl_id: None)
    monkeypatch.setattr(qbittorrent, "remove_torrent", lambda h, delete_files=False: calls["removed"].append((h, delete_files)))
    monkeypatch.setattr(qbittorrent, "get_content_path", lambda h: calls["paths"].get(h))

    async def plex(url, token, kind):
        calls["plex"].append(kind)
        return ["3"]
    monkeypatch.setattr(media_servers, "refresh_plex_sections", plex)
    return calls


def _download(Session, rip, status="completed", have=False, settings=()):
    with Session() as db:
        for k, v in settings:
            db.merge(Setting(key=k, value=v))
        w = WatchlistItem(kind="artist", mbid="art-1", title="Phoebe Bridgers", search_query="Phoebe Bridgers",
                          season=1, episode=1)
        db.add(w)
        db.flush()
        al = MusicAlbum(watchlist_id=w.id, mbid="rg-2", title="Punisher", release_date="2020-06-18",
                        status="have" if have else "grabbed")
        db.add(al)
        db.flush()
        dl = Download(title=rip.name, download_path=str(rip.parent), torrent_hash="h1",
                      status=status if have else "downloading", watchlist_id=w.id, music_album_id=al.id)
        db.add(dl)
        db.commit()
        if not have:
            dl.status = status
            db.commit()
        return dl.id


def _rip(folder):
    for i in (1, 2, 3):
        clip(folder / f"{i}.flac", artist="Phoebe Bridgers", albumartist="Phoebe Bridgers", album="Punisher",
             date="2020", title=f"Song {i}", tracknumber=f"{i}/3")
    return folder


def test_download_inside_the_library_is_trashed_and_plex_refreshed(root, qbit):
    client, Session, music = root
    rip = _rip(music / "Phoebe Bridgers - Punisher [FLAC]")
    qbit["paths"]["h1"] = str(rip)
    _download(Session, rip, settings=[("plex_enabled", "true"), ("plex_url", "http://plex"), ("plex_token", "t")])
    dest = music / "Phoebe Bridgers" / "Punisher (2020)"
    assert len(list(dest.glob("*.flac"))) == 3
    assert qbit["removed"] == [("h1", False)] and not rip.exists()
    assert (music / ".trash" / dt.date.today().isoformat() / rip.name / "1.flac").exists()
    assert qbit["plex"] == ["artist"]
    with Session() as db:
        assert db.query(Download).one().qbit_removed
        assert [a.folder for a in db.query(MusicLibAlbum)] == [str(dest)]


def test_download_outside_the_library_is_removed_with_its_files(root, qbit, tmp_path):
    client, Session, music = root
    rip = _rip(tmp_path / "downloads" / "Phoebe Bridgers - Punisher [FLAC]")
    qbit["paths"]["h1"] = str(rip)
    _download(Session, rip)
    assert qbit["removed"] == [("h1", True)]
    assert len(list((music / "Phoebe Bridgers" / "Punisher (2020)").glob("*.flac"))) == 3
    assert qbit["plex"] == []                                     # Plex not set up: no refresh


def test_switched_off_keeps_the_torrent(root, qbit):
    client, Session, music = root
    rip = _rip(music / "Phoebe Bridgers - Punisher [FLAC]")
    qbit["paths"]["h1"] = str(rip)
    _download(Session, rip, settings=[("music_remove_after_import", "false")])
    assert qbit["removed"] == [] and rip.exists()


def test_hourly_tidy_cleans_up_older_downloads(root, qbit):
    client, Session, music = root
    rip = _rip(music / "Phoebe Bridgers - Punisher [FLAC]")
    qbit["paths"]["h1"] = str(rip)
    _download(Session, rip, status="seeding", have=True)          # finished before clean-up existed
    assert qbit["removed"] == []
    with Session() as db:
        bind = db.get_bind()
    assert ml.tidy_finished(bind) == 1
    assert qbit["removed"] == [("h1", False)] and not rip.exists()
    assert ml.tidy_finished(bind) == 0


def test_not_every_track_placed_keeps_everything(root, qbit, monkeypatch):
    client, Session, music = root
    rip = _rip(music / "Phoebe Bridgers - Punisher [FLAC]")
    qbit["paths"]["h1"] = str(rip)
    monkeypatch.setattr(ml, "_placed", lambda src, folder: not src.endswith("3.flac"))
    _download(Session, rip)
    assert qbit["removed"] == [] and rip.exists()


def test_refresh_plex_sections_only_refreshes_music(monkeypatch):
    import asyncio

    import httpx
    hits = []

    def handler(req):
        hits.append(req.url.path)
        if req.url.path == "/library/sections":
            return httpx.Response(200, text='<MediaContainer><Directory key="1" type="movie"/>'
                                            '<Directory key="3" type="artist"/><Directory key="2" type="show"/>'
                                            '</MediaContainer>')
        return httpx.Response(200)
    real = httpx.AsyncClient
    monkeypatch.setattr(media_servers.httpx, "AsyncClient",
                        lambda **kw: real(transport=httpx.MockTransport(handler), **kw))
    assert asyncio.run(media_servers.refresh_plex_sections("http://plex", "t", "artist")) == ["3"]
    assert hits == ["/library/sections", "/library/sections/3/refresh"]
