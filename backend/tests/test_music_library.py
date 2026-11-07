"""The music library: tags, scanning and checks, MusicBrainz tag fixes, organising for
Plex, importing finished album downloads, and the routes. Audio clips are made with ffmpeg."""
import json
import os
import shutil
import subprocess

import pytest

from app.models import Download, MusicAlbum, MusicLibAlbum, MusicTrack, Setting, WatchlistItem
from app.services import music_library as ml
from app.services import musicbrainz

from tests.test_pipeline import env  # noqa: F401
from tests.test_organise import lib  # noqa: F401

pytestmark = pytest.mark.skipif(not shutil.which("ffmpeg"), reason="ffmpeg not installed")


@pytest.fixture(autouse=True)
def offline(monkeypatch):
    """Never reach the real MusicBrainz; tests that need it set their own answers."""
    def nope(*a, **k):
        raise musicbrainz.MusicBrainzUnavailable("offline in tests")
    monkeypatch.setattr(musicbrainz, "_get", nope)
    monkeypatch.setattr(musicbrainz, "cover_bytes", lambda rg: None)


def clip(path, kbps=None, **tags):
    path.parent.mkdir(parents=True, exist_ok=True)
    codec = ["-c:a", "flac"] if path.suffix == ".flac" else ["-c:a", "libmp3lame", "-b:a", f"{kbps or 320}k"]
    subprocess.run(["ffmpeg", "-loglevel", "error", "-y", "-f", "lavfi", "-i", "sine=d=1", *codec, str(path)],
                   check=True)
    if tags:
        assert ml.write(str(path), tags)
    return path


def album(folder, artist="Phoebe Bridgers", title="Punisher", year="2020", n=3, ext=".flac", **kw):
    for i in range(1, n + 1):
        clip(folder / f"{i:02d} track{ext}", artist=artist, albumartist=artist, album=title, date=year,
             title=f"Song {i}", tracknumber=f"{i}/{n}", **kw)
    return folder


@pytest.fixture
def root(lib):
    client, Session, media = lib
    music = media.parent / "music"
    music.mkdir()
    with Session() as db:
        db.merge(Setting(key="organise_music_dir", value=str(music)))
        db.commit()
    return client, Session, music


# ── tags ────────────────────────────────────────────────────────────────────


@pytest.mark.parametrize("ext", [".flac", ".mp3"])
def test_tags_round_trip(tmp_path, ext):
    p = clip(tmp_path / f"a{ext}", title="Garden Song", artist="Phoebe Bridgers", album="Punisher",
             date="2020-06-18", tracknumber="1/11", discnumber="1/1", musicbrainz_releasegroupid="rg-2")
    t = ml.read(str(p))
    assert (t["title"], t["artist"], t["album_artist"], t["album"], t["year"]) == \
        ("Garden Song", "Phoebe Bridgers", "Phoebe Bridgers", "Punisher", 2020)
    assert (t["track_no"], t["track_total"], t["disc_no"]) == (1, 11, 1)
    assert t["mb_release_group_id"] == "rg-2" and t["duration_sec"] > 0.5
    assert t["lossless"] == (ext == ".flac")


# ── scanning and checks ─────────────────────────────────────────────────────


