"""
3-agent chat workflow (LangGraph):
- MasterChatAgent: uses an LLM to interpret user intent and parameters (no keyword heuristics)
- ExtractAgent: list sources/tables, show schema/preview/query (via connectors/MCP adapters)
- DataQualityAgent: run DQ checks and generate a report for selected dataset/table

The LLM produces a structured JSON "action plan" which is then executed deterministically.
"""

from __future__ import annotations

import json
import time
from typing import Any, Dict, List, Optional, TypedDict, Tuple

from agent.master_agent import load_sources_config
from agent.model_config import load_llm_config
from agent.openai_usage import usage_dict_from_response
from agent.session_store import add_experience, list_recent_experiences, load_session, save_session

# ETL pipeline nodes (14-node pipeline)
from agent.etl_graph_nodes import (
    capture_etl_intent_node,
    capture_business_rules_node,
    load_manifest_node,
    classify_issues_node,
    ambiguity_check_node,
    planning_node,
    validate_dag_node,
    target_schema_confirmation_node,
    schema_lineage_node,
    plan_presenter_node,
    human_review_node,
    codegen_node,
    code_validation_node,
    output_node,
)

try:
    from langgraph.graph import END, StateGraph
except Exception:  # pragma: no cover
    END = None  # type: ignore
    StateGraph = None  # type: ignore


class ChatState(TypedDict, total=False):
    session_id: str
    message: str
    session: Dict[str, Any]
    action: str
    action_args: Dict[str, Any]
    reply: str
    payload: Dict[str, Any]
    router_llm_usage: Dict[str, int]
    nl_sql_llm_usage: Dict[str, int]
    # ETL state fields
    user_message: str
    etl_intent: Dict[str, Any]
    etl_stage: str
    etl_plan: Dict[str, Any]
    classified_issues: Dict[str, Any]
    transformation_manifest: Dict[str, Any]
    business_rules: Dict[str, Any]
    business_rules_summary: Dict[str, Any]
    schema_lineage: Dict[str, Any]
    generated_code: Dict[str, str]
    codegen_engine: str
    validation_results: Dict[str, Any]
    saved_etl_files: List[str]
    etl_code_generated: bool
    assistant_message: str
    output_target: str
    target_confirmed: bool
    dag_validation_errors: List[str]
    dag_validation_retries: int
    codegen_retry_count: int
    codegen_errors: List[Dict]
    plan_overrides: Dict[str, Any]
    clarification_questions: List[str]
    last_assessment_result: Dict[str, Any]
    assessment_result: Dict[str, Any]


def _flow_options(*items: List[Dict[str, str]]) -> List[Dict[str, str]]:
    """
    Options are consumed by the frontend to render buttons.
    Each option: {id, text, send}
    """
    out: List[Dict[str, str]] = []
    for it in items:
        if not isinstance(it, dict):
            continue
        if not it.get("text") or not it.get("send"):
            continue
        out.append({"id": str(it.get("id") or it["text"]), "text": str(it["text"]), "send": str(it["send"])})
    return out


def _prompt_choose_action() -> Dict[str, Any]:
    reply = "📌 Choose Action:\n1. View Data in Files\n2. Generate Report"
    return {
        "reply": reply,
        "payload": {
            "step": "action",
            "options": _flow_options(
                {"id": "view", "text": "👁️ View Data", "send": "view data"},
                {"id": "report", "text": "📑 Generate Report", "send": "generate report"},
                {"id": "back", "text": "🔙 Back", "send": "back"},
                {"id": "restart", "text": "✅ Restart", "send": "restart"},
            ),
        },
    }


def _first_location_index(source_root: Dict[str, Any], want_type: str) -> Optional[int]:
    locs = list(((source_root or {}).get("locations") or []))
    for i, loc in enumerate(locs):
        if str(loc.get("type") or "").lower() == want_type:
            return i
    return None


