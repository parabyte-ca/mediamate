"""Stars and collections, synced with Plex both ways (services/plex_sync.py), against a
fake Plex server."""
import json
from urllib.parse import parse_qs, urlsplit

import httpx
import pytest

from app.models import DownloadPath, Setting
from app.services import plex_sync
from app.services.media import store

from tests.test_pipeline import env  # noqa: F401
from tests.test_organise import lib  # noqa: F401


class FakePlex:
    """Two sections: Movies (1) and TV (2). A 'Sci-Fi' collection holds Dune."""

    def __init__(self):
        self.ratings = {"100": None, "200": None}
        self.colls = {"1": {"Sci-Fi": {"100"}}, "2": {}}
        self.puts: list[tuple[str, dict]] = []
        self.down = False

    def _coll_xml(self, sec):
        out = ""
        for i, name in enumerate(self.colls[sec]):
            out += f'<Directory ratingKey="9{sec}{i}" title="{name}"/>'
        return f"<MediaContainer>{out}</MediaContainer>"

    def _children(self, ck):
        for sec, colls in self.colls.items():
            for i, (name, members) in enumerate(colls.items()):
                if ck == f"9{sec}{i}":
                    kind = "Video" if sec == "1" else "Directory"
                    return "<MediaContainer>" + "".join(f'<{kind} ratingKey="{m}"/>' for m in members) + \
                        "</MediaContainer>"
        return "<MediaContainer/>"

    def _rating(self, key):
        r = self.ratings[key]
        return f' userRating="{r}"' if r is not None else ""

    def handler(self, req: httpx.Request) -> httpx.Response:
        if self.down:
            raise httpx.ConnectError("down")
        path, q = req.url.path, {k: v[0] for k, v in parse_qs(urlsplit(str(req.url)).query).items()}
        assert q.pop("X-Plex-Token") == "tok"
        if req.method == "PUT":
            self.puts.append((path, q))
            if path == "/:/rate":
                r = float(q["rating"])
                self.ratings[q["key"]] = None if r < 0 else r
            else:
                sec = path.split("/")[3]
                coll_holder = q["id"]
                colls = self.colls[sec]
                for k, v in q.items():
                    if k.startswith("collection[") and k.endswith("].tag.tag") and k != "collection[].tag.tag":
                        colls.setdefault(v, set()).add(coll_holder)
                for name in (q.get("collection[].tag.tag-") or "").split(","):
                    from urllib.parse import unquote
                    name = unquote(name)
                    if name in colls:
                        colls[name].discard(coll_holder)
                        if not colls[name]:
                            del colls[name]
            return httpx.Response(200)
        if path == "/library/sections":
            body = '<MediaContainer><Directory key="1" type="movie"/><Directory key="2" type="show"/>' \
                   '<Directory key="3" type="artist"/></MediaContainer>'
        elif path.endswith("/collections"):
            body = self._coll_xml(path.split("/")[3])
        elif path.startswith("/library/collections/"):
            body = self._children(path.split("/")[3])
        elif path == "/library/sections/1/all":
            body = (f'<MediaContainer><Video ratingKey="100"{self._rating("100")}><Media>'
                    '<Part file="/data/movies/Dune (2021)/Dune (2021).mkv" size="11"/></Media></Video>'
                    '<Video ratingKey="101"><Media><Part file="/other/Dune (2021).mkv" size="99"/></Media></Video>'
                    '</MediaContainer>')
        elif path == "/library/sections/2/all":
            body = (f'<MediaContainer><Video ratingKey="200" grandparentRatingKey="20"{self._rating("200")}>'
                    '<Media><Part file="/data/tv/Stick/Season 01/Stick S01E01.mkv" size="5"/></Media></Video>'
                    '</MediaContainer>')
        else:
            return httpx.Response(404)
        return httpx.Response(200, text=body)


@pytest.fixture
def plex(lib, monkeypatch):
    client, Session, media = lib
    fake = FakePlex()
    monkeypatch.setattr(plex_sync, "_client", lambda: httpx.Client(transport=httpx.MockTransport(fake.handler)))
    plex_sync.forget_cache()
    with Session() as db:
        for k, v in {"plex_enabled": "true", "plex_url": "http://plex:32400", "plex_token": "tok",
                     "plex_sync_enabled": "true"}.items():
            db.merge(Setting(key=k, value=v))
        db.commit()
    movie = media / "Movies" / "Dune (2021)" / "Dune (2021).mkv"
    ep = media / "TV" / "Stick" / "Stick S01E01.mkv"
    for f in (movie, ep):
        f.parent.mkdir(parents=True, exist_ok=True)
        f.write_bytes(b"x")
    ids = {"movie": store.upsert_file(str(movie), movie.name, 11, 60.0, "hevc", False),
           "ep": store.upsert_file(str(ep), ep.name, 5, 60.0, "hevc", False)}
    yield client, Session, media, fake, ids
    plex_sync.forget_cache()


