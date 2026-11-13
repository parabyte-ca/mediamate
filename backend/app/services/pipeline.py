"""Auto pipeline: when a download completes (or a scan finds a new file), match it
against the user's rules and run the rule's steps — trim, encode, split — one after
another, then refresh Plex/Jellyfin and notify.

Each file gets a PipelinePlan inside the item's journey. A plan creates one media job
at a time (two ffmpeg jobs on the same file at once would corrupt it) and advances
when that job finishes (store.set_job_status → on_job_finished). A periodic tick()
restarts anything a restart or a missed event left waiting.

Downloads are only touched once their torrent has left qBittorrent: encoding a file
in place while it is still seeding would break the torrent. By default the pipeline
removes the torrent itself (files are kept), like "Remove on complete".
"""
import asyncio
import json
import logging
import os
import shutil
import threading
from datetime import datetime, timezone

from sqlalchemy import text
from sqlalchemy.orm import Session

from ..models import Download, PathMapping, PipelinePlan, PipelineRule, Setting
from . import events, journeys
from .media import codec_detector, config as media_config, scanner, store

logger = logging.getLogger(__name__)

ACTION_TYPES = ("trim", "encode", "split", "organise")
ACTION_LABEL = {"trim": "Trim", "encode": "Encode", "split": "Split", "organise": "Rename & organise"}
SETTINGS_DEFAULTS = {
    "pipeline_enabled": "true",
    "pipeline_on_scan": "false",        # off by default: a first scan shouldn't queue a whole library
    "pipeline_remove_torrent": "true",  # remove the torrent (keep files) before modifying them
}
DISK_HEADROOM = 1.1  # need free space for the output plus 10 %
FALLBACK_ENCODE_RATIO = 0.45  # share of size an h264→x265 encode typically saves

_lock = threading.RLock()


def _utcnow() -> datetime:
    return datetime.now(timezone.utc).replace(tzinfo=None)


# ── Settings, rules, validation ──────────────────────────────────────────────

def get_settings(db: Session) -> dict[str, str]:
    rows = {r.key: r.value for r in db.query(Setting).filter(Setting.key.in_(SETTINGS_DEFAULTS))}
    return {k: rows.get(k) or v for k, v in SETTINGS_DEFAULTS.items()}


def _on(settings: dict, key: str) -> bool:
    return str(settings.get(key, "")).lower() == "true"


def validate(match: dict, actions: list) -> str | None:
    """Return an error message, or None if the rule is usable."""
    if not isinstance(match, dict) or not isinstance(actions, list):
        return "match must be an object and actions a list"
    if match.get("source", "any") not in ("any", "download", "scan"):
        return "source must be any, download or scan"
    if not actions:
        return "Add at least one step"
    types = [a.get("type") if isinstance(a, dict) else None for a in actions]
    if any(t not in ACTION_TYPES for t in types):
        return f"Steps must be one of {', '.join(ACTION_TYPES)}"
    if len(set(types)) != len(types):
        return "Each step can appear once"
    if "split" in types and "organise" in types:
        return "Split and Rename & organise can't be in the same rule (split makes several new files)"
    if "split" in types and types[-1] != "split":
        return "Split must be the last step (it replaces the file with several new ones)"
    if "organise" in types and types[-1] != "organise":
        return "Rename & organise must be the last step (it moves the file into the library)"
    for a in actions:
        if a["type"] == "encode":
            res = a.get("resolution", "original")
            if res != "original" and res not in media_config.RESOLUTION_MAP:
                return f"Unknown resolution {res}"
    return None


def rule_dict(r: PipelineRule) -> dict:
    return {"id": r.id, "name": r.name, "enabled": bool(r.enabled), "priority": r.priority,
            "match": json.loads(r.match or "{}"), "actions": json.loads(r.actions or "[]")}


def load_rules(db: Session) -> list[dict]:
    rows = db.query(PipelineRule).order_by(PipelineRule.priority, PipelineRule.id).all()
    return [rule_dict(r) for r in rows]