def test_scan_finds_albums_and_flags_problems(root):
    client, Session, music = root
    album(music / "Phoebe Bridgers" / "Punisher (2020)")
    album(music / "Phoebe Bridgers" / "Copy of Punisher")                    # a duplicate
    gaps = music / "Phoebe Bridgers" / "Stranger in the Alps (2017)"
    clip(gaps / "01.flac", artist="Phoebe Bridgers", album="Stranger in the Alps", title="Smoke", tracknumber="1/3")
    clip(gaps / "03.flac", artist="Phoebe Bridgers", album="Stranger in the Alps", title="Motion", tracknumber="3/3")
    clip(music / "Unknown" / "rip" / "track.flac")                             # no tags at all
    album(music / "Lo" / "Fi (2001)", artist="Lo", title="Fi", ext=".mp3", n=2, )
    for f in (music / "Lo" / "Fi (2001)").iterdir():                          # re-encode at 128 kbps
        clip(f, kbps=128, artist="Lo", albumartist="Lo", album="Fi", title=f.stem, tracknumber="1")
    res = ml.scan()
    assert res["ok"] and res["albums"] == 5
    with Session() as db:
        by = {os.path.basename(a.folder): json.loads(a.issues or "[]") for a in db.query(MusicLibAlbum)}
        assert by["Punisher (2020)"] == ["duplicate"] and by["Copy of Punisher"] == ["duplicate"]
        assert by["Stranger in the Alps (2017)"] == ["missing_tracks"]
        assert "untagged" in by["rip"]
        assert "low_bitrate" in by["Fi (2001)"]
        p = db.query(MusicLibAlbum).filter(MusicLibAlbum.folder.endswith("Punisher (2020)")).one()
        assert (p.artist, p.title, p.year, p.format, p.tracks) == ("Phoebe Bridgers", "Punisher", 2020, "flac", 3)
    shutil.rmtree(music / "Phoebe Bridgers" / "Copy of Punisher")
    res = ml.scan()
    with Session() as db:
        assert res["removed"] == 1
        p = db.query(MusicLibAlbum).filter(MusicLibAlbum.folder.endswith("Punisher (2020)")).one()
        assert p.issues is None


def test_disc_folders_are_one_album(root):
    client, Session, music = root
    a = music / "Band" / "Double (1999)"
    clip(a / "CD1" / "01.flac", artist="Band", album="Double", title="One", tracknumber="1", discnumber="1")
    clip(a / "CD2" / "01.flac", artist="Band", album="Double", title="Two", tracknumber="1", discnumber="2")
    ml.scan()
    with Session() as db:
        assert db.query(MusicLibAlbum).count() == 1 and db.query(MusicLibAlbum).one().tracks == 2


# ── tags from MusicBrainz ───────────────────────────────────────────────────


@pytest.fixture
def mbz(monkeypatch):
    rel = {"mbid": "rel-1", "title": "Punisher", "date": "2020-06-18", "artist": "Phoebe Bridgers", "discs": 1,
           "tracks": [{"disc": 1, "no": i, "title": t, "artist": "Phoebe Bridgers"}
                      for i, t in enumerate(["DVD Menu", "Garden Song", "Kyoto"], 1)]}
    monkeypatch.setattr(musicbrainz, "find_release_group", lambda a, b: "rg-2")
    monkeypatch.setattr(musicbrainz, "releases", lambda rg: [dict(rel, tracks=rel["tracks"][:2]), rel])
    monkeypatch.setattr(musicbrainz, "cover_bytes", lambda rg: b"\xff\xd8jpeg")


def test_fix_tags_from_musicbrainz(tmp_path, mbz):
    files = [str(clip(tmp_path / "rip" / f"track{i}.flac")) for i in (1, 2, 3)]
    res = ml.fix_tags(files, "Phoebe Bridgers", "Punisher")
    assert res["ok"] and res["tagged"] == 3
    t = [ml.read(f) for f in files]
    assert [x["title"] for x in t] == ["DVD Menu", "Garden Song", "Kyoto"]
    assert t[1]["track_no"] == 2 and t[1]["track_total"] == 3 and t[1]["year"] == 2020
    assert t[0]["mb_release_group_id"] == "rg-2"
    assert (tmp_path / "rip" / "cover.jpg").read_bytes() == b"\xff\xd8jpeg"
    assert not ml.fix_tags(files[:1] * 5, "Phoebe Bridgers", "Punisher")["ok"]          # no 5-track release


# ── organising ──────────────────────────────────────────────────────────────


