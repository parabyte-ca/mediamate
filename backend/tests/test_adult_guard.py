"""Adult content never reaches the Plex folders: protected folders, the adult-name
check inside Rename & organise, the pipeline skip, and finding / moving back files
that were moved there before."""
import os

import pytest
from sqlalchemy import create_engine, text

from app.models import ContentRating, DownloadPath, OrganiseMove, PipelinePlan, Setting
from app.services import adult, doctor, organise
from app.services.media import config, store

from tests.test_pipeline import HAS_FFMPEG, _clip, _complete, _download, _rule, env  # noqa: F401
from tests.test_organise import lib  # noqa: F401


# ── the adult-name check ─────────────────────────────────────────────────────


@pytest.mark.parametrize("path", [
    "/m/dl/OnlyFans.2024.720p.mp4", "/m/dl/Only Fans 2024.mp4", "/m/dl/[WIFEY] HotWife X.mp4",
    "/m/dl/Vixen - Jojo Austin 2024 720p.mp4", "/m/dl/TushyRAW - Maric.mp4", "/m/dl/frolicme.26.04.08.mp4",
    "/m/dl/[VIXEN] Jojo.mp4", "/m/bellesa/C67 (2024).mp4", "/m/Adult/XXX/clip.mp4",
])
def test_adult_names(path):
    assert adult.looks_adult(path)


@pytest.mark.parametrize("path", [
    "/m/Movies/Wicked (2024)/Wicked (2024).mkv", "/m/Movies/Saving Private Ryan (1998).mkv",
    "/m/dl/Vixenville.2020.mkv", "/m/dl/The.Bear.S03E01.mkv", "/m/dl/Babes.2024.1080p.mkv",
    "/m/Movies/The Hustler (1961)/The Hustler (1961).mkv",
])
def test_ordinary_titles_are_not_adult(path):
    assert adult.looks_adult(path) is None


def test_extra_keywords():
    assert adult.looks_adult("/m/dl/Somesite.2024.mp4", frozenset({"somesite"}))
    assert adult.guess_protected("Bellesa") and adult.guess_protected("/mnt/video/bellesa")
    assert not adult.guess_protected("TV Shows")


# ── organise refuses ─────────────────────────────────────────────────────────


@pytest.fixture
def setup(lib):
    client, Session, media = lib
    bell = media / "bellesa"
    mature = media / "Mature Movies"
    bell.mkdir()
    mature.mkdir()
    with Session() as db:
        db.add(DownloadPath(name="bellesa", path=str(bell), local_path=str(bell), downloads=False,
                            scan=True, protected=True))
        db.merge(Setting(key="organise_movie_mature_dir", value=str(mature)))
        db.commit()
    return client, Session, media, bell, mature


def _file(path, codec="hevc"):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(b"x")
    return store.upsert_file(str(path), path.name, 1, 60.0, codec, False)


@pytest.mark.parametrize("name", ["C67.2024.1080p.mp4", "Veronica.Rodriguez.03.07.2025.1080p.mp4",
                                  "OnlyFans.2024.720p.mp4"])
def test_files_in_a_protected_folder_are_never_moved(setup, name):
    client, Session, media, bell, mature = setup
    f = bell / name
    fid = _file(f)
    r = client.get(f"/api/media/files/{fid}/organise").json()
    assert not r["ok"] and "protected folder" in r["msg"]
    r = client.post(f"/api/media/files/{fid}/organise").json()
    assert not r.get("ok") and f.exists()
    r = client.post("/api/media/files/bulk", json={"action": "organise", "file_ids": [fid]}).json()
    assert r["done"] == 0 and f.exists()
    # and directly, whatever the caller
    with pytest.raises(organise.OrganiseError):
        organise.organise_file(organise.get_settings(Session()), fid)
    assert f.exists() and not any(mature.iterdir())


def test_adult_named_files_elsewhere_are_never_moved(setup):
    client, Session, media, bell, mature = setup
    f = media / "dl" / "Vixen.Jojo.Austin.2024.720p.mp4"
    fid = _file(f)
    r = client.post(f"/api/media/files/{fid}/organise").json()
    assert not r.get("ok") and f.exists()


def test_normal_movies_still_organise(setup):
    client, Session, media, bell, mature = setup
    f = media / "dl" / "Dune.2021.1080p.mkv"
    fid = _file(f)
    r = client.post(f"/api/media/files/{fid}/organise", json={"audience": "general"}).json()
    assert r["ok"] and not f.exists()
    with Session() as db:
        move = db.query(OrganiseMove).one()
        assert move.from_path == str(f) and move.to_path == r["path"]


def test_doctor_never_offers_to_organise_protected_files(setup):
    client, Session, media, bell, mature = setup
    _file(bell / "C67.2024.1080p.mp4")
    with Session() as db:
        report = doctor.check(db)
    assert not [i for i in report["issues"] if i["kind"] in ("name", "audience")]


# ── finding and moving back ──────────────────────────────────────────────────