def _row(fid):
    r = store.get_file(fid)
    return r["rating"], json.loads(r["collections"] or "[]"), r["plex_dirty"]


# ── matching ────────────────────────────────────────────────────────────────


def test_match_by_name_then_size_then_folders():
    items = [plex_sync.PlexItem("1", "1", "movie", "/a/Dune (2021)/Dune (2021).mkv", 5),
             plex_sync.PlexItem("2", "1", "movie", "/b/Other/Dune (2021).mkv", 9)]
    m = plex_sync.Matcher(items)
    assert m.find("/mnt/x/Dune (2021)/Dune (2021).mkv", 1).key == "1"     # more folders match
    assert m.find("/mnt/x/Dune (2021)/Dune (2021).mkv", 9).key == "2"     # same size wins
    assert m.find("/mnt/x/dune (2021).MKV", None) is not None              # case-insensitive
    assert m.find("/mnt/x/Alien.mkv", None) is None


def test_stars_from_plex_rating():
    assert plex_sync._stars("10.0") == 5 and plex_sync._stars("6") == 3 and plex_sync._stars("1") == 1
    assert plex_sync._stars(None) is None and plex_sync._stars("0") is None and plex_sync._stars("x") is None


# ── edits are sent straight away ────────────────────────────────────────────


def test_rating_is_sent_as_double_and_cleared_with_minus_one(plex):
    client, Session, media, fake, ids = plex
    r = client.post(f"/api/media/files/{ids['movie']}/rating", json={"stars": 4}).json()
    assert r["plex"] == "sent" and r["rating"] == 4
    assert fake.ratings["100"] == 8.0 and fake.puts == [("/:/rate", fake.puts[0][1])]   # rating only
    assert fake.puts[0][1]["key"] == "100" and fake.colls["1"]["Sci-Fi"] == {"100"}       # the right Dune
    assert _row(ids["movie"])[0::2] == (4, 0)
    client.post(f"/api/media/files/{ids['movie']}/rating", json={"stars": 0})
    assert fake.puts[-1][1]["rating"] == "-1" and fake.ratings["100"] is None
    assert store.get_file(ids["movie"])["rating"] is None


def test_bad_stars_rejected(plex):
    client, Session, media, fake, ids = plex
    assert client.post(f"/api/media/files/{ids['movie']}/rating", json={"stars": 7}).status_code == 400
    assert client.post("/api/media/files/9999/rating", json={"stars": 3}).status_code == 404


def test_collections_add_and_remove(plex):
    client, Session, media, fake, ids = plex
    plex_sync.sync()                                              # learns Dune is in Sci-Fi
    r = client.post(f"/api/media/files/{ids['movie']}/collections",
                    json={"add": ["Christmas"], "remove": ["Sci-Fi"]}).json()
    assert r["plex"] == "sent" and r["collections"] == ["Christmas"]
    assert fake.colls["1"] == {"Christmas": {"100"}}
    path, q = fake.puts[-1]
    assert path == "/library/sections/1/all" and q["type"] == "1" and q["collection.locked"] == "1"


def test_adding_before_any_sync_keeps_plex_collections(plex):
    client, Session, media, fake, ids = plex
    r = client.post(f"/api/media/files/{ids['movie']}/collections", json={"add": ["Christmas"]}).json()
    assert r["collections"] == ["Christmas", "Sci-Fi"]
    assert fake.colls["1"] == {"Sci-Fi": {"100"}, "Christmas": {"100"}}


def test_episode_collections_go_on_the_show(plex):
    client, Session, media, fake, ids = plex
    r = client.post(f"/api/media/files/{ids['ep']}/collections", json={"add": ["Crime"]}).json()
    assert r["plex"] == "sent"
    path, q = fake.puts[-1]
    assert path == "/library/sections/2/all" and q["type"] == "2" and q["id"] == "20"
    assert fake.colls["2"] == {"Crime": {"20"}}


def test_plex_down_keeps_the_edit_for_later(plex):
    client, Session, media, fake, ids = plex
    fake.down = True
    r = client.post(f"/api/media/files/{ids['movie']}/rating", json={"stars": 3}).json()
    assert r["plex"] == "queued" and _row(ids["movie"])[2] == 1
    assert client.get("/api/media/plex/status").json()["pending"] == 1
    fake.down = False
    res = client.post("/api/media/plex/sync").json()
    assert res["ok"] and res["pushed"] == 1
    assert fake.ratings["100"] == 6.0 and _row(ids["movie"])[2] == 0