def test_organise_names_files_for_plex(root, tmp_path):
    client, Session, music = root
    src = album(tmp_path / "dl" / "Phoebe.Bridgers-Punisher-2020-FLAC")
    (src / "folder.jpg").write_bytes(b"img")
    files = sorted(str(p) for p in src.glob("*.flac"))
    with Session() as db:
        res = ml.organise(db, files, str(music), keep_source=True)
    dest = music / "Phoebe Bridgers" / "Punisher (2020)"
    assert res["ok"] and res["folder"] == str(dest) and res["moved"] == 3
    assert sorted(p.name for p in dest.iterdir()) == ["01 - Song 1.flac", "02 - Song 2.flac", "03 - Song 3.flac",
                                                    "cover.jpg"]
    assert all(os.path.exists(f) for f in files)                                # still seeding
    with Session() as db:
        again = ml.organise(db, files, str(music), keep_source=True)
    assert again["moved"] == 0 and again["skipped"] == 3                         # never overwritten


def test_organise_multi_disc_and_untagged(root, tmp_path):
    client, Session, music = root
    a = clip(tmp_path / "x" / "a.flac", artist="Band", album="Double", title="One", tracknumber="1", discnumber="1")
    b = clip(tmp_path / "x" / "b.flac", artist="Band", album="Double", title="Two", tracknumber="1", discnumber="2")
    with Session() as db:
        res = ml.organise(db, [str(a), str(b)], str(music), keep_source=False)
        assert sorted(p.name for p in (music / "Band" / "Double").iterdir()) == ["1-01 - One.flac", "2-01 - Two.flac"]
        assert not a.exists()
        bare = clip(tmp_path / "y" / "c.flac")
        assert not ml.organise(db, [str(bare)], str(music), keep_source=False)["ok"]


# ── a finished album download ───────────────────────────────────────────────


def test_finished_download_is_tagged_organised_and_indexed(root, tmp_path, mbz, monkeypatch):
    client, Session, music = root
    monkeypatch.setattr(ml, "IMPORT_SYNC", True)
    monkeypatch.setattr("app.services.pipeline.on_download_complete", lambda bind, dl_id: None)
    dl_dir = tmp_path / "downloads"
    rip = dl_dir / "Phoebe Bridgers - Punisher [FLAC]"
    for i in (1, 2, 3):
        clip(rip / f"{i}.flac")
    with Session() as db:
        w = WatchlistItem(kind="artist", mbid="art-1", title="Phoebe Bridgers", search_query="Phoebe Bridgers",
                          season=1, episode=1)
        db.add(w)
        db.flush()
        al = MusicAlbum(watchlist_id=w.id, mbid="rg-2", title="Punisher", release_date="2020-06-18", status="grabbed")
        db.add(al)
        db.flush()
        dl = Download(title=rip.name, download_path=str(dl_dir), status="downloading", watchlist_id=w.id,
                      music_album_id=al.id, qbit_removed=True)
        db.add(dl)
        db.commit()
        dl.status = "completed"
        db.commit()
    dest = music / "Phoebe Bridgers" / "Punisher (2020)"
    assert sorted(p.name for p in dest.glob("*.flac")) == ["01 - DVD Menu.flac", "02 - Garden Song.flac",
                                                           "03 - Kyoto.flac"]
    assert (dest / "cover.jpg").exists() and (rip / "1.flac").exists()
    with Session() as db:
        assert db.query(MusicAlbum).one().status == "have"
        a = db.query(MusicLibAlbum).one()
        assert a.folder == str(dest) and a.tracks == 3 and a.cover == "cover.jpg" and a.issues is None


# ── routes ──────────────────────────────────────────────────────────────────


