"""
FastAPI router for ETL code generation — Agent Dhara.

Mounted in mcp_server.py via:
    from agent.etl_endpoint import etl_router
    app.include_router(etl_router)

Endpoints:
    POST /generate_etl_code   → build & return ETL plan for human review
    POST /approve_etl_code    → approve plan → generate + validate + save code
    GET  /etl_status/{sid}    → check if a pending plan exists for a session
"""
from __future__ import annotations

import json
import logging
import os
from pathlib import Path
from typing import Any, Dict, List, Optional

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from agent.etl_graph_nodes import (
    capture_etl_intent_node,
    capture_business_rules_node,
    load_manifest_node,
    classify_issues_node,
    ambiguity_check_node,
    planning_node,
    validate_dag_node,
    schema_lineage_node,
    plan_presenter_node,
    human_review_node,
    codegen_node,
    code_validation_node,
    output_node,
)

logger = logging.getLogger(__name__)
etl_router = APIRouter(tags=["etl"])

# ── report paths ──────────────────────────────────────────────────────────────
_HERE = Path(__file__).parent.parent          # Agent Dhara Backend/
REPORTS_DIR = _HERE / "output" / "reports"
ETL_OUTPUT_DIR = _HERE / "output" / "etl_code"
ETL_OUTPUT_DIR.mkdir(parents=True, exist_ok=True)

# ── in-memory plan cache (session_id → state dict) ───────────────────────────
_plan_cache: Dict[str, Dict[str, Any]] = {}


# ── helpers ───────────────────────────────────────────────────────────────────

def _load_latest_assessment() -> Dict[str, Any]:
    """Load the latest assessment result from output/reports/report.json."""
    report_path = REPORTS_DIR / "report.json"
    if report_path.exists():
        try:
            with open(report_path, "r", encoding="utf-8") as f:
                return json.load(f)
        except Exception as exc:
            logger.warning("Failed to read report.json: %s", exc)
    return {}


def _run_plan_nodes(state: Dict[str, Any]) -> Dict[str, Any]:
    """Run nodes 1-10 (plan building only, no code generation)."""
    state = capture_etl_intent_node(state)
    if state.get("etl_stage") == "no_assessment":
        return state

    state = capture_business_rules_node(state)
    state = load_manifest_node(state)
    state = classify_issues_node(state)

    if state.get("etl_stage") == "blocked":
        return state

    state = ambiguity_check_node(state)
    if state.get("etl_stage") == "awaiting_clarification":
        return state

    state = planning_node(state)
    state = validate_dag_node(state)

    if state.get("etl_stage") in ("dag_validation_failed",):
        return state

    state = schema_lineage_node(state)
    state = plan_presenter_node(state)
    return state


def _run_codegen_nodes(state: Dict[str, Any]) -> Dict[str, Any]:
    """Run nodes 12-14 (code generation + validation + output)."""
    state = codegen_node(state)
    state = code_validation_node(state)

    if state.get("etl_stage") == "codegen_retry":
        state = codegen_node(state)
        state = code_validation_node(state)

    if state.get("etl_stage") == "validation_failed":
        return state

    state = output_node(state)
    return state


# ── request / response models ─────────────────────────────────────────────────

class ETLRequest(BaseModel):
    session_id: Optional[str] = "default"
    engine: str = "python"          # python | sql | pyspark | adf
    target: str = "local_file"      # local_file | sql_table | blob_file
    target_path: Optional[str] = None
    message: str = "generate etl code"


class ETLApproveRequest(BaseModel):
    session_id: Optional[str] = "default"
    user_reply: str = "approve"     # approve | cancel | <modification text>


class ETLClarifyRequest(BaseModel):
    session_id: Optional[str] = "default"
    answer: str


# ── endpoints ─────────────────────────────────────────────────────────────────

