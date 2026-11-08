"""Share links: a public link plays one video or album without logging in, until it
expires or is revoked; public responses never carry paths; a token reaches only its item."""
import datetime as dt
import shutil

import pytest

from app.models import DownloadPath, MusicLibAlbum, MusicTrack, Setting, ShareLink

from tests.helpers import from_address
from tests.test_movie_genres import _add
from tests.test_organise import lib  # noqa: F401
from tests.test_pipeline import env  # noqa: F401


@pytest.fixture
def setup(lib):
    client, Session, media = lib
    clip = media / "Movies" / "Some Film (2020)" / "Some Film (2020).mp4"
    row = _add(clip)
    other = _add(media / "Movies" / "Other (2021).mp4")
    album_dir = media / "Music" / "Phoebe Bridgers" / "Punisher (2020)"
    album_dir.mkdir(parents=True)
    (album_dir / "cover.jpg").write_bytes(b"\xff\xd8jpg")
    with Session() as s:
        a = MusicLibAlbum(folder=str(album_dir), artist="Phoebe Bridgers", title="Punisher", format="flac",
                          cover="cover.jpg")
        s.add(a)
        s.flush()
        for i in (1, 2):
            p = album_dir / f"0{i}.flac"
            p.write_bytes(b"fLaC" + bytes(range(256)) * 40)
            s.add(MusicTrack(album_id=a.id, path=str(p), title=f"Song {i}", track_no=i, disc_no=1))
        s.commit()
        aid = a.id
    return client, Session, media, row, other, aid


def test_create_list_revoke(setup):
    client, Session, media, vid, _, aid = setup
    r = client.post("/api/shares", json={"kind": "video", "id": vid, "days": 7})
    assert r.status_code == 201
    out = r.json()
    assert out["title"] == "Some Film (2020)" and out["url"].startswith("/s/") and not out["adult"]
    exp = dt.datetime.fromisoformat(out["expires_at"])
    assert dt.timedelta(days=6, hours=23) < exp - dt.datetime.utcnow() <= dt.timedelta(days=7)
    forever = client.post("/api/shares", json={"kind": "album", "id": aid, "days": 0}).json()
    assert forever["expires_at"] is None and forever["title"] == "Phoebe Bridgers – Punisher"
    assert {s["id"] for s in client.get("/api/shares").json()} == {out["id"], forever["id"]}
    assert client.post("/api/shares", json={"kind": "video", "id": vid, "days": 3}).status_code == 400
    assert client.delete(f"/api/shares/{out['id']}").json() == {"ok": True}
    assert [s["id"] for s in client.get("/api/shares").json()] == [forever["id"]]


def test_public_base_address(setup):
    client, Session, media, vid, _, _ = setup
    with Session() as s:
        s.merge(Setting(key="public_url", value="https://mm.example.com/"))
        s.commit()
    url = client.post("/api/shares", json={"kind": "video", "id": vid}).json()["url"]
    assert url.startswith("https://mm.example.com/s/")


def _token(client, kind, tid, days=7):
    return client.post("/api/shares", json={"kind": kind, "id": tid, "days": days}).json()["url"].rsplit("/", 1)[1]


def test_public_pages_need_no_login_and_leak_nothing(setup, monkeypatch):
    from app.main import app
    from fastapi.testclient import TestClient
    client, Session, media, vid, _, aid = setup
    with Session() as s:
        s.merge(Setting(key="ui_password", value="hunter2"))
        s.merge(Setting(key="session_secret", value="s3cret"))
        s.commit()
    tv, ta = _token(client, "video", vid), _token(client, "album", aid)
    monkeypatch.setattr("app.middleware.auth.SessionLocal", Session)
    outside = TestClient(from_address(app, "8.8.8.8"))
    assert outside.get("/api/shares").status_code == 401                         # the rest still needs a login
    meta = outside.get(f"/api/public/s/{tv}")
    assert meta.status_code == 200 and meta.json()["kind"] == "video"
    assert str(media) not in meta.text and "Movies" not in meta.text
    v = outside.get(f"/api/public/s/{tv}/video", headers={"Range": "bytes=0-0"})
    assert v.status_code == 206
    album = outside.get(f"/api/public/s/{ta}").json()
    assert [t["title"] for t in album["tracks"]] == ["Song 1", "Song 2"] and album["has_cover"]
    assert str(media) not in str(album)
    assert outside.get(f"/api/public/s/{ta}/track/2", headers={"Range": "bytes=0-9"}).status_code == 206
    assert outside.get(f"/api/public/s/{ta}/track/3").status_code == 404
    assert outside.get(f"/api/public/s/{ta}/cover").content == b"\xff\xd8jpg"
    assert outside.get(f"/api/public/s/{tv}/track/1").status_code == 404           # a video token: no tracks
    with Session() as s:
        assert s.query(ShareLink).filter(ShareLink.kind == "video").one().views == 1


def test_expired_and_revoked_links_are_gone(setup):
    client, Session, media, vid, _, aid = setup
    t1 = _token(client, "video", vid, 1)
    t2 = _token(client, "album", aid)
    with Session() as s:
        s.query(ShareLink).filter(ShareLink.kind == "video").update(
            {ShareLink.expires_at: dt.datetime.utcnow() - dt.timedelta(minutes=1)})
        s.commit()
    sid = next(x["id"] for x in client.get("/api/shares").json() if x["kind"] == "album")
    client.delete(f"/api/shares/{sid}")
    for url in (f"/api/public/s/{t1}", f"/api/public/s/{t1}/video", f"/api/public/s/{t1}/play",
                f"/api/public/s/{t2}", f"/api/public/s/{t2}/track/1", f"/api/public/s/{t2}/cover",
                "/api/public/s/not-a-real-token"):
        r = client.get(url)
        assert r.status_code == 404 and "expired or was removed" in r.json()["detail"], url


def test_play_converts_through_the_preview(setup, monkeypatch):
    from app.services.media import preview
    client, Session, media, vid, _, _ = setup
    seen = {}

    class P:
        def chunks(self):
            yield b"ftyp"

        def stop(self):
            seen["stopped"] = True
    monkeypatch.setattr(preview, "open_player", lambda path, start: seen.setdefault("args", (path, start)) and P())
    t = _token(client, "video", vid)
    r = client.get(f"/api/public/s/{t}/play?start=12")
    assert r.status_code == 200 and r.content == b"ftyp"
    assert seen["args"][0].endswith("Some Film (2020).mp4") and seen["args"][1] == 12


def test_adult_videos_are_flagged(setup):
    client, Session, media, vid, _, _ = setup
    bel = media / "Bellesa"
    row = _add(bel / "Studio - Scene.mp4")
    with Session() as s:
        s.add(DownloadPath(name="Bellesa", path=str(bel), protected=True))
        s.commit()
    out = client.post("/api/shares", json={"kind": "video", "id": row}).json()
    assert out["adult"] is True
    t = out["url"].rsplit("/", 1)[1]
    assert client.get(f"/api/public/s/{t}").json()["adult"] is True


pytestmark = pytest.mark.skipif(not shutil.which("ffmpeg"), reason="ffmpeg not installed")