def matches(rule: dict, info: dict, source: str, watchlist_id: int | None = None) -> bool:
    """Does a file (media_files-shaped dict) satisfy a rule's conditions?"""
    m = rule.get("match") or {}
    if m.get("source", "any") not in ("any", source):
        return False
    codecs = [c.lower() for c in m.get("codecs") or []]
    if codecs and (info.get("codec") or "").lower() not in codecs:
        return False
    height = info.get("height") or 0
    if m.get("min_height") and height < int(m["min_height"]):
        return False
    if m.get("max_height") and height > int(m["max_height"]):
        return False
    if m.get("min_size_mb") and (info.get("size_bytes") or 0) < int(m["min_size_mb"]) * 1_000_000:
        return False
    minutes = (info.get("duration_sec") or 0) / 60
    if m.get("min_duration_min") and minutes < float(m["min_duration_min"]):
        return False
    if m.get("max_duration_min") and minutes > float(m["max_duration_min"]):
        return False
    keywords = [k.lower() for k in m.get("keywords") or [] if k]
    if keywords and not any(k in (info.get("filename") or "").lower() for k in keywords):
        return False
    if m.get("anthology") is not None and bool(info.get("is_anthology")) != bool(m["anthology"]):
        return False
    prefix = m.get("path_prefix")
    if prefix and not (info.get("path") or "").startswith(prefix):
        return False
    wl = m.get("watchlist_ids") or []
    if wl and watchlist_id not in wl:
        return False
    return True


def choose_rule(rules: list[dict], info: dict, source: str, watchlist_id: int | None = None) -> dict | None:
    for rule in rules:
        if rule["enabled"] and matches(rule, info, source, watchlist_id):
            return rule
    return None


# ── Paths ─────────────────────────────────────────────────────────────────────

def map_path(db: Session, path: str) -> str:
    """qBittorrent's view of a path → this container's, via the longest matching mapping."""
    best = None
    for m in db.query(PathMapping).all():
        remote = m.remote_prefix.rstrip("/")
        if (path == remote or path.startswith(remote + "/")) and (best is None or len(remote) > len(best[0])):
            best = (remote, m.local_prefix.rstrip("/"))
    if best is None:
        return path
    return best[1] + path[len(best[0]):]


def find_video_files(path: str) -> list[str]:
    exts = media_config.VIDEO_EXTENSIONS
    if os.path.isfile(path):
        return [path] if os.path.splitext(path)[1].lower() in exts else []
    found = []
    for root, dirs, files in os.walk(path):
        dirs[:] = [d for d in dirs if not d.startswith(".")]
        for f in sorted(files):
            if os.path.splitext(f)[1].lower() in exts and "sample" not in f.lower():
                found.append(os.path.join(root, f))
    return found


def index_file(path: str) -> dict | None:
    """Probe a file and upsert it into the library; returns its media_files row."""
    existing = store.get_file_by_path(path)
    info, err = codec_detector.probe_with_error(path)
    if info is None:
        logger.warning("Pipeline could not read %s: %s", path, err)
        return None
    fname = os.path.basename(path)
    if existing and existing["anthology_override"] is not None:
        anthology = bool(existing["anthology_override"])
    else:
        anthology = scanner._is_anthology(fname, info.size_bytes, info.duration_sec)
    file_id = store.upsert_file(path=path, filename=fname, size_bytes=info.size_bytes,
                                duration_sec=info.duration_sec, codec=info.codec,
                                is_anthology=anthology, width=info.width, height=info.height)
    return dict(store.get_file(file_id))


# ── Entry points ──────────────────────────────────────────────────────────────