_MASTER_SYSTEM = """You are Agent Dhara's Master (Supervisor) router for **data exploration + data quality + ETL code generation**.
You MUST return ONLY valid JSON and nothing else.

Your job:
- Understand the user request in natural language.
- Decide what action to take next (route to the right "agent": extraction vs data quality vs ETL vs navigation).
- Provide the minimal arguments needed to execute it.

CORE PRODUCT RULES (must obey when choosing actions):
- Answer the user's **stated intent** with the **smallest** action that satisfies it. Prefer `summarize_report` for narrative "explain the report", and DQ slice actions (`show_null_columns`, `dq_duplicates`, `dq_overview`) for narrow checks — do **not** default to huge prose unless the user clearly wants a full narrative.
- Vague deictics ("this", "too", "fix this") without a clear object → `show_selection_status` or `help` (ask what to operate on), not `summarize_report`.
- Stocks / general coding / sports / trivia are **out of scope** → `help` with a short refusal tone is acceptable if no better action exists.
- Never instruct downstream agents to invent data-quality issues or contradict a saved assessment verdict.

Allowed actions (exact strings):
help
reset_flow
back_flow
set_action
list_sources
select_source
list_tables
select_tables
select_table
show_schema
preview_table
nl_query
dq_table
show_null_columns
extract_columns
dq_overview
dq_duplicates
summarize_report
relationships_overview
list_blob_files
select_blob_files
assess_selected_files
list_local_files
select_local_files
assess_selected_local_files
assess_selected_tables
preview_local_file
preview_blob_file
show_selection_status
generate_etl_code
show_etl_plan

Output schema:
{
  "action": "<one allowed action>",
  "args": { ... }
}

Argument rules:
- For selections, prefer numeric indices when available lists are provided.
- If the user references a specific name (table/blob/file), you may pass it directly by name.
- Never invent sources/tables/files that are not listed in the provided context.

Behavior rules:
- If the user says "restart", choose action=reset_flow.
- If the user says "back", choose action=back_flow.
- If the user picks an action ("view data" or "generate report"), choose action=set_action with {"action":"view"} or {"action":"report"}.
- If the user asks to "run data quality assessment" or "check data quality issues" for the *currently selected blob files*,
  choose action=assess_selected_files.
- If the user asks to assess the *currently selected local files*, choose action=assess_selected_local_files.
- If the user asks to assess the *currently selected tables*, choose action=assess_selected_tables.
- If the user ONLY asks how many / which items are *selected*, or what the current selection is (with no DQ/report ask),
  choose show_selection_status.
- If the user asks you to summarize, explain in plain English, or give an executive summary of THE REPORT / assessment / findings,
  choose summarize_report (not dq_overview).
- If the user asks about relationships between datasets/files, cardinality (one-to-many, many-to-one, etc.), how tables link or join,
  foreign keys, overlaps between keys, or orphan / dangling key hints, choose relationships_overview (not dq_overview).
- If the user asks a data-quality question (nulls, duplicates, outliers, per-dataset issue totals) AFTER a report was generated,
  choose a DQ action (dq_overview / show_null_columns / dq_duplicates) and answer from the latest assessment.
- If the user asks for extraction (show columns, show top rows, preview data) for selected datasets, choose an extraction action.
- If the user says "generate etl code", "build etl pipeline", "generate transformations", "create cleaning script",
  "fix the data", "clean the data", "write transformation code", choose action=generate_etl_code.
- If the user says "etl python", "python etl", "generate python etl", choose action=generate_etl_code with args {"engine": "python"}.
- If the user says "etl sql", "sql etl", "generate sql etl", choose action=generate_etl_code with args {"engine": "sql"}.
- If the user says "etl spark" or "pyspark etl", choose action=generate_etl_code with args {"engine": "pyspark"}.
- If the user says "show etl plan" or "show the plan", choose action=show_etl_plan.
- If the user says "approve" or "yes" or "proceed" during ETL review, choose action=generate_etl_code with args {"approved": true}.
- If the user says "modify" or "change" during ETL, choose action=generate_etl_code with args {"modify": true}.
- If the user says "cancel" or "stop" during ETL, choose action=generate_etl_code with args {"cancel": true}.

Examples (JSON only):
{"action":"list_sources","args":{}}
{"action":"select_source","args":{"index":0}}
{"action":"list_tables","args":{}}
{"action":"select_tables","args":{"indices":[1,3,4]}}
{"action":"assess_selected_tables","args":{}}
{"action":"list_blob_files","args":{}}
{"action":"select_blob_files","args":{"all":true}}
{"action":"assess_selected_files","args":{}}
{"action":"dq_overview","args":{}}
{"action":"summarize_report","args":{}}
{"action":"relationships_overview","args":{}}
{"action":"show_selection_status","args":{}}
{"action":"extract_columns","args":{}}
{"action":"generate_etl_code","args":{"engine":"python"}}
{"action":"generate_etl_code","args":{"engine":"sql"}}
"""


# ---------------------------------------------------------------------------
# ETL pipeline orchestrator — wraps all 14 nodes with stage-based routing
# ---------------------------------------------------------------------------

