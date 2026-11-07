"""Album downloads qBittorrent can't start: the reason is shown, errored torrents don't
pile up, and the folder Test points at qBittorrent's write access."""
from types import SimpleNamespace
from unittest.mock import patch

import pytest

from app.models import Download, DownloadPath
from app.services import folders, music, qbittorrent

from tests.test_music_watchlist import _album, _r, mb  # noqa: F401
from tests.test_watchlist_movies import Session, db, grabbing  # noqa: F401

DENIED = 'File error alert. Torrent: "Phoebe Bridgers - Punisher [FLAC]". File: "/dl/music/x.flac". ' \
         'Reason: "Phoebe Bridgers - Punisher [FLAC] file_open (/dl/music/x.flac) error: Permission denied"'


class FakeClient:
    def __init__(self, add="Ok.", state="downloading", log=(), save="/dl/music"):
        self.add, self.state, self.log, self.save = add, state, list(log), save
        self.deleted = []

    def torrents_add(self, **kw):
        return self.add

    def torrents_info(self, torrent_hashes=None):
        return [SimpleNamespace(state=self.state, progress=0.0, size=1, dlspeed=0, eta=0)]

    def torrents_properties(self, torrent_hash=None):
        return {"save_path": self.save}

    def log_main(self, **kw):
        return [{"id": i, "message": m} for i, m in enumerate(self.log)]

    def torrents_delete(self, delete_files=False, torrent_hashes=None):
        self.deleted.append(torrent_hashes)


@pytest.fixture
def qbit(monkeypatch):
    holder = {"client": FakeClient()}
    monkeypatch.setattr(qbittorrent, "_get_client", lambda: holder["client"])
    return holder


# ── qBittorrent ─────────────────────────────────────────────────────────────


def test_add_raises_when_qbittorrent_refuses(qbit):
    qbit["client"] = FakeClient(add="Fails.")
    with pytest.raises(qbittorrent.AddFailed):
        qbittorrent.add_torrent("magnet:?xt=urn:btih:" + "a" * 40, "/dl/music", "music")
    qbit["client"] = FakeClient(add="Ok.")
    assert qbittorrent.add_torrent("magnet:?xt=urn:btih:" + "a" * 40, "/dl/music") == "a" * 40


def test_error_reason_reads_qbittorrents_log(qbit):
    qbit["client"] = FakeClient(log=["Something else", DENIED, "Another torrent: Permission denied"])
    assert "Permission denied" in qbittorrent.error_reason("a" * 40, "Phoebe Bridgers - Punisher [FLAC]")
    qbit["client"] = FakeClient(log=[])
    assert qbittorrent.error_reason("a" * 40, "x") == "qBittorrent couldn't save to /dl/music"
    assert qbittorrent.is_folder_problem("qBittorrent couldn't save to /dl/music")
    assert not qbittorrent.is_folder_problem("tracker unreachable")


def test_confirm_added_raises_on_an_errored_torrent(qbit):
    qbit["client"] = FakeClient(state="error", log=[DENIED])
    with pytest.raises(qbittorrent.AddFailed, match="Permission denied"):
        qbittorrent.confirm_added("a" * 40, "Phoebe Bridgers - Punisher [FLAC]", wait=0)
    qbit["client"] = FakeClient(state="downloading")
    qbittorrent.confirm_added("a" * 40, "x", wait=0)


# ── grabbing ────────────────────────────────────────────────────────────────


def test_a_refused_download_says_why_and_is_not_retried_by_itself(db, mb, grabbing, qbit):
    db.add(DownloadPath(name="Music", path="/dl/music", music_downloads=True))
    db.commit()
    item, album = _album(db, mb)
    grabbing["results"] = [_r("Phoebe Bridgers - Punisher [FLAC]", "c"), _r("Phoebe Bridgers - Punisher FLAC 16bit", "d")]
    qbit["client"] = FakeClient(state="error", log=[DENIED])
    out = music.grab(db, item, album, force=True)
    assert out["status"] == "error" and "Permission denied" in out["message"] and "/dl/music" in out["message"]
    dl = db.query(Download).one()
    assert dl.status == "error" and "Permission denied" in dl.error_detail and dl.qbit_removed
    assert qbit["client"].deleted == ["c" * 40]                  # not left errored in qBittorrent
    assert album.status == "wanted"
    # The next scans don't pile up more errored torrents.
    out = music.scan_artist(db, item, {})
    assert out[0]["status"] == "blocked" and "Permission denied" in out[0]["message"]
    assert db.query(Download).count() == 1
    assert music.problem(db, album).startswith("qBittorrent couldn't save it: ")
    # Pressing download tries again (here: fixed).
    qbit["client"] = FakeClient(state="downloading")
    assert music.grab(db, item, album, force=True)["status"] == "started"
    assert music.problem(db, album) is None


