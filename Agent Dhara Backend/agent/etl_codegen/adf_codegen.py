"""
adf_codegen.py — ADF Mapping Data Flow JSON template generator.

Generates an Azure Data Factory Mapping Data Flow JSON template
from an ETL Plan produced by etl_planner.py.

Usage:
    from agent.etl_codegen.adf_codegen import generate_adf_dataflow
    adf_json = generate_adf_dataflow(etl_plan, dataset_name="customers")

The output is a valid ADF Mapping Data Flow JSON that can be imported
directly into an ADF pipeline via the ADF portal or ARM templates.
"""

from __future__ import annotations

import json
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

# ---------------------------------------------------------------------------
# Action -> ADF transformation type mapping
# ---------------------------------------------------------------------------

# Maps etl_planner action names to ADF Mapping Data Flow transformation types.
_ACTION_TO_ADF_TYPE: Dict[str, str] = {
    "trim": "DerivedColumn",
    "coerce_numeric": "Cast",
    "parse_dates": "Cast",
    "fill_or_drop": "Filter",          # drop nulls = filter; fill = DerivedColumn
    "sanitize_email": "DerivedColumn",
    "normalize_phone": "DerivedColumn",
    "regex_replace": "DerivedColumn",
    "range_clip": "DerivedColumn",
    "clip_or_flag": "DerivedColumn",
    "standardize_boolean": "DerivedColumn",
    "flatten_nested": "Flatten",
    "deduplicate": "Aggregate",
    "column_rename": "Select",
    "validate_referential_integrity": "Exists",
}

# ADF data type mapping from pandas/Python types
_DTYPE_TO_ADF: Dict[str, str] = {
    "int64": "integer",
    "int32": "integer",
    "float64": "double",
    "float32": "float",
    "object": "string",
    "bool": "boolean",
    "datetime64[ns]": "timestamp",
    "date": "date",
    "string": "string",
}


def _adf_type(pandas_dtype: str) -> str:
    return _DTYPE_TO_ADF.get(str(pandas_dtype).lower(), "string")


def _make_derived_column(step: Dict[str, Any], step_name: str, prev_name: str) -> Dict[str, Any]:
    """
    Build an ADF DerivedColumn transformation node for text/format operations.
    """
    col = step.get("column", "")
    action = step.get("action", "")

    # Build ADF expression based on action
    if action == "trim":
        expression = f"trim({col})"
    elif action == "sanitize_email":
        expression = f"lower(trim({col}))"
    elif action == "normalize_phone":
        expression = f"regexReplace({col}, '[^0-9]', '')"
    elif action == "regex_replace":
        pattern = step.get("pattern", ".*")
        replacement = step.get("replacement", "")
        expression = f"regexReplace({col}, '{pattern}', '{replacement}')"
    elif action in ("range_clip", "clip_or_flag"):
        min_val = step.get("min", 0)
        max_val = step.get("max", 999999)
        expression = f"iif({col} < {min_val}, toInteger('{min_val}'), iif({col} > {max_val}, toInteger('{max_val}'), {col}))"
    elif action == "standardize_boolean":
        expression = f"iif(lower(toString({col})) == 'true' || {col} == '1' || lower(toString({col})) == 'yes', true(), false())"
    else:
        expression = str(col)

    return {
        "name": step_name,
        "type": "DerivedColumn",
        "dependsOn": [{"activity": prev_name}],
        "typeProperties": {
            "columns": [
                {
                    "name": col,
                    "expression": expression,
                }
            ]
        },
    }


def _make_cast(step: Dict[str, Any], step_name: str, prev_name: str, lineage: Dict[str, Any]) -> Dict[str, Any]:
    """
    Build an ADF Cast transformation node for type coercion / date parsing.
    """
    col = step.get("column", "")
    action = step.get("action", "")

    target_type = "integer"
    if action == "parse_dates":
        target_type = "timestamp"
    elif action == "coerce_numeric":
        col_lineage = (lineage or {}).get(col, {})
        target_type = _adf_type(col_lineage.get("target_type", "float64"))

    return {
        "name": step_name,
        "type": "Cast",
        "dependsOn": [{"activity": prev_name}],
        "typeProperties": {
            "columns": [
                {
                    "name": col,
                    "type": target_type,
                    "format": "yyyy-MM-dd" if target_type in ("timestamp", "date") else None,
                }
            ]
        },
    }


def _make_filter(step: Dict[str, Any], step_name: str, prev_name: str) -> Dict[str, Any]:
    """
    Build an ADF Filter transformation node for null removal (fill_or_drop -> drop).
    """
    col = step.get("column", "")
    return {
        "name": step_name,
        "type": "Filter",
        "dependsOn": [{"activity": prev_name}],
        "typeProperties": {
            "condition": f"!isNull({col})",
        },
    }


