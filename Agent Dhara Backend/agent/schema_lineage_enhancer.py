"""
schema_lineage_enhancer.py — Agent Dhara Phase 2
Enhances the existing schema_lineage.py output with:
  - Source type → target type mapping per column
  - Human-readable transform chain descriptions
  - Markdown + JSON export of lineage for frontend visualizer
  - Lineage diff: compares pre/post assessment for changed columns

Usage:
    from agent.schema_lineage_enhancer import SchemaLineageEnhancer
    enhancer = SchemaLineageEnhancer(etl_plan, assessment_result)
    report = enhancer.build_lineage_report()
    md = enhancer.to_markdown(report)
    json_out = enhancer.to_json(report)
"""

import json
from typing import Any


# ── Action → human description map ─────────────────────────────────────────────
ACTION_DESCRIPTIONS: dict[str, str] = {
    "trim": "Strip leading/trailing whitespace",
    "fill_or_drop": "Fill nulls with mode value",
    "drop_nulls": "Drop rows where column is null",
    "coerce_numeric": "Cast to numeric (float)",
    "coerce_integer": "Cast to integer",
    "parse_dates": "Parse string to datetime",
    "sanitize_email": "Lowercase, trim, and validate email format",
    "normalize_phone": "Remove non-digit characters from phone",
    "standardize_boolean": "Normalize to True/False boolean",
    "regex_replace": "Apply regex substitution",
    "clip_or_flag": "Clip values to allowed range",
    "range_clip": "Clip values to allowed range",
    "column_rename": "Rename column",
    "deduplicate": "Remove duplicate rows by key",
    "deduplicate_all": "Remove fully duplicate rows",
    "flatten_nested": "Serialize nested JSON/dict to string",
    "validate_referential_integrity": "Validate foreign key relationship",
}

# ── Source type → target type inference ────────────────────────────────────────
TYPE_TRANSITIONS: dict[str, dict[str, str]] = {
    "coerce_numeric":    {"object": "float64", "str": "float64", "string": "float64"},
    "coerce_integer":   {"object": "int64",   "str": "int64",   "float64": "int64"},
    "parse_dates":      {"object": "datetime64[ns]", "str": "datetime64[ns]"},
    "standardize_boolean": {"object": "bool", "str": "bool", "int64": "bool"},
    "column_rename":    {},  # type unchanged
    "trim":             {"object": "object"},  # type unchanged
    "sanitize_email":   {"object": "object"},
    "normalize_phone":  {"object": "object"},
}


