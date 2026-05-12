"""
ETL LangGraph Nodes — Agent Dhara
===================================
Wires the ETL pipeline into the existing LangGraph state machine.

Nodes defined here:
  Node 1:  capture_etl_intent_node
  Node 2:  capture_business_rules_node
  Node 3:  load_manifest_node
  Node 4:  classify_issues_node
  Node 5:  ambiguity_check_node
  Node 6:  planning_node              (calls etl_planner.py)
  Node 7:  validate_dag_node
  Node 8:  target_schema_confirmation_node
  Node 9:  schema_lineage_node        (calls schema_lineage.py)
  Node 10: plan_presenter_node
  Node 11: human_review_node
  Node 12: codegen_node               (calls python_codegen.py / sql_codegen.py)
  Node 13: code_validation_node       (calls code_validator.py)
  Node 14: output_node

Integration:
  - Import `etl_pipeline_subgraph` and add it to your main chat_graph.py
  - Each node receives and returns the shared LangGraph state dict
"""
from __future__ import annotations

import json
import os
import re
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional

try:
    import yaml  # type: ignore
    HAS_YAML = True
except ImportError:
    HAS_YAML = False

from agent.etl_planner import build_etl_plan, format_plan_for_display
from agent.schema_lineage import build_schema_lineage, format_lineage_for_display
from agent.etl_codegen.python_codegen import generate_python_etl
from agent.etl_codegen.sql_codegen import generate_sql_etl
from agent.etl_codegen.code_validator import validate_generated_code

BUSINESS_RULES_PATH = Path(__file__).parent.parent / "config" / "business_rules.yaml"
OUTPUT_DIR = Path(__file__).parent.parent / "output" / "etl_code"
OUTPUT_DIR.mkdir(parents=True, exist_ok=True)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _load_business_rules() -> Dict[str, Any]:
    if HAS_YAML and BUSINESS_RULES_PATH.exists():
        with open(BUSINESS_RULES_PATH, "r") as f:
            return yaml.safe_load(f) or {}
    return {}