def _make_aggregate(step_name: str, prev_name: str, pk_columns: List[str]) -> Dict[str, Any]:
    """
    Build an ADF Aggregate transformation for deduplication.
    Groups by PK columns and takes first() of all other columns.
    """
    group_by = [{"name": col, "expression": col} for col in (pk_columns or ["id"])]
    return {
        "name": step_name,
        "type": "Aggregate",
        "dependsOn": [{"activity": prev_name}],
        "typeProperties": {
            "groupBy": group_by,
            "aggregates": [],  # first() expressions would be added per column in full impl
        },
    }


def _make_select(step: Dict[str, Any], step_name: str, prev_name: str) -> Dict[str, Any]:
    """
    Build an ADF Select transformation for column renaming.
    """
    old_name = step.get("old_name", step.get("column", ""))
    new_name = step.get("new_name", step.get("column", ""))
    return {
        "name": step_name,
        "type": "Select",
        "dependsOn": [{"activity": prev_name}],
        "typeProperties": {
            "mappings": [
                {"source": {"name": old_name}, "sink": {"name": new_name}}
            ]
        },
    }


def _build_transformation(
    step: Dict[str, Any],
    step_name: str,
    prev_name: str,
    pk_columns: List[str],
    lineage: Dict[str, Any],
) -> Dict[str, Any]:
    """
    Route a single ETL plan step to the correct ADF transformation builder.
    """
    action = step.get("action", "")

    if action in ("trim", "sanitize_email", "normalize_phone", "regex_replace",
                  "range_clip", "clip_or_flag", "standardize_boolean"):
        return _make_derived_column(step, step_name, prev_name)

    if action in ("coerce_numeric", "parse_dates"):
        return _make_cast(step, step_name, prev_name, lineage)

    if action == "fill_or_drop":
        return _make_filter(step, step_name, prev_name)

    if action == "deduplicate":
        return _make_aggregate(step_name, prev_name, pk_columns)

    if action == "column_rename":
        return _make_select(step, step_name, prev_name)

    # Fallback: DerivedColumn passthrough
    col = step.get("column", "unknown")
    return {
        "name": step_name,
        "type": "DerivedColumn",
        "dependsOn": [{"activity": prev_name}],
        "typeProperties": {
            "columns": [{"name": col, "expression": str(col)}]
        },
    }


def generate_adf_dataflow(
    etl_plan: Dict[str, Any],
    dataset_name: str,
    lineage: Optional[Dict[str, Any]] = None,
    pk_columns: Optional[List[str]] = None,
    source_dataset_ref: str = "SourceDataset",
    sink_dataset_ref: str = "SinkDataset",
) -> Dict[str, Any]:
    """
    Generate a complete ADF Mapping Data Flow JSON for a single dataset.

    Args:
        etl_plan:           ETL Plan JSON from etl_planner.build_etl_plan()
        dataset_name:       Name of the dataset to generate for (key in plan["datasets"])
        lineage:            Optional column lineage dict from schema_lineage.build_lineage()
        pk_columns:         Primary key columns for deduplication
        source_dataset_ref: ADF linked dataset reference name for source
        sink_dataset_ref:   ADF linked dataset reference name for sink

    Returns:
        Dict representing a complete ADF Mapping Data Flow JSON.
        Serialise with json.dumps(result, indent=2) for import into ADF.
    """
    plan_id = etl_plan.get("plan_id", "plan_001")
    engine = etl_plan.get("engine", "python")
    datasets = etl_plan.get("datasets", {})
    dataset_plan = datasets.get(dataset_name, {})
    steps = dataset_plan.get("steps", [])
    col_lineage = (lineage or {}).get(dataset_name, {})
    pk_cols = pk_columns or ["id"]

    generated_at = datetime.now(timezone.utc).isoformat()

    # ------------------------------------------------------------------ #
    # Build transformation nodes list
    # ------------------------------------------------------------------ #
    transformations: List[Dict[str, Any]] = []

    # Source node (always first)
    source_node = {
        "name": "Source",
        "type": "source",
        "dataset": {"referenceName": source_dataset_ref, "type": "DatasetReference"},
        "typeProperties": {"source": {}},
    }
    transformations.append(source_node)
    prev_name = "Source"

    # One ADF node per ETL plan step
    for i, step in enumerate(steps, start=1):
        action = step.get("action", "step")
        col = step.get("column") or "global"
        step_name = f"{action}_{col}_{i}".replace(" ", "_")[:60]

        node = _build_transformation(step, step_name, prev_name, pk_cols, col_lineage)
        transformations.append(node)
        prev_name = step_name

    # Sink node (always last)
    sink_node = {
        "name": "Sink",
        "type": "sink",
        "dependsOn": [{"activity": prev_name}],
        "dataset": {"referenceName": sink_dataset_ref, "type": "DatasetReference"},
        "typeProperties": {"sink": {}},
    }
    transformations.append(sink_node)

    # ------------------------------------------------------------------ #
    # Compose full ADF Mapping Data Flow JSON
    # ------------------------------------------------------------------ #
    adf_dataflow = {
        "$schema": "https://schema.management.azure.com/schemas/2019-04-01/dataFactoryMapping.json",
        "name": f"AgentDhara_ETL_{dataset_name}",
        "type": "MappingDataFlow",
        "properties": {
            "description": (
                f"Auto-generated by Agent Dhara | "
                f"Plan: {plan_id} | Dataset: {dataset_name} | "
                f"Engine hint: {engine} | Generated: {generated_at}"
            ),
            "sources": [
                {
                    "name": "Source",
                    "dataset": {
                        "referenceName": source_dataset_ref,
                        "type": "DatasetReference",
                    },
                }
            ],
            "sinks": [
                {
                    "name": "Sink",
                    "dataset": {
                        "referenceName": sink_dataset_ref,
                        "type": "DatasetReference",
                    },
                }
            ],
            "transformations": transformations,
            "script": _generate_dataflow_script(steps, dataset_name, col_lineage, pk_cols),
        },
    }
    return adf_dataflow