def test_routes(root, mbz, monkeypatch):
    client, Session, music = root
    monkeypatch.setattr(ml, "IMPORT_SYNC", True)
    src = music / "incoming" / "rip"
    for i in (1, 2, 3):
        clip(src / f"{i}.flac", artist="Phoebe Bridgers", albumartist="Phoebe Bridgers", album="Punisher")
    (src / "cover.png").write_bytes(b"png")
    assert client.post("/api/music/library/scan").json()["ok"]
    st = client.get("/api/music/library/status").json()
    assert st["last"]["albums"] == 1 and st["music_dir"] == str(music)
    a = client.get("/api/music/library/albums").json()[0]
    assert a["issues"] == ["untagged"] and a["has_cover"] and a["tracks"] == 3
    detail = client.get(f"/api/music/library/albums/{a['id']}").json()
    assert len(detail["track_list"]) == 3
    assert client.get(f"/api/music/library/albums/{a['id']}/cover").content == b"png"
    tid = detail["track_list"][0]["id"]
    assert client.get(f"/api/music/library/tracks/{tid}/stream").status_code == 200

    r = client.post(f"/api/music/library/albums/{a['id']}/fix-tags").json()
    assert r["ok"] and r["tagged"] == 3
    a = client.get("/api/music/library/albums").json()[0]
    assert a["issues"] == [] and a["title"] == "Punisher"

    r = client.post(f"/api/music/library/albums/{a['id']}/organise").json()
    assert r["ok"] and r["folder"] == str(music / "Phoebe Bridgers" / "Punisher (2020)")
    a = client.get("/api/music/library/albums").json()[0]
    assert a["folder"] == r["folder"] and not src.exists()

    r = client.post(f"/api/music/library/albums/{a['id']}/rating", json={"stars": 4}).json()
    assert r["plex"] == "off" and r["rating"] == 4
    r = client.post(f"/api/music/library/albums/{a['id']}/collections", json={"add": ["Sad Girl Indie"]}).json()
    assert r["collections"] == ["Sad Girl Indie"]
    assert client.get("/api/music/library/collections").json()["names"] == ["Sad Girl Indie"]
    assert client.post("/api/music/library/albums/999/rating", json={"stars": 1}).status_code == 404
    assert client.get("/api/music/library/tracks/999/stream").status_code == 404


def test_organise_needs_a_music_folder(lib):
    client, Session, media = lib
    with Session() as db:
        a = MusicLibAlbum(folder="/nowhere", artist="X", title="Y")
        db.add(a)
        db.commit()
        aid = a.id
    r = client.post(f"/api/music/library/albums/{aid}/organise")
    assert r.status_code == 400 and "music library folder" in r.json()["detail"]


def test_tracks_table_cleared_with_album(root):
    client, Session, music = root
    album(music / "A" / "B (2000)", artist="A", title="B", n=2)
    ml.scan()
    shutil.rmtree(music / "A")
    ml.scan()
    with Session() as db:
        assert db.query(MusicLibAlbum).count() == 0 and db.query(MusicTrack).count() == 0


# ── covers ──────────────────────────────────────────────────────────────────

JPEG = b"\xff\xd8\xff\xe0embedded"


def embed(path, data=JPEG):
    if path.suffix == ".flac":
        from mutagen.flac import FLAC, Picture
        f = FLAC(str(path))
        pic = Picture()
        pic.type, pic.mime, pic.data = 3, "image/jpeg", data
        f.add_picture(pic)
        f.save()
    else:
        from mutagen.id3 import APIC, ID3
        tags = ID3(str(path))
        tags.add(APIC(encoding=3, mime="image/jpeg", type=3, desc="Cover", data=data))
        tags.save()


@pytest.mark.parametrize("ext", [".flac", ".mp3"])
def test_embedded_artwork_becomes_cover_jpg(tmp_path, ext, monkeypatch):
    monkeypatch.setattr(musicbrainz, "cover_bytes", lambda rg: pytest.fail("no network when files have art"))
    f = clip(tmp_path / "a" / f"01{ext}", title="x")
    embed(f)
    assert ml.embedded_cover(str(f)) == (JPEG, ".jpg")
    res = ml.ensure_cover(str(f.parent), None, "A", "B", "rg-1")
    assert res == {"cover": "cover.jpg", "source": "embedded"}
    assert (f.parent / "cover.jpg").read_bytes() == JPEG


def test_cover_from_musicbrainz_and_never_replaced(tmp_path, mbz):
    folder = tmp_path / "a"
    clip(folder / "01.flac")
    assert ml.ensure_cover(str(folder), None, "Phoebe Bridgers", "Punisher")["source"] == "musicbrainz"
    assert (folder / "cover.jpg").read_bytes() == b"\xff\xd8jpeg"
    (folder / "cover.jpg").write_bytes(b"mine")
    assert ml.ensure_cover(str(folder), None, "Phoebe Bridgers", "Punisher")["source"] == "existing"
    assert (folder / "cover.jpg").read_bytes() == b"mine"


