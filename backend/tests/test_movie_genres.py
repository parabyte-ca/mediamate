"""Genre folders for movies and the nightly organise of loose files (replacing the
organize_tv.py / organize_movies.py scripts)."""
import os
import time

import pytest

from app.services import auto_organise, doctor, movie_genres, organise
from app.services.media import store

from tests.test_organise import _settings, lib  # noqa: F401
from tests.test_pipeline import env  # noqa: F401

FOLDERS = {"Action & Adventure": frozenset({"Action", "Adventure"}), "Comedy": frozenset({"Comedy"}),
           "Sci-Fi": frozenset({"Science Fiction"}), "Drama": frozenset({"Drama"}),
           "Horror & Thriller": frozenset({"Horror", "Thriller"})}


@pytest.mark.parametrize("name,genres", [
    ("Action & Adventure", {"Action", "Adventure"}), ("Sci-Fi", {"Science Fiction"}),
    ("Science Fiction", {"Science Fiction"}), ("Kids and Family", {"Family"}),
    ("Documentaries", {"Documentary"}), ("Rom-Com", set()), ("Romance, Drama", {"Romance", "Drama"}),
])
def test_folder_names(name, genres):
    assert movie_genres.folder_genres(name) == genres


@pytest.mark.parametrize("genres,want", [
    (["Action", "Adventure", "Science Fiction"], ("Action & Adventure", "exact")),
    (["Comedy", "Romance"], ("Comedy", "single")),
    (["Science Fiction", "Thriller"], ("Sci-Fi", "single")),
    (["Thriller", "Crime"], ("Horror & Thriller", "borderline")),
    (["Western"], ("_Unsorted", "none")),
    ([], ("_Unsorted", "none")),
])
def test_pick(genres, want):
    assert movie_genres.pick(genres, FOLDERS) == want


def test_genre_folders_skip_title_year_and_the_skip_list(tmp_path):
    for n in ("Comedy", "Holiday", "_Extras", "_Unsorted", "Dune (2021)", ".trash", "Superhero",
              "Some.Movie.2019.1080p", "Loose Movie"):
        (tmp_path / n).mkdir()
    (tmp_path / "Superhero" / "Iron Man (2008)").mkdir()
    (tmp_path / "Loose Movie" / "Loose Movie.mkv").write_bytes(b"x")
    s = {"organise_movie_genre_skip": "Holiday, _Extras"}
    assert list(movie_genres.genre_folders(str(tmp_path), s)) == ["Comedy", "Superhero"]


# ── organising with genres ──────────────────────────────────────────────────


@pytest.fixture
def genres(lib, monkeypatch):
    client, Session, media = lib
    movies = media / "Movies"
    for n in ("Action & Adventure", "Comedy", "Holiday"):
        (movies / n).mkdir()
    _settings(Session, organise_movie_genres="true", organise_movie_genre_skip="Holiday, _Extras")
    table = {"Dune Part Two": ["Science Fiction", "Adventure", "Action"], "Airplane": ["Comedy"],
             "Obscure Thing": []}
    monkeypatch.setattr(movie_genres, "genres_for", lambda db, title, year, lookup=True: table.get(title))
    return client, Session, media, movies


def _add(path):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(b"x")
    old = time.time() - 7200
    os.utime(path, (old, old))
    return store.upsert_file(str(path), path.name, 1, 60.0, "hevc", False)


def test_plan_puts_a_movie_in_its_genre_folder(genres):
    client, Session, media, movies = genres
    f = movies / "Dune.Part.Two.2024.2160p.mkv"
    _add(f)
    with Session() as db:
        p = organise.plan(db, str(f))
    assert p["target"] == str(movies / "Action & Adventure" / "Dune Part Two (2024)" / "Dune Part Two (2024).mkv")
    assert p["genre_confidence"] == "exact"
    r = client.get(f"/api/media/files/{store.get_file_by_path(str(f))['id']}/organise").json()
    assert r["genre"] == "Action & Adventure" and r["genre_confidence"] == "exact"


def test_a_movie_already_in_a_genre_folder_stays_there(genres):
    client, Session, media, movies = genres
    f = movies / "Comedy" / "Dune Part Two (2024)" / "Dune Part Two (2024).mkv"   # placed by hand
    _add(f)
    with Session() as db:
        p = organise.plan(db, str(f))
        assert p["target"] == str(f) and p["genre_confidence"] == "kept"
        held = movies / "Holiday" / "Airplane.1980.mkv"
        _add(held)
        with pytest.raises(organise.OrganiseError, match="leaves alone"):
            organise.plan(db, str(held))
        report = doctor.check(db)
    assert not [i for i in report["issues"] if i["kind"] == "name" and "Movies" in i["path"]]