def on_download_complete(bind, download_id: int) -> int:
    """Evaluate rules for a finished download. Returns the number of plans created."""
    with _lock, Session(bind=bind) as db:
        settings = get_settings(db)
        if not _on(settings, "pipeline_enabled"):
            return 0
        dl = db.query(Download).filter(Download.id == download_id).first()
        if dl is None or db.query(PipelinePlan).filter(PipelinePlan.download_id == dl.id).first():
            return 0
        rules = load_rules(db)
        if not any(r["enabled"] for r in rules):
            return 0
        journey = journeys.for_download(db, dl)
        db.commit()
        journey_id = journey.id

        remote = None
        if dl.torrent_hash and not dl.qbit_removed:
            from .qbittorrent import get_content_path
            remote = get_content_path(dl.torrent_hash)
        if not remote:
            remote = os.path.join(dl.download_path or "", dl.title)
        local = map_path(db, remote)

        if not os.path.exists(local):
            hint = (f"Downloaded to {remote}, which isn't visible here as {local}. "
                    "Add a path mapping in Settings → Auto pipeline.")
            db.add(PipelinePlan(journey_id=journey_id, download_id=dl.id, status="failed",
                                detail=hint, source_path=local))
            db.commit()
            journeys.refresh(db, journey_id)
            return 0

        created = []
        for path in find_video_files(local):
            info = index_file(path)
            if info is None:
                continue
            rule = choose_rule(rules, info, "download", dl.watchlist_id)
            if rule is None:
                continue
            plan = PipelinePlan(journey_id=journey_id, download_id=dl.id, file_id=info["id"],
                                rule_id=rule["id"], rule_name=rule["name"],
                                actions=json.dumps(rule["actions"]), status="waiting",
                                source_path=path)
            db.add(plan)
            db.commit()
            created.append(plan.id)
            # Link the file to this journey so later manual jobs land here too.
            db.execute(text("UPDATE journeys SET media_file_id=COALESCE(media_file_id, :f) WHERE id=:j"),
                       {"f": info["id"], "j": journey_id})
            db.commit()
    for plan_id in created:
        advance(bind, plan_id)
    if not created:
        with Session(bind=bind) as db:
            journeys.refresh(db, journey_id)
    return len(created)


def on_scanned(bind, file_ids: list[int]) -> int:
    """Apply rules to files a library scan just discovered (when enabled)."""
    with Session(bind=bind) as db:
        settings = get_settings(db)
        if not (_on(settings, "pipeline_enabled") and _on(settings, "pipeline_on_scan")):
            return 0
    return apply_to_files(bind, file_ids, source="scan")


def apply_to_files(bind, file_ids: list[int], source: str = "scan", rule_override: dict | None = None) -> int:
    """Create plans for library files (scan discoveries, or 'Apply' from the preview)."""
    created = []
    with _lock, Session(bind=bind) as db:
        rules = [rule_override] if rule_override else load_rules(db)
        for fid in file_ids:
            if db.query(PipelinePlan).filter(PipelinePlan.file_id == fid,
                                             PipelinePlan.status.in_(["waiting", "running"])).first():
                continue
            if store.get_active_job_for_file(fid) is not None:
                continue
            row = store.get_file(fid)
            if row is None:
                continue
            info = dict(row)
            rule = choose_rule(rules, info, source)
            if rule is None:
                continue
            journey = journeys.for_media_file(db, fid)
            plan = PipelinePlan(journey_id=journey.id, file_id=fid, rule_id=rule.get("id"),
                                rule_name=rule["name"], actions=json.dumps(rule["actions"]),
                                status="running", source_path=info["path"])
            db.add(plan)
            db.commit()
            created.append(plan.id)
    for plan_id in created:
        advance(bind, plan_id)
    return len(created)


def on_job_finished(bind, job_id: int, status: str) -> None:
    """Called by the media store when a job reaches done / error / cancelled."""
    try:
        with Session(bind=bind) as db:
            job = db.execute(text("SELECT file_id, journey_id FROM media_jobs WHERE id=:id"),
                             {"id": job_id}).first()
            if job is None or job.journey_id is None:
                return
            plan = (db.query(PipelinePlan)
                    .filter(PipelinePlan.file_id == job.file_id, PipelinePlan.journey_id == job.journey_id)
                    .order_by(PipelinePlan.id.desc()).first())
            if plan is None or plan.status in ("done", "cancelled"):
                return
            if status == "done":
                plan.status = "running"
                plan.detail = None
                db.commit()
                plan_id = plan.id
            else:
                plan.status = "failed" if status == "error" else "cancelled"
                db.commit()
                journeys.refresh(db, plan.journey_id)  # remaining steps are now skipped
                return
        advance(bind, plan_id)
    except Exception:
        logger.exception("Pipeline could not continue after job %s", job_id)


