"""
etl_router_patch.py
====================
Patch module for Agent Dhara ETL routing.

PROBLEM
-------
The _MASTER_SYSTEM prompt in chat_graph.py has a **closed list** of 25 allowed
actions. ETL-related intents ("generate ETL code", "build transformation",
"generate pipeline") are not in that list, so the LLM router silently falls
through to `help` or returns the previous response unchanged.

SOLUTION (3 parts)
------------------
1. ETL_ACTIONS_ADDENDUM  — a string block to APPEND to _MASTER_SYSTEM.
   Add these 8 new allowed actions + routing rules to the existing prompt.

2. route_etl_action()    — called inside the chat_graph dispatcher for any
   action that starts with "etl_". Delegates to etl_graph_nodes.

3. ETL_FLOW_OPTIONS      — the UI button set returned after ETL code is generated.

HOW TO INTEGRATE (chat_graph.py changes — minimal, surgical)
------------------------------------------------------------
Step A: Append ETL_ACTIONS_ADDENDUM to _MASTER_SYSTEM:

    _MASTER_SYSTEM = _MASTER_SYSTEM + ETL_ACTIONS_ADDENDUM

Step B: In the action dispatcher (the big if/elif block that handles
        action="list_sources", "dq_overview", etc.), add at the top:

    from agent.etl_router_patch import route_etl_action
    ...
    elif action.startswith("etl_"):
        return route_etl_action(action=action, args=args, session=session,
                                session_id=session_id)

That is the entire integration. No other files need changing.
"""

from __future__ import annotations

from typing import Any, Dict, Optional

# ---------------------------------------------------------------------------
# 1. ADDENDUM — append this to _MASTER_SYSTEM in chat_graph.py
# ---------------------------------------------------------------------------

ETL_ACTIONS_ADDENDUM = """

# ── ETL / TRANSFORMATION ACTIONS (added) ──────────────────────────────────
# These actions are available ONLY after a data quality assessment has been
# run and a report exists in the session.

Additional allowed actions for ETL / code generation:
etl_start              # User says "generate ETL", "build transformation", "generate code", "fix the data"
etl_set_engine         # User picks engine: "Python", "SQL", "PySpark", "ADF"
etl_set_target         # User picks output target: "overwrite", "new table", "file", "return DataFrame"
etl_confirm_plan       # User says "approve", "looks good", "yes generate", "confirm"
etl_modify_plan        # User says "change", "skip dedup", "don't drop nulls", "modify step"
etl_reject_plan        # User says "cancel", "don't generate", "abort"
etl_download_code      # User says "download", "save the code", "get the file"
etl_status             # User asks "what's the ETL status", "did the code generate"

ETL routing rules:
- If the user says "generate ETL code" / "generate transformations" / "build pipeline" /
  "fix the data" / "clean the data" / "write the code" → etl_start
- If a report exists and the user says "Python" / "SQL" / "PySpark" / "pandas" → etl_set_engine
- If the user says "overwrite" / "new file" / "save to" / "write to" → etl_set_target
- If the user says "approve" / "yes" / "confirm" / "looks good" after an ETL plan was shown → etl_confirm_plan
- If the user says "change" / "skip" / "don't" / "modify" after an ETL plan was shown → etl_modify_plan
- If the user says "cancel" / "abort" / "don't generate" → etl_reject_plan
- If the user says "download" / "save" / "get the code" → etl_download_code

ETL context rules:
- NEVER route to etl_* actions if no assessment result exists in the session.
  Instead reply with: {"action": "help", "args": {"hint": "no_assessment"}}
- etl_confirm_plan and etl_modify_plan are only valid when etl_plan_pending=true in session.
  Otherwise treat as etl_start.

ETL output schema examples:
{"action": "etl_start", "args": {}}
{"action": "etl_set_engine", "args": {"engine": "python"}}
{"action": "etl_set_target", "args": {"target": "new_file", "path": "output/cleaned/"}}
{"action": "etl_confirm_plan", "args": {}}
{"action": "etl_modify_plan", "args": {"instruction": "skip deduplication for customers"}}
{"action": "etl_reject_plan", "args": {}}
{"action": "etl_download_code", "args": {}}
{"action": "etl_status", "args": {}}
"""


