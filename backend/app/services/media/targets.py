"""Target formats: what each folder's files should be, and whether a file already is.

Each folder in Settings → Folders has a target (all H.265):
  * TV shows  — 720p to 1080p
  * Movies    — 1080p to 4K
  * Custom    — your own range, e.g. Bellesa: 720p only
  * Auto      — TV or Movies from the file name (S01E02 = TV)

A file **meets** its target when it is H.265. It can't be upscaled, so one below the
range still meets it (flagged "lower than preferred"); one above the range also meets
it (flagged "larger than target") unless you shrink it — that only happens when
asked, or automatically for folders with "shrink" on when the file is being encoded
anyway (it isn't H.265 yet).
"""
import os
import re
from dataclasses import dataclass

HEVC = {"hevc", "h265", "x265"}
HEIGHTS = (480, 720, 1080, 1440, 2160)
LABEL = {480: "480p", 720: "720p", 1080: "1080p", 1440: "1440p", 2160: "4K"}
RES_KEY = {480: "480p", 720: "720p", 1080: "1080p", 1440: "1440p", 2160: "2160p"}
PRESETS = {"tv": (720, 1080, False), "movies": (1080, 2160, False)}
KINDS = ("auto", "tv", "movies", "custom")
_EPISODE = re.compile(r"(?i)(?<![a-z0-9])(S\d{1,2}[ ._-]?E\d{1,3}|\d{1,2}x\d{2,3})(?![0-9])")


@dataclass
class Target:
    kind: str          # tv | movies | custom
    min: int
    max: int
    shrink: bool
    folder: str = ""

    def describe(self) -> str:
        span = LABEL[self.min] if self.min == self.max else f"{LABEL[self.min]}–{LABEL[self.max]}"
        name = {"tv": "TV", "movies": "Movies"}.get(self.kind, self.folder or "Custom")
        return f"H.265, {span} ({name})"


@dataclass
class Check:
    meets: bool
    resolution: int    # 480 / 720 / 1080 / 1440 / 2160 (0 = unknown)
    above: bool
    below: bool
    shrink_to: str     # "original" or an encoder resolution key ("720p")
    target: Target

    @property
    def reason(self) -> str:
        res = LABEL.get(self.resolution, "unknown size")
        if not self.meets:
            return f"Not H.265 yet ({res}); target {self.target.describe()}"
        if self.above:
            return f"H.265 {res}, larger than the target {self.target.describe()}"
        if self.below:
            return f"H.265 {res}, lower than preferred for {self.target.describe()}"
        return f"Already in target format: H.265 {res}"


def resolution_of(width: int | None, height: int | None) -> int:
    """The resolution class of a picture. Width counts too, so a widescreen 1920x800
    film is 1080p and 1280x536 is 720p."""
    w, h = width or 0, height or 0
    by_h = next((r for r, low in ((2160, 1600), (1440, 1200), (1080, 900), (720, 600), (480, 400)) if h >= low), 0)
    by_w = next((r for r, low in ((2160, 3200), (1440, 2200), (1080, 1700), (720, 1100), (480, 600)) if w >= low), 0)
    return max(by_h, by_w)


def is_hevc(codec: str | None) -> bool:
    return (codec or "").lower() in HEVC


def _norm(p: str) -> str:
    return os.path.normpath(p or "").rstrip(os.sep) or os.sep


def folder_target(kind: str | None, tmin: int | None, tmax: int | None, shrink: bool | None,
                  filename: str, folder: str = "") -> Target:
    kind = kind if kind in KINDS else "auto"
    if kind == "auto":
        kind = "tv" if _EPISODE.search(os.path.basename(filename or "")) else "movies"
    if kind in PRESETS:
        lo, hi, sh = PRESETS[kind]
        return Target(kind, lo, hi, sh, folder)
    lo = tmin if tmin in HEIGHTS else 720
    hi = tmax if tmax in HEIGHTS else lo
    lo, hi = min(lo, hi), max(lo, hi)
    return Target("custom", lo, hi, bool(shrink), folder)


def load_folders(db=None) -> list[dict]:
    """Scan folders with their targets, deepest first: [{root, kind, min, max, shrink, name}]."""
    from sqlalchemy.orm import Session
    from ...models import DownloadPath
    from .. import folders as folder_service
    from . import store

    own = db is None
    db = db or Session(bind=store._engine())
    try:
        out = []
        for dp in db.query(DownloadPath).filter(DownloadPath.scan == True).all():  # noqa: E712
            root = folder_service.local_of(db, dp)
            if root:
                out.append({"id": dp.id, "root": _norm(root), "kind": dp.target, "min": dp.target_min,
                            "max": dp.target_max, "shrink": dp.target_shrink, "name": dp.name})
        return sorted(out, key=lambda f: -len(f["root"]))
    finally:
        if own:
            db.close()


def target_for(path: str, folders: list[dict] | None = None) -> Target:
    folders = load_folders() if folders is None else folders
    p = _norm(path)
    for f in folders:
        if p == f["root"] or p.startswith(f["root"] + os.sep):
            return folder_target(f["kind"], f["min"], f["max"], f["shrink"], path, f["name"])
    return folder_target("auto", None, None, None, path)


def check(row, folders: list[dict] | None = None) -> Check:
    """How a Library file (media_files row) compares with its folder's target."""
    t = target_for(row["path"], folders)
    res = resolution_of(row["width"], row["height"])
    hevc = is_hevc(row["codec"])
    above = bool(res) and res > t.max
    below = bool(res) and res < t.min
    shrink_to = RES_KEY[t.max] if (not hevc and above and t.shrink) else "original"
    return Check(meets=hevc, resolution=res, above=above, below=below, shrink_to=shrink_to, target=t)


def classify(file_id: int, folders: list[dict] | None = None) -> str | None:
    """Set a pending / in-target file's status from its target. Returns the new status, or
    None when the file isn't pending or ok (queued, done, skipped… are left alone)."""
    from . import store
    row = store.get_file(file_id)
    if row is None or row["status"] not in ("pending", "ok"):
        return None
    status = "ok" if check(row, folders).meets else "pending"
    if status != row["status"]:
        store.set_file_status(file_id, status)
    return status


def reclassify(root: str | None = None) -> int:
    """Re-check pending / in-target files (all, or under ``root``) after targets change."""
    from . import store
    folders = load_folders()
    changed = 0
    r = _norm(root) if root else None
    for row in store.get_all_files():
        if row["status"] not in ("pending", "ok"):
            continue
        p = _norm(row["path"])
        if r and not (p == r or p.startswith(r + os.sep)):
            continue
        before = row["status"]
        if classify(row["id"], folders) not in (None, before):
            changed += 1
    return changed