def test_tagged_download_still_gets_a_cover(root, tmp_path, mbz, monkeypatch):
    client, Session, music = root
    monkeypatch.setattr(ml, "IMPORT_SYNC", True)
    monkeypatch.setattr("app.services.pipeline.on_download_complete", lambda bind, dl_id: None)
    monkeypatch.setattr(ml, "fix_tags", lambda *a, **k: pytest.fail("tags are fine; no tag fix"))
    rip = album(tmp_path / "downloads" / "Phoebe Bridgers - Punisher [FLAC]")
    with Session() as db:
        w = WatchlistItem(kind="artist", mbid="art-1", title="Phoebe Bridgers", search_query="Phoebe Bridgers",
                          season=1, episode=1)
        db.add(w)
        db.flush()
        al = MusicAlbum(watchlist_id=w.id, mbid="rg-2", title="Punisher", status="grabbed")
        db.add(al)
        db.flush()
        dl = Download(title=rip.name, download_path=str(rip.parent), status="downloading", watchlist_id=w.id,
                      music_album_id=al.id, qbit_removed=True)
        db.add(dl)
        db.commit()
        dl.status = "completed"
        db.commit()
    dest = music / "Phoebe Bridgers" / "Punisher (2020)"
    assert (dest / "cover.jpg").read_bytes() == b"\xff\xd8jpeg"
    with Session() as db:
        assert db.query(MusicLibAlbum).one().cover == "cover.jpg"


def test_scan_fills_missing_covers_and_waits_before_asking_again(root, monkeypatch):
    client, Session, music = root
    calls = []
    monkeypatch.setattr(musicbrainz, "find_release_group", lambda a, b: calls.append(b) or None)
    monkeypatch.setattr(musicbrainz, "cover_bytes", lambda rg: pytest.fail("no group, no download"))
    album(music / "A" / "Art (2001)", artist="A", title="Art", n=1)
    embed(next((music / "A" / "Art (2001)").glob("*.flac")))
    album(music / "B" / "Bare (2002)", artist="B", title="Bare", n=1)
    res = ml.scan()
    assert res["covers"]["embedded"] == 1 and calls == ["Bare"]
    with Session() as db:
        by = {a.title: a for a in db.query(MusicLibAlbum)}
        assert by["Art"].cover == "cover.jpg" and by["Bare"].cover is None and by["Bare"].cover_checked_at
    ml.scan()
    assert calls == ["Bare"]                                     # not asked again for 30 days


def test_get_cover_route(root, mbz):
    client, Session, music = root
    album(music / "Phoebe Bridgers" / "Punisher (2020)", n=1)
    ml.scan()
    with Session() as db:
        aid = db.query(MusicLibAlbum).one().id
    r = client.post(f"/api/music/library/albums/{aid}/cover").json()
    assert r["ok"] and r["source"] in ("musicbrainz", "existing")
    assert client.get("/api/music/library/albums").json()[0]["has_cover"]
    assert client.post(f"/api/music/library/albums/{aid}/cover").json()["source"] == "existing"


# ── editing tags and artwork by hand ────────────────────────────────────────


def _png():
    import io
    from PIL import Image
    b = io.BytesIO()
    Image.new("RGB", (8, 8), (200, 30, 90)).save(b, "PNG")
    return b.getvalue()


def _jpeg():
    import io
    from PIL import Image
    b = io.BytesIO()
    Image.new("RGB", (8, 8), (20, 30, 200)).save(b, "JPEG")
    return b.getvalue()


@pytest.mark.parametrize("name,expect", [
    ("03 - Song Name.flac", ("Song Name", 3, None)),
    ("d1t03 Jam.flac", ("Jam", 3, 1)),
    ("gd1977-05-08d2t01.flac", (None, 1, 2)),
    ("2-05 Encore.flac", ("Encore", 5, 2)),
    ("01_Opening_Theme.mp3", ("Opening Theme", 1, None)),
    ("Intro.flac", ("Intro", None, None)),
])
def test_names_to_tags(name, expect):
    g = ml.from_name(name)
    assert (g["title"], g["track_no"], g["disc_no"]) == expect