def cancel_plan(bind, plan_id: int) -> None:
    with _lock, Session(bind=bind) as db:
        plan = db.get(PipelinePlan, plan_id)
        if plan and plan.status in ("waiting", "running", "failed"):
            plan.status = "cancelled"
            db.commit()
            if plan.journey_id:
                journeys.refresh(db, plan.journey_id)
    finish_deferred(bind)


def retry_plan(bind, plan_id: int) -> str:
    """Re-run a plan that failed outside a job (path not found, disk space)."""
    with Session(bind=bind) as db:
        plan = db.get(PipelinePlan, plan_id)
        if plan is None:
            return "Plan not found"
        if plan.file_id is None and plan.download_id:
            # Path resolution failed: start over for this download.
            dl_id = plan.download_id
            db.delete(plan)
            db.commit()
            n = on_download_complete(bind, dl_id)
            return f"Found {n} file(s) to process" if n else "Still nothing to process — check the path mapping"
        plan.status = "running"
        plan.detail = None
        db.commit()
    advance(bind, plan_id)
    return "Pipeline resumed"


# ── The engine ────────────────────────────────────────────────────────────────

def advance(bind, plan_id: int) -> None:
    """Start the plan's next step, or finish it. Safe to call repeatedly."""
    finalize_id = None
    with _lock, Session(bind=bind) as db:
        plan = db.get(PipelinePlan, plan_id)
        if plan is None or plan.status in ("done", "cancelled", "failed") or plan.file_id is None:
            return
        if store.get_active_job_for_file(plan.file_id) is not None:
            return  # one step at a time per file

        if plan.download_id:
            dl = db.get(Download, plan.download_id)
            if dl is not None and dl.torrent_hash and not dl.qbit_removed:
                if not _on(get_settings(db), "pipeline_remove_torrent"):
                    plan.status = "waiting"
                    plan.detail = "Waiting for the torrent to leave qBittorrent (seeding)"
                    db.commit()
                    journeys.refresh(db, plan.journey_id)
                    return
                from .qbittorrent import remove_torrent
                try:
                    remove_torrent(dl.torrent_hash, delete_files=False)  # keep the files
                    dl.qbit_removed = True
                    db.commit()
                except Exception as exc:
                    plan.status = "waiting"
                    plan.detail = f"Couldn't remove the torrent from qBittorrent yet ({exc})"
                    db.commit()
                    journeys.refresh(db, plan.journey_id)
                    return

        actions = json.loads(plan.actions or "[]")
        row = store.get_file(plan.file_id)
        if row is None:
            plan.status = "failed"
            plan.detail = "The file is no longer in the library"
            db.commit()
            journeys.refresh(db, plan.journey_id)
            return

        # An encode to the folder's target is pointless when the file is already H.265.
        while (plan.next_index < len(actions) and actions[plan.next_index]["type"] == "encode"
               and actions[plan.next_index].get("resolution", "original") == "original"):
            try:
                from .media import targets
                done_already = targets.check(row).meets
            except Exception:
                done_already = False
            if not done_already:
                break
            plan.next_index += 1
            plan.detail = "Encode skipped: already in target format (H.265)"
            db.commit()

        if plan.next_index >= len(actions):
            plan.status = "done"
            plan.finished_at = _utcnow()
            db.commit()
            finalize_id = plan.id
        elif actions[plan.next_index]["type"] == "organise":
            # Runs here, not as an ffmpeg job: a move is quick (a rename on the same disk).
            from . import organise
            plan.next_index += 1
            plan.status = "running"
            try:
                organise.guard(row["path"], db)
                protected = None
            except organise.OrganiseError as exc:
                protected = exc
            if protected is not None:
                # Protected folder / adult content: never moved. Skip the step; the plan finishes.
                plan.organise_status = "skipped"
                plan.organised_to = None
                plan.detail = f"Left where it is: {protected}"
            else:
                try:
                    # Decided before moving: may look up the age rating (regular vs Mature folder).
                    where = organise.plan(db, row["path"], plan.download_id)
                    db.commit()         # release the write lock before the Library row is updated
                    dest = organise.organise_file(where["settings"], plan.file_id, where["show_hint"],
                                                  where["audience"], where.get("genre"))
                    plan.organise_status, plan.organised_to = "done", dest
                except (organise.OrganiseError, OSError) as exc:
                    plan.organise_status = "failed"
                    plan.organised_to = None
                    plan.status = "failed"
                    plan.detail = f"Rename & organise: {exc}"
                    plan.finished_at = _utcnow()
                    db.commit()
                    journeys.refresh(db, plan.journey_id)
                    return
            plan.status = "done"
            plan.finished_at = _utcnow()
            db.commit()
            finalize_id = plan.id
        else:
            action = actions[plan.next_index]
            if action["type"] in ("encode", "trim"):
                try:
                    free = shutil.disk_usage(os.path.dirname(row["path"]) or ".").free
                except OSError:
                    free = None
                need = int((row["size_bytes"] or 0) * DISK_HEADROOM)
                if free is not None and free < need:
                    plan.status = "failed"
                    plan.detail = (f"Not enough free space to {action['type']}: "
                                   f"need {need / 1e9:.1f} GB, {free / 1e9:.1f} GB free")
                    db.commit()
                    journeys.refresh(db, plan.journey_id)
                    return
            plan.status = "running"
            plan.detail = None
            plan.next_index += 1
            db.commit()
            store.create_job(plan.file_id, action["type"], action.get("resolution", "original"),
                             int(action.get("episode_count") or 0), journey_id=plan.journey_id,
                             reason=f"Rule: {plan.rule_name}" if plan.rule_name else None)
            store.set_file_status(plan.file_id, "queued")
            return
    if finalize_id is not None:
        if journeys.PIPELINE_SYNC:
            _finalize(bind, finalize_id)
        else:
            threading.Thread(target=_finalize, args=(bind, finalize_id), daemon=True).start()