@etl_router.post("/generate_etl_code")
def generate_etl_code(req: ETLRequest) -> Dict[str, Any]:
    """
    Phase 1 — build the ETL plan and return it for human review.
    The frontend must call /approve_etl_code to trigger actual code generation.
    """
    assessment = _load_latest_assessment()
    if not assessment:
        raise HTTPException(
            status_code=400,
            detail=(
                "No assessment found. "
                "Run a data quality assessment first (use 'run assessment' in the chat)."
            ),
        )

    cache_key = (req.session_id or "default").strip() or "default"

    state: Dict[str, Any] = {
        "user_message": req.message,
        "last_assessment_result": assessment,
        "etl_intent": {
            "engine": req.engine,
            "target": req.target,
            "target_path": req.target_path,
        },
        # pre-confirm target so we don't block on interactive confirmation
        "target_confirmed": True,
        "output_target": req.target,
        "output_target_path": req.target_path or str(ETL_OUTPUT_DIR),
    }

    try:
        state = _run_plan_nodes(state)
    except Exception as exc:
        logger.exception("ETL plan generation failed")
        raise HTTPException(status_code=500, detail=str(exc))

    stage = state.get("etl_stage", "")

    if stage == "no_assessment":
        return {"status": "error", "message": state.get("assistant_message", "No assessment found.")}

    if stage == "blocked":
        return {"status": "blocked", "message": state.get("assistant_message", "")}

    if stage == "awaiting_clarification":
        _plan_cache[cache_key] = state
        return {
            "status": "needs_clarification",
            "message": state.get("assistant_message", ""),
            "questions": state.get("clarification_questions", []),
            "session_key": cache_key,
        }

    if stage == "dag_validation_failed":
        return {"status": "error", "message": state.get("assistant_message", "")}

    # Cache plan for approval
    _plan_cache[cache_key] = state

    return {
        "status": "plan_ready",
        "message": state.get("assistant_message", ""),
        "etl_plan": state.get("etl_plan", {}),
        "schema_lineage": state.get("schema_lineage", {}),
        "classified_issues": state.get("classified_issues", {}),
        "manual_review_items": state.get("manual_review_items", []),
        "engine": req.engine,
        "session_key": cache_key,
    }


@etl_router.post("/approve_etl_code")
def approve_etl_code(req: ETLApproveRequest) -> Dict[str, Any]:
    """
    Phase 2 — human approved (or cancelled/modified) the plan.
    On approval: generate, validate, and save ETL code.
    On modification: rebuild plan with overrides and return updated plan.
    On cancel: discard plan.
    """
    cache_key = (req.session_id or "default").strip() or "default"
    state = _plan_cache.get(cache_key)

    if not state:
        raise HTTPException(
            status_code=400,
            detail="No pending ETL plan for this session. Call /generate_etl_code first.",
        )

    state["user_message"] = req.user_reply

    try:
        state = human_review_node(state)
    except Exception as exc:
        logger.exception("human_review_node failed")
        raise HTTPException(status_code=500, detail=str(exc))

    stage = state.get("etl_stage", "")

    # ── cancelled ──────────────────────────────────────────────────────────────
    if stage == "cancelled":
        _plan_cache.pop(cache_key, None)
        return {"status": "cancelled", "message": state.get("assistant_message", "ETL generation cancelled.")}

    # ── modification requested ─────────────────────────────────────────────────
    if stage == "modification_requested":
        try:
            state = planning_node(state)
            state = validate_dag_node(state)
            state = schema_lineage_node(state)
            state = plan_presenter_node(state)
        except Exception as exc:
            logger.exception("Re-planning failed")
            raise HTTPException(status_code=500, detail=str(exc))
        _plan_cache[cache_key] = state
        return {
            "status": "plan_updated",
            "message": state.get("assistant_message", ""),
            "etl_plan": state.get("etl_plan", {}),
            "session_key": cache_key,
        }

    # ── approved → generate code ───────────────────────────────────────────────
    try:
        state = _run_codegen_nodes(state)
    except Exception as exc:
        logger.exception("Code generation failed")
        raise HTTPException(status_code=500, detail=str(exc))

    if state.get("etl_stage") == "validation_failed":
        _plan_cache.pop(cache_key, None)
        return {
            "status": "error",
            "message": state.get("assistant_message", "Code validation failed."),
            "validation_errors": state.get("validation_errors", []),
        }

    _plan_cache.pop(cache_key, None)

    return {
        "status": "success",
        "message": state.get("assistant_message", "ETL code generated successfully."),
        "saved_files": state.get("saved_etl_files", []),
        "generated_code": state.get("generated_code", {}),
        "validation_results": state.get("validation_results", {}),
        "output_dir": str(ETL_OUTPUT_DIR),
    }


