"""Downloads go to the right library: Automatic routing by kind and audience, the
one-time folder review for existing Watchlist items, and the doctor's
"In the wrong library" check for files already misplaced."""
from unittest.mock import patch

import pytest

from app.models import DownloadPath, WatchlistItem
from app.services import doctor, folders, movie_genres, ratings

from tests.test_doctor import clip, lib  # noqa: F401
from tests.test_ratings import db  # noqa: F401


@pytest.fixture
def libs(db):  # noqa: F811
    """Download folders for each library role, plus a default (Mature TV Shows, as on the
    user's server) — the default is where everything used to go."""
    s, media, index = db
    rows = {}
    for name, role, key in (("TV Shows", "tv", "organise_tv_dir"),
                            ("Mature TV Shows", "mature_tv", "organise_tv_mature_dir"),
                            ("Movies", "movies", "organise_movie_dir"),
                            ("Mature Movies", "mature_movies", "organise_movie_mature_dir")):
        dp = DownloadPath(name=name, path=str(media / key), downloads=True, scan=False, role=role,
                          is_default=(role == "mature_tv"))
        s.add(dp)
        rows[role] = dp
    s.commit()
    return s, media, index, rows


def _item(s, **kw):
    it = WatchlistItem(title=kw.pop("title", "Dune"), search_query=kw.pop("search_query", "dune"), **kw)
    s.add(it)
    s.commit()
    return it


def test_a_folder_you_chose_wins(libs):
    s, _, _, rows = libs
    it = _item(s, kind="movie", audience="general", download_path_id=rows["tv"].id)
    dp, why = folders.route_for(s, it)
    assert dp.id == rows["tv"].id and why == "chosen by you"


@pytest.mark.parametrize("audience,role", [("general", "movies"), ("mature", "mature_movies")])
def test_automatic_movies_go_to_a_movies_library(libs, audience, role):
    s, _, _, rows = libs
    it = _item(s, kind="movie", audience=audience)
    dp, why = folders.route_for(s, it)
    assert dp.id == rows[role].id and why.startswith("movie")


def test_a_movie_without_a_choice_follows_its_rating(libs):
    s, _, _, rows = libs
    it = _item(s, title="Paddington", kind="movie", year=2014)

    async def g(*a, **k):
        return ["Family"]
    with patch.object(ratings, "fetch", side_effect=lambda *a, **k: "PG"), \
            patch.object(movie_genres, "_fetch", side_effect=g):
        dp, _ = folders.route_for(s, it)
    assert dp.id == rows["movies"].id


def test_a_show_follows_the_library_it_already_lives_in(libs):
    s, media, _, rows = libs
    (media / "organise_tv_mature_dir" / "The Haunting Hour").mkdir(parents=True)
    it = _item(s, title="The Haunting Hour", search_query="the haunting hour")
    with patch.object(ratings, "fetch", side_effect=AssertionError("the show's folder decides")):
        dp, why = folders.route_for(s, it)
    assert dp.id == rows["mature_tv"].id and "already in the Mature library" in why


def test_falls_back_to_the_default_when_the_library_takes_no_downloads(libs):
    s, _, _, rows = libs
    rows["movies"].downloads = False
    s.commit()
    it = _item(s, kind="movie", audience="general")
    dp, why = folders.route_for(s, it)
    assert dp.id == rows["mature_tv"].id and "default folder" in why


def test_folder_review_lists_mismatches_and_applies(libs, lib, monkeypatch):  # noqa: F811
    from fastapi.testclient import TestClient
    from app.main import app
    s, _, _, rows = libs
    monkeypatch.setattr("app.database.SessionLocal", lib[0])
    film = _item(s, title="Dune", kind="movie", audience="general", download_path_id=rows["mature_tv"].id)
    show = _item(s, title="Bluey", search_query="bluey", audience="general", download_path_id=rows["tv"].id)
    client = TestClient(app)
    r = client.get("/api/watchlist/folder-review").json()
    by = {i["id"]: i for i in r["items"]}
    assert by[film.id]["differs"] and by[film.id]["suggested"] == "Movies"
    assert not by[show.id]["differs"] and r["to_review"] == 1
    other = _item(s, title="Heat", kind="movie", audience="mature", download_path_id=rows["tv"].id)
    assert client.post("/api/watchlist/folder-review/apply",
                       json={"automatic": [film.id], "keep": [other.id]}).status_code == 200
    s.expire_all()
    assert s.get(WatchlistItem, film.id).download_path_id is None
    assert s.get(WatchlistItem, other.id).save_to_confirmed
    r = client.get("/api/watchlist/folder-review").json()
    assert r["to_review"] == 0
    listed = client.get("/api/watchlist").json()
    row = next(i for i in listed if i["id"] == film.id)
    assert row["save_to"]["auto"] and row["save_to"]["folder"] == "Movies"


def test_doctor_moves_a_movie_out_of_a_tv_library(db, monkeypatch):  # noqa: F811
    s, media, index = db
    monkeypatch.setattr(doctor, "refresh_media_servers", lambda _db: None)
    index(clip(media / "organise_tv_mature_dir" / "_Unsorted" / "Dune.2021.1080p.mkv"))
    index(clip(media / "organise_movie_dir" / "Bluey.S01E01.mkv"))
    index(clip(media / "organise_tv_mature_dir" / "_Unsorted" / "random clip.mkv"))   # can't tell: left
    with patch.object(ratings, "fetch", side_effect=AssertionError("placement decides")), \
            patch.object(movie_genres, "_fetch", side_effect=AssertionError("no genre lookups")):
        issues = [i for i in doctor.check(s)["issues"] if i["kind"] == "wrong_library"]
        by = {i["path"].rsplit("/", 1)[-1]: i for i in issues}
        assert set(by) == {"Dune.2021.1080p.mkv", "Bluey.S01E01.mkv"}
        dune = by["Dune.2021.1080p.mkv"]
        assert dune["audience"] == "mature" and "Movie in a TV library" in dune["detail"]
        assert dune["target"].startswith(str(media / "organise_movie_mature_dir"))
        assert by["Bluey.S01E01.mkv"]["target"].startswith(str(media / "organise_tv_dir"))
        doctor.fix(s, dune)
    assert (media / "organise_movie_mature_dir" / "Dune (2021)" / "Dune (2021).mkv").exists()


def test_folders_say_when_a_library_takes_no_downloads(libs):
    from app.services import folder_roles
    s, _, _, rows = libs
    rows["movies"].downloads = False
    s.commit()
    allf = s.query(DownloadPath).all()
    assert any("Tick Downloads" in n for n in folder_roles.notes(s, rows["movies"], allf))
    assert not any("Tick Downloads" in n for n in folder_roles.notes(s, rows["tv"], allf))