@pytest.fixture
def bootleg(root):
    client, Session, music = root
    folder = music / "incoming" / "gd77-05-08"
    for n in ("gd77-05-08d1t01 Minglewood.flac", "gd77-05-08d1t02 Loser.mp3", "gd77-05-08d2t01 Scarlet.flac"):
        clip(folder / n)
    ml.scan()
    with Session() as db:
        aid = db.query(MusicLibAlbum).one().id
    return client, Session, music, folder, aid


def test_tag_editor_round_trip_with_live_fields(bootleg):
    client, Session, music, folder, aid = bootleg
    tags = client.get(f"/api/music/library/albums/{aid}/tags").json()
    assert len(tags["tracks"]) == 3 and tags["album"]["album"] == ""
    guesses = {g["path"]: g for g in client.get(f"/api/music/library/albums/{aid}/from-names").json()}
    tracks = [{"path": t["path"], "title": guesses[t["path"]]["title"], "artist": "",
               "track_no": guesses[t["path"]]["track_no"], "disc_no": guesses[t["path"]]["disc_no"]}
              for t in tags["tracks"]]
    r = client.put(f"/api/music/library/albums/{aid}/tags", json={
        "album": {"album_artist": "Grateful Dead", "album": "", "genre": "Rock"},
        "tracks": tracks, "live": {"date": "1977-05-08", "venue": "Barton Hall", "city": "Ithaca NY"}}).json()
    assert r["ok"] and r["saved"] == 3
    infos = {os.path.basename(p): ml.read(str(p)) for p in folder.iterdir() if p.suffix in (".flac", ".mp3")}
    d1 = infos["gd77-05-08d1t01 Minglewood.flac"]
    assert d1["album"] == "1977-05-08 Barton Hall, Ithaca NY" and d1["album_artist"] == "Grateful Dead"
    assert d1["artist"] == "Grateful Dead" and d1["genre"] == "Rock" and d1["date"] == "1977-05-08"
    assert (d1["track_no"], d1["track_total"], d1["disc_no"]) == (1, 2, 1)
    assert d1["comment"] == "Live at Barton Hall, Ithaca NY on 1977-05-08"
    mp3 = infos["gd77-05-08d1t02 Loser.mp3"]
    assert mp3["title"] == "Loser" and mp3["comment"] == d1["comment"]           # MP3 comments work too
    assert infos["gd77-05-08d2t01 Scarlet.flac"]["disc_no"] == 2
    a = client.get("/api/music/library/albums").json()[0]
    assert a["title"] == "1977-05-08 Barton Hall, Ithaca NY" and "untagged" not in a["issues"]
    # an empty value removes the tag
    client.put(f"/api/music/library/albums/{aid}/tags", json={"album": {"genre": ""}, "tracks": [
        {"path": t["path"]} for t in tags["tracks"]]})
    assert ml.read(str(folder / "gd77-05-08d1t01 Minglewood.flac"))["genre"] is None


def test_tag_editor_rejects_other_files_and_bad_numbers(bootleg, tmp_path):
    client, Session, music, folder, aid = bootleg
    outside = clip(tmp_path / "x.flac")
    r = client.put(f"/api/music/library/albums/{aid}/tags", json={"album": {}, "tracks": [{"path": str(outside)}]})
    assert r.status_code == 400
    p = str(next(folder.glob("*.flac")))
    r = client.put(f"/api/music/library/albums/{aid}/tags", json={"album": {}, "tracks": [{"path": p, "track_no": "x"}]})
    assert r.status_code == 422


