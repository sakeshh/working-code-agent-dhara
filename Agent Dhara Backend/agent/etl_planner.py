"""
ETL Planner (Node 6: planning_node)

Consumes the transformation manifest from get_transformation_manifest_for_etl()
and produces an ordered, dependency-aware ETL Plan JSON.

Canonical transform ordering is deterministic (NOT LLM-decided).
Business rules from business_rules.yaml are applied as constraints.

Patches applied (2026-05-12):
  - validate_etl_plan: fixed column schema key — actual assessment_result uses
    'column_stats' not 'columns' to store per-column metadata.
"""
from __future__ import annotations

import json
import os
from datetime import datetime
from typing import Any, Dict, List, Optional, Tuple

import yaml

# ============================================================
# CANONICAL TRANSFORM ORDER
# Lower priority number = runs first
# ============================================================
TRANSFORM_ORDER: Dict[str, int] = {
    "column_rename":                          1,
    "column_name_whitespace":                 1,
    "trim":                                   2,
    "proactive_trim":                         2,
    "fill_or_drop":                           3,
    "zero_to_null":                           3,
    "coerce_numeric":                         4,
    "parse_dates":                            5,
    "ancient_dates":                          5,
    "sanitize_email":                         6,
    "normalize_phone":                        6,
    "regex_replace":                          7,
    "extremely_long_strings":                 7,
    "control_characters_in_text":             7,
    "range_clip":                             8,
    "clip_or_flag":                           8,
    "numeric_outliers_iqr":                   8,
    "replace_values":                         9,
    "standardize_boolean":                    10,
    "binary_like_column":                     10,
    "flatten_nested":                         11,
    "nested_structure":                       11,
    "deduplicate":                            12,
    "deduplicate_or_alert":                   12,
    "duplicate_rows":                         12,
    "duplicate_primary_key":                  12,
    "validate_referential_integrity_or_stage": 13,  # always last (global)
    "review_manually":                        99,  # never auto-generated
}

DEFAULT_ORDER = 50  # fallback for unknown actions


# ============================================================
# BUSINESS RULES LOADER
# ============================================================

def load_business_rules(config_path: Optional[str] = None) -> Dict[str, Any]:
    """Load business_rules.yaml. Returns empty dict if not found."""
    path = config_path or os.environ.get("BUSINESS_RULES_PATH")
    if not path:
        candidates = [
            os.path.join("config", "business_rules.yaml"),
            os.path.join("..", "config", "business_rules.yaml"),
            os.path.join(os.path.dirname(__file__), "..", "config", "business_rules.yaml"),
        ]
        for c in candidates:
            if os.path.isfile(c):
                path = c
                break
    if not path or not os.path.isfile(path):
        return {}
    try:
        with open(path, "r", encoding="utf-8") as f:
            return yaml.safe_load(f) or {}
    except Exception as e:
        print(f"[WARN] Could not load business_rules.yaml: {e}")
        return {}


def get_dataset_rules(business_rules: Dict[str, Any], dataset_name: str) -> Dict[str, Any]:
    """Get rules for a specific dataset. Merges global + dataset-specific."""
    global_rules = business_rules.get("global", {}) or {}
    dataset_rules = (business_rules.get("datasets", {}) or {}).get(dataset_name, {}) or {}
    return {"global": global_rules, "dataset": dataset_rules}


# ============================================================
# BUSINESS RULE CONFLICT CHECKER
# ============================================================

