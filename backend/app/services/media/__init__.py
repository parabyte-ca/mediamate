"""Media engine — FileSplitter's scan / encode / split / trim pipeline, ported.

startup() is called from the FastAPI lifespan; nothing here runs at import time.
"""
import logging

logger = logging.getLogger(__name__)

_started = False


def startup() -> None:
    global _started
    if _started:
        return
    from . import config, encoder, store, worker

    config.load_from_db()
    _sync_folders()
    store.recover_interrupted()
    encoder.start_probe()
    worker.start()
    from . import thumbs
    thumbs.start()
    _started = True
    logger.info("Media engine started (%d worker(s), backend=%s)",
                config.MAX_WORKERS, config.ENCODER_BACKEND)


def _sync_folders() -> None:
    """Media folders come from the Folders list (merged from the old setting on upgrade)."""
    from sqlalchemy.orm import Session
    from .. import folders
    from . import store

    try:
        with Session(bind=store._engine()) as db:
            roots = folders.startup(db)
        logger.info("Media folders: %s", ", ".join(roots) or "none")
    except Exception:
        logger.exception("Could not read the Folders list; using the media folders setting")


def shutdown() -> None:
    """Stop running ffmpeg jobs so they clean up their temp files before exit."""
    from . import worker

    worker.stop()
    for event in list(worker._cancel_events.values()):
        event.set()
