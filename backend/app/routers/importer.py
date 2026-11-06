"""/api/import — bring an AuTorrent and/or FileSplitter database into MediaMate."""
from fastapi import APIRouter, Body, File, HTTPException, UploadFile
from fastapi.concurrency import run_in_threadpool

from ..database import DATABASE_URL, engine
from ..services import importer

router = APIRouter(prefix="/import", tags=["import"])


@router.post("/inspect")
async def inspect(files: list[UploadFile] = File(...)):
    """Upload one or two databases; reports what each one contains. Nothing is changed yet."""
    out = []
    for f in files:
        try:
            token = await run_in_threadpool(importer.save_upload, f.file)
            info = await run_in_threadpool(importer.inspect_upload, token)
            out.append({"filename": f.filename, **info})
        except importer.ImportFailed as exc:
            out.append({"filename": f.filename, "error": str(exc)})
    return out


@router.post("/suggest-paths")
def suggest_paths(body: dict = Body(...)):
    """Where the old apps' folders are on this server, worked out from the uploads."""
    tokens = body.get("tokens") or []
    if not tokens:
        raise HTTPException(status_code=400, detail="Upload a database first")
    try:
        return importer.suggest_paths(engine, tokens)
    except importer.ImportFailed as exc:
        raise HTTPException(status_code=400, detail=str(exc))


@router.post("/compare-settings")
def compare_settings(body: dict = Body(...)):
    """Settings from the uploads that differ from this install's, side by side."""
    tokens = body.get("tokens") or []
    if not tokens:
        raise HTTPException(status_code=400, detail="Upload a database first")
    try:
        return importer.compare_settings(engine, tokens)
    except importer.ImportFailed as exc:
        raise HTTPException(status_code=400, detail=str(exc))


@router.post("/run")
def run(body: dict = Body(...)):
    tokens = body.get("tokens") or []
    if not tokens:
        raise HTTPException(status_code=400, detail="Upload a database first")
    rewrite = None
    rw = body.get("path_rewrite") or {}
    if rw.get("from") and rw.get("to"):
        if not (rw["from"].startswith("/") and rw["to"].startswith("/")):
            raise HTTPException(status_code=400, detail="Use absolute paths for the folder change")
        rewrite = (rw["from"], rw["to"])
    mappings = []
    for m in body.get("path_mappings") or []:
        remote, local = (m.get("from") or "").strip(), (m.get("to") or "").strip()
        if not (remote.startswith("/") and local.startswith("/")):
            raise HTTPException(status_code=400, detail="Path mappings need absolute paths")
        mappings.append((remote, local))
    raw_choices = body.get("settings_choices")
    choices = {str(k): bool(v) for k, v in raw_choices.items()} if isinstance(raw_choices, dict) else None
    db_path = DATABASE_URL.replace("sqlite:///", "", 1) if DATABASE_URL.startswith("sqlite") else None
    try:
        return importer.run(engine, tokens,
                            overwrite_settings=bool(body.get("overwrite_settings")),
                            rewrite=rewrite, db_path=db_path, mappings=mappings,
                            choices=choices)
    except importer.ImportFailed as exc:
        raise HTTPException(status_code=400, detail=str(exc))
