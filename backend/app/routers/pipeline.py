"""/api/pipeline — auto-pipeline rules, path mappings, settings, dry-run and apply."""
import json
import os

from fastapi import APIRouter, Body, Depends, HTTPException
from sqlalchemy.orm import Session

from ..database import get_db
from ..models import PathMapping, PipelineRule, Setting
from ..services import pipeline

router = APIRouter(prefix="/pipeline", tags=["pipeline"])

# One-click starting points for the rules editor.
TEMPLATES = [
    {"name": "Shrink to x265", "match": {"source": "any", "codecs": ["h264", "mpeg4", "mpeg2video", "vc1"]},
     "actions": [{"type": "encode", "resolution": "original"}],
     "description": "Re-encode older codecs to HEVC — typically 40–60 % smaller."},
    {"name": "4K → 1080p x265", "match": {"source": "any", "min_height": 1500},
     "actions": [{"type": "encode", "resolution": "1080p"}],
     "description": "Downscale 4K files to 1080p HEVC to save a lot of space."},
    {"name": "Split anthologies", "match": {"source": "any", "anthology": True},
     "actions": [{"type": "split", "episode_count": 0}],
     "description": "Cut long compilation files into one file per scene."},
    {"name": "Trim intro, then shrink", "match": {"source": "download", "codecs": ["h264"]},
     "actions": [{"type": "trim"}, {"type": "encode", "resolution": "original"}],
     "description": "Cut the first 3.5 s (studio bumper) and re-encode to HEVC."},
]


def _parse_rule(body: dict) -> tuple[str, dict, list, bool, int | None]:
    name = (body.get("name") or "").strip()
    if not name:
        raise HTTPException(status_code=400, detail="Give the rule a name")
    match, actions = body.get("match") or {}, body.get("actions") or []
    err = pipeline.validate(match, actions)
    if err:
        raise HTTPException(status_code=400, detail=err)
    return name, match, actions, bool(body.get("enabled", True)), body.get("priority")


# ── Settings ──────────────────────────────────────────────────────────────────

@router.get("/settings")
def get_settings(db: Session = Depends(get_db)):
    return {k: v == "true" for k, v in pipeline.get_settings(db).items()}


@router.put("/settings")
def put_settings(body: dict = Body(...), db: Session = Depends(get_db)):
    for key, val in body.items():
        if key not in pipeline.SETTINGS_DEFAULTS:
            continue
        row = db.get(Setting, key) or Setting(key=key)
        row.value = "true" if val in (True, "true", "1", 1) else "false"
        db.merge(row)
    db.commit()
    return get_settings(db)


# ── Rename & organise ─────────────────────────────────────────────────────────

def _organise_status(db: Session, values: dict) -> dict:
    """The settings plus a warning per folder that won't work."""
    from ..services import folders, organise
    roots = folders.scan_roots(db)
    warnings = {}
    for key, label in (("organise_tv_dir", "TV"), ("organise_movie_dir", "Movies"),
                       ("organise_tv_mature_dir", "Mature TV"), ("organise_movie_mature_dir", "Mature movie")):
        path = (values.get(key) or "").strip()
        if not path:
            continue
        if not os.path.isdir(path):
            warnings[key] = f"{path} doesn't exist in MediaMate's container. Mount it, or check Settings → Folders → Test."
        elif not any(path == r or path.startswith(r.rstrip("/") + "/") for r in roots):
            warnings[key] = (f"{path} isn't inside a folder with Scan & encode on, so the Library would lose "
                             f"track of organised {label.lower()} files. Add it in Settings → Folders.")
    for kind in ("tv", "movie"):
        reg = (values.get(f"organise_{kind}_dir") or "").strip().rstrip("/")
        mat = (values.get(f"organise_{kind}_mature_dir") or "").strip().rstrip("/")
        if reg and mat and (reg == mat or mat.startswith(reg + "/") or reg.startswith(mat + "/")):
            warnings[f"organise_{kind}_mature_dir"] = (
                "Use a separate folder (not the same one, or one inside the other), so Plex can show it "
                "to adults only.")
    from ..services.show_metadata import tmdb_key
    no_key = organise.mature_folders_set(values) and not tmdb_key(db)
    return {**{k: values.get(k, v) for k, v in organise.SETTINGS_DEFAULTS.items()}, "warnings": warnings,
            "ratings_need_key": no_key}


@router.get("/organise")
def get_organise(db: Session = Depends(get_db)):
    from ..services import organise
    return _organise_status(db, organise.get_settings(db))


@router.put("/organise")
def put_organise(body: dict = Body(...), db: Session = Depends(get_db)):
    from ..services import organise
    corrected = []
    for key in organise.SETTINGS_DEFAULTS:
        if key in body and isinstance(body[key], str):
            value = body[key].strip()
            if key.endswith("_template") and value:
                value, changed = organise.fix_template(value)
                if changed:
                    corrected.append(key)
            row = db.get(Setting, key) or Setting(key=key)
            row.value = value
            db.merge(row)
    db.commit()
    out = _organise_status(db, organise.get_settings(db))
    for key in corrected:
        out["warnings"][key] = ("Changed to end with {ext}: renamed files always keep their own extension "
                                "(an .mp4 stays .mp4).")
    return out


