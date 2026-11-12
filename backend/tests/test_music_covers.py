"""Music covers: small cached JPEGs for lists and players, the original on request."""
import io
import os

import pytest
from PIL import Image

from app.models import MusicLibAlbum
from app.services import music_covers

from tests.test_pipeline import env  # noqa: F401
from tests.test_organise import lib  # noqa: F401


def _png(px=2000):
    b = io.BytesIO()
    Image.new("RGB", (px, px), (200, 30, 30)).save(b, "PNG")
    return b.getvalue()


@pytest.fixture
def cover(lib, tmp_path, monkeypatch):
    client, Session, media = lib
    monkeypatch.setattr(music_covers, "cache_dir", lambda: tmp_path / "covers")
    folder = tmp_path / "music" / "Album"
    folder.mkdir(parents=True)
    (folder / "cover.png").write_bytes(_png())
    with Session() as db:
        a = MusicLibAlbum(folder=str(folder), artist="A", title="Album", cover="cover.png")
        db.add(a)
        db.commit()
        aid = a.id
    return client, Session, folder, aid, tmp_path / "covers"


def test_small_cover_is_made_once_and_cached(cover, monkeypatch):
    client, _, folder, aid, cache = cover
    r = client.get(f"/api/music/library/albums/{aid}/cover?size=96&v=x")
    assert r.status_code == 200 and r.headers["content-type"] == "image/jpeg"
    assert "immutable" in r.headers["cache-control"]
    assert len(r.content) < 10_000 and len(r.content) < len((folder / "cover.png").read_bytes())
    assert max(Image.open(io.BytesIO(r.content)).size) <= 96
    assert len(list(cache.glob("*.jpg"))) == 1
    monkeypatch.setattr("PIL.Image.open", lambda *a, **k: pytest.fail("served from the cache"))
    assert client.get(f"/api/music/library/albums/{aid}/cover?size=96").status_code == 200


def test_new_cover_gives_a_new_thumbnail(cover):
    client, _, folder, aid, cache = cover
    client.get(f"/api/music/library/albums/{aid}/cover?size=160")
    st = os.stat(folder / "cover.png")
    (folder / "cover.png").write_bytes(_png(800))
    os.utime(folder / "cover.png", (st.st_atime, st.st_mtime + 10))
    client.get(f"/api/music/library/albums/{aid}/cover?size=160")
    assert len(list(cache.glob("*.jpg"))) == 2


def test_original_bad_size_broken_and_missing(cover, lib):
    client, Session, folder, aid, _ = cover
    assert client.get(f"/api/music/library/albums/{aid}/cover").content == (folder / "cover.png").read_bytes()
    assert client.get(f"/api/music/library/albums/{aid}/cover?size=50").status_code == 422
    (folder / "cover.png").write_bytes(b"not an image")
    assert client.get(f"/api/music/library/albums/{aid}/cover?size=96").content == b"not an image"
    (folder / "cover.png").unlink()
    assert client.get(f"/api/music/library/albums/{aid}/cover?size=96").status_code == 404