DEFERRED = "deferred"          # tail waits for the download's other files


def _download_open(db: Session, plan: PipelinePlan) -> bool:
    """Another file of the same download still has work waiting or running."""
    if not plan.download_id:
        return False
    return db.query(PipelinePlan).filter(PipelinePlan.download_id == plan.download_id,
                                         PipelinePlan.id != plan.id,
                                         PipelinePlan.status.in_(["waiting", "running"])).first() is not None


def _finalize(bind, plan_id: int) -> None:
    """After the last step: refresh the media server(s) and send a notification, once
    per download (a season pack waits for its last episode, then sends one summary)."""
    try:
        with Session(bind=bind) as db:
            plan = db.get(PipelinePlan, plan_id)
            if plan is None:
                return
            if _download_open(db, plan):
                plan.library_status = plan.notify_status = DEFERRED
                db.commit()
                if plan.journey_id:
                    journeys.refresh(db, plan.journey_id)
                return
            group = ([p for p in db.query(PipelinePlan).filter(PipelinePlan.download_id == plan.download_id,
                                                                PipelinePlan.file_id.isnot(None))]
                     if plan.download_id else [plan])
            settings = {s.key: s.value for s in db.query(Setting).all()}
            has_server = any(settings.get(f"{s}_enabled", "false").lower() == "true"
                             for s in ("plex", "jellyfin"))
            if has_server:
                from .media_servers import trigger_media_refresh
                asyncio.run(trigger_media_refresh(settings))
            library = "done" if has_server else "skipped"

            url = settings.get("apprise_url", "")
            from .webpush import has_subscribers
            notified = "skipped"
            if url or has_subscribers():
                ids = [p.file_id for p in group]
                saved = db.execute(text(
                    "SELECT COALESCE(SUM(saved_bytes),0) FROM media_jobs WHERE journey_id=:j AND file_id IN ("
                    + ",".join(str(int(i)) for i in ids) + ")"), {"j": plan.journey_id}).scalar() or 0
                steps = ", ".join(ACTION_LABEL[a["type"]].lower() for a in json.loads(plan.actions))
                done = sum(1 for p in group if p.status == "done")
                bad = len(group) - done
                if len(group) > 1:
                    title = db.execute(text("SELECT title FROM journeys WHERE id=:j"), {"j": plan.journey_id}).scalar() \
                        or "Download"
                    body = (f"🎬 {title}: {done} of {len(group)} files — {steps} done"
                            + (f", {bad} stopped or failed" if bad else "")
                            + (f", saved {saved / 1e9:.2f} GB" if saved else ""))
                else:
                    row = store.get_file(plan.file_id)
                    name = row["filename"] if row else "file"
                    body = f"🎬 {name}: {steps} done" + (f", saved {saved / 1e9:.2f} GB" if saved else "")
                from .apprise_notify import notify
                notify(url, "MediaMate — Processing complete", body, url=f"/activity?open={plan.journey_id}")
                notified = "done"
            for p in group:
                p.library_status, p.notify_status = library, notified
            db.commit()
            if plan.journey_id:
                journeys.refresh(db, plan.journey_id)
    except Exception:
        logger.exception("Pipeline finalize failed for plan %s", plan_id)


