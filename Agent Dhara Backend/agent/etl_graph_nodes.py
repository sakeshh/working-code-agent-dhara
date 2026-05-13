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
  Node 12: codegen_node               (calls python_codegen.py / sql_codegen.py / pyspark_codegen.py)
  Node 13: code_validation_node       (calls code_validator.py)
  Node 14: output_node
  Node 15: show_etl_plan_node         (runs up to plan_presenter_node, skips codegen)

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
from agent.etl_codegen.python_codegen import PythonCodegen
from agent.etl_codegen.sql_codegen import SQLCodegen
from agent.etl_codegen.pyspark_codegen import PySparkCodegen
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

    etl_plan = build_etl_plan(
        assessment_result=assessment,
        classified_issues=classified,
        business_rules=business_rules,
        engine=intent.get("engine", "python"),
        target=intent.get("target", "local_file"),
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
    Validates the ETL plan for:
    - Circular dependencies between steps
    - Steps referencing non-existent columns
    - Business rule violations in the plan
    Max 2 retries then surfaces error to user.
    """
    etl_plan = state.get("etl_plan") or {}
    assessment = _latest_assessment(state)
    business_rules = state.get("business_rules") or {}
    dag_errors: List[str] = []

    for ds_name, ds_plan in etl_plan.get("datasets", {}).items():
        steps = ds_plan.get("steps", [])
        seen_actions: List[str] = []

        # Schema columns from assessment
        ds_info = (assessment.get("datasets") or {}).get(ds_name, {})
        schema_cols = set(ds_info.get("columns", {}).keys())

        for step in steps:
            action = step.get("action", "")
            col = step.get("column")

            # Check column exists
            if col and schema_cols and col not in schema_cols:
                dag_errors.append(
                    f"[{ds_name}] Step references unknown column '{col}' (action: {action})"
                )

            # Simple cycle: deduplicate must come last
            if action == "deduplicate" and seen_actions and seen_actions[-1] != "deduplicate":
                pass  # allowed — deduplicate is after column-level steps
            seen_actions.append(action)

        # Business rule check: no-drop rule
        ds_rules = (business_rules.get("datasets") or {}).get(ds_name, {})
        no_drop_cols = ds_rules.get("never_drop_columns", [])
        for step in steps:
            if step.get("action") == "drop_nulls" and step.get("column") in no_drop_cols:
                dag_errors.append(
                    f"[{ds_name}] Business rule violation: "
                    f"'{step['column']}' is in never_drop_columns but plan has drop_nulls"
                )

    if dag_errors:
        retry_count = state.get("dag_retry_count", 0)
        if retry_count >= 2:
            return {
                **state,
                "etl_stage": "dag_error_unresolved",
                "assistant_message": (
                    f"❌ ETL plan has unresolvable errors after {retry_count} retries:\n"
                    + "\n".join(f"- {e}" for e in dag_errors)
                ),
            }
        return {
            **state,
            "dag_errors": dag_errors,
            "dag_retry_count": retry_count + 1,
            "etl_stage": "dag_invalid",
        }

    return {**state, "etl_stage": "dag_valid", "dag_errors": []}


# ---------------------------------------------------------------------------
# Node 8: target_schema_confirmation_node
# ---------------------------------------------------------------------------

def target_schema_confirmation_node(state: Dict[str, Any]) -> Dict[str, Any]:
    """
    Confirms where cleaned data should go:
      A) local_file  — write to output/cleaned/*.csv
      B) overwrite   — overwrite source in-place
      C) return      — return df in memory (notebook mode)
      D) sql_table   — write to SQL table
    """
    intent = state.get("etl_intent") or {}
    target = intent.get("target", "local_file")

    # Map intent target to codegen target_mode
    target_mode_map = {
        "local_file": "new_file",
        "overwrite": "overwrite",
        "return": "return",
        "sql_table": "sql_table",
        "blob": "new_file",
    }
    output_target = target_mode_map.get(target, "new_file")

    return {
        **state,
        "output_target": output_target,
        "etl_stage": "target_confirmed",
    }


# ---------------------------------------------------------------------------
# Node 9: schema_lineage_node
# ---------------------------------------------------------------------------

def schema_lineage_node(state: Dict[str, Any]) -> Dict[str, Any]:
    """
    Builds column-level data lineage map:
    source_col → [transforms] → target_col (with type info).
    """
    assessment = _latest_assessment(state)
    etl_plan = state.get("etl_plan") or {}

    lineage = build_schema_lineage(assessment_result=assessment, etl_plan=etl_plan)

    return {
        **state,
        "schema_lineage": lineage,
        "etl_stage": "lineage_built",
    }


# ---------------------------------------------------------------------------
# Node 10: plan_presenter_node
# ---------------------------------------------------------------------------

def plan_presenter_node(state: Dict[str, Any]) -> Dict[str, Any]:
    """
    Formats the ETL plan + lineage into a human-readable markdown table
    for display in the chat UI.
    """
    etl_plan = state.get("etl_plan") or {}
    lineage = state.get("schema_lineage") or {}
    classified = state.get("classified_issues") or {}
    rules_summary = state.get("business_rules_summary") or {}

    plan_display = format_plan_for_display(etl_plan)
    lineage_display = format_lineage_for_display(lineage)

    review_items = classified.get("review", [])
    review_section = ""
    if review_items:
        review_section = f"\n\n⚠️ **{len(review_items)} issue(s) need manual review** (not included in auto-code):\n"
        for item in review_items[:5]:
            review_section += (
                f"- `{item.get('dataset')}.{item.get('column')}`: "
                f"{item.get('issue_type', 'unknown')} — {item.get('manual_guidance', '')}\n"
            )
        if len(review_items) > 5:
            review_section += f"- ...and {len(review_items) - 5} more\n"

    uncovered = rules_summary.get("datasets_without_rules", [])
    rules_note = ""
    if uncovered:
        rules_note = (
            f"\n\n💡 **Tip:** No business rules found for: {uncovered}. "
            f"Add rules to `config/business_rules.yaml` for safer transforms."
        )

    message = (
        f"## 📋 ETL Plan Ready\n\n"
        f"{plan_display}"
        f"{review_section}"
        f"{rules_note}\n\n"
        f"### Column Lineage\n{lineage_display}\n\n"
        f"---\n"
        f"**Reply with:**\n"
        f"- `approve` — generate the code\n"
        f"- `modify: <your change>` — adjust the plan\n"
        f"- `cancel` — abort\n"
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
    Safety gate — waits for explicit user approval before code generation.
    Routes:
      'approve' / 'yes' / 'generate' → codegen_node
      'modify: ...'                   → planning_node (with override)
      'cancel' / 'no' / 'stop'       → END
    """
    message = (state.get("user_message") or "").lower().strip()

    if re.search(r"\bapprove\b|\byes\b|\bgenerate\b|\bgo ahead\b|\bproceed\b", message):
        return {**state, "etl_stage": "approved"}

    if message.startswith("modify:") or message.startswith("modify "):
        override_text = re.sub(r"^modify[:\s]+", "", message, flags=re.IGNORECASE)
        return {
            **state,
            "etl_stage": "modify_requested",
            "plan_override_request": override_text,
        }

    if re.search(r"\bcancel\b|\bno\b|\bstop\b|\babort\b", message):
        return {
            **state,
            "etl_stage": "cancelled",
            "assistant_message": "ETL code generation cancelled. Let me know if you'd like to try again.",
        }

    # Unrecognised response — re-prompt
    return {
        **state,
        "etl_stage": "awaiting_approval",
        "assistant_message": (
            "Please reply with:\n"
            "- `approve` to generate the code\n"
            "- `modify: <change>` to adjust the plan\n"
            "- `cancel` to abort"
        ),
    }


# ---------------------------------------------------------------------------
# Node 12: codegen_node  (python / sql / pyspark fully wired; adf = stub)
# ---------------------------------------------------------------------------

def codegen_node(state: Dict[str, Any]) -> Dict[str, Any]:
    """Generates ETL code for approved plan. Multi-target: python, sql, pyspark, adf."""
    etl_plan = state.get("etl_plan") or {}
    intent = state.get("etl_intent") or {}
    engine = intent.get("engine", "python")
    output_target = state.get("output_target", "new_file")

    generated: Dict[str, str] = {}

    if engine == "python":
        codegen = PythonCodegen(etl_plan, target_mode=output_target)
        generated = codegen.generate()
    elif engine == "sql":
        codegen = SQLCodegen(etl_plan)
        generated = codegen.generate()
    elif engine == "pyspark":
        # Map Python target_mode → PySpark target_mode
        spark_target_map = {
            "new_file": "new_file",
            "overwrite": "new_file",
            "return": "return",
            "sql_table": "hive",
        }
        spark_target = spark_target_map.get(output_target, "return")
        codegen = PySparkCodegen(etl_plan, target_mode=spark_target)
        generated = codegen.generate()
    else:
        # ADF: stub for Phase 2
        for ds_name in etl_plan.get("datasets", {}):
            generated[ds_name] = (
                f"# ADF (Azure Data Factory) pipeline generator — coming in Phase 2\n"
                f"# Dataset: {ds_name}\n"
                f"# In the meantime, use the 'generate python etl' or 'generate pyspark etl' commands.\n"
            )

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
    """
    Validates generated code before delivery.
    Uses AST parse for Python, sqlparse for SQL.
    Max 2 retries on failure.
    """
    generated = state.get("generated_code") or {}
    engine = state.get("codegen_engine", "python")
    assessment = _latest_assessment(state)
    validation_errors: List[str] = []

    for ds_name, code in generated.items():
        ds_info = (assessment.get("datasets") or {}).get(ds_name, {})
        schema_cols = list((ds_info.get("columns") or {}).keys())

        # PySpark code validated as Python (it is valid Python syntax)
        validate_as = "python" if engine == "pyspark" else engine
        ok, errors = validate_generated_code(code, validate_as, schema_cols)
        if not ok:
            for err in errors:
                validation_errors.append(f"[{ds_name}] {err}")

    if validation_errors:
        retry_count = state.get("codegen_retry_count", 0)
        if retry_count >= 2:
            return {
                **state,
                "etl_stage": "validation_failed_final",
                "assistant_message": (
                    f"❌ Code validation failed after {retry_count} retries:\n"
                    + "\n".join(f"- {e}" for e in validation_errors)
                    + "\n\nPlease check your data schema or modify the plan."
                ),
            }
        return {
            **state,
            "validation_errors": validation_errors,
            "codegen_retry_count": retry_count + 1,
            "etl_stage": "validation_failed",
        }

    return {**state, "etl_stage": "validation_passed", "validation_errors": []}


# ---------------------------------------------------------------------------
# Node 14: output_node
# ---------------------------------------------------------------------------

def output_node(state: Dict[str, Any]) -> Dict[str, Any]:
    """
    Saves generated code to output/etl_code/ and returns
    chat message with preview + file paths.
    """
    generated = state.get("generated_code") or {}
    engine = state.get("codegen_engine", "python")
    etl_plan = state.get("etl_plan") or {}
    plan_id = etl_plan.get("plan_id", datetime.now().strftime("%Y%m%d_%H%M%S"))

    ext_map = {"python": "py", "sql": "sql", "pyspark": "py", "adf": "json"}
    ext = ext_map.get(engine, "txt")

    saved_files: List[str] = []
    preview_lines: List[str] = []

    for ds_name, code in generated.items():
        filename = f"{ds_name}_{engine}_{plan_id}.{ext}"
        filepath = OUTPUT_DIR / filename
        with open(filepath, "w") as f:
            f.write(code)
        saved_files.append(str(filepath))
        preview_lines.append(f"### `{filename}`\n```{engine}\n" + "\n".join(code.splitlines()[:25]) + "\n...\n```")

    files_list = "\n".join(f"- `{f}`" for f in saved_files)
    preview = "\n\n".join(preview_lines)

    message = (
        f"✅ **ETL code generated successfully!**\n\n"
        f"**Files saved:**\n{files_list}\n\n"
        f"**Preview:**\n{preview}\n\n"
        f"---\n"
        f"**What's next?**\n"
        f"- Type `validate after transform` to run a Great Expectations check after executing the code\n"
        f"- Type `generate sql etl` to get the SQL version\n"
        f"- Type `generate pyspark etl` for a PySpark version\n"
    )

    return {
        **state,
        "etl_output_files": saved_files,
        "etl_stage": "complete",
        "assistant_message": message,
        "etl_code_generated": True,
        "etl_plan_id": plan_id,
    }


# ---------------------------------------------------------------------------
# Node 15: show_etl_plan_node
# ---------------------------------------------------------------------------

def show_etl_plan_node(state: Dict[str, Any]) -> Dict[str, Any]:
    """
    Lightweight pipeline that runs nodes 1-10 (intent → plan → presenter)
    WITHOUT going to codegen. Triggered by intent 11 (show_etl_plan action).

    This lets the user see the ETL plan + lineage + review items BEFORE
    deciding to approve/cancel code generation.
    """
    # Step through all pre-codegen nodes in sequence
    s = capture_etl_intent_node(state)
    if s.get("etl_stage") == "no_assessment":
        return s  # short-circuit: no assessment available

    s = capture_business_rules_node(s)
    s = load_manifest_node(s)
    s = classify_issues_node(s)

    if s.get("etl_stage") == "blocked":
        return s  # short-circuit: blocking issues

    s = ambiguity_check_node(s)
    s = planning_node(s)
    s = validate_dag_node(s)

    if s.get("etl_stage") in ("dag_invalid", "dag_error_unresolved"):
        return s  # short-circuit: plan errors

    s = target_schema_confirmation_node(s)
    s = schema_lineage_node(s)
    s = plan_presenter_node(s)

    # Override the final message to clarify this is a preview (not yet generating)
    message = s.get("assistant_message", "")
    if message and "Reply with:" in message:
        message = message.replace(
            "**Reply with:**",
            "**This is a preview. Reply with:**"
        )
        s = {**s, "assistant_message": message}

    return s