@etl_router.post("/clarify_etl")
def clarify_etl(req: ETLClarifyRequest) -> Dict[str, Any]:
    """
    Provide a clarification answer when /generate_etl_code returned status='needs_clarification'.
    Resumes planning from the ambiguity_check_node with the answer injected.
    """
    cache_key = (req.session_id or "default").strip() or "default"
    state = _plan_cache.get(cache_key)

    if not state:
        raise HTTPException(
            status_code=400,
            detail="No pending clarification state for this session. Call /generate_etl_code first.",
        )

    state["user_message"] = req.answer
    state["clarification_answer"] = req.answer
    state.pop("etl_stage", None)  # reset stage to allow replanning

    try:
        state = _run_plan_nodes(state)
    except Exception as exc:
        logger.exception("Clarification re-planning failed")
        raise HTTPException(status_code=500, detail=str(exc))

    _plan_cache[cache_key] = state

    stage = state.get("etl_stage", "")
    if stage in ("blocked", "dag_validation_failed"):
        return {"status": "error", "message": state.get("assistant_message", "")}

    return {
        "status": "plan_ready",
        "message": state.get("assistant_message", ""),
        "etl_plan": state.get("etl_plan", {}),
        "schema_lineage": state.get("schema_lineage", {}),
        "session_key": cache_key,
    }


@etl_router.get("/etl_status/{session_id}")
def etl_status(session_id: str) -> Dict[str, Any]:
    """Check whether a pending ETL plan exists for a session."""
    plan = _plan_cache.get(session_id, {})
    return {
        "has_pending_plan": bool(plan),
        "etl_stage": plan.get("etl_stage"),
        "engine": (plan.get("etl_intent") or {}).get("engine"),
        "dataset_count": len((plan.get("etl_plan") or {}).get("datasets", {})),
    }


@etl_router.get("/etl_files")
def list_etl_files() -> Dict[str, Any]:
    """List all generated ETL code files in the output directory."""
    try:
        files = [
            {
                "name": f.name,
                "size_bytes": f.stat().st_size,
                "modified": f.stat().st_mtime,
                "path": str(f),
            }
            for f in sorted(ETL_OUTPUT_DIR.glob("*"), key=lambda p: p.stat().st_mtime, reverse=True)
            if f.is_file()
        ]
        return {"ok": True, "files": files, "output_dir": str(ETL_OUTPUT_DIR)}
    except Exception as exc:
        raise HTTPException(status_code=500, detail=str(exc))


@etl_router.get("/etl_files/{filename}")
def get_etl_file(filename: str) -> Dict[str, Any]:
    """Return the content of a generated ETL file by name."""
    # Sanitise: no path traversal
    safe_name = Path(filename).name
    file_path = ETL_OUTPUT_DIR / safe_name
    if not file_path.exists() or not file_path.is_file():
        raise HTTPException(status_code=404, detail=f"File '{safe_name}' not found.")
    try:
        content = file_path.read_text(encoding="utf-8")
        return {"ok": True, "filename": safe_name, "content": content}
    except Exception as exc:
        raise HTTPException(status_code=500, detail=str(exc))