def test_doctor_finds_and_moves_back(setup):
    client, Session, media, bell, mature = setup
    logged = mature / "C67 (2024)" / "C67 (2024).mp4"          # moved by an earlier organise
    adultname = mature / "Vixen Jojo (2024)" / "Vixen Jojo (2024).mp4"
    unknown = mature / "Veronica Rodriguez 03 07 (2025)" / "Veronica Rodriguez 03 07 (2025).mp4"
    real = mature / "Alien (1979)" / "Alien (1979).mkv"
    ids = {p: _file(p) for p in (logged, adultname, unknown, real)}
    (logged.parent / "C67 (2024).en.srt").write_text("1")
    with Session() as db:
        db.add(OrganiseMove(file_id=ids[logged], from_path=str(bell / "C67.2024.1080p.mp4"), to_path=str(logged)))
        db.add(ContentRating(kind="movie", title_key="veronicarodriguez0307", year=2025, cert=""))
        db.add(ContentRating(kind="movie", title_key="alien", year=1979, cert="R"))
        db.commit()
        report = doctor.check(db)
    found = {i["path"]: i for i in report["issues"] if i["kind"] == "adult_in_plex"}
    assert set(found) == {str(logged), str(adultname), str(unknown)}
    assert report["issues"][0]["kind"] == "adult_in_plex"                       # listed first
    assert found[str(logged)]["target"] == str(bell / "C67.2024.1080p.mp4")      # original name back
    assert found[str(unknown)]["target"] == str(bell / unknown.name)
    assert not found[str(logged)]["safe"]

    with Session() as db:
        for issue in found.values():
            assert doctor.fix(db, issue).startswith("Moved back")
    assert (bell / "C67.2024.1080p.mp4").exists() and (bell / "C67.2024.1080p.en.srt").exists()
    assert not logged.parent.exists()                                          # emptied folder removed
    assert (bell / adultname.name).exists() and (bell / unknown.name).exists() and real.exists()
    assert store.get_file(ids[logged])["path"] == str(bell / "C67.2024.1080p.mp4")


def test_no_protected_folder_means_no_destination(lib):
    client, Session, media = lib
    mature = media / "Mature Movies"
    with Session() as db:
        db.merge(Setting(key="organise_movie_mature_dir", value=str(mature)))
        db.commit()
    _file(mature / "OnlyFans (2024)" / "OnlyFans (2024).mp4")
    with Session() as db:
        issue = next(i for i in doctor.check(db)["issues"] if i["kind"] == "adult_in_plex")
        assert issue["target"] is None
        with pytest.raises(ValueError, match="Protected"):
            doctor.fix(db, issue)


def test_bulk_move_to_folder(setup):
    client, Session, media, bell, mature = setup
    a = mature / "C67 (2024)" / "C67 (2024).mp4"
    b = mature / "Clash (2020)" / "C67 (2024).mp4"                     # same name: never overwritten
    ids = [_file(a), _file(b)]
    with Session() as db:
        folder = db.query(DownloadPath).filter_by(name="bellesa").one().id
    r = client.post("/api/media/files/bulk", json={"action": "move", "file_ids": ids, "folder_id": folder}).json()
    assert r["done"] == 1 and len(r["errors"]) == 1 and "already exists" in r["errors"][0]
    assert (bell / "C67 (2024).mp4").exists() and b.exists()
    assert client.post("/api/media/files/bulk", json={"action": "move", "file_ids": ids,
                                                      "folder_id": 999}).status_code == 400


# ── pipeline ─────────────────────────────────────────────────────────────────


@pytest.mark.skipif(not HAS_FFMPEG, reason="ffmpeg not installed")
def test_pipeline_skips_organise_for_adult_downloads(env):
    engine, Session, tmp = env
    movies = tmp / "movies"
    movies.mkdir()
    with Session() as db:
        db.merge(Setting(key="organise_movie_dir", value=str(movies)))
        db.commit()
    _rule(Session, name="File it", match={}, actions=[{"type": "organise"}])
    dl_id, dl_dir = _download(Session, tmp, name="Vixen.Jojo.Austin.2024.mp4")
    _clip(dl_dir / "Vixen.Jojo.Austin.2024.mp4")
    _complete(Session, dl_id, content="/remote/dl/Vixen.Jojo.Austin.2024.mp4")
    assert (dl_dir / "Vixen.Jojo.Austin.2024.mp4").exists() and not any(movies.iterdir())
    with Session() as db:
        plan = db.query(PipelinePlan).one()
        assert (plan.status, plan.organise_status) == ("done", "skipped")
        assert "adult" in plan.detail


# ── migration ────────────────────────────────────────────────────────────────


def test_migration_protects_bellesa(tmp_path):
    from app.migrations import run_migrations
    engine = create_engine(f"sqlite:///{tmp_path / 'old.db'}")
    with engine.connect() as c:
        c.execute(text("CREATE TABLE download_paths (id INTEGER PRIMARY KEY, name TEXT NOT NULL, path TEXT NOT NULL, "
                       "is_default INTEGER, local_path TEXT)"))
        c.execute(text("INSERT INTO download_paths (id, name, path) VALUES (1, 'bellesa', '/x'), "
                       "(2, 'Mature Movies', '/y'), (3, 'Grownups', '/mnt/video/bellesa')"))
        c.commit()
    run_migrations(engine)
    with engine.connect() as c:
        rows = c.execute(text("SELECT name, protected FROM download_paths ORDER BY id")).all()
    assert rows == [("bellesa", 1), ("Mature Movies", 0), ("Grownups", 1)]


def test_doctor_asks_tmdb_when_it_has_not_yet(setup):
    from unittest.mock import AsyncMock, patch
    client, Session, media, bell, mature = setup
    odd = mature / "C67 (2024)" / "C67 (2024).mp4"
    real = mature / "Alien (1979)" / "Alien (1979).mkv"
    _file(odd)
    _file(real)
    with Session() as db:
        db.merge(Setting(key="tmdb_api_key", value="a" * 32))
        db.commit()
        with patch("app.services.ratings.fetch", new=AsyncMock(side_effect=lambda k, kind, t, y=None:
                                                                 "R" if t == "Alien" else None)):
            found = [i["path"] for i in doctor.check(db)["issues"] if i["kind"] == "adult_in_plex"]
    assert found == [str(odd)]
