"""Rename & organise: move a finished file into the library with a Plex-friendly name.

TV:     <TV folder>/Show/Season 01/Show - S01E02.mkv
Movies: <Movie folder>/Title (2020)/Title (2020).mkv

The show/movie name comes from the watchlist item when the file came from one
(clean, the way you typed it), otherwise from the file name. Subtitle files next to
the video (same name, .srt/.ass/…) move with it. Nothing is overwritten: if the
target exists, the file is left where it is and the reason is reported.

Folders and templates are settings (organise_*). Destination folders should be in
Settings → Folders with "Scan & encode" on, so the Library keeps tracking the file.
"""
import os
import re
import shutil
from dataclasses import dataclass

from sqlalchemy.orm import Session

from ..models import Setting

SETTINGS_DEFAULTS = {
    "organise_tv_dir": "",
    "organise_movie_dir": "",
    "organise_tv_mature_dir": "",       # optional: Mature TV Shows (see services/ratings.py)
    "organise_movie_mature_dir": "",    # optional: Mature Movies
    "organise_tv_template": "{show}/Season {season:02d}/{show} - S{season:02d}E{episode:02d}{ext}",
    "organise_movie_template": "{title} ({year})/{title} ({year}){ext}",
}
SIDECAR_EXTS = (".srt", ".ass", ".ssa", ".sub", ".idx", ".vtt", ".sup")

# Underscores count as separators, so no \b (it treats "_" as part of a word).
_EPISODE = re.compile(r"(?i)(?<![a-z0-9])S(\d{1,2})[ ._-]?E(\d{1,3})(?:[-_ ]?E?(\d{1,3}))?(?![0-9])")
_EPISODE_X = re.compile(r"(?i)(?<![a-z0-9])(\d{1,2})x(\d{2,3})(?![0-9])")
_YEAR = re.compile(r"(?<!\d)(19[2-9]\d|20\d\d)(?!\d)")
# Words that start the release junk after a title: quality, source, codec…
_JUNK = re.compile(
    r"(?i)\b(2160p|1080p|720p|576p|480p|4k|uhd|hdr\d*|dv|web[- .]?dl|webrip|web|bluray|blu[- .]?ray|brrip|"
    r"bdrip|dvdrip|hdtv|remux|proper|repack|extended|unrated|x26[45]|h\.?26[45]|hevc|avc|aac\d*|ac3|dts|"
    r"ddp?\d|atmos|10bit|8bit|multi|subs?)\b")
_BAD_CHARS = re.compile(r'[<>:"/\\|?*\x00-\x1f]')


@dataclass
class Parsed:
    kind: str | None            # "episode" | "movie" | None
    title: str = ""             # show or movie title
    season: int = 0
    episode: int = 0
    episode_end: int = 0        # multi-episode files: S01E01-E02
    year: int | None = None


def _clean(name: str) -> str:
    name = re.sub(r"[._]+", " ", name)
    name = re.sub(r"\[[^\]]*\]|\{[^}]*\}", " ", name)          # [group] tags
    name = re.sub(r"\s+-\s*$", "", name)
    name = re.sub(r"\s+", " ", name).strip(" -")
    return " ".join(w if (w.isupper() and len(w) <= 4) else w[:1].upper() + w[1:] for w in name.split())


def safe(part: str) -> str:
    """A name that's valid as a folder or file name everywhere (Plex, SMB, Windows)."""
    part = _BAD_CHARS.sub("", part).strip().rstrip(".")
    return part or "Unknown"


def parse(filename: str) -> Parsed:
    stem = os.path.splitext(os.path.basename(filename))[0]
    m = _EPISODE.search(stem) or _EPISODE_X.search(stem)
    if m:
        title = _clean(stem[:m.start()])
        title = _YEAR.sub("", title).strip(" ()-") or title     # "Show 2019 S01E01" → "Show"
        end = int(m.group(3)) if m.re is _EPISODE and m.group(3) else 0
        return Parsed("episode", title, int(m.group(1)), int(m.group(2)), end)
    years = list(_YEAR.finditer(stem))
    junk = _JUNK.search(stem)
    if years:
        # The year closest to the release junk is the release year ("2001 A Space Odyssey 1968").
        y = years[-1] if not junk else max((y for y in years if y.start() < junk.start()), default=years[-1],
                                           key=lambda y: y.start())
        cut = min(y.start(), junk.start()) if junk else y.start()
        title = _clean(stem[:cut]).strip(" (")
        if title:
            return Parsed("movie", title, year=int(y.group(1)))
    return Parsed(None, _clean(stem[:junk.start()] if junk else stem))