# ---------------------------------------------------------------------------
# 2. ETL action dispatcher — called from chat_graph for action.startswith("etl_")
# ---------------------------------------------------------------------------

def route_etl_action(
    *,
    action: str,
    args: Dict[str, Any],
    session: Dict[str, Any],
    session_id: str,
) -> Dict[str, Any]:
    """
    Central dispatcher for all etl_* actions.
    Returns a dict with keys: reply, payload  (same shape as other chat_graph handlers).
    """
    try:
        from agent import etl_graph_nodes as egn  # type: ignore
    except ImportError:
        return _etl_error("ETL graph nodes module not found. Check agent/etl_graph_nodes.py.")

    # Guard: assessment must exist
    last_result = _get_last_assessment(session)
    if action != "etl_status" and last_result is None:
        return {
            "reply": (
                "⚠️ No data quality assessment found in the current session.\n"
                "Please run an assessment first (select your files/tables and "
                "click **Generate Report**), then come back to generate ETL code."
            ),
            "payload": {"step": "etl_no_assessment", "options": []},
        }

    # ── etl_start ──────────────────────────────────────────────────────────
    if action == "etl_start":
        return _handle_etl_start(egn=egn, session=session, session_id=session_id,
                                  last_result=last_result, args=args)

    # ── etl_set_engine ─────────────────────────────────────────────────────
    elif action == "etl_set_engine":
        engine = str(args.get("engine", "python")).lower()
        valid = {"python", "sql", "pyspark", "adf"}
        if engine not in valid:
            engine = "python"
        session.setdefault("etl_state", {})["engine"] = engine
        return {
            "reply": (
                f"✅ Engine set to **{engine.upper()}**.\n"
                "Now, where should the cleaned data go?\n"
                "- **Overwrite** the source\n"
                "- Write to a **new file / table** (specify path)\n"
                "- Just **return** as in-memory DataFrame (for notebooks)"
            ),
            "payload": {
                "step": "etl_set_target",
                "engine": engine,
                "options": _etl_target_options(),
            },
        }

    # ── etl_set_target ─────────────────────────────────────────────────────
    elif action == "etl_set_target":
        target = str(args.get("target", "new_file")).lower()
        path = args.get("path", "output/cleaned/")
        session.setdefault("etl_state", {})["target"] = target
        session["etl_state"]["target_path"] = path
        # Now build the plan
        return _build_and_present_plan(
            egn=egn, session=session, session_id=session_id, last_result=last_result
        )

    # ── etl_confirm_plan ───────────────────────────────────────────────────
    elif action == "etl_confirm_plan":
        return _handle_etl_confirm(egn=egn, session=session, session_id=session_id)

    # ── etl_modify_plan ────────────────────────────────────────────────────
    elif action == "etl_modify_plan":
        instruction = str(args.get("instruction", ""))
        session.setdefault("etl_state", {})["modify_instruction"] = instruction
        # Re-build plan with modification hint
        return _build_and_present_plan(
            egn=egn, session=session, session_id=session_id,
            last_result=last_result, modify_hint=instruction
        )

    # ── etl_reject_plan ────────────────────────────────────────────────────
    elif action == "etl_reject_plan":
        session.pop("etl_state", None)
        return {
            "reply": "🚫 ETL code generation cancelled. Your data has not been modified.",
            "payload": {"step": "etl_cancelled", "options": []},
        }

    # ── etl_download_code ──────────────────────────────────────────────────
    elif action == "etl_download_code":
        return _handle_etl_download(session=session)

    # ── etl_status ─────────────────────────────────────────────────────────
    elif action == "etl_status":
        return _handle_etl_status(session=session)

    else:
        return _etl_error(f"Unknown ETL action: {action}")


# ---------------------------------------------------------------------------
# 3. UI button sets
# ---------------------------------------------------------------------------

def ETL_ENGINE_OPTIONS() -> list:
    return [
        {"id": "etl_engine_python", "text": "🐍 Python (pandas)", "send": "Python"},
        {"id": "etl_engine_sql",    "text": "🗄️ SQL",             "send": "SQL"},
        {"id": "etl_engine_spark",  "text": "⚡ PySpark",         "send": "PySpark"},
        {"id": "etl_cancel",        "text": "✖ Cancel",          "send": "cancel ETL"},
    ]