def test_doctor_flags_a_movie_at_the_root_and_fix_moves_it_into_its_genre(genres):
    client, Session, media, movies = genres
    f = movies / "Airplane (1980)" / "Airplane (1980).mkv"
    _add(f)
    with Session() as db:
        issue = next(i for i in doctor.check(db)["issues"] if i["kind"] == "name")
        assert "Comedy" in issue["detail"]
        doctor.fix(db, issue)
    assert (movies / "Comedy" / "Airplane (1980)" / "Airplane (1980).mkv").exists() and not f.exists()


def test_genre_folders_off_keeps_the_old_layout(lib):
    client, Session, media = lib
    f = media / "Movies" / "Airplane.1980.mkv"
    _add(f)
    with Session() as db:
        p = organise.plan(db, str(f))
    assert p["target"] == str(media / "Movies" / "Airplane (1980)" / "Airplane (1980).mkv") and p["genre"] is None


# ── nightly organise ────────────────────────────────────────────────────────


def test_nightly_organises_loose_files_only(genres, monkeypatch):
    client, Session, media, movies = genres
    tv = media / "TV"
    tv.mkdir()
    _settings(Session, organise_tv_dir=str(tv))
    loose_ep = tv / "The.Bear.S03E01.1080p.mkv"
    placed_ep = tv / "The Bear" / "Season 03" / "The Bear - S03E02.mkv"
    weird = tv / "Holiday Special Something.mkv"                  # not an episode
    movie = movies / "Airplane.1980.mkv"
    film_in_tv = tv / "Some.Film.2019.mkv"
    no_genre = movies / "Obscure.Thing.2010.mkv"
    held = movies / "Holiday" / "Elf.2003.mkv"
    fresh = movies / "New.Movie.2025.mkv"
    for p in (loose_ep, placed_ep, weird, movie, film_in_tv, no_genre, held):
        _add(p)
    fresh.write_bytes(b"x")
    store.upsert_file(str(fresh), fresh.name, 1, 60.0, "hevc", False)        # just copied in: left alone
    notes = []
    monkeypatch.setattr("app.services.apprise_notify.notify", lambda *a, **k: notes.append(k.get("body") or a[2]))
    with Session() as db:
        dry = auto_organise.run(db, dry_run=True)
        assert loose_ep.exists() and dry["moved_count"] == 2 and dry["unsorted_count"] == 3
        r = auto_organise.run(db)
    assert (tv / "The Bear" / "Season 03" / "The Bear - S03E01.mkv").exists()
    assert placed_ep.exists()                                                  # already in place: untouched
    assert (movies / "Comedy" / "Airplane (1980)" / "Airplane (1980).mkv").exists()
    assert (tv / "_Unsorted" / "Holiday Special Something.mkv").exists()
    assert (tv / "_Unsorted" / "Some.Film.2019.mkv").exists()
    assert (movies / "_Unsorted" / "Obscure Thing (2010)" / "Obscure Thing (2010).mkv").exists()
    assert held.exists() and fresh.exists()
    assert r["moved_count"] == 2 and r["unsorted_count"] == 3
    assert any("changed in the last hour" in s for s in r["skipped"])
    assert notes and "_Unsorted" in notes[0]
    with Session() as db:
        assert auto_organise.last_report(db)["moved_count"] == 2


def test_nightly_leaves_files_still_in_qbittorrent(genres):
    from app.models import Download
    client, Session, media, movies = genres
    f = movies / "Airplane.1980.1080p.BluRay" / "Airplane.1980.1080p.BluRay.mkv"
    _add(f)
    with Session() as db:
        db.add(Download(title="Airplane.1980.1080p.BluRay", status="downloading"))
        db.commit()
        r = auto_organise.run(db)
    assert f.exists() and any("still in qBittorrent" in s for s in r["skipped"]), {k: v for k, v in r.items() if v}


def test_routes(genres):
    client, Session, media, movies = genres
    r = client.get("/api/pipeline/organise").json()
    assert r["organise_movie_genres"] == "true" and r["genre_folders"][str(movies)] == ["Action & Adventure", "Comedy"]
    _add(movies / "Airplane.1980.mkv")
    out = client.post("/api/pipeline/organise/auto/run?dry_run=true").json()
    assert out["dry_run"] and out["moved"][0]["to"].startswith("Comedy")
    assert client.get("/api/pipeline/organise").json()["auto_report"]["dry_run"] is True