def finish_deferred(bind) -> None:
    """A season pack whose last files were cancelled or failed: send its one refresh
    and notification now (run by tick and after cancelling)."""
    with Session(bind=bind) as db:
        waiting = [p for p in db.query(PipelinePlan).filter(PipelinePlan.library_status == DEFERRED)]
        ready = {}
        for p in waiting:
            if p.download_id not in ready and not _download_open(db, p):
                ready[p.download_id] = p.id
    for plan_id in ready.values():
        _finalize(bind, plan_id)


def tick(bind=None) -> None:
    """Periodic safety net: resume plans whose torrent has since left qBittorrent, or
    whose next step never got queued (e.g. the app restarted between steps)."""
    from ..database import engine
    bind = bind or engine
    with Session(bind=bind) as db:
        ids = [p.id for p in db.query(PipelinePlan).filter(PipelinePlan.status.in_(["waiting", "running"]))]
    for plan_id in ids:
        try:
            advance(bind, plan_id)
        except Exception:
            logger.exception("Pipeline tick failed for plan %s", plan_id)
    try:
        finish_deferred(bind)
    except Exception:
        logger.exception("Pipeline tick: finishing season packs failed")


# ── Journey steps for plans ───────────────────────────────────────────────────

def plan_steps(db: Session, journey_id: int) -> list[dict]:
    """Steps that a plan adds to its journey beyond the jobs it already created."""
    steps = []
    plans = (db.query(PipelinePlan).filter(PipelinePlan.journey_id == journey_id)
             .order_by(PipelinePlan.id).all())
    multi = len({p.file_id for p in plans if p.file_id}) > 1
    tails: list[tuple[str, str, dict]] = []
    for plan in plans:
        ref = {"type": "plan", "id": plan.id}
        subject = journeys.subject_for(plan.file_id) if multi else None
        if plan.file_id is None:
            steps.append({"kind": "pipeline", "label": "Find files", "status": "failed",
                          "detail": plan.detail, "ref": ref})
            continue
        actions = json.loads(plan.actions or "[]")
        stopped = plan.status in ("failed", "cancelled")
        if plan.status == "waiting" and plan.detail:
            steps.append({"kind": "waiting", "label": "Waiting", "status": "pending",
                          "detail": plan.detail, "ref": ref})
        elif plan.status == "failed" and plan.detail:
            steps.append({"kind": "pipeline", "label": "Pipeline", "status": "failed",
                          "detail": plan.detail, "ref": ref})
        if plan.organise_status == "done":
            steps.append({"kind": "organise", "label": ACTION_LABEL["organise"], "status": "done",
                          "detail": f"Moved to {plan.organised_to}", "ref": ref})
        elif plan.organise_status == "skipped":
            steps.append({"kind": "organise", "label": ACTION_LABEL["organise"], "status": "skipped",
                          "detail": plan.detail, "ref": ref})
        for action in actions[plan.next_index:]:
            label = ACTION_LABEL[action["type"]]
            steps.append({"kind": action["type"], "label": f"{label} · {subject}" if subject else label,
                          "subject": subject, "status": "skipped" if stopped else "pending",
                          "detail": f"Rule: {plan.rule_name}" if plan.rule_name else None,
                          "ref": ref})
        tail = "skipped" if stopped else "pending"
        tails.append((plan.library_status or tail, plan.notify_status or tail, ref))
    if tails:
        # One refresh and one notification per journey, after its last file.
        def merged(vals):
            vals = ["pending" if v == DEFERRED else v for v in vals]
            for state in ("running", "pending", "failed", "done"):
                if state in vals:
                    return state
            return vals[-1]
        ref = tails[-1][2]
        steps.append({"kind": "library", "label": "Library refresh", "status": merged([t[0] for t in tails]),
                      "detail": "After the last file" if multi and any(t[0] in ("pending", DEFERRED) for t in tails) else None,
                      "ref": ref})
        steps.append({"kind": "notify", "label": "Notify", "status": merged([t[1] for t in tails]), "ref": ref})
    return steps