def check_business_rule_conflicts(
    steps: List[Dict[str, Any]],
    dataset_name: str,
    business_rules: Dict[str, Any],
) -> List[Dict[str, Any]]:
    """
    Check if any planned ETL step violates business rules.
    Returns list of conflict dicts: {column, action, rule, reason}
    """
    conflicts = []
    rules = get_dataset_rules(business_rules, dataset_name)
    dataset_cfg = rules.get("dataset", {})
    col_rules = (dataset_cfg.get("columns") or {})
    table_rules = (dataset_cfg.get("table_rules") or {})

    never_drop = table_rules.get("never_drop_rows", False)

    global_cfg = rules.get("global", {})
    pk_patterns = global_cfg.get("protected_pk_column_patterns", ["_id", "id"])

    for step in steps:
        col = step.get("column")
        action = step.get("action", "")
        col_lower = (col or "").lower()

        if never_drop and action in ("fill_or_drop", "deduplicate", "deduplicate_or_alert"):
            conflicts.append({
                "column": col,
                "action": action,
                "rule": "never_drop_rows",
                "reason": (
                    f"Dataset '{dataset_name}' has never_drop_rows=true. "
                    f"Action '{action}' may delete rows. Use fill only, not drop."
                )
            })

        col_cfg = col_rules.get(col, {})
        if col_cfg.get("never_modify") and action not in ("review_manually", "trim"):
            conflicts.append({
                "column": col,
                "action": action,
                "rule": "never_modify",
                "reason": (
                    f"Column '{col}' in '{dataset_name}' is marked never_modify. "
                    f"Action '{action}' would change its value."
                )
            })

        if any(col_lower.endswith(p) or col_lower == p.lstrip("_") for p in pk_patterns):
            if action in ("fill_or_drop", "zero_to_null"):
                conflicts.append({
                    "column": col,
                    "action": action,
                    "rule": "protected_pk_column_patterns",
                    "reason": (
                        f"Column '{col}' matches a PK pattern. "
                        f"Action '{action}' must not nullify or drop PK values."
                    )
                })

    return conflicts


# ============================================================
# CORE PLANNER
# ============================================================