def get_settings(db: Session) -> dict[str, str]:
    rows = {r.key: r.value for r in db.query(Setting).filter(Setting.key.in_(SETTINGS_DEFAULTS))}
    return {k: (rows.get(k) if rows.get(k) not in (None, "") else v) for k, v in SETTINGS_DEFAULTS.items()}


class OrganiseError(Exception):
    """A user-facing reason the file wasn't organised."""


def _root(settings: dict, kind: str, audience: str) -> str:
    """The folder for this kind and audience. Mature files use the Mature folder when one
    is set; without one, everything shares the regular folder (as before)."""
    base = "organise_tv" if kind == "episode" else "organise_movie"
    if audience == "mature" and settings.get(f"{base}_mature_dir", "").strip():
        return settings[f"{base}_mature_dir"].strip()
    return settings.get(f"{base}_dir", "").strip()


def mature_folders_set(settings: dict) -> bool:
    return bool(settings.get("organise_tv_mature_dir", "").strip()
                or settings.get("organise_movie_mature_dir", "").strip())


def target_for(settings: dict, path: str, show_hint: str | None = None,
               audience: str = "general") -> tuple[str, Parsed]:
    """Where `path` belongs. Raises OrganiseError when it can't be worked out."""
    p = parse(path)
    ext = os.path.splitext(path)[1].lower()
    if p.kind == "episode":
        root = _root(settings, "episode", audience)
        if not root:
            raise OrganiseError("Set a TV folder in Settings → Auto pipeline → Rename & organise")
        show = safe(show_hint or p.title)
        if not show or show == "Unknown":
            raise OrganiseError("Couldn't tell the show's name from the file name")
        rel = settings["organise_tv_template"].format(show=show, season=p.season, episode=p.episode, ext=ext,
                                                      title=show, year=p.year or "")
        if p.episode_end:   # Show - S01E01-E02.mkv
            stem, e = os.path.splitext(rel)
            rel = f"{stem}-E{p.episode_end:02d}{e}"
    elif p.kind == "movie":
        root = _root(settings, "movie", audience)
        if not root:
            raise OrganiseError("Set a Movies folder in Settings → Auto pipeline → Rename & organise")
        title = safe(p.title)
        rel = settings["organise_movie_template"].format(title=title, year=p.year, ext=ext,
                                                         show=title, season=0, episode=0)
    else:
        raise OrganiseError("Couldn't tell whether this is an episode (S01E02) or a movie (Title 2020)")
    parts = [safe(x) for x in rel.replace("\\", "/").split("/") if x.strip()]
    return os.path.join(root, *parts), p


def _sidecars(path: str) -> list[str]:
    folder, stem = os.path.dirname(path), os.path.splitext(os.path.basename(path))[0]
    try:
        names = os.listdir(folder)
    except OSError:
        return []
    return [os.path.join(folder, n) for n in names
            if n != os.path.basename(path) and n.startswith(stem)
            and os.path.splitext(n)[1].lower() in SIDECAR_EXTS]


def move(src: str, dest: str) -> list[str]:
    """Move the video (and its subtitles) to `dest`. Returns the paths it moved."""
    if os.path.abspath(src) == os.path.abspath(dest):
        return []
    if os.path.exists(dest):
        raise OrganiseError(f"{dest} already exists, so the file was left where it is")
    os.makedirs(os.path.dirname(dest), exist_ok=True)
    src_stem = os.path.splitext(src)[0]
    dest_stem = os.path.splitext(dest)[0]
    sidecars = _sidecars(src)
    shutil.move(src, dest)              # a rename on the same filesystem, else copy + delete
    moved = [dest]
    for s in sidecars:
        to = dest_stem + s[len(src_stem):]   # keep ".en.srt" style suffixes
        if not os.path.exists(to):
            shutil.move(s, to)
            moved.append(to)
    _prune_empty(os.path.dirname(src))
    return moved


def _prune_empty(folder: str) -> None:
    """Remove the download's folder once nothing but junk is left in it."""
    junk = (".nfo", ".txt", ".url", ".jpg", ".png", ".exe", ".sfv", ".md5", ".nzb")
    try:
        names = os.listdir(folder)
    except OSError:
        return
    if names and not all(os.path.splitext(n)[1].lower() in junk or n.lower().startswith("sample") for n in names):
        return
    if any(os.path.isdir(os.path.join(folder, n)) for n in names):
        return
    for n in names:
        try:
            os.remove(os.path.join(folder, n))
        except OSError:
            return
    try:
        os.rmdir(folder)
    except OSError:
        pass


