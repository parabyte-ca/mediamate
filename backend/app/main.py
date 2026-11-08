import logging
import os
import pathlib
import secrets
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles

from .database import SessionLocal
from .migrations import run_migrations
from .middleware.auth import AuthMiddleware
from .models import Setting
from .routers import (
    activity, auth, backup, browse, calendar, doctor, downloads, events, health, history, importer, insights, media, music,
    onboarding, paths, pipeline, profiles, push, search, settings, trakt, updates, watchlist,
)
from .services import download_poller, journeys
from .services import media as media_engine
from .middleware.scrub import ScrubMiddleware
from .services.secret_settings import install_log_redaction

install_log_redaction()  # mask tokens and API keys that appear in URLs in any log line

# Keep journeys in step with every Download write, wherever it happens.
journeys.install_download_hooks()
from .services.scheduler import start_scheduler, stop_scheduler

STATIC_DIR = pathlib.Path("/app/static")
DATA_DIR = pathlib.Path("/app/data")


@asynccontextmanager
async def lifespan(app: FastAPI):
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    run_migrations()

    # Seed session_secret on first run (single-threaded here — no race)
    db = SessionLocal()
    try:
        if not db.query(Setting).filter(Setting.key == "session_secret").first():
            db.add(Setting(key="session_secret", value=secrets.token_hex(32)))
            db.commit()
        try:
            from .services import watchlist_add
            watchlist_add.ensure_format_defaults(db)
        except Exception:
            db.rollback()
            logging.getLogger(__name__).exception("Creating the TV/movie quality defaults failed")
    finally:
        db.close()

    db = SessionLocal()
    try:
        from .services import updater
        took = updater.record_upgrade_finished(db)
        if took:
            logging.getLogger(__name__).info("Upgrade finished in %d s", took)
    except Exception:
        logging.getLogger(__name__).exception("Recording the upgrade time failed")
    finally:
        db.close()

    db = SessionLocal()
    try:
        from .services import folder_roles, rename
        rename.carry_over(db)
        folder_roles.migrate_once(db)
    except Exception:
        logging.getLogger(__name__).exception("Carrying settings over to MediaMate failed")
    finally:
        db.close()

    db = SessionLocal()
    try:
        journeys.backfill(db)
    except Exception:
        logging.getLogger(__name__).exception("Journey backfill failed")
    finally:
        db.close()

    # Once after upgrading: look for adult content that was moved into the Plex folders
    # before folders could be protected, so the Library can point it out straight away.
    def _adult_check_once():
        db = SessionLocal()
        try:
            if not db.query(Setting).filter(Setting.key == "adult_check_v1").first():
                from .services import doctor
                doctor.run(db, auto_fix=False)
                db.add(Setting(key="adult_check_v1", value="1"))
                db.commit()
        except Exception:
            logging.getLogger(__name__).exception("Adult-content check after upgrade failed")
        finally:
            db.close()
    import threading as _threading
    _threading.Thread(target=_adult_check_once, name="adult-check", daemon=True).start()

    start_scheduler()
    media_engine.startup()
    download_poller.start()
    yield
    download_poller.stop()
    media_engine.shutdown()
    stop_scheduler()


app = FastAPI(title="MediaMate", lifespan=lifespan)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)
# AuthMiddleware is added after CORSMiddleware so CORS pre-flights are exempt
app.add_middleware(AuthMiddleware)
# Outermost: no JSON response leaves with a secret in it (security audit 2)
app.add_middleware(ScrubMiddleware)

app.include_router(health.router)  # no auth, must be registered first
app.include_router(auth.router, prefix="/api")
app.include_router(backup.router, prefix="/api")
app.include_router(history.router, prefix="/api")
app.include_router(search.router, prefix="/api")
app.include_router(watchlist.router, prefix="/api")
app.include_router(downloads.router, prefix="/api")
app.include_router(settings.router, prefix="/api")
app.include_router(updates.router, prefix="/api")
app.include_router(paths.router, prefix="/api")
app.include_router(profiles.router, prefix="/api")
app.include_router(calendar.router, prefix="/api")
app.include_router(browse.router, prefix="/api")
app.include_router(trakt.router, prefix="/api")
app.include_router(onboarding.router, prefix="/api")
app.include_router(media.router, prefix="/api")
app.include_router(activity.router, prefix="/api")
app.include_router(events.router, prefix="/api")
app.include_router(pipeline.router, prefix="/api")
app.include_router(importer.router, prefix="/api")
app.include_router(push.router, prefix="/api")
app.include_router(insights.router, prefix="/api")
app.include_router(doctor.router, prefix="/api")
app.include_router(music.router, prefix="/api")

# Serve compiled React frontend (production)
if STATIC_DIR.exists():
    app.mount("/assets", StaticFiles(directory=STATIC_DIR / "assets"), name="assets")

    # SW and manifest must not be cached by the browser so updates apply promptly
    _NO_CACHE_HEADERS = {"Cache-Control": "no-cache, no-store, must-revalidate"}
    _NO_CACHE_FILES = {"sw.js", "manifest.webmanifest"}

    @app.get("/{full_path:path}", include_in_schema=False)
    async def serve_spa(full_path: str):
        candidate = STATIC_DIR / full_path
        if candidate.exists() and candidate.is_file():
            headers = _NO_CACHE_HEADERS if full_path in _NO_CACHE_FILES else None
            return FileResponse(candidate, headers=headers)
        return FileResponse(STATIC_DIR / "index.html")
