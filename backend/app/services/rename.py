"""ScreenMate is now MediaMate: carry an existing install's settings over once.

* The qBittorrent category stays "screenmate" for installs that never set one, so
  torrents keep the category qBittorrent already knows. New installs use "mediamate".
* A release-check URL still pointing at the old repository name is updated. GitHub
  redirects the old name too, so this is only tidying.
"""
from ..models import Download, Setting, WatchlistItem

DONE_KEY = "rename_mediamate_v1"
OLD_URL = "https://github.com/parabyte-ca/screenmate"
NEW_URL = "https://github.com/parabyte-ca/mediamate"


def _get(db, key):
    return db.query(Setting).filter(Setting.key == key).first()


def carry_over(db) -> bool:
    """Run once. Returns True if it ran now."""
    if _get(db, DONE_KEY):
        return False
    # An install that's been used: set up, or with downloads or a watchlist.
    done = _get(db, "onboarding_done")
    existing = bool((done and done.value == "true") or db.query(Download.id).first()
                    or db.query(WatchlistItem.id).first())
    if existing and not _get(db, "qbit_category"):
        db.add(Setting(key="qbit_category", value="screenmate"))
    url = _get(db, "app_source_url")
    if url and (url.value or "").rstrip("/").removesuffix(".git") == OLD_URL:
        url.value = NEW_URL
    db.add(Setting(key=DONE_KEY, value="1"))
    db.commit()
    return True