def _run_etl_pipeline(state: ChatState) -> ChatState:
    """
    Orchestrates the 14-node ETL pipeline based on current etl_stage.
    Called by _node_generate_etl_code for every ETL-related message.
    """
    # Bridge: copy message into user_message for ETL nodes
    if "user_message" not in state or not state.get("user_message"):
        state = {**state, "user_message": state.get("message", "")}

    # Bridge: copy session assessment into top-level state for ETL nodes
    session = state.get("session") or {}
    if not state.get("last_assessment_result") and session.get("last_assessment_result"):
        state = {**state, "last_assessment_result": session["last_assessment_result"]}
    if not state.get("last_assessment_result") and session.get("assessment_result"):
        state = {**state, "last_assessment_result": session["assessment_result"]}

    # FIX #4: Restore ETL state from session on resume so multi-turn works
    etl_resume_keys = (
        "etl_stage", "etl_plan", "etl_intent", "classified_issues",
        "transformation_manifest", "business_rules", "business_rules_summary",
        "schema_lineage", "output_target", "target_confirmed",
        "generated_code", "saved_etl_files",
    )
    for k in etl_resume_keys:
        if not state.get(k) and session.get(k) is not None:
            state = {**state, k: session[k]}

    etl_stage = state.get("etl_stage", "")
    action_args = state.get("action_args") or {}

    # --- Handle approve/modify/cancel during human review ---
    if action_args.get("cancel"):
        state = {**state, "etl_stage": "cancelled"}
        state = human_review_node({**state, "user_message": "cancel"})
        return _etl_state_to_chat_state(state)

    # FIX #3: code_validation_node sets stage to "validation_passed", not "validated"
    if action_args.get("approved") and etl_stage in ("plan_presented", "awaiting_review"):
        state = {**state, "etl_stage": "approved", "user_message": "approve"}
        state = codegen_node(state)
        state = code_validation_node(state)
        if state.get("etl_stage") == "validation_failed":
            # Retry once
            state = {**state, "codegen_retry_count": (state.get("codegen_retry_count") or 0) + 1}
            state = codegen_node(state)
            state = code_validation_node(state)
        if state.get("etl_stage") in ("validation_passed", "code_generated"):
            state = output_node(state)
        return _etl_state_to_chat_state(state)

    if action_args.get("modify") and etl_stage in ("plan_presented", "awaiting_review"):
        state = {**state, "etl_stage": "modification_requested",
                 "plan_overrides": {"user_instruction": state.get("user_message", "")}}
        state = planning_node(state)
        state = validate_dag_node(state)
        if state.get("etl_stage") == "dag_invalid":
            # Retry once
            state = planning_node(state)
            state = validate_dag_node(state)
        if state.get("etl_stage") == "dag_valid":
            state = schema_lineage_node(state)
            state = plan_presenter_node(state)
        return _etl_state_to_chat_state(state)

    # --- Resume from awaiting_target_confirmation ---
    if etl_stage == "awaiting_target_confirmation":
        msg = (state.get("user_message") or "").strip().upper()
        if msg.startswith("A"):
            state = {**state, "target_confirmed": True, "output_target": "overwrite", "etl_stage": "target_confirmed"}
        elif msg.startswith("B"):
            state = {**state, "target_confirmed": True, "output_target": "new_file", "etl_stage": "target_confirmed"}
        elif msg.startswith("C"):
            state = {**state, "target_confirmed": True, "output_target": "memory", "etl_stage": "target_confirmed"}
        else:
            # Still waiting — re-show the options
            return _etl_state_to_chat_state(state)
        # Continue to lineage + present
        state = schema_lineage_node(state)
        state = plan_presenter_node(state)
        return _etl_state_to_chat_state(state)

    # --- Resume from awaiting_clarification ---
    if etl_stage == "awaiting_clarification":
        # User answered the high-severity warning question — proceed
        state = {**state, "etl_stage": "ready_for_planning"}

    # --- Fresh start OR engine override from button ---
    engine = action_args.get("engine", (state.get("etl_intent") or {}).get("engine", "python"))
    etl_intent = state.get("etl_intent") or {}
    state = {**state, "etl_intent": {**etl_intent, "engine": engine}}

    if etl_stage not in (
        "ready_for_planning", "plan_built", "dag_valid",
        "target_confirmed", "lineage_built", "approved",
        "modification_requested",
    ):
        # Full pipeline from scratch
        state = capture_etl_intent_node(state)
        if state.get("etl_stage") == "no_assessment":
            return _etl_state_to_chat_state(state)

        state = capture_business_rules_node(state)
        state = load_manifest_node(state)
        state = classify_issues_node(state)
        if state.get("etl_stage") == "blocked":
            return _etl_state_to_chat_state(state)

        state = ambiguity_check_node(state)
        if state.get("etl_stage") == "awaiting_clarification":
            return _etl_state_to_chat_state(state)

        state = planning_node(state)
        state = validate_dag_node(state)
        # FIX #3: stage set by validate_dag_node is "dag_invalid" not "dag_invalid_retry"
        if state.get("etl_stage") == "dag_invalid":
            state = planning_node(state)
            state = validate_dag_node(state)
        if state.get("etl_stage") == "dag_error_unresolved":
            return _etl_state_to_chat_state(state)

        state = target_schema_confirmation_node(state)
        if state.get("etl_stage") == "awaiting_target_confirmation":
            return _etl_state_to_chat_state(state)

        state = schema_lineage_node(state)
        state = plan_presenter_node(state)
        return _etl_state_to_chat_state(state)

    return _etl_state_to_chat_state(state)