def build_etl_plan(
    manifest: Dict[str, Any],
    engine: str = "python",
    target_mode: str = "new_file",
    target_path: Optional[str] = None,
    business_rules: Optional[Dict[str, Any]] = None,
    plan_overrides: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    """
    Build an ordered, dependency-aware ETL Plan JSON from the transformation manifest.

    Args:
        manifest:        Output of get_transformation_manifest_for_etl()
        engine:          'python' | 'sql' | 'pyspark' | 'adf'
        target_mode:     'overwrite_source' | 'new_file' | 'in_memory'
        target_path:     Output path/table name (for new_file or sql target)
        business_rules:  Loaded business_rules.yaml dict
        plan_overrides:  User overrides from modify loop e.g. {"skip_deduplicate": True}

    Returns:
        ETL Plan JSON dict
    """
    business_rules = business_rules or {}
    plan_overrides = plan_overrides or {}
    datasets_manifest = manifest.get("datasets", {}) or {}
    skip_actions = set(plan_overrides.get("skip_actions", []))
    force_engine = plan_overrides.get("engine") or engine

    plan_id = f"etl_plan_{datetime.utcnow().strftime('%Y%m%d_%H%M%S')}"
    plan: Dict[str, Any] = {
        "plan_id": plan_id,
        "engine": force_engine,
        "target_mode": target_mode,
        "target_path": target_path or f"output/etl/cleaned_{plan_id}",
        "created_at": datetime.utcnow().isoformat() + "Z",
        "datasets": {},
        "global_steps": [],
        "business_rule_conflicts": [],
        "summary": {},
    }

    total_auto = 0
    total_manual = 0
    total_blocked = 0

    for ds_name, items in datasets_manifest.items():
        if ds_name == "_global":
            for item in items:
                action = item.get("suggested_action", "")
                if action in skip_actions:
                    continue
                if action == "review_manually":
                    total_manual += 1
                    continue
                plan["global_steps"].append({
                    "action": action,
                    "issue_type": item.get("issue_type", ""),
                    "column": item.get("column"),
                    "auto": True,
                    "order": TRANSFORM_ORDER.get(action, DEFAULT_ORDER),
                })
                total_auto += 1
            continue

        auto_steps: List[Dict[str, Any]] = []
        manual_review: List[Dict[str, Any]] = []
        blocked: List[Dict[str, Any]] = []

        for item in items:
            action = item.get("suggested_action", "")
            col = item.get("column")
            issue_type = item.get("issue_type", "")

            if action in skip_actions:
                continue

            if action == "review_manually":
                total_manual += 1
                manual_review.append({
                    "column": col,
                    "issue_type": issue_type,
                    "guidance": f"Manual review required for issue: {issue_type}",
                })
                continue

            if issue_type in ("empty_dataset", "duplicate_column_names", "case_insensitive_column_collision"):
                total_blocked += 1
                blocked.append({
                    "column": col,
                    "issue_type": issue_type,
                    "reason": f"Issue '{issue_type}' must be resolved before ETL can proceed.",
                })
                continue

            auto_steps.append({
                "column": col,
                "action": action,
                "issue_type": issue_type,
                "auto": True,
                "order": TRANSFORM_ORDER.get(action, DEFAULT_ORDER),
            })
            total_auto += 1

        auto_steps.sort(key=lambda s: (s["order"], s["column"] or ""))

        for i, step in enumerate(auto_steps, start=1):
            step["step_number"] = i

        conflicts = check_business_rule_conflicts(auto_steps, ds_name, business_rules)
        if conflicts:
            plan["business_rule_conflicts"].extend([
                {**c, "dataset": ds_name} for c in conflicts
            ])

        plan["datasets"][ds_name] = {
            "steps": auto_steps,
            "manual_review": manual_review,
            "blocked": blocked,
            "step_count": len(auto_steps),
            "has_conflicts": len(conflicts) > 0,
        }

    plan["global_steps"].sort(key=lambda s: s["order"])
    for i, step in enumerate(plan["global_steps"], start=1):
        step["step_number"] = i

    plan["summary"] = {
        "total_datasets": len(plan["datasets"]),
        "total_auto_steps": total_auto,
        "total_manual_review": total_manual,
        "total_blocked": total_blocked,
        "total_conflicts": len(plan["business_rule_conflicts"]),
        "ready_for_codegen": (
            total_blocked == 0 and len(plan["business_rule_conflicts"]) == 0
        ),
    }

    return plan


# ============================================================
# DAG VALIDATOR (Node 7: validate_dag_node)
# ============================================================

def validate_etl_plan(
    plan: Dict[str, Any],
    assessment_result: Optional[Dict[str, Any]] = None,
) -> Tuple[bool, List[str]]:
    """
    Validate the ETL plan before presenting to user.

    Checks:
    1. No duplicate (dataset, column, action) combinations
    2. All referenced columns exist in the assessment schema
    3. No blocked issues exist
    4. No business rule conflicts exist
    5. Global referential integrity step comes after all per-dataset steps

    Returns:
        (is_valid: bool, errors: List[str])
    """
    errors: List[str] = []

    # Check blocked issues
    for ds_name, ds_plan in plan.get("datasets", {}).items():
        if ds_plan.get("blocked"):
            for b in ds_plan["blocked"]:
                errors.append(
                    f"BLOCKED [{ds_name}]: Issue '{b['issue_type']}' on column "
                    f"'{b['column']}' must be resolved before ETL. {b['reason']}"
                )

    # Check business rule conflicts
    for conflict in plan.get("business_rule_conflicts", []):
        errors.append(
            f"CONFLICT [{conflict.get('dataset')}] Column '{conflict.get('column')}': "
            f"Action '{conflict.get('action')}' violates rule '{conflict.get('rule')}'. "
            f"{conflict.get('reason')}"
        )

    # Check duplicate steps
    for ds_name, ds_plan in plan.get("datasets", {}).items():
        seen = set()
        for step in ds_plan.get("steps", []):
            key = (step.get("column"), step.get("action"))
            if key in seen:
                errors.append(
                    f"DUPLICATE [{ds_name}]: Step (column='{key[0]}', "
                    f"action='{key[1]}') appears twice."
                )
            seen.add(key)

    # FIX: actual assessment_result stores per-column info under 'column_stats',
    # not 'columns'. Support both keys for backwards compatibility.
    if assessment_result:
        datasets_meta = assessment_result.get("datasets", {}) or {}
        for ds_name, ds_plan in plan.get("datasets", {}).items():
            ds_meta = datasets_meta.get(ds_name, {}) or {}
            # Support both 'column_stats' (actual schema) and 'columns' (legacy)
            col_meta = ds_meta.get("column_stats") or ds_meta.get("columns") or {}
            schema_cols = set(col_meta.keys())
            for step in ds_plan.get("steps", []):
                col = step.get("column")
                if col and schema_cols and col not in schema_cols:
                    errors.append(
                        f"MISSING COLUMN [{ds_name}]: Column '{col}' referenced in "
                        f"plan does not exist in dataset schema."
                    )

    is_valid = len(errors) == 0
    return is_valid, errors


# ============================================================
# PLAN FORMATTER (for plan_presenter_node)
# ============================================================

def format_plan_for_display(plan: Dict[str, Any]) -> str:
    """
    Format the ETL plan as a human-readable markdown table for chat UI.
    """
    lines = []
    lines.append(f"## \U0001f4cb ETL Plan `{plan.get('plan_id', 'unknown')}`")
    lines.append(
        f"**Engine:** `{plan.get('engine', 'python')}` | "
        f"**Target:** `{plan.get('target_mode', 'new_file')}` | "
        f"**Output:** `{plan.get('target_path', '')}`"
    )
    lines.append("")

    summary = plan.get("summary", {})
    lines.append(
        f"\u2705 **{summary.get('total_auto_steps', 0)} auto steps** | "
        f"\u26a0\ufe0f **{summary.get('total_manual_review', 0)} manual reviews** | "
        f"\U0001f6ab **{summary.get('total_blocked', 0)} blocked**"
    )
    lines.append("")

    for ds_name, ds_plan in plan.get("datasets", {}).items():
        lines.append(f"### Dataset: `{ds_name}`")
        steps = ds_plan.get("steps", [])
        if steps:
            lines.append("| Step | Column | Action | Auto? |")
            lines.append("|------|--------|--------|-------|")
            for step in steps:
                col = step.get("column") or "(all rows)"
                lines.append(
                    f"| {step.get('step_number', '')} "
                    f"| `{col}` "
                    f"| `{step.get('action', '')}` "
                    f"| {'\u2705' if step.get('auto') else '\u274c'} |"
                )
        else:
            lines.append("_No auto steps for this dataset._")

        manual = ds_plan.get("manual_review", [])
        if manual:
            lines.append("")
            lines.append("\u26a0\ufe0f **Manual Review Required:**")
            for m in manual:
                lines.append(
                    f"- `{m.get('column')}`: {m.get('issue_type')} "
                    f"— {m.get('guidance', '')}"
                )

        blocked = ds_plan.get("blocked", [])
        if blocked:
            lines.append("")
            lines.append("\U0001f6ab **Blocked — Must Resolve Before ETL:**")
            for b in blocked:
                lines.append(f"- `{b.get('column')}`: {b.get('reason', '')}")

        lines.append("")

    global_steps = plan.get("global_steps", [])
    if global_steps:
        lines.append("### Global Steps (cross-dataset)")
        lines.append("| Step | Action | Issue Type |")
        lines.append("|------|--------|------------|")
        for step in global_steps:
            lines.append(
                f"| {step.get('step_number', '')} "
                f"| `{step.get('action', '')}` "
                f"| `{step.get('issue_type', '')}` |"
            )
        lines.append("")

    conflicts = plan.get("business_rule_conflicts", [])
    if conflicts:
        lines.append("\U0001f534 **Business Rule Conflicts — Plan Cannot Proceed:**")
        for c in conflicts:
            lines.append(
                f"- [{c.get('dataset')}] `{c.get('column')}`: {c.get('reason')}"
            )
        lines.append("")

    if summary.get("ready_for_codegen"):
        lines.append("\U0001f449 **Plan is ready for code generation.**")
        lines.append("> Reply with **approve**, **modify**, or **cancel**.")
    else:
        lines.append("\u274c **Plan has issues that must be resolved before code generation.**")
        lines.append("> Please review the conflicts/blocked items above.")

    return "\n".join(lines)


# ============================================================
# SAVE PLAN (audit trail — only after human approval)
# ============================================================

def save_etl_plan(plan: Dict[str, Any], output_dir: str = "output/etl") -> str:
    """Save approved ETL plan to disk for audit. Returns file path."""
    os.makedirs(output_dir, exist_ok=True)
    plan_id = plan.get("plan_id", "etl_plan")
    path = os.path.join(output_dir, f"{plan_id}.json")
    with open(path, "w", encoding="utf-8") as f:
        json.dump(plan, f, indent=2, ensure_ascii=False, default=str)
    return path