def test_other_failures_get_one_more_try(db, mb, grabbing, qbit):
    item, album = _album(db, mb)
    grabbing["results"] = [_r("Phoebe Bridgers - Punisher [FLAC]", "c"), _r("Phoebe Bridgers - Punisher FLAC 16bit", "d"),
                           _r("Phoebe Bridgers - Punisher FLAC 24bit", "e")]
    assert music.grab(db, item, album)["status"] == "started"
    db.query(Download).one().status = "failed"
    db.commit()
    assert music.problem(db, album) is None
    assert music.scan_artist(db, item, {})[0]["status"] == "started"
    db.query(Download).filter(Download.status == "downloading").one().status = "failed"
    db.commit()
    assert "2 downloads failed" in music.problem(db, album)
    assert music.scan_artist(db, item, {})[0]["status"] == "blocked"


def test_album_route_shows_the_problem(db, mb, grabbing, qbit):
    from app.routers.music import _album_out
    item, album = _album(db, mb)
    db.add(Download(title="x", status="error", music_album_id=album.id, error_detail="Permission denied",
                    download_path="/dl/music"))
    db.commit()
    assert "Permission denied" in _album_out(album, db)["problem"]


# ── folder Test ─────────────────────────────────────────────────────────────


def test_folder_test_reports_qbittorrent_cannot_write(db, tmp_path, qbit):
    d = tmp_path / "music"
    d.mkdir()
    dp = DownloadPath(name="Music", path="/dl/music", local_path=str(d), downloads=True, music_downloads=True)
    db.add(dp)
    db.commit()
    with patch.object(qbittorrent, "directory_exists", return_value=True):
        assert all(c["key"] != "qbit_write" for c in folders.check(db, dp)["checks"])
        db.add(Download(title="x", status="error", error_detail="Permission denied", download_path="/dl/music/"))
        db.commit()
        r = folders.check(db, dp)
    row = next(c for c in r["checks"] if c["key"] == "qbit_write")
    assert row["status"] == "fail" and "Permission denied" in row["detail"]
    assert r["hint"]["kind"] == "qbit-write" and "docker exec -u" in r["hint"]["steps"][2]["copy"] and "docker top" in r["hint"]["steps"][0]["copy"]
    assert "nfs4xdr_setfacl -a \"group:apps:" in r["hint"]["steps"][4]["copy"]
    assert not r["ok"]


def test_a_failed_download_stops_showing_as_downloading(db, mb, grabbing, qbit):
    item, album = _album(db, mb)
    grabbing["results"] = [_r("Phoebe Bridgers - Punisher [FLAC]", "c")]
    assert music.grab(db, item, album)["status"] == "started" and album.status == "grabbed"
    dl = db.query(Download).one()
    dl.status, dl.error_detail = "error", "qBittorrent couldn't save to /dl/music"
    db.commit()
    s = music.summary(db, item)
    assert album.status == "wanted" and s["grabbed"] == 0 and s["wanted"] >= 1
    assert music.problem(db, album).startswith("qBittorrent couldn't save")


def test_event_sweep_failure_resets_the_album(db, mb, grabbing, qbit):
    from app.services import download_events
    item, album = _album(db, mb)
    grabbing["results"] = [_r("Phoebe Bridgers - Punisher [FLAC]", "c")]
    music.grab(db, item, album)
    dl = db.query(Download).one()
    with patch.object(download_events, "_alert"):
        download_events._fail_download(db, dl, {}, "qBittorrent reported 'error'", "notify_on_failed", "notified_failed")
    db.refresh(album)
    assert album.status == "wanted"
