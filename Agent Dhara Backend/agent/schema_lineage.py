"""
Schema Lineage Builder (Node 9: schema_lineage_node)

Builds a source → transformation → target column lineage map
from the ETL plan and assessment result.

Output is used for:
- Lineage display in the chat UI
- Audit documentation
- Downstream schema validation
"""
from __future__ import annotations

from typing import Any, Dict, List, Optional

# Map of actions to their effect on dtype
ACTION_TYPE_EFFECTS: Dict[str, Dict[str, str]] = {
    "trim":                                   {"input": "string",  "output": "string"},
    "proactive_trim":                         {"input": "string",  "output": "string"},
    "fill_or_drop":                           {"input": "any",     "output": "any (nulls handled)"},
    "zero_to_null":                           {"input": "numeric", "output": "numeric (0 → null)"},
    "coerce_numeric":                         {"input": "object",  "output": "int64 or float64"},
    "parse_dates":                            {"input": "string",  "output": "datetime64"},
    "sanitize_email":                         {"input": "string",  "output": "string (lowercased, validated)"},
    "normalize_phone":                        {"input": "string",  "output": "string (digits only)"},
    "regex_replace":                          {"input": "string",  "output": "string (cleaned)"},
    "range_clip":                             {"input": "numeric", "output": "numeric (clipped)"},
    "clip_or_flag":                           {"input": "numeric", "output": "numeric (clipped/flagged)"},
    "replace_values":                         {"input": "any",     "output": "any (mapped)"},
    "standardize_boolean":                    {"input": "object",  "output": "bool"},
    "flatten_nested":                         {"input": "dict/list", "output": "string or expanded columns"},
    "deduplicate":                            {"input": "any",     "output": "any (duplicates removed)"},
    "deduplicate_or_alert":                   {"input": "any",     "output": "any (duplicates removed or alerted)"},
    "validate_referential_integrity_or_stage": {"input": "any",   "output": "any (orphans flagged/removed)"},
}


def build_schema_lineage(
    etl_plan: Dict[str, Any],
    assessment_result: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    """
    Build column-level lineage map from ETL plan.

    Returns:
    {
      "lineage": {
        "<dataset>": {
          "<column>": {
            "source_dtype": str,
            "transforms": [str, ...],
            "target_dtype": str,
            "auto": bool,
          },
          ...
        },
        ...
      },
      "global_steps": [...],
      "summary": {...}
    }
    """
    lineage: Dict[str, Any] = {}
    datasets_meta = (assessment_result or {}).get("datasets", {}) or {}

    for ds_name, ds_plan in etl_plan.get("datasets", {}).items():
        ds_lineage: Dict[str, Any] = {}
        schema_cols = (datasets_meta.get(ds_name, {}) or {}).get("columns", {}) or {}

        # Group steps by column
        col_steps: Dict[str, List[Dict[str, Any]]] = {}
        for step in ds_plan.get("steps", []):
            col = step.get("column") or "__table__"
            col_steps.setdefault(col, []).append(step)

        for col, steps in col_steps.items():
            # Get source dtype from assessment
            col_meta = schema_cols.get(col, {})
            source_dtype = col_meta.get("dtype", "unknown")
            dtype_hint = col_meta.get("dtype_inference") or ""
            if source_dtype == "object" and dtype_hint:
                source_dtype = f"object ({dtype_hint})"

            transforms = [s["action"] for s in steps]

            # Determine target dtype (last type-changing action wins)
            target_dtype = source_dtype
            for action in reversed(transforms):
                effect = ACTION_TYPE_EFFECTS.get(action)
                if effect and effect["output"] != "any":
                    target_dtype = effect["output"]
                    break

            ds_lineage[col] = {
                "source_dtype": source_dtype,
                "transforms": transforms,
                "target_dtype": target_dtype,
                "step_count": len(steps),
                "auto": all(s.get("auto", True) for s in steps),
                "null_pct": col_meta.get("null_percentage"),
                "unique_count": col_meta.get("unique_count"),
                "semantic_type": col_meta.get("semantic_type"),
            }

        # Add columns that have no transforms (pass-through)
        for col, col_meta in schema_cols.items():
            if col not in ds_lineage and col != "__table__":
                source_dtype = col_meta.get("dtype", "unknown")
                ds_lineage[col] = {
                    "source_dtype": source_dtype,
                    "transforms": [],
                    "target_dtype": source_dtype,
                    "step_count": 0,
                    "auto": True,
                    "null_pct": col_meta.get("null_percentage"),
                    "unique_count": col_meta.get("unique_count"),
                    "semantic_type": col_meta.get("semantic_type"),
                    "pass_through": True,
                }

        lineage[ds_name] = ds_lineage

    # Global steps lineage
    global_steps_lineage = [
        {
            "action": s.get("action"),
            "issue_type": s.get("issue_type"),
            "scope": "cross-dataset",
        }
        for s in etl_plan.get("global_steps", [])
    ]

    # Summary
    total_cols = sum(len(v) for v in lineage.values())
    transformed_cols = sum(
        1
        for ds in lineage.values()
        for col_info in ds.values()
        if col_info.get("step_count", 0) > 0
    )
    pass_through_cols = total_cols - transformed_cols

    return {
        "lineage": lineage,
        "global_steps": global_steps_lineage,
        "summary": {
            "total_columns": total_cols,
            "transformed_columns": transformed_cols,
            "pass_through_columns": pass_through_cols,
            "datasets": list(lineage.keys()),
        },
    }


def format_lineage_for_display(lineage_result: Dict[str, Any]) -> str:
    """
    Format lineage as a markdown table for display in chat UI or report.
    """
    lines = []
    lines.append("## 🔍 Schema Lineage Map")
    summary = lineage_result.get("summary", {})
    lines.append(
        f"**{summary.get('total_columns', 0)} columns** across "
        f"**{len(summary.get('datasets', []))} datasets** | "
        f"**{summary.get('transformed_columns', 0)} transformed** | "
        f"**{summary.get('pass_through_columns', 0)} pass-through**"
    )
    lines.append("")

    for ds_name, ds_lineage in lineage_result.get("lineage", {}).items():
        lines.append(f"### `{ds_name}`")
        lines.append("| Column | Source Type | Transforms | Target Type |")
        lines.append("|--------|-------------|------------|-------------|")
        for col, info in ds_lineage.items():
            if col == "__table__":
                continue
            transforms_str = " → ".join(info.get("transforms", [])) or "— (pass-through)"
            lines.append(
                f"| `{col}` "
                f"| `{info.get('source_dtype', '?')}` "
                f"| {transforms_str} "
                f"| `{info.get('target_dtype', '?')}` |"
            )
        lines.append("")

    global_steps = lineage_result.get("global_steps", [])
    if global_steps:
        lines.append("### Global Steps")
        for step in global_steps:
            lines.append(f"- `{step.get('action')}` ({step.get('issue_type')}) — {step.get('scope')}")

    return "\n".join(lines)