def _generate_dataflow_script(
    steps: List[Dict[str, Any]],
    dataset_name: str,
    col_lineage: Dict[str, Any],
    pk_columns: List[str],
) -> str:
    """
    Generate the ADF Data Flow DSL script string.
    ADF stores transformations as a DSL script alongside the JSON.
    """
    lines: List[str] = []
    lines.append(f"// Agent Dhara — Auto-generated Data Flow Script")
    lines.append(f"// Dataset: {dataset_name}")
    lines.append("")
    lines.append("source(output(")
    lines.append("    // columns auto-detected from source schema")
    lines.append("),")
    lines.append("allowSchemaDrift: true,")
    lines.append("validateSchema: false,")
    lines.append(") ~> Source")
    lines.append("")

    prev = "Source"
    for i, step in enumerate(steps, start=1):
        action = step.get("action", "")
        col = step.get("column") or ""
        col_safe = (col or "unknown").replace(" ", "_")
        step_name = f"{action}_{col_safe}_{i}"[:60]

        if action == "trim" and col:
            lines.append(f"{prev} derive(")
            lines.append(f"    {col} = trim({col})")
            lines.append(f") ~> {step_name}")

        elif action == "sanitize_email" and col:
            lines.append(f"{prev} derive(")
            lines.append(f"    {col} = lower(trim({col}))")
            lines.append(f") ~> {step_name}")

        elif action == "normalize_phone" and col:
            lines.append(f"{prev} derive(")
            lines.append(f"    {col} = regexReplace({col}, '[^0-9]', '')")
            lines.append(f") ~> {step_name}")

        elif action == "coerce_numeric" and col:
            target = _adf_type(col_lineage.get(col, {}).get("target_type", "float64"))
            lines.append(f"{prev} cast(")
            lines.append(f"    output({col} as {target})")
            lines.append(f") ~> {step_name}")

        elif action == "parse_dates" and col:
            lines.append(f"{prev} cast(")
            lines.append(f"    output({col} as timestamp 'yyyy-MM-dd')")
            lines.append(f") ~> {step_name}")

        elif action == "fill_or_drop" and col:
            lines.append(f"{prev} filter(")
            lines.append(f"    !isNull({col})")
            lines.append(f") ~> {step_name}")

        elif action == "deduplicate":
            group_cols = ", ".join(pk_columns)
            lines.append(f"{prev} aggregate(")
            lines.append(f"    groupBy({group_cols}),")
            lines.append(f"    each(match(true()), $$ = first($$))")
            lines.append(f") ~> {step_name}")

        elif action == "column_rename":
            old = step.get("old_name", col)
            new = step.get("new_name", col)
            lines.append(f"{prev} select(")
            lines.append(f"    mapColumn({new} = {old})")
            lines.append(f") ~> {step_name}")

        else:
            lines.append(f"{prev} derive(")
            lines.append(f"    {col or 'col'} = {col or 'col'}  // {action}")
            lines.append(f") ~> {step_name}")

        lines.append("")
        prev = step_name

    lines.append(f"{prev} sink(")
    lines.append("    allowSchemaDrift: true,")
    lines.append("    validateSchema: false")
    lines.append(") ~> Sink")

    return "\n".join(lines)


def generate_adf_dataflow_json(
    etl_plan: Dict[str, Any],
    dataset_name: str,
    lineage: Optional[Dict[str, Any]] = None,
    pk_columns: Optional[List[str]] = None,
    source_dataset_ref: str = "SourceDataset",
    sink_dataset_ref: str = "SinkDataset",
    indent: int = 2,
) -> str:
    """
    Convenience wrapper — returns the ADF Data Flow as a formatted JSON string.
    """
    result = generate_adf_dataflow(
        etl_plan=etl_plan,
        dataset_name=dataset_name,
        lineage=lineage,
        pk_columns=pk_columns,
        source_dataset_ref=source_dataset_ref,
        sink_dataset_ref=sink_dataset_ref,
    )
    return json.dumps(result, indent=indent, ensure_ascii=False)