def _etl_target_options() -> list:
    return [
        {"id": "etl_target_overwrite", "text": "♻️ Overwrite source",       "send": "overwrite"},
        {"id": "etl_target_newfile",   "text": "📁 New file (output/cleaned/)","send": "new file"},
        {"id": "etl_target_memory",    "text": "🧠 Return DataFrame only",   "send": "return DataFrame"},
    ]


def ETL_POST_CODEGEN_OPTIONS() -> list:
    return [
        {"id": "etl_download",  "text": "⬇️ Download code",     "send": "download ETL code"},
        {"id": "etl_restart",   "text": "🔄 Generate another",  "send": "generate ETL code"},
        {"id": "back",          "text": "🔙 Back to report",    "send": "back"},
    ]


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------

def _get_last_assessment(session: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    """Return the latest assessment result stored in the session, or None."""
    result = session.get("last_assessment_result")
    if isinstance(result, dict) and result.get("datasets"):
        return result
    # Fallback: check dq_result
    dq = session.get("dq_result")
    if isinstance(dq, dict) and dq.get("datasets"):
        return dq
    return None


def _handle_etl_start(
    *, egn, session: Dict[str, Any], session_id: str,
    last_result: Dict[str, Any], args: Dict[str, Any]
) -> Dict[str, Any]:
    """Ask the user to pick an engine — first step of ETL flow."""
    datasets = list((last_result.get("datasets") or {}).keys())
    ds_list = ", ".join(f"`{d}`" for d in datasets[:10])
    session["etl_state"] = {"started": True}
    return {
        "reply": (
            f"🚀 **ETL Code Generation**\n\n"
            f"Assessment found for: {ds_list}\n\n"
            "Which code engine do you want?\n"
            "- **Python** (pandas) — local scripts, notebooks\n"
            "- **SQL** — database transformations\n"
            "- **PySpark** — large-scale Spark clusters\n"
        ),
        "payload": {
            "step": "etl_choose_engine",
            "datasets": datasets,
            "options": ETL_ENGINE_OPTIONS(),
        },
    }


def _build_and_present_plan(
    *, egn, session: Dict[str, Any], session_id: str,
    last_result: Dict[str, Any], modify_hint: str = ""
) -> Dict[str, Any]:
    """Call etl_graph_nodes to build the ETL plan and present it to the user."""
    etl_state = session.get("etl_state", {})
    engine = etl_state.get("engine", "python")
    target = etl_state.get("target", "new_file")
    target_path = etl_state.get("target_path", "output/cleaned/")

    try:
        # etl_graph_nodes.build_etl_plan() must accept these kwargs
        plan = egn.build_etl_plan(
            assessment_result=last_result,
            engine=engine,
            target=target,
            target_path=target_path,
            modify_hint=modify_hint,
        )
    except Exception as exc:
        return _etl_error(f"ETL planner error: {exc}")

    # Store pending plan in session
    session["etl_state"]["pending_plan"] = plan
    session["etl_state"]["etl_plan_pending"] = True

    # Format plan as Markdown table for chat
    md = _format_plan_markdown(plan)
    return {
        "reply": (
            f"📋 **ETL Plan ({engine.upper()})** — please review:\n\n"
            + md
            + "\n\n"
            "👉 **Approve** to generate code, **Modify** to change steps, or **Cancel**."
        ),
        "payload": {
            "step": "etl_plan_review",
            "etl_plan": plan,
            "options": [
                {"id": "etl_approve", "text": "✅ Approve & Generate", "send": "approve ETL plan"},
                {"id": "etl_modify",  "text": "✏️ Modify plan",        "send": "modify ETL plan"},
                {"id": "etl_cancel",  "text": "✖ Cancel",             "send": "cancel ETL"},
            ],
        },
    }


def _handle_etl_confirm(
    *, egn, session: Dict[str, Any], session_id: str
) -> Dict[str, Any]:
    """Generate code from the approved plan."""
    etl_state = session.get("etl_state", {})
    plan = etl_state.get("pending_plan")
    if not plan:
        return _etl_error("No pending ETL plan found. Please start ETL generation again.")

    engine = etl_state.get("engine", "python")
    try:
        code_result = egn.generate_etl_code(plan=plan, engine=engine)
    except Exception as exc:
        return _etl_error(f"Code generation error: {exc}")

    # Store result
    session["etl_state"]["etl_plan_pending"] = False
    session["etl_state"]["generated_code"] = code_result
    session["etl_code_generated"] = True

    # Preview first 30 lines
    code_str = code_result.get("code", "") if isinstance(code_result, dict) else str(code_result)
    preview_lines = code_str.splitlines()[:30]
    preview = "\n".join(preview_lines)
    if len(code_str.splitlines()) > 30:
        preview += "\n... (truncated — download for full code)"

    return {
        "reply": (
            f"✅ **ETL code generated ({engine.upper()})!**\n\n"
            f"```{engine}\n{preview}\n```\n\n"
            "You can now download the complete script."
        ),
        "payload": {
            "step": "etl_code_ready",
            "engine": engine,
            "code_preview": preview,
            "full_code": code_str,
            "options": ETL_POST_CODEGEN_OPTIONS(),
        },
    }


def _handle_etl_download(session: Dict[str, Any]) -> Dict[str, Any]:
    """Return the generated code for download."""
    etl_state = session.get("etl_state", {})
    code_result = etl_state.get("generated_code")
    if not code_result:
        return _etl_error("No generated code found. Please generate ETL code first.")
    code_str = code_result.get("code", "") if isinstance(code_result, dict) else str(code_result)
    engine = etl_state.get("engine", "python")
    ext = {"python": "py", "sql": "sql", "pyspark": "py", "adf": "json"}.get(engine, "txt")
    return {
        "reply": f"⬇️ Your ETL script is ready to download (`etl_transform.{ext}`).",
        "payload": {
            "step": "etl_download",
            "engine": engine,
            "filename": f"etl_transform.{ext}",
            "full_code": code_str,
            "options": ETL_POST_CODEGEN_OPTIONS(),
        },
    }


def _handle_etl_status(session: Dict[str, Any]) -> Dict[str, Any]:
    """Report current ETL generation status."""
    etl_state = session.get("etl_state", {})
    if not etl_state:
        return {"reply": "No ETL session active. Say \"generate ETL code\" to start.",
                "payload": {"step": "etl_status_none", "options": []}}
    pending = etl_state.get("etl_plan_pending", False)
    generated = session.get("etl_code_generated", False)
    engine = etl_state.get("engine", "not set")
    status_lines = [
        f"**Engine**: {engine.upper() if engine != 'not set' else 'not set'}",
        f"**Plan pending approval**: {'Yes' if pending else 'No'}",
        f"**Code generated**: {'Yes ✅' if generated else 'No'}",
    ]
    return {
        "reply": "📊 **ETL Status**\n" + "\n".join(f"- {l}" for l in status_lines),
        "payload": {"step": "etl_status", "etl_state": etl_state, "options": []},
    }


def _format_plan_markdown(plan: Dict[str, Any]) -> str:
    """Format ETL plan dict as a Markdown step table."""
    if not isinstance(plan, dict):
        return str(plan)
    lines = []
    datasets = plan.get("datasets", {})
    for ds_name, ds_info in (datasets or {}).items():
        lines.append(f"**Dataset: `{ds_name}`**")
        lines.append("| # | Column | Action | Auto? | Notes |")
        lines.append("|---|--------|--------|-------|-------|")
        for step in (ds_info.get("steps") or []):
            col = step.get("column") or "(all)"
            action = step.get("action", "")
            auto = "✅" if step.get("auto", True) else "⚠️ Review"
            notes = step.get("notes", "")
            lines.append(f"| {step.get('order', '')} | `{col}` | `{action}` | {auto} | {notes} |")
        manual = ds_info.get("manual_review") or []
        if manual:
            lines.append("\n⚠️ **Manual Review Required:**")
            for m in manual:
                lines.append(f"- `{m.get('column')}`: {m.get('guidance', m.get('issue', ''))}")
        lines.append("")
    global_steps = plan.get("global_steps") or []
    if global_steps:
        lines.append("**Global Steps (cross-dataset):**")
        for g in global_steps:
            lines.append(f"- `{g.get('action')}`: {g.get('from', '')} → {g.get('to', '')}")
    return "\n".join(lines)


def _etl_error(msg: str) -> Dict[str, Any]:
    return {
        "reply": f"❌ ETL Error: {msg}",
        "payload": {"step": "etl_error", "error": msg, "options": []},
    }