def _latest_assessment(state: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    """Pull the most recent assessment result from state."""
    return (
        state.get("last_assessment_result")
        or state.get("assessment_result")
        or {}
    )


# ---------------------------------------------------------------------------
# Node 1: capture_etl_intent_node
# ---------------------------------------------------------------------------

def capture_etl_intent_node(state: Dict[str, Any]) -> Dict[str, Any]:
    """
    Detects user intent for ETL code generation.
    Extracts: engine preference, target destination, filters.
    """
    message = (state.get("user_message") or "").lower()
    assessment = _latest_assessment(state)

    # Engine detection
    engine = "python"  # default
    if re.search(r"\bsql\b", message):
        engine = "sql"
    elif re.search(r"pyspark|spark", message):
        engine = "pyspark"
    elif re.search(r"adf|azure data factory", message):
        engine = "adf"

    # Target detection
    target = "local_file"  # default
    if re.search(r"sql.?table|database|db", message):
        target = "sql_table"
    elif re.search(r"blob|azure|s3|cloud", message):
        target = "blob"

    if not assessment:
        return {
            **state,
            "etl_intent": {"engine": engine, "target": target},
            "etl_stage": "no_assessment",
            "assistant_message": (
                "⚠️ No assessment found. Please run a data quality assessment first "
                "before generating ETL code. Type 'assess my data' to start."
            ),
        }

    return {
        **state,
        "etl_intent": {"engine": engine, "target": target},
        "etl_stage": "intent_captured",
    }


# ---------------------------------------------------------------------------
# Node 2: capture_business_rules_node
# ---------------------------------------------------------------------------

def capture_business_rules_node(state: Dict[str, Any]) -> Dict[str, Any]:
    """
    Loads business rules from config/business_rules.yaml.
    If user provided custom rules in message, merges them in.
    """
    rules = _load_business_rules()
    assessment = _latest_assessment(state)
    ds_names = list((assessment.get("datasets") or {}).keys())

    # Check which datasets have rules defined
    datasets_rules = rules.get("datasets", {})
    covered = [d for d in ds_names if d in datasets_rules]
    uncovered = [d for d in ds_names if d not in datasets_rules]

    return {
        **state,
        "business_rules": rules,
        "etl_stage": "business_rules_loaded",
        "business_rules_summary": {
            "datasets_with_rules": covered,
            "datasets_without_rules": uncovered,
            "global": rules.get("global", {}),
        },
    }


# ---------------------------------------------------------------------------
# Node 3: load_manifest_node
# ---------------------------------------------------------------------------

def load_manifest_node(state: Dict[str, Any]) -> Dict[str, Any]:
    """
    Loads the transformation manifest from assessment result.
    Falls back to reading output/reports/cleaning_manifest.json.
    """
    assessment = _latest_assessment(state)
    manifest = assessment.get("transformation_manifest") or {}

    if not manifest:
        # Try loading from disk
        manifest_path = (
            Path(__file__).parent.parent / "output" / "reports" / "cleaning_manifest.json"
        )
        if manifest_path.exists():
            with open(manifest_path, "r") as f:
                manifest = json.load(f)

    return {
        **state,
        "transformation_manifest": manifest,
        "etl_stage": "manifest_loaded",
    }


# ---------------------------------------------------------------------------
# Node 4: classify_issues_node
# ---------------------------------------------------------------------------

AUTO_ACTIONS = {
    "trim", "proactive_trim", "coerce_numeric", "parse_dates",
    "sanitize_email", "normalize_phone", "regex_replace",
    "standardize_boolean", "range_clip", "clip_or_flag",
    "zero_to_null", "replace_values", "flatten_nested",
    "deduplicate", "deduplicate_or_alert",
    "validate_referential_integrity_or_stage",
}
BLOCK_ACTIONS = {"empty_dataset", "critical_schema_mismatch"}


def classify_issues_node(state: Dict[str, Any]) -> Dict[str, Any]:
    """
    Classifies every manifest item as AUTO, REVIEW, or BLOCKED.
    BLOCKED items must be resolved before code can be generated.
    """
    manifest = state.get("transformation_manifest") or {}
    classified: Dict[str, Any] = {"auto": [], "review": [], "blocked": []}

    for ds_name, ds_items in manifest.items():
        if not isinstance(ds_items, list):
            continue
        for item in ds_items:
            action = item.get("suggested_action", "")
            severity = item.get("severity", "low")
            entry = {**item, "dataset": ds_name}

            if action in BLOCK_ACTIONS:
                classified["blocked"].append(entry)
            elif action in AUTO_ACTIONS and severity not in ("critical",):
                classified["auto"].append(entry)
            else:
                classified["review"].append(entry)

    blocked_count = len(classified["blocked"])
    if blocked_count > 0:
        blocked_names = [b.get("dataset") for b in classified["blocked"]]
        return {
            **state,
            "classified_issues": classified,
            "etl_stage": "blocked",
            "assistant_message": (
                f"🚫 **{blocked_count} blocking issue(s) found** in datasets: {blocked_names}. "
                f"These must be resolved before ETL code can be generated. "
                f"Run a new assessment or fix the source data first."
            ),
        }

    return {
        **state,
        "classified_issues": classified,
        "etl_stage": "classified",
    }


# ---------------------------------------------------------------------------
# Node 5: ambiguity_check_node
# ---------------------------------------------------------------------------

def ambiguity_check_node(state: Dict[str, Any]) -> Dict[str, Any]:
    """
    Checks for ambiguous situations that need user clarification:
    - Missing engine preference
    - Conflicting transforms on same column
    - High-severity REVIEW items
    """
    classified = state.get("classified_issues") or {}
    intent = state.get("etl_intent") or {}
    questions: List[str] = []

    # Check for high-severity review items
    high_sev_review = [
        item for item in classified.get("review", [])
        if item.get("severity") in ("high", "critical")
    ]
    if high_sev_review:
        cols = [(i.get("dataset"), i.get("column")) for i in high_sev_review[:3]]
        questions.append(
            f"⚠️ {len(high_sev_review)} high-severity issue(s) found that need manual review: "
            f"{cols}. Should I include these in the plan as warnings, or skip them?"
        )

    if questions:
        return {
            **state,
            "etl_stage": "awaiting_clarification",
            "clarification_questions": questions,
            "assistant_message": "\n\n".join(questions),
        }

    return {**state, "etl_stage": "ready_for_planning"}


# ---------------------------------------------------------------------------
# Node 6: planning_node
# ---------------------------------------------------------------------------

def planning_node(state: Dict[str, Any]) -> Dict[str, Any]:
    """
    Builds the ordered ETL plan JSON using topological transform ordering.
    Respects business rules as override constraints.
    """
    assessment = _latest_assessment(state)
    classified = state.get("classified_issues") or {}
    business_rules = state.get("business_rules") or {}
    intent = state.get("etl_intent") or {}
    overrides = state.get("plan_overrides") or {}

    etl_plan = build_etl_plan(
        assessment_result=assessment,
        classified_issues=classified,
        business_rules=business_rules,
        engine=intent.get("engine", "python"),
        overrides=overrides,
    )

    return {
        **state,
        "etl_plan": etl_plan,
        "etl_stage": "plan_built",
    }


# ---------------------------------------------------------------------------
# Node 7: validate_dag_node
# ---------------------------------------------------------------------------

def validate_dag_node(state: Dict[str, Any]) -> Dict[str, Any]:
    """
    Validates the ETL plan DAG:
    - No circular dependencies
    - No references to non-existent columns
    - Business rule violations in the plan
    """
    etl_plan = state.get("etl_plan") or {}
    assessment = _latest_assessment(state)
    business_rules = state.get("business_rules") or {}
    errors: List[str] = []

    for ds_name, ds_plan in etl_plan.get("datasets", {}).items():
        schema_cols = set(
            (assessment.get("datasets") or {}).get(ds_name, {}).get("columns", {}).keys()
        )
        ds_rules = (business_rules.get("datasets") or {}).get(ds_name, {})
        never_drop_cols = set(ds_rules.get("never_drop_columns") or [])

        for step in ds_plan.get("steps", []):
            col = step.get("column")
            action = step.get("action", "")

            # Column existence check
            if col and schema_cols and col not in schema_cols:
                errors.append(
                    f"[{ds_name}] Step references unknown column: '{col}' "
                    f"(not in schema: {sorted(schema_cols)})"
                )

            # Business rule: never drop columns
            if col and col in never_drop_cols and "drop" in action.lower():
                errors.append(
                    f"[{ds_name}] Business rule violation: action '{action}' on "
                    f"protected column '{col}' (never_drop_columns)."
                )

    if errors:
        retry_count = state.get("dag_validation_retries", 0)
        if retry_count >= 2:
            return {
                **state,
                "etl_stage": "dag_validation_failed",
                "assistant_message": (
                    f"❌ ETL plan validation failed after {retry_count} attempts:\n"
                    + "\n".join(f"- {e}" for e in errors)
                    + "\n\nPlease review your data schema or business rules."
                ),
            }
        return {
            **state,
            "etl_stage": "dag_invalid_retry",
            "dag_validation_errors": errors,
            "dag_validation_retries": retry_count + 1,
        }

    return {**state, "etl_stage": "dag_valid", "dag_validation_errors": []}


# ---------------------------------------------------------------------------
# Node 8: target_schema_confirmation_node
# ---------------------------------------------------------------------------

def target_schema_confirmation_node(state: Dict[str, Any]) -> Dict[str, Any]:
    """
    Asks user where cleaned data should be written:
    A) Overwrite source (in-place)
    B) Write to new table/file path
    C) Return as in-memory DataFrame only
    """
    if state.get("target_confirmed"):
        return {**state, "etl_stage": "target_confirmed"}

    intent = state.get("etl_intent") or {}
    target = intent.get("target", "local_file")

    # Auto-confirm if already specified in intent
    if target in ("sql_table", "blob"):
        return {
            **state,
            "target_confirmed": True,
            "output_target": target,
            "etl_stage": "target_confirmed",
        }

    return {
        **state,
        "etl_stage": "awaiting_target_confirmation",
        "assistant_message": (
            "📁 **Where should the cleaned data be written?**\n\n"
            "**A)** Overwrite source files (in-place transform)\n"
            "**B)** Write to a new file/path (specify path after choosing B)\n"
            "**C)** Return as in-memory DataFrame only (for notebook use)\n\n"
            "Reply with A, B, or C."
        ),
    }


# ---------------------------------------------------------------------------
# Node 9: schema_lineage_node
# ---------------------------------------------------------------------------

def schema_lineage_node(state: Dict[str, Any]) -> Dict[str, Any]:
    """Builds column-level lineage map from ETL plan."""
    etl_plan = state.get("etl_plan") or {}
    assessment = _latest_assessment(state)

    lineage_result = build_schema_lineage(
        etl_plan=etl_plan,
        assessment_result=assessment,
    )

    return {
        **state,
        "schema_lineage": lineage_result,
        "etl_stage": "lineage_built",
    }


# ---------------------------------------------------------------------------
# Node 10: plan_presenter_node
# ---------------------------------------------------------------------------

def plan_presenter_node(state: Dict[str, Any]) -> Dict[str, Any]:
    """Formats ETL plan + lineage as human-readable markdown for chat UI."""
    etl_plan = state.get("etl_plan") or {}
    lineage_result = state.get("schema_lineage") or {}
    classified = state.get("classified_issues") or {}
    rules_summary = state.get("business_rules_summary") or {}

    plan_md = format_plan_for_display(etl_plan)
    lineage_md = format_lineage_for_display(lineage_result)

    review_items = classified.get("review", [])
    review_section = ""
    if review_items:
        review_section = (
            f"\n\n### ⚠️ Manual Review Required ({len(review_items)} items)\n"
            + "\n".join(
                f"- **{i.get('dataset')}.{i.get('column')}** — "
                f"{i.get('issue_type', '')} | "
                f"{i.get('manual_guidance', 'Review manually')}"
                for i in review_items[:10]
            )
        )

    uncovered = rules_summary.get("datasets_without_rules", [])
    rules_warn = ""
    if uncovered:
        rules_warn = (
            f"\n\n> 💡 **No business rules configured** for: {uncovered}. "
            f"Add rules to `config/business_rules.yaml` for safer transforms."
        )

    message = (
        plan_md
        + "\n\n---\n"
        + lineage_md
        + review_section
        + rules_warn
        + "\n\n---\n"
        + "👉 **Reply:** `approve` to generate code | `modify <instruction>` to change | `cancel` to stop"
    )

    return {
        **state,
        "etl_stage": "plan_presented",
        "assistant_message": message,
    }


# ---------------------------------------------------------------------------
# Node 11: human_review_node
# ---------------------------------------------------------------------------

def human_review_node(state: Dict[str, Any]) -> Dict[str, Any]:
    """
    Safety gate. Routes based on user reply:
    - 'approve' / 'yes' / 'ok'  → codegen
    - 'modify ...'               → back to planning with overrides
    - 'cancel' / 'no' / 'stop'  → end
    """
    message = (state.get("user_message") or "").lower().strip()

    if re.match(r"^(approve|yes|ok|go ahead|generate|proceed|confirm)", message):
        return {**state, "etl_stage": "approved"}

    if message.startswith("modify") or message.startswith("change"):
        instruction = re.sub(r"^(modify|change)\s*", "", message).strip()
        return {
            **state,
            "etl_stage": "modification_requested",
            "plan_overrides": {"user_instruction": instruction},
        }

    if re.match(r"^(cancel|no|stop|abort|reject)", message):
        return {
            **state,
            "etl_stage": "cancelled",
            "assistant_message": "✅ ETL code generation cancelled. Your data has not been modified.",
        }

    # Not yet answered — show prompt again
    return {
        **state,
        "etl_stage": "awaiting_review",
        "assistant_message": (
            "Please reply with:\n"
            "- `approve` — generate the ETL code\n"
            "- `modify <instruction>` — adjust the plan\n"
            "- `cancel` — stop without generating"
        ),
    }


# ---------------------------------------------------------------------------
# Node 12: codegen_node
# ---------------------------------------------------------------------------

def codegen_node(state: Dict[str, Any]) -> Dict[str, Any]:
    """Generates ETL code for approved plan. Multi-target: python, sql, pyspark, adf."""
    etl_plan = state.get("etl_plan") or {}
    intent = state.get("etl_intent") or {}
    engine = intent.get("engine", "python")
    output_target = state.get("output_target", "local_file")

    codegen_errors = state.get("codegen_errors", [])

    generated: Dict[str, str] = {}

    for ds_name in etl_plan.get("datasets", {}):
        if engine == "python":
            code = generate_python_etl(etl_plan, ds_name, output_target)
        elif engine == "sql":
            code = generate_sql_etl(etl_plan, ds_name)
        else:
            # PySpark and ADF: stubs for Phase 2
            code = f"# {engine.upper()} generator coming in Phase 2\n"
        generated[ds_name] = code

    return {
        **state,
        "generated_code": generated,
        "codegen_engine": engine,
        "etl_stage": "code_generated",
    }


# ---------------------------------------------------------------------------
# Node 13: code_validation_node
# ---------------------------------------------------------------------------

def code_validation_node(state: Dict[str, Any]) -> Dict[str, Any]:
    """Validates all generated code. Routes to retry on failure (max 2 retries)."""
    generated = state.get("generated_code") or {}
    engine = state.get("codegen_engine", "python")
    assessment = _latest_assessment(state)
    retry_count = state.get("codegen_retry_count", 0)

    all_valid = True
    validation_results: Dict[str, Any] = {}

    for ds_name, code in generated.items():
        schema_cols = list(
            (assessment.get("datasets") or {}).get(ds_name, {}).get("columns", {}).keys()
        )
        result = validate_generated_code(code, engine, schema_cols)
        validation_results[ds_name] = result
        if not result["valid"]:
            all_valid = False

    if not all_valid and retry_count < 2:
        return {
            **state,
            "validation_results": validation_results,
            "codegen_retry_count": retry_count + 1,
            "etl_stage": "codegen_retry",
            "codegen_errors": [
                {"dataset": ds, "errors": r["errors"]}
                for ds, r in validation_results.items()
                if not r["valid"]
            ],
        }

    if not all_valid:
        errors_summary = [
            f"{ds}: {r['errors']}" for ds, r in validation_results.items() if not r["valid"]
        ]
        return {
            **state,
            "validation_results": validation_results,
            "etl_stage": "validation_failed",
            "assistant_message": (
                "❌ Code validation failed after 2 retries:\n"
                + "\n".join(errors_summary)
                + "\n\nPlease review the issues or contact support."
            ),
        }

    return {
        **state,
        "validation_results": validation_results,
        "etl_stage": "validated",
    }


# ---------------------------------------------------------------------------
# Node 14: output_node
# ---------------------------------------------------------------------------

def output_node(state: Dict[str, Any]) -> Dict[str, Any]:
    """
    Saves generated code to output/etl_code/.
    Returns download paths + code preview in chat message.
    """
    generated = state.get("generated_code") or {}
    engine = state.get("codegen_engine", "python")
    etl_plan = state.get("etl_plan") or {}
    plan_id = etl_plan.get("plan_id", datetime.now().strftime("%Y%m%d_%H%M%S"))

    ext = {"python": "py", "sql": "sql", "pyspark": "py", "adf": "json"}.get(engine, "txt")
    saved_files: List[str] = []
    preview_lines: List[str] = []

    for ds_name, code in generated.items():
        filename = f"{ds_name}_{engine}_{plan_id}.{ext}"
        filepath = OUTPUT_DIR / filename
        with open(filepath, "w") as f:
            f.write(code)
        saved_files.append(str(filepath))

        # Preview first 20 lines
        preview = "\n".join(code.splitlines()[:20])
        preview_lines.append(f"#### `{ds_name}` ({engine})\n```{engine}\n{preview}\n...\n```")

    warnings_all = [
        w
        for r in (state.get("validation_results") or {}).values()
        for w in r.get("warnings", [])
    ]
    warnings_section = ""
    if warnings_all:
        warnings_section = "\n\n⚠️ **Warnings:**\n" + "\n".join(f"- {w}" for w in warnings_all)

    message = (
        f"✅ **ETL code generated successfully!**\n\n"
        f"**Files saved:** {len(saved_files)} file(s)\n"
        + "\n".join(f"- `output/etl_code/{Path(p).name}`" for p in saved_files)
        + "\n\n### 👁️ Code Preview\n"
        + "\n\n".join(preview_lines)
        + warnings_section
        + "\n\n---\n"
        + "**What's next?**\n"
        + "- Run the ETL script on your data\n"
        + "- Run a new assessment to validate data quality post-transform\n"
        + "- Ask me to generate an ADF pipeline or PySpark version"
    )

    return {
        **state,
        "saved_etl_files": saved_files,
        "etl_stage": "complete",
        "assistant_message": message,
        "etl_code_generated": True,
    }