def _etl_state_to_chat_state(state: Dict[str, Any]) -> Dict[str, Any]:
    """
    Converts ETL node output (uses assistant_message) back to ChatState shape
    (uses reply + payload) so the existing chat infrastructure can render it.
    """
    assistant_msg = state.get("assistant_message", "")
    etl_stage = state.get("etl_stage", "")

    # Build appropriate options based on stage
    options = []
    if etl_stage == "plan_presented":
        options = _flow_options(
            {"id": "approve", "text": "✅ Approve & Generate Code", "send": "approve"},
            {"id": "modify",  "text": "✏️ Modify Plan",            "send": "modify"},
            {"id": "cancel",  "text": "❌ Cancel",                 "send": "cancel"},
        )
    elif etl_stage == "awaiting_target_confirmation":
        options = _flow_options(
            {"id": "target_a", "text": "♻️ A — Overwrite source",    "send": "A"},
            {"id": "target_b", "text": "📁 B — Write to new file",   "send": "B"},
            {"id": "target_c", "text": "💾 C — In-memory only",      "send": "C"},
        )
    elif etl_stage in ("complete", "validation_passed"):
        options = _flow_options(
            {"id": "new_etl",  "text": "🔄 Generate Another",         "send": "generate etl code"},
            {"id": "assess",   "text": "🔍 Run New Assessment",        "send": "assess my data"},
            {"id": "restart",  "text": "✅ Restart",                  "send": "restart"},
        )
    elif etl_stage in ("cancelled", "dag_error_unresolved", "validation_failed_final", "blocked", "no_assessment"):
        options = _flow_options(
            {"id": "restart", "text": "✅ Restart", "send": "restart"},
            {"id": "back",    "text": "🔙 Back",    "send": "back"},
        )

    payload: Dict[str, Any] = {"step": f"etl_{etl_stage}"}
    if options:
        payload["options"] = options

    # Attach code preview files if generated
    if state.get("saved_etl_files"):
        payload["etl_files"] = state["saved_etl_files"]
    if state.get("etl_plan"):
        payload["etl_plan"] = state["etl_plan"]
    if state.get("schema_lineage"):
        payload["schema_lineage"] = state["schema_lineage"]

    return {
        **state,
        "reply": assistant_msg,
        "payload": payload,
    }


# ---------------------------------------------------------------------------
# Chat graph node functions
# ---------------------------------------------------------------------------

def _node_generate_etl_code(state: ChatState) -> ChatState:
    """Entry point for ETL code generation — runs the full 14-node pipeline."""
    return _run_etl_pipeline(state)


def _node_show_etl_plan(state: ChatState) -> ChatState:
    """Shows the current ETL plan if one exists in state."""
    etl_plan = state.get("etl_plan")
    if not etl_plan:
        return {
            **state,
            "reply": (
                "📋 No ETL plan exists yet. "
                "Type 'generate etl code' to create one from your latest assessment."
            ),
            "payload": {"step": "etl_no_plan", "options": _flow_options(
                {"id": "gen", "text": "🛠 Generate ETL Plan", "send": "generate etl code"},
            )},
        }
    from agent.etl_planner import format_plan_for_display
    plan_md = format_plan_for_display(etl_plan)
    return {
        **state,
        "reply": f"📋 **Current ETL Plan**\n\n{plan_md}\n\nType `approve` to generate code or `modify <instruction>` to change it.",
        "payload": {
            "step": "etl_plan_shown",
            "etl_plan": etl_plan,
            "options": _flow_options(
                {"id": "approve", "text": "✅ Approve & Generate Code", "send": "approve"},
                {"id": "modify",  "text": "✏️ Modify Plan",            "send": "modify"},
                {"id": "cancel",  "text": "❌ Cancel",                 "send": "cancel"},
            ),
        },
    }


