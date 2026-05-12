"""
download_etl.py — Agent Dhara Phase 2
FastAPI router: endpoints for downloading generated ETL code files.

Mount in main app with:
    from api.download_etl import router as etl_download_router
    app.include_router(etl_download_router, prefix='/api')
"""

import json
import os
from datetime import datetime
from pathlib import Path
from typing import Optional

from fastapi import APIRouter, HTTPException, Query
from fastapi.responses import FileResponse, JSONResponse, PlainTextResponse

# Adjust this path to match your project structure
OUTPUT_DIR = Path("output/etl_code")
OUTPUT_DIR.mkdir(parents=True, exist_ok=True)

router = APIRouter(tags=["ETL Code Download"])


# ── Helper: save generated code to disk ────────────────────────────────────────

def save_etl_code(
    code: str,
    dataset_name: str,
    engine: str,
    plan_id: str,
    extension: str = ".py"
) -> Path:
    """
    Saves generated ETL code string to output/etl_code/ and returns the file path.
    Called by etl_graph_nodes.py after codegen is validated.
    """
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    safe_name = dataset_name.lower().replace(" ", "_").replace("-", "_")
    filename = f"{safe_name}_{engine}_{plan_id}_{timestamp}{extension}"
    filepath = OUTPUT_DIR / filename
    filepath.write_text(code, encoding="utf-8")
    return filepath


def save_etl_manifest(plan: dict, plan_id: str) -> Path:
    """
    Saves the full ETL Plan JSON to output/etl_code/ for audit trail.
    """
    filepath = OUTPUT_DIR / f"plan_{plan_id}.json"
    filepath.write_text(json.dumps(plan, indent=2, default=str), encoding="utf-8")
    return filepath


# ── GET /api/etl-code/list ──────────────────────────────────────────────────────

@router.get("/etl-code/list")
async def list_etl_files():
    """
    Returns a list of all generated ETL code files with metadata.
    """
    if not OUTPUT_DIR.exists():
        return JSONResponse({"files": []})

    files = []
    for f in sorted(OUTPUT_DIR.iterdir(), key=lambda x: x.stat().st_mtime, reverse=True):
        if f.is_file():
            files.append({
                "filename": f.name,
                "size_bytes": f.stat().st_size,
                "created_at": datetime.fromtimestamp(f.stat().st_mtime).isoformat(),
                "engine": _infer_engine(f.name),
                "download_url": f"/api/etl-code/download?filename={f.name}",
                "preview_url": f"/api/etl-code/preview?filename={f.name}",
            })
    return JSONResponse({"files": files, "count": len(files)})


# ── GET /api/etl-code/download ─────────────────────────────────────────────────

@router.get("/etl-code/download")
async def download_etl_file(
    filename: str = Query(..., description="Filename from /etl-code/list")
):
    """
    Downloads a generated ETL code file by filename.
    """
    filepath = OUTPUT_DIR / filename
    if not filepath.exists() or not filepath.is_file():
        raise HTTPException(status_code=404, detail=f"File '{filename}' not found.")

    # Security: prevent path traversal
    if not str(filepath.resolve()).startswith(str(OUTPUT_DIR.resolve())):
        raise HTTPException(status_code=403, detail="Access denied.")

    media_type = "application/json" if filename.endswith(".json") else "text/plain"
    return FileResponse(
        path=str(filepath),
        filename=filename,
        media_type=media_type,
    )


# ── GET /api/etl-code/preview ──────────────────────────────────────────────────

@router.get("/etl-code/preview")
async def preview_etl_file(
    filename: str = Query(..., description="Filename to preview"),
    lines: int = Query(50, ge=1, le=300, description="Number of lines to return")
):
    """
    Returns first N lines of a generated ETL code file as plain text.
    Used by the frontend chat UI to show code preview in-chat.
    """
    filepath = OUTPUT_DIR / filename
    if not filepath.exists():
        raise HTTPException(status_code=404, detail=f"File '{filename}' not found.")

    if not str(filepath.resolve()).startswith(str(OUTPUT_DIR.resolve())):
        raise HTTPException(status_code=403, detail="Access denied.")

    content = filepath.read_text(encoding="utf-8")
    preview_lines = content.splitlines()[:lines]
    total_lines = len(content.splitlines())
    truncated = total_lines > lines

    return JSONResponse({
        "filename": filename,
        "preview": "\n".join(preview_lines),
        "lines_shown": len(preview_lines),
        "total_lines": total_lines,
        "truncated": truncated,
        "download_url": f"/api/etl-code/download?filename={filename}"
    })


# ── GET /api/etl-code/latest ───────────────────────────────────────────────────

@router.get("/etl-code/latest")
async def get_latest_etl_file(
    engine: Optional[str] = Query(None, description="Filter by engine: python | sql | pyspark")
):
    """
    Returns metadata + preview of the most recently generated ETL file.
    Optionally filter by engine type.
    """
    if not OUTPUT_DIR.exists():
        raise HTTPException(status_code=404, detail="No ETL files generated yet.")

    files = [
        f for f in OUTPUT_DIR.iterdir()
        if f.is_file() and not f.name.startswith("plan_")
    ]

    if engine:
        files = [f for f in files if engine.lower() in f.name.lower()]

    if not files:
        raise HTTPException(status_code=404, detail="No matching ETL files found.")

    latest = max(files, key=lambda f: f.stat().st_mtime)
    content = latest.read_text(encoding="utf-8")
    preview = "\n".join(content.splitlines()[:40])

    return JSONResponse({
        "filename": latest.name,
        "engine": _infer_engine(latest.name),
        "size_bytes": latest.stat().st_size,
        "created_at": datetime.fromtimestamp(latest.stat().st_mtime).isoformat(),
        "total_lines": len(content.splitlines()),
        "preview": preview,
        "download_url": f"/api/etl-code/download?filename={latest.name}",
    })


# ── DELETE /api/etl-code/delete ────────────────────────────────────────────────

@router.delete("/etl-code/delete")
async def delete_etl_file(
    filename: str = Query(..., description="Filename to delete")
):
    """
    Deletes a specific generated ETL code file.
    """
    filepath = OUTPUT_DIR / filename
    if not filepath.exists():
        raise HTTPException(status_code=404, detail=f"File '{filename}' not found.")

    if not str(filepath.resolve()).startswith(str(OUTPUT_DIR.resolve())):
        raise HTTPException(status_code=403, detail="Access denied.")

    filepath.unlink()
    return JSONResponse({"message": f"Deleted '{filename}' successfully."})


# ── Internal helper ─────────────────────────────────────────────────────────────

def _infer_engine(filename: str) -> str:
    name = filename.lower()
    if "pyspark" in name:
        return "pyspark"
    elif "sql" in name:
        return "sql"
    elif ".py" in name:
        return "python"
    elif ".json" in name:
        return "plan"
    return "unknown"
