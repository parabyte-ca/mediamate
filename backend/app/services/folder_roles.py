"""One Folders list: what each folder is for.

Before, folders were set in several places: Settings → Folders, typed paths under Rename
& organise (TV, Movies, Mature TV, Mature Movies), Settings → Music (the music library)
and Settings → qBittorrent (the music download folder). Now each folder in Settings →
Folders carries its job:

* ``role``: the library that kind of media is organised into (one folder per role).
* ``music_downloads``: album downloads go here.

The rest of the app still reads the familiar setting names (``organise_tv_dir`` …);
:func:`overlay` fills them from the folders. :func:`migrate_once` moves an install's
old typed paths onto folders the first time it starts.
"""
import logging
import os

logger = logging.getLogger(__name__)

ROLES = ("tv", "movies", "mature_tv", "mature_movies", "music")
SETTING = {"tv": "organise_tv_dir", "movies": "organise_movie_dir", "mature_tv": "organise_tv_mature_dir",
           "mature_movies": "organise_movie_mature_dir", "music": "organise_music_dir"}
LABEL = {"tv": "TV library", "movies": "Movies library", "mature_tv": "Mature TV library",
         "mature_movies": "Mature Movies library", "music": "Music library"}
DONE_KEY = "folder_roles_v1"


def _local(db, dp) -> str | None:
    from . import folders
    return folders.local_of(db, dp)


def paths(db) -> dict[str, str]:
    """{setting name: folder path here} for every folder with a library role."""
    from ..models import DownloadPath
    out = {}
    for dp in db.query(DownloadPath).filter(DownloadPath.role.isnot(None)).all():
        if dp.role in SETTING:
            local = _local(db, dp)
            if local:
                out[SETTING[dp.role]] = local
    return out


def path_for(db, role: str) -> str | None:
    return paths(db).get(SETTING[role])


def overlay(db, settings: dict) -> dict:
    """The settings with each library folder taken from its folder's role."""
    return {**settings, **paths(db)}


def music_download_folder(db):
    from ..models import DownloadPath
    return (db.query(DownloadPath)
            .filter(DownloadPath.music_downloads.is_(True), DownloadPath.downloads.is_(True)).first())


def notes(db, dp, all_folders: list) -> list[str]:
    """Things worth knowing about a folder: a near-twin path, or overlapping scans."""
    out = []
    mine = os.path.normpath(_local(db, dp) or dp.path or "")
    for other in all_folders:
        if other.id == dp.id:
            continue
        theirs = os.path.normpath(_local(db, other) or other.path or "")
        if theirs != mine and theirs.lower() == mine.lower():
            out.append(f"Same path as {other.name} apart from capital letters: on the server these are two "
                       "different folders. Keep one.")
        elif dp.scan and other.scan and mine.startswith(theirs.rstrip(os.sep) + os.sep):
            out.append(f"Inside {other.name}, which is scanned too. Turn Scan off on one of them.")
    return out


def migrate_once(db) -> dict:
    """Move typed library paths and the music download setting onto folders. Once."""
    from ..models import DownloadPath, Setting
    from . import folders as folder_service
    if db.get(Setting, DONE_KEY):
        return {}
    done = {}
    all_folders = db.query(DownloadPath).all()
    scanned = [os.path.normpath(r) for r in folder_service.scan_roots(db)]
    for role in ROLES:
        row = db.get(Setting, SETTING[role])
        value = (row.value or "").strip() if row else ""
        if not value or db.query(DownloadPath).filter(DownloadPath.role == role).first():
            continue
        target = os.path.normpath(value)
        dp = next((d for d in all_folders
                   if os.path.normpath(folder_service.local_of(db, d) or d.path or "") == target), None)
        if dp is None:
            inside = any(target == s or target.startswith(s.rstrip(os.sep) + os.sep) for s in scanned if s)
            dp = DownloadPath(name=LABEL[role], path=value, local_path=value,
                              downloads=False, scan=not inside,
                              target="music" if role == "music" else
                              ("tv" if "tv" in role else "movies"))
            db.add(dp)
            all_folders.append(dp)
        if dp.protected:
            logger.warning("Not giving protected folder %s the %s role", dp.name, role)
            continue
        dp.role = role
        row.value = ""                       # the folder is the only place now
        done[role] = value
    row = db.get(Setting, "music_download_path_id")
    if row and (row.value or "").isdigit():
        dp = db.get(DownloadPath, int(row.value))
        if dp is not None and dp.downloads:
            dp.music_downloads = True
            done["music_downloads"] = dp.name
        row.value = ""
    db.add(Setting(key=DONE_KEY, value="1"))
    db.commit()
    if done:
        logger.info("Folder settings moved onto folders: %s", done)
    return done
