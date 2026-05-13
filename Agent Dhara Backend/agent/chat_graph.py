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


_MASTER_SYSTEM = """You are Agent Dhara’s Master (Supervisor) router for **data exploration + data quality only**.
You MUST return ONLY valid JSON and nothing else.

Your job:
- Understand the user request in natural language.
- Decide what action to take next (route to the right "agent": extraction vs data quality vs navigation).
- Provide the minimal arguments needed to execute it.

CORE PRODUCT RULES (must obey when choosing actions):
- Answer the user’s **stated intent** with the **smallest** action that satisfies it. Prefer `summarize_report` for narrative “explain the report”, and DQ slice actions (`show_null_columns`, `dq_duplicates`, `dq_overview`) for narrow checks — do **not** default to huge prose unless the user clearly wants a full narrative.
- Vague deictics (“this”, “too”, “fix this”) without a clear object → `show_selection_status` or `help` (ask what to operate on), not `summarize_report`.
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
- If the user says "generate etl code", "build etl pipeline", "generate transformations", or "create cleaning script", choose action=generate_etl_code.

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
"""

# ... (rest of file unchanged, including new _node_generate_etl_code registration)