def test_sync_off_saves_locally(plex):
    client, Session, media, fake, ids = plex
    with Session() as db:
        db.merge(Setting(key="plex_sync_enabled", value="false"))
        db.commit()
    r = client.post(f"/api/media/files/{ids['movie']}/rating", json={"stars": 5}).json()
    assert r["plex"] == "off" and not fake.puts and store.get_file(ids["movie"])["rating"] == 5
    assert not client.post("/api/media/plex/sync").json()["ok"]


# ── the sync ────────────────────────────────────────────────────────────────


def test_sync_pulls_plex_values(plex):
    client, Session, media, fake, ids = plex
    fake.ratings["200"] = 10.0
    res = plex_sync.sync()
    assert res["matched"] == 2 and res["pulled"] == 2
    assert _row(ids["movie"]) == (None, ["Sci-Fi"], 0)
    assert _row(ids["ep"]) == (5, [], 0)
    assert store.get_file(ids["movie"])["plex_key"] == "100"
    assert client.get("/api/media/plex/status").json()["last"]["matched"] == 2


def test_plex_wins_when_both_changed(plex):
    client, Session, media, fake, ids = plex
    plex_sync.sync()
    fake.down = True
    client.post(f"/api/media/files/{ids['movie']}/rating", json={"stars": 2})     # queued here
    fake.down = False
    fake.ratings["100"] = 8.0                                                       # and changed in Plex
    plex_sync.sync()
    assert _row(ids["movie"])[0] == 4 and fake.ratings["100"] == 8.0


def test_first_sync_keeps_both_sides(plex):
    client, Session, media, fake, ids = plex
    fake.down = True
    client.post(f"/api/media/files/{ids['movie']}/collections", json={"add": ["Christmas"]})
    fake.down = False
    plex_sync.sync()
    assert _row(ids["movie"]) == (None, ["Christmas", "Sci-Fi"], 0)
    assert fake.colls["1"] == {"Sci-Fi": {"100"}, "Christmas": {"100"}}


def test_unmatched_files_stay_pending(plex):
    client, Session, media, fake, ids = plex
    f = media / "Movies" / "Alien (1979).mkv"
    f.write_bytes(b"x")
    fid = store.upsert_file(str(f), f.name, 1, 60.0, "hevc", False)
    r = client.post(f"/api/media/files/{fid}/rating", json={"stars": 5}).json()
    assert r["plex"] == "queued"
    res = plex_sync.sync()
    assert res["unmatched"] == 1 and _row(fid) == (5, [], 1)


# ── bulk, names, adult guard ────────────────────────────────────────────────


def test_bulk_rate_and_collect(plex):
    client, Session, media, fake, ids = plex
    r = client.post("/api/media/files/bulk", json={"action": "rate", "file_ids": list(ids.values()), "stars": 3}).json()
    assert r["done"] == 2 and r["sent"] == 2
    r = client.post("/api/media/files/bulk", json={"action": "collect", "file_ids": list(ids.values()),
                                                   "add": ["Favourites"]}).json()
    assert r["done"] == 2
    assert fake.colls["1"]["Favourites"] == {"100"} and fake.colls["2"]["Favourites"] == {"20"}
    names = client.get("/api/media/plex/collections").json()["names"]
    assert "Favourites" in names and "Sci-Fi" in names


def test_adult_files_never_reach_plex(plex):
    client, Session, media, fake, ids = plex
    bell = media / "bellesa"
    bell.mkdir()
    f = bell / "Dune (2021).mkv"                     # same name as a Plex item, on purpose
    f.write_bytes(b"x")
    fid = store.upsert_file(str(f), f.name, 11, 60.0, "hevc", False)
    with Session() as db:
        db.add(DownloadPath(name="bellesa", path=str(bell), local_path=str(bell), downloads=False,
                            scan=True, protected=True))
        db.commit()
    r = client.post(f"/api/media/files/{fid}/collections", json={"add": ["Sci-Fi"]})
    assert r.status_code == 400 and "protected" in r.json()["msg"]
    r = client.post(f"/api/media/files/{fid}/rating", json={"stars": 5}).json()
    assert r["plex"] == "local" and not fake.puts
    assert plex_sync.sync()["skipped"] == 1 and not fake.puts


def test_migration_adds_columns(tmp_path):
    from sqlalchemy import create_engine, text
    from app.migrations import run_migrations
    engine = create_engine(f"sqlite:///{tmp_path / 'old.db'}")
    with engine.connect() as c:
        c.execute(text("CREATE TABLE media_files (id INTEGER PRIMARY KEY, path TEXT, filename TEXT)"))
        c.commit()
    run_migrations(engine)
    with engine.connect() as c:
        cols = {r[1] for r in c.execute(text("PRAGMA table_info(media_files)"))}
    assert {"rating", "collections", "plex_key", "plex_seen", "plex_dirty"} <= cols