class SchemaLineageEnhancer:
    """Builds a rich column-level lineage report from ETL plan + assessment."""

    def __init__(self, etl_plan: dict[str, Any], assessment_result: dict[str, Any]):
        """
        Args:
            etl_plan:          Approved ETL Plan JSON from etl_planner.py
            assessment_result: Assessment JSON from intelligent_data_assessment.py
        """
        self.plan = etl_plan
        self.assessment = assessment_result

    # ── Public API ──────────────────────────────────────────────────────────────

    def build_lineage_report(self) -> dict[str, Any]:
        """
        Builds and returns the full lineage report dict.
        Structure:
          {
            plan_id: str,
            generated_at: str,
            datasets: {
              <dataset>: {
                columns: {
                  <col>: {
                    source_type: str,
                    target_type: str,
                    transforms: [str, ...],
                    transform_descriptions: [str, ...],
                    auto: bool,
                    issues_resolved: [str, ...]
                  }
                },
                rows_affected_estimate: int,
                manual_review_items: [...]
              }
            },
            global_steps: [...]
          }
        """
        report: dict[str, Any] = {
            "plan_id": self.plan.get("plan_id", "unknown"),
            "generated_at": self._now(),
            "engine": self.plan.get("engine", "python"),
            "datasets": {},
            "global_steps": self.plan.get("global_steps", []),
        }

        for dataset_name, dataset_plan in self.plan.get("datasets", {}).items():
            report["datasets"][dataset_name] = self._build_dataset_lineage(
                dataset_name, dataset_plan
            )

        return report

    def to_markdown(self, report: dict[str, Any]) -> str:
        """Converts lineage report to readable Markdown string."""
        lines = [
            f"# Schema Lineage Report",
            f"**Plan ID**: `{report['plan_id']}`  ",
            f"**Engine**: `{report['engine']}`  ",
            f"**Generated**: {report['generated_at']}\n",
        ]
        for dataset_name, dataset_data in report.get("datasets", {}).items():
            lines.append(f"## Dataset: `{dataset_name}`")
            lines.append(f"")
            lines.append(f"| Column | Source Type | Transforms | Target Type | Auto? |")
            lines.append(f"|--------|-------------|------------|-------------|-------|")
            for col, col_data in dataset_data.get("columns", {}).items():
                transforms = " → ".join(col_data.get("transforms", []))
                source_type = col_data.get("source_type", "unknown")
                target_type = col_data.get("target_type", source_type)
                auto = "✅" if col_data.get("auto") else "⚠️"
                lines.append(f"| `{col}` | `{source_type}` | {transforms} | `{target_type}` | {auto} |")

            manual = dataset_data.get("manual_review_items", [])
            if manual:
                lines.append(f"")
                lines.append(f"### ⚠️ Manual Review Required")
                for item in manual:
                    lines.append(f"- **{item.get('column', 'N/A')}**: {item.get('issue', '')} — {item.get('guidance', '')}")

            lines.append("")

        if report.get("global_steps"):
            lines.append("## Global Steps")
            for gstep in report["global_steps"]:
                action = gstep.get("action", "")
                desc = ACTION_DESCRIPTIONS.get(action, action)
                lines.append(f"- **{desc}**: `{gstep.get('from', '')}` → `{gstep.get('to', '')}`")

        return "\n".join(lines)

    def to_json(self, report: dict[str, Any]) -> str:
        """Serializes lineage report to JSON string."""
        return json.dumps(report, indent=2, default=str)

    def to_frontend_graph(self, report: dict[str, Any]) -> dict[str, Any]:
        """
        Converts lineage report to a graph structure for the frontend visualizer.
        Returns {nodes: [...], edges: [...]} compatible with React Flow / D3.
        """
        nodes = []
        edges = []
        node_id = 0

        for dataset_name, dataset_data in report.get("datasets", {}).items():
            # Dataset source node
            src_id = f"src_{dataset_name}"
            nodes.append({"id": src_id, "type": "source", "label": f"📂 {dataset_name}", "data": {}})

            for col, col_data in dataset_data.get("columns", {}).items():
                transforms = col_data.get("transforms", [])
                col_src_id = f"col_src_{dataset_name}_{col}"
                col_tgt_id = f"col_tgt_{dataset_name}_{col}"

                # Column source node
                nodes.append({
                    "id": col_src_id,
                    "type": "column_source",
                    "label": f"{col}\n({col_data.get('source_type', '?')})",
                    "data": {"dataset": dataset_name, "column": col, "type": col_data.get("source_type")},
                })
                edges.append({"source": src_id, "target": col_src_id, "label": ""})

                # Transform nodes
                prev_id = col_src_id
                for i, transform in enumerate(transforms):
                    t_id = f"t_{dataset_name}_{col}_{i}"
                    nodes.append({
                        "id": t_id,
                        "type": "transform",
                        "label": ACTION_DESCRIPTIONS.get(transform, transform),
                        "data": {"action": transform},
                    })
                    edges.append({"source": prev_id, "target": t_id, "label": ""})
                    prev_id = t_id

                # Column target node
                nodes.append({
                    "id": col_tgt_id,
                    "type": "column_target",
                    "label": f"{col}\n({col_data.get('target_type', col_data.get('source_type', '?'))})",
                    "data": {"dataset": dataset_name, "column": col, "type": col_data.get("target_type")},
                })
                edges.append({"source": prev_id, "target": col_tgt_id, "label": ""})

        return {"nodes": nodes, "edges": edges}

    # ── Private helpers ─────────────────────────────────────────────────────────

    def _build_dataset_lineage(self, dataset_name: str, dataset_plan: dict) -> dict:
        steps = dataset_plan.get("steps", [])
        manual_items = dataset_plan.get("manual_review", [])
        col_lineage: dict[str, dict] = {}

        # Get source types from assessment
        assessment_cols = self._get_assessment_cols(dataset_name)

        for step in sorted(steps, key=lambda s: s.get("order", 99)):
            action = step.get("action", "")
            col = step.get("column") or "__row__"
            is_auto = step.get("auto", True)

            if col not in col_lineage:
                col_lineage[col] = {
                    "source_type": assessment_cols.get(col, {}).get("dtype", "unknown"),
                    "target_type": assessment_cols.get(col, {}).get("dtype", "unknown"),
                    "transforms": [],
                    "transform_descriptions": [],
                    "auto": is_auto,
                    "issues_resolved": [],
                }

            col_lineage[col]["transforms"].append(action)
            col_lineage[col]["transform_descriptions"].append(
                ACTION_DESCRIPTIONS.get(action, action)
            )

            # Update target type if action causes a type transition
            transitions = TYPE_TRANSITIONS.get(action, {})
            src_type = col_lineage[col]["source_type"]
            if src_type in transitions:
                col_lineage[col]["target_type"] = transitions[src_type]
            elif transitions and src_type not in transitions:
                # Best guess: first value in transitions
                col_lineage[col]["target_type"] = list(transitions.values())[0] if transitions else src_type

            # Track resolved issues
            issues = assessment_cols.get(col, {}).get("issues", [])
            col_lineage[col]["issues_resolved"].extend(issues)

        return {
            "columns": col_lineage,
            "manual_review_items": manual_items,
            "rows_affected_estimate": self._estimate_rows(dataset_name),
        }

    def _get_assessment_cols(self, dataset_name: str) -> dict:
        """Extracts per-column info from the assessment result."""
        datasets = self.assessment.get("datasets", {})
        ds = datasets.get(dataset_name, {})
        columns = ds.get("columns", {})
        if not columns:
            # Try alternate structure
            for col_info in ds.get("column_analysis", []):
                col_name = col_info.get("column_name", col_info.get("name", ""))
                columns[col_name] = col_info
        return columns

    def _estimate_rows(self, dataset_name: str) -> int:
        """Returns row count from assessment for the dataset."""
        datasets = self.assessment.get("datasets", {})
        ds = datasets.get(dataset_name, {})
        return ds.get("row_count", ds.get("rows", 0))

    def _now(self) -> str:
        from datetime import datetime
        return datetime.now().strftime("%Y-%m-%d %H:%M:%S")