def protected_root(path: str, db: Session | None = None) -> str | None:
    """The protected folder (name) this file is in, or None. Protected folders (e.g.
    Bellesa) are never renamed or moved."""
    from sqlalchemy.orm import Session as _S
    from ..models import DownloadPath
    from . import folders
    from .media import store
    own = db is None
    db = db or _S(bind=store._engine())
    try:
        p = os.path.normpath(os.path.abspath(path))
        for dp in db.query(DownloadPath).filter(DownloadPath.protected == True).all():  # noqa: E712
            for root in {folders.local_of(db, dp), dp.path, dp.local_path}:
                if not root:
                    continue
                r = os.path.normpath(os.path.abspath(root))
                if p == r or p.startswith(r.rstrip(os.sep) + os.sep):
                    return dp.name
        return None
    finally:
        if own:
            db.close()


def guard(path: str, db: Session | None = None) -> None:
    """Refuse to organise adult content: files in a protected folder, or that look adult.
    Raises OrganiseError. Every organise path (Library, doctor, pipeline) goes through here."""
    from . import adult
    name = protected_root(path, db)
    if name:
        raise OrganiseError(f"It's in a protected folder ({name}): never renamed or moved")
    why = adult.looks_adult(path, adult.extra_keywords(db))
    if why:
        raise OrganiseError(f"It looks like adult content ({why}), so it's never moved into a Plex folder")


def _log_move(file_id: int, src: str, dest: str) -> None:
    try:
        from sqlalchemy.orm import Session as _S
        from ..models import OrganiseMove
        from .media import store
        with _S(bind=store._engine()) as db:
            db.add(OrganiseMove(file_id=file_id, from_path=src, to_path=dest))
            db.commit()
    except Exception:
        import logging
        logging.getLogger(__name__).exception("Couldn't record the move of %s", src)


def organise_file(settings: dict, file_id: int, show_hint: str | None = None,
                  audience: str = "general") -> str:
    """Move a library file into place and update the Library. Returns the new path.
    Takes the settings rather than a session, so no database transaction is held
    open while the Library row is updated."""
    from .media import store
    row = store.get_file(file_id)
    if row is None:
        raise OrganiseError("The file is no longer in the library")
    guard(row["path"])          # again here, whatever the caller checked
    if not os.path.isfile(row["path"]):
        raise OrganiseError(f"{row['path']} isn't there any more")
    dest, _ = target_for(settings, row["path"], show_hint, audience)
    src = row["path"]
    move(src, dest)
    store.rename_file(file_id, dest, os.path.basename(dest))
    if os.path.abspath(src) != os.path.abspath(dest):
        _log_move(file_id, src, dest)
    return dest


def watchlist_for_download(db: Session, download_id: int | None):
    """The watchlist item a download came from, if any."""
    if not download_id:
        return None
    from ..models import Download, WatchlistItem
    dl = db.get(Download, download_id)
    if dl is None or not dl.watchlist_id:
        return None
    return db.get(WatchlistItem, dl.watchlist_id)


def show_hint_for_download(db: Session, download_id: int | None) -> str | None:
    """The watchlist's show name for a download, when it came from the watchlist."""
    item = watchlist_for_download(db, download_id)
    return item.title if item else None


def _watchlist_by_title(db: Session, title: str):
    from ..models import WatchlistItem
    from .ratings import _key
    k = _key(title)
    return next((w for w in db.query(WatchlistItem).all() if _key(w.title) == k), None) if k else None


def plan(db: Session, path: str, download_id: int | None = None, override: str | None = None,
         lookup: bool = True) -> dict:
    """Where a file would go and why: {target, kind, audience, reason, show_hint, settings}.
    Raises OrganiseError.

    The audience is the override if given, else the watchlist show's setting (the show the
    download came from, or one with the same name), else the age rating (services/ratings)."""
    from . import ratings
    guard(path, db)
    settings = get_settings(db)
    item = watchlist_for_download(db, download_id)
    parsed = parse(path)
    if item is None and parsed.kind == "episode":
        item = _watchlist_by_title(db, parsed.title)
    hint = item.title if item is not None else None
    if override in (ratings.GENERAL, ratings.MATURE):
        aud, why = override, "chosen by you"
    elif not mature_folders_set(settings):
        aud, why = ratings.GENERAL, ""
    else:
        aud, why = ratings.audience(db, parsed.kind, hint or parsed.title, parsed.year,
                                    watchlist_item=item, lookup=lookup)
    dest, p = target_for(settings, path, hint, aud)
    return {"target": dest, "kind": p.kind, "audience": aud, "reason": why, "show_hint": hint,
            "settings": settings}