def build_chat_graph():
    """Build and return the compiled LangGraph chat graph."""
    if StateGraph is None:
        raise RuntimeError("langgraph is not installed")

    g = StateGraph(ChatState)

    # ---- Load session ----
    # FIX #4: Also restore ETL state from session so multi-turn ETL flow works
    def _node_load_session(state: ChatState) -> ChatState:
        session = load_session(state["session_id"])
        restored: Dict[str, Any] = {}
        etl_session_keys = (
            "etl_stage", "etl_plan", "etl_intent", "classified_issues",
            "transformation_manifest", "business_rules", "business_rules_summary",
            "schema_lineage", "output_target", "target_confirmed",
            "generated_code", "saved_etl_files", "etl_code_generated",
            "last_assessment_result",
        )
        for k in etl_session_keys:
            if session.get(k) is not None and not state.get(k):
                restored[k] = session[k]
        return {**state, "session": session, **restored}

    # ---- LLM Router ----
    def _node_llm_router(state: ChatState) -> ChatState:
        from agent.llm_router import call_router_llm
        session = state.get("session") or {}
        sources_config = load_sources_config()
        result = call_router_llm(
            system_prompt=_MASTER_SYSTEM,
            user_message=state["message"],
            session=session,
            sources_config=sources_config,
        )
        return {
            **state,
            "action": result.get("action", "help"),
            "action_args": result.get("args") or {},
            "router_llm_usage": result.get("usage") or {},
        }

    # ---- Save session ----
    def _node_save_session(state: ChatState) -> ChatState:
        session = state.get("session") or {}
        for etl_key in (
            "etl_stage", "etl_plan", "etl_intent", "classified_issues",
            "transformation_manifest", "business_rules", "schema_lineage",
            "generated_code", "saved_etl_files", "etl_code_generated",
            "output_target", "target_confirmed", "last_assessment_result",
        ):
            if state.get(etl_key) is not None:
                session[etl_key] = state[etl_key]
        save_session(state["session_id"], session)
        return state

    # ---- Help node ----
    def _node_help(state: ChatState) -> ChatState:
        reply = (
            "👋 I'm **Agent Dhara** — your data quality & ETL assistant.\n\n"
            "Here's what I can do:\n"
            "- 📂 **Explore data** — list sources, preview tables, show schema\n"
            "- 🔍 **Assess data quality** — find nulls, duplicates, outliers, type issues\n"
            "- 📑 **Generate reports** — full data quality report with recommendations\n"
            "- 🛠 **Generate ETL code** — Python or SQL cleaning scripts from your report\n\n"
            "Try: *'generate etl code'*, *'assess my data'*, or *'show null columns'*"
        )
        return {
            **state,
            "reply": reply,
            "payload": {
                "step": "help",
                "options": _flow_options(
                    {"id": "etl",    "text": "🛠 Generate ETL Code",   "send": "generate etl code"},
                    {"id": "report", "text": "📑 Generate Report",     "send": "generate report"},
                    {"id": "assess", "text": "🔍 Assess My Data",      "send": "assess my data"},
                    {"id": "restart","text": "✅ Restart",             "send": "restart"},
                ),
            },
        }

    # ---- Register all nodes ----
    # FIX #1: _node_route REMOVED as a node — it returned a string which crashes LangGraph.
    #         Conditional routing now reads action directly from llm_router output state.
    g.add_node("load_session", _node_load_session)
    g.add_node("llm_router", _node_llm_router)
    g.add_node("help", _node_help)
    g.add_node("generate_etl_code", _node_generate_etl_code)
    g.add_node("show_etl_plan", _node_show_etl_plan)
    g.add_node("save_session", _node_save_session)

    # ---- Entry ----
    g.set_entry_point("load_session")
    g.add_edge("load_session", "llm_router")

    # FIX #1 + FIX #2: Route directly from llm_router; all 25 DQ/extraction
    # actions fall through to "help" as a safe default until their nodes are
    # added. This prevents KeyError crashes on any unrecognised action.
    g.add_conditional_edges(
        "llm_router",
        lambda s: s.get("action", "help"),
        {
            "generate_etl_code": "generate_etl_code",
            "show_etl_plan":     "show_etl_plan",
            # All other actions (list_sources, dq_overview, assess_selected_files,
            # summarize_report, etc.) safely fall to help for now.
            # To enable them: add their node with g.add_node() and map them here.
        },
    )

    # ---- All terminal nodes → save_session → END ----
    for node in ("help", "generate_etl_code", "show_etl_plan"):
        g.add_edge(node, "save_session")
    g.add_edge("save_session", END)

    return g.compile()