# ── Dry run ───────────────────────────────────────────────────────────────────

def _encode_ratio(db: Session) -> float:
    """Average share of size saved by finished encodes, or a typical default."""
    row = db.execute(text(
        "SELECT SUM(j.saved_bytes), SUM(f.size_bytes + j.saved_bytes) FROM media_jobs j"
        " JOIN media_files f ON f.id = j.file_id"
        " WHERE j.job_type='encode' AND j.status='done' AND j.saved_bytes > 0"
    )).first()
    if row and row[0] and row[1]:
        return max(0.0, min(0.9, row[0] / row[1]))
    return FALLBACK_ENCODE_RATIO


def preview(db: Session, rules: list[dict] | None = None, limit: int = 500) -> dict:
    """What would the rules do to the library right now? Nothing is changed."""
    rules = rules if rules is not None else load_rules(db)
    ratio = _encode_ratio(db)
    planned_or_busy = {r[0] for r in db.execute(text(
        "SELECT file_id FROM pipeline_plans WHERE status IN ('waiting','running') AND file_id IS NOT NULL"
        " UNION SELECT file_id FROM media_jobs WHERE status IN ('queued','running')"))}
    items, total_size, total_saved = [], 0, 0
    for row in store.get_all_files():
        info = dict(row)
        if info["status"] in ("done", "skipped", "ok") or info["id"] in planned_or_busy:
            continue
        rule = choose_rule(rules, info, "scan")
        if rule is None:
            continue
        types = [a["type"] for a in rule["actions"]]
        size = info["size_bytes"] or 0
        est = int(size * ratio) if "encode" in types and (info["codec"] or "").lower() != "hevc" else 0
        total_size += size
        total_saved += est
        if len(items) < limit:
            items.append({"file_id": info["id"], "filename": info["filename"], "path": info["path"],
                          "codec": info["codec"], "height": info["height"], "size_bytes": size,
                          "rule_id": rule.get("id"), "rule_name": rule["name"], "actions": types,
                          "est_saved_bytes": est})
    return {"count": len(items), "total_size_bytes": total_size,
            "est_saved_bytes": total_saved, "encode_ratio": round(ratio, 3), "items": items}


def test_path(db: Session, path: str) -> dict:
    """Check a qBittorrent path: where it maps to, what's there, and which rule would apply."""
    local = map_path(db, path)
    exists = os.path.exists(local)
    files = find_video_files(local)[:20] if exists else []
    rules = load_rules(db)
    results = []
    for f in files[:10]:
        info, err = codec_detector.probe_with_error(f)
        if info is None:
            results.append({"path": f, "error": err})
            continue
        d = {"path": f, "filename": os.path.basename(f), "codec": info.codec, "height": info.height,
             "size_bytes": info.size_bytes, "duration_sec": info.duration_sec,
             "is_anthology": scanner._is_anthology(os.path.basename(f), info.size_bytes, info.duration_sec)}
        rule = choose_rule(rules, d, "download")
        results.append({**d, "rule_name": rule["name"] if rule else None,
                        "actions": [a["type"] for a in rule["actions"]] if rule else []})
    return {"path": path, "mapped": local, "exists": exists, "video_files": len(files), "files": results}