@router.post("/organise/example")
def organise_example(body: dict = Body(...), db: Session = Depends(get_db)):
    """Where a file name would go with these (possibly unsaved) settings."""
    from ..services import organise
    settings = organise.get_settings(db)
    settings.update({k: v for k, v in body.items() if k in organise.SETTINGS_DEFAULTS and isinstance(v, str) and v})
    name = str(body.get("filename") or "")
    try:
        from ..services import ratings
        parsed = organise.parse("/x/" + os.path.basename(name))
        aud, why = (ratings.audience(db, parsed.kind, body.get("show") or parsed.title, parsed.year)
                    if organise.mature_folders_set(settings) else (ratings.GENERAL, ""))
        dest, parsed = organise.target_for(settings, "/x/" + os.path.basename(name), body.get("show") or None, aud)
        return {"ok": True, "target": dest, "kind": parsed.kind, "audience": aud, "reason": why}
    except (organise.OrganiseError, KeyError, ValueError, IndexError) as exc:
        msg = str(exc) if isinstance(exc, organise.OrganiseError) else f"The template has a problem: {exc}"
        return {"ok": False, "error": msg}


# ── Rules ─────────────────────────────────────────────────────────────────────

@router.get("/templates")
def templates():
    return TEMPLATES


@router.get("/rules")
def list_rules(db: Session = Depends(get_db)):
    return pipeline.load_rules(db)


@router.post("/rules", status_code=201)
def create_rule(body: dict = Body(...), db: Session = Depends(get_db)):
    name, match, actions, enabled, priority = _parse_rule(body)
    if priority is None:
        last = db.query(PipelineRule).order_by(PipelineRule.priority.desc()).first()
        priority = (last.priority + 10) if last else 10
    rule = PipelineRule(name=name, match=json.dumps(match), actions=json.dumps(actions),
                        enabled=enabled, priority=int(priority))
    db.add(rule)
    db.commit()
    return pipeline.rule_dict(rule)


@router.put("/rules/{rule_id}")
def update_rule(rule_id: int, body: dict = Body(...), db: Session = Depends(get_db)):
    rule = db.get(PipelineRule, rule_id)
    if rule is None:
        raise HTTPException(status_code=404, detail="Rule not found")
    name, match, actions, enabled, priority = _parse_rule(body)
    rule.name, rule.match, rule.actions, rule.enabled = name, json.dumps(match), json.dumps(actions), enabled
    if priority is not None:
        rule.priority = int(priority)
    db.commit()
    return pipeline.rule_dict(rule)


@router.delete("/rules/{rule_id}")
def delete_rule(rule_id: int, db: Session = Depends(get_db)):
    rule = db.get(PipelineRule, rule_id)
    if rule is None:
        raise HTTPException(status_code=404, detail="Rule not found")
    db.delete(rule)
    db.commit()
    return {"ok": True}


@router.post("/rules/reorder")
def reorder_rules(body: dict = Body(...), db: Session = Depends(get_db)):
    """Set evaluation order: the first rule that matches a file wins."""
    ids = body.get("ids") or []
    rules = {r.id: r for r in db.query(PipelineRule).all()}
    if sorted(ids) != sorted(rules):
        raise HTTPException(status_code=400, detail="ids must list every rule exactly once")
    for n, rid in enumerate(ids):
        rules[rid].priority = (n + 1) * 10
    db.commit()
    return pipeline.load_rules(db)


# ── Path mappings ─────────────────────────────────────────────────────────────

@router.get("/mappings")
def list_mappings(db: Session = Depends(get_db)):
    return [{"id": m.id, "remote_prefix": m.remote_prefix, "local_prefix": m.local_prefix}
            for m in db.query(PathMapping).order_by(PathMapping.id)]


@router.put("/mappings")
def replace_mappings(body: list = Body(...), db: Session = Depends(get_db)):
    rows = []
    for m in body:
        remote, local = (m.get("remote_prefix") or "").strip(), (m.get("local_prefix") or "").strip()
        if not remote or not local:
            raise HTTPException(status_code=400, detail="Each mapping needs both paths")
        if not remote.startswith("/") or not local.startswith("/"):
            raise HTTPException(status_code=400, detail="Use absolute paths (starting with /)")
        rows.append(PathMapping(remote_prefix=remote, local_prefix=local))
    db.query(PathMapping).delete()
    db.add_all(rows)
    db.commit()
    return list_mappings(db)


@router.post("/test-path")
def test_path(body: dict = Body(...), db: Session = Depends(get_db)):
    path = (body.get("path") or "").strip()
    if not path:
        raise HTTPException(status_code=400, detail="Enter a path as qBittorrent sees it")
    return pipeline.test_path(db, path)


# ── Dry run & apply ───────────────────────────────────────────────────────────

@router.post("/preview")
def preview(body: dict = Body(default={}), db: Session = Depends(get_db)):
    """Which library files would the rules process, and roughly how much space it'd save.
    Pass {"rule": {...}} to preview an unsaved rule on its own."""
    rule = body.get("rule")
    if rule is not None:
        err = pipeline.validate(rule.get("match") or {}, rule.get("actions") or [])
        if err:
            raise HTTPException(status_code=400, detail=err)
        rules = [{"id": rule.get("id"), "name": rule.get("name") or "Preview", "enabled": True,
                  "match": rule.get("match") or {}, "actions": rule.get("actions") or []}]
    else:
        rules = None
    return pipeline.preview(db, rules)


@router.post("/apply")
def apply(body: dict = Body(...), db: Session = Depends(get_db)):
    """Queue the rules for these library files (from the preview's 'Apply')."""
    ids = [int(i) for i in body.get("file_ids") or []]
    if not ids:
        raise HTTPException(status_code=400, detail="No files selected")
    rule = None
    if body.get("rule_id") is not None:
        r = db.get(PipelineRule, int(body["rule_id"]))
        if r is None:
            raise HTTPException(status_code=404, detail="Rule not found")
        rule = pipeline.rule_dict(r) | {"enabled": True}
    n = pipeline.apply_to_files(db.get_bind(), ids, source="scan", rule_override=rule)
    return {"ok": True, "queued": n}
