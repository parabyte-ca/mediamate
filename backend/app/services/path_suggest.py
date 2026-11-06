"""Work out where the old apps' folders live on this server, from the imported data.

FileSplitter stored full file paths; AuTorrent stored the folders qBittorrent saves
into. When MediaMate runs in a different container, the same files usually sit
under a different prefix (/media/tv → /mnt/nas/tv). We find that prefix by looking
for each old path's tail (…/tv/Show/ep.mkv) under MediaMate's media folders, then
let the most common answer win. Nothing here writes anything.
"""
import os
import sqlite3
from collections import Counter

SAMPLE = 200       # paths checked per database
MAX_TAIL = 6       # deepest tail tried (folders + file name)


def _parts(path: str) -> list[str]:
    return [p for p in path.split("/") if p]


def _join(*parts: str) -> str:
    return "/" + "/".join(p.strip("/") for p in parts if p.strip("/"))


def _locate(path: str, roots: list[str], exists) -> tuple[str, str] | None:
    """(old prefix, new prefix) that puts `path` somewhere real under one of `roots`."""
    parts = _parts(path)
    for root in roots:
        root = root.rstrip("/") or "/"
        # A folder can be the root itself under another name: /downloads → /data/downloads
        if parts and _parts(root) and _parts(root)[-1] == parts[-1] and exists(root):
            return _join(*parts), root
        # Longest tail first: the most specific match is the most trustworthy.
        for k in range(min(MAX_TAIL, len(parts) - 1), 0, -1):
            if exists(_join(root, *parts[-k:])):
                return _join(*parts[:-k]), root
    return None


def _vote(paths: list[str], roots: list[str], exists, kind_exists) -> list[dict]:
    """Group the paths that aren't here by the prefix change that finds them."""
    missing = [p for p in paths if p and not kind_exists(p)]
    votes: Counter = Counter()
    for p in missing:
        hit = _locate(p, roots, exists)
        if hit and hit[0] != hit[1]:
            votes[hit] += 1
    return [{"from": old, "to": new, "matched": n, "checked": len(missing)}
            for (old, new), n in votes.most_common()]


def _apply(path: str, old: str, new: str) -> str:
    if path == old or path.startswith(old.rstrip("/") + "/"):
        return new.rstrip("/") + path[len(old.rstrip("/")):]
    return path


def _from_filesplitter(qbit_dirs: list[str], fs_paths: list[str]) -> list[dict]:
    """No local evidence for a qBittorrent folder? Find its tail inside FileSplitter's paths."""
    out: Counter = Counter()
    for d in qbit_dirs:
        parts = _parts(d)
        for k in range(min(MAX_TAIL, len(parts)), 0, -1):
            tail = "/" + "/".join(parts[-k:]) + "/"
            hits = [p[:p.index(tail)] + tail.rstrip("/") for p in fs_paths if tail in p]
            if hits:
                local = Counter(hits).most_common(1)[0][0]
                remote = _join(*parts)
                if local != remote:
                    out[(remote, local)] += 1
                break
    return [{"from": r, "to": l, "matched": n, "checked": len(qbit_dirs), "via": "filesplitter"}
            for (r, l), n in out.most_common()]


def suggest(autorrent_db: str | None, filesplitter_db: str | None, roots: list[str],
            existing_mappings: list[tuple[str, str]] = (), exists=os.path.exists,
            isdir=os.path.isdir, isfile=os.path.isfile) -> dict:
    """
    rewrite:  the folder change for FileSplitter's files (its paths are stored).
    mappings: qBittorrent folder → folder here, for the Auto pipeline's path mappings.
    """
    fs_paths: list[str] = []
    if filesplitter_db:
        con = sqlite3.connect(f"file:{filesplitter_db}?mode=ro", uri=True)
        try:
            fs_paths = [r[0] for r in con.execute("SELECT path FROM files ORDER BY id LIMIT ?", (SAMPLE,))]
        except sqlite3.DatabaseError:
            pass
        finally:
            con.close()

    qbit_dirs: list[str] = []
    if autorrent_db:
        con = sqlite3.connect(f"file:{autorrent_db}?mode=ro", uri=True)
        try:
            seen: list[str] = []
            for sql in ("SELECT path FROM download_paths",
                        "SELECT DISTINCT download_path FROM downloads WHERE download_path IS NOT NULL LIMIT 200"):
                try:
                    seen += [r[0].rstrip("/") for r in con.execute(sql) if r[0]]
                except sqlite3.DatabaseError:
                    pass
            qbit_dirs = list(dict.fromkeys(seen))
        finally:
            con.close()

    rewrites = _vote(fs_paths, roots, exists, isfile)
    rewrite = rewrites[0] if rewrites else None
    fs_here = [_apply(p, rewrite["from"], rewrite["to"]) if rewrite else p for p in fs_paths]

    # qBittorrent folders: already mapped or present here need nothing.
    def mapped(d: str) -> bool:
        return any(d == r.rstrip("/") or d.startswith(r.rstrip("/") + "/") for r, _ in existing_mappings)
    todo = [d for d in qbit_dirs if not isdir(d) and not mapped(d)]
    mappings = _vote(todo, roots, exists, isdir)
    found = {d for d in todo for m in mappings if d == m["from"] or d.startswith(m["from"] + "/")}
    mappings += _from_filesplitter([d for d in todo if d not in found], fs_here)
    # Collapse to one mapping per old prefix, keeping the best-supported.
    best: dict[str, dict] = {}
    for m in mappings:
        if m["from"] not in best or m["matched"] > best[m["from"]]["matched"]:
            best[m["from"]] = m
    return {
        "rewrite": rewrite,
        "files_checked": len(fs_paths),
        "mappings": sorted(best.values(), key=lambda m: -m["matched"]),
        "folders_checked": len(qbit_dirs),
        "folders_missing": len(todo),
    }