def test_upload_cover_replaces_and_embeds(bootleg):
    client, Session, music, folder, aid = bootleg
    (folder / "folder.jpg").write_bytes(_jpeg())
    ml.scan()
    r = client.post(f"/api/music/library/albums/{aid}/cover/upload", data={"embed": "true"},
                    files={"file": ("art.png", _png(), "image/png")}).json()
    assert r["ok"] and r["cover"] == "cover.png" and "3 tracks" in r["msg"]
    assert (folder / "cover.png").exists() and not (folder / "folder.jpg").exists()
    for f in folder.iterdir():
        if f.suffix in (".flac", ".mp3"):
            assert ml.embedded_cover(str(f)) == (_png(), ".png")
    a = client.get("/api/music/library/albums").json()[0]
    assert a["has_cover"] and client.get(f"/api/music/library/albums/{aid}/cover").content == _png()
    bad = client.post(f"/api/music/library/albums/{aid}/cover/upload", files={"file": ("x.png", b"nope", "image/png")})
    assert bad.status_code == 400 and "JPEG or PNG" in bad.json()["detail"]


def test_cover_from_musicbrainz_choice(bootleg, monkeypatch):
    client, Session, music, folder, aid = bootleg
    monkeypatch.setattr(musicbrainz, "search_release_groups", lambda a, b, q=None: [
        {"mbid": "rg-live", "title": "Cornell 5/8/77", "artist": "Grateful Dead", "date": "2017-05-05",
         "type": "Album · Live", "cover": "https://coverartarchive.org/release-group/rg-live/front-250"}])
    monkeypatch.setattr(musicbrainz, "cover_bytes", lambda rg: _jpeg() if rg == "rg-live" else None)
    c = client.get(f"/api/music/library/albums/{aid}/cover/candidates", params={"q": "cornell 77"}).json()
    assert c[0]["type"] == "Album · Live"
    r = client.post(f"/api/music/library/albums/{aid}/cover/musicbrainz", json={"mbid": "rg-live"}).json()
    assert r["cover"] == "cover.jpg" and (folder / "cover.jpg").read_bytes() == _jpeg()
    assert client.post(f"/api/music/library/albums/{aid}/cover/musicbrainz", json={"mbid": "rg-none"}).status_code == 404
    r = client.post(f"/api/music/library/albums/{aid}/cover/embed").json()
    assert r["embedded"] == 3


# ── many albums at once ─────────────────────────────────────────────────────


@pytest.fixture
def shelf(root, monkeypatch):
    from app.services import music_batch
    monkeypatch.setattr(music_batch, "SYNC", True)
    client, Session, music = root
    album(music / "A" / "One (2001)", artist="A", title="One", n=1)
    album(music / "B" / "Two (2002)", artist="B", title="Two", n=1)
    embed(next((music / "B" / "Two (2002)").glob("*.flac")), _jpeg())
    monkeypatch.setattr(musicbrainz, "find_release_group", lambda a, b: None)
    ml.scan()                                     # B gets its embedded art; MusicBrainz knows nothing of A
    with Session() as db:
        ids = {a.title: a.id for a in db.query(MusicLibAlbum)}
    return client, Session, music, ids


def test_bulk_find_artwork_ignores_the_30_day_wait(shelf, monkeypatch):
    client, Session, music, ids = shelf
    with Session() as db:
        assert db.get(MusicLibAlbum, ids["One"]).cover_checked_at is not None   # looked already
    monkeypatch.setattr(musicbrainz, "find_release_group", lambda a, b: "rg-one")
    monkeypatch.setattr(musicbrainz, "cover_bytes", lambda rg: _jpeg())
    r = client.post("/api/music/library/bulk", json={"action": "covers", "album_ids": [ids["One"], ids["Two"]]}).json()
    assert r["total"] == 2
    st = client.get("/api/music/library/bulk/status").json()
    assert not st["running"] and st["done"] == 2 and st["changed"] == 1 and st["msg"].startswith("1 of 2")
    assert (music / "A" / "One (2001)" / "cover.jpg").exists()
    albums = {a["title"]: a for a in client.get("/api/music/library/albums").json()}
    assert albums["One"]["has_cover"] and albums["Two"]["has_cover"]


def test_bulk_reports_albums_it_could_not_do(shelf):
    client, Session, music, ids = shelf
    client.post("/api/music/library/bulk", json={"action": "covers", "album_ids": [ids["One"]]})
    st = client.get("/api/music/library/bulk/status").json()
    assert st["changed"] == 0 and len(st["failed"]) == 1 and "One" in st["failed"][0]


def test_bulk_embed_rate_collect_and_errors(shelf):
    client, Session, music, ids = shelf
    client.post("/api/music/library/bulk", json={"action": "embed", "album_ids": [ids["Two"], ids["One"]]})
    st = client.get("/api/music/library/bulk/status").json()
    assert st["changed"] == 1 and "no cover" in st["failed"][0]
    r = client.post("/api/music/library/bulk", json={"action": "rate", "album_ids": list(ids.values()), "stars": 4}).json()
    assert r["done"] == 2
    r = client.post("/api/music/library/bulk", json={"action": "collect", "album_ids": list(ids.values()),
                                                     "add": ["Road trip"]}).json()
    assert r["done"] == 2
    albums = client.get("/api/music/library/albums").json()
    assert all(a["rating"] == 4 and a["collections"] == ["Road trip"] for a in albums)
    assert client.post("/api/music/library/bulk", json={"action": "nope", "album_ids": [1]}).status_code == 422
    assert client.post("/api/music/library/bulk", json={"action": "covers", "album_ids": []}).status_code == 422


def test_bulk_organise(shelf):
    client, Session, music, ids = shelf
    loose = music / "incoming" / "rip"
    album(loose, artist="C", title="Three", year="2003", n=2)
    ml.scan()
    with Session() as db:
        cid = db.query(MusicLibAlbum).filter(MusicLibAlbum.title == "Three").one().id
    client.post("/api/music/library/bulk", json={"action": "organise", "album_ids": [cid]})
    st = client.get("/api/music/library/bulk/status").json()
    assert st["changed"] == 1, st
    assert (music / "C" / "Three (2003)" / "01 - Song 1.flac").exists() and not loose.exists()


# ── bootleg discs, download folders, folder test ────────────────────────────


def test_bootleg_disc_folders_are_one_album(root):
    client, Session, music = root
    show = music / "2001-08-03 Molson Park, Barrie"
    clip(show / "rh2001-08-03d1" / "t01.flac")
    clip(show / "rh2001-08-03d1" / "t02.flac")
    clip(show / "rh2001-08-03d2" / "t01.flac")
    clip(music / "Solo" / "Live d1" / "a.flac")                 # one "d1" alone is just a folder
    ml.scan()
    with Session() as db:
        by = {os.path.basename(a.folder): a.tracks for a in db.query(MusicLibAlbum)}
    assert by == {"2001-08-03 Molson Park, Barrie": 3, "Live d1": 1}
    assert ml.disc_dirs(["gd77d1", "gd77d2", "CD1", "Extras"]) == {"gd77d1", "gd77d2", "CD1"}


def test_scan_skips_download_folders_inside_the_library(root):
    from app.models import DownloadPath
    client, Session, music = root
    album(music / "A" / "One (2001)", artist="A", title="One", n=1)
    clip(music / "incoming" / "A - One [FLAC]" / "01.flac")
    with Session() as db:
        db.add(DownloadPath(name="Music downloads", path=str(music / "incoming"),
                            local_path=str(music / "incoming"), downloads=True, scan=False))
        db.commit()
    ml.scan()
    with Session() as db:
        assert [os.path.basename(a.folder) for a in db.query(MusicLibAlbum)] == ["One (2001)"]


def test_folder_test_counts_audio_for_music_folders(root):
    from app.models import DownloadPath
    from app.services import folders
    client, Session, music = root
    album(music / "A" / "One (2001)", artist="A", title="One", n=2)
    with Session() as db:
        dp = DownloadPath(name="Music", path=str(music), local_path=str(music), downloads=False,
                          scan=True, target="music")
        db.add(dp)
        db.commit()
        read = next(c for c in folders.check(db, dp)["checks"] if c["key"] == "read")
    assert read["detail"].startswith("2 audio files")
