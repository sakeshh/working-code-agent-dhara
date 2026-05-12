"""
pyspark_codegen.py — Agent Dhara Phase 2
Generates PySpark ETL scripts from an ETL Plan JSON.

Input:  etl_plan dict (produced by etl_planner.py)
Output: PySpark source code string per dataset
"""

from datetime import datetime
from typing import Any


# ── Spark type mapping from pandas/Python inferred types ───────────────────────
TYPE_MAP: dict[str, str] = {
    "int": "IntegerType()",
    "int64": "LongType()",
    "float": "DoubleType()",
    "float64": "DoubleType()",
    "bool": "BooleanType()",
    "boolean": "BooleanType()",
    "date": "DateType()",
    "datetime": "TimestampType()",
    "string": "StringType()",
    "str": "StringType()",
    "object": "StringType()",
}


class PySparkCodegen:
    """Generates PySpark ETL transformation scripts from an approved ETL Plan JSON."""

    def __init__(self, etl_plan: dict[str, Any], target_mode: str = "return"):
        """
        Args:
            etl_plan:    ETL Plan dict from etl_planner.py
            target_mode: 'return'     — function returns cleaned DataFrame
                         'new_file'   — writes to parquet at target_path
                         'delta'      — writes to Delta Lake table
                         'hive'       — writes to Hive table
        """
        self.plan = etl_plan
        self.target_mode = target_mode
        self.target_path = etl_plan.get("target_path", "output/cleaned/")
        self.generated_at = datetime.now().strftime("%Y-%m-%d %H:%M:%S")

    # ── Public API ──────────────────────────────────────────────────────────────

    def generate(self) -> dict[str, str]:
        """Returns {dataset_name: pyspark_code_string}."""
        results = {}
        for dataset_name, dataset_plan in self.plan.get("datasets", {}).items():
            results[dataset_name] = self._generate_dataset_script(dataset_name, dataset_plan)
        return results

    def generate_combined(self) -> str:
        """Returns a single PySpark file with all dataset functions + main()."""
        scripts = self.generate()
        header = self._file_header()
        imports = self._imports()
        body = "\n\n".join(scripts.values())
        main = self._main_block(list(scripts.keys()))
        return f"{header}\n{imports}\n\n{body}\n\n{main}"

    # ── Private helpers ─────────────────────────────────────────────────────────

    def _file_header(self) -> str:
        plan_id = self.plan.get("plan_id", "unknown")
        datasets = ", ".join(self.plan.get("datasets", {}).keys())
        return (
            f"# =================================================================\n"
            f"# Agent Dhara — Auto-Generated PySpark ETL Script\n"
            f"# Plan ID  : {plan_id}\n"
            f"# Datasets : {datasets}\n"
            f"# Generated: {self.generated_at}\n"
            f"# Target   : {self.target_mode}\n"
            f"# WARNING  : Review before running on production clusters.\n"
            f"# =================================================================\n"
        )

    def _imports(self) -> str:
        return (
            "from pyspark.sql import SparkSession, DataFrame\n"
            "from pyspark.sql import functions as F\n"
            "from pyspark.sql.types import (\n"
            "    IntegerType, LongType, DoubleType, FloatType,\n"
            "    BooleanType, StringType, DateType, TimestampType\n"
            ")\n"
        )

    def _generate_dataset_script(self, name: str, dataset_plan: dict) -> str:
        steps = dataset_plan.get("steps", [])
        fn_name = f"transform_{name.lower().replace(' ', '_').replace('-', '_')}"
        lines = []
        lines.append(f"def {fn_name}(df: DataFrame) -> DataFrame:")
        lines.append(f'    """Auto-generated PySpark ETL transform for dataset: {name}"""')

        if not steps:
            lines.append("    return df  # No auto-fixable steps found")
            return "\n".join(lines) + "\n"

        prev_group = None
        for step in sorted(steps, key=lambda s: s.get("order", 99)):
            action = step.get("action", "")
            col = step.get("column") or ""
            params = step.get("params", {})

            group = action.split("_")[0]
            if group != prev_group:
                lines.append(f"\n    # ── {action.replace('_', ' ').title()} ──")
                prev_group = group

            code = self._render_action(action, col, params, name)
            if code:
                lines.append(code.rstrip())

        # Global RI checks
        for gstep in self.plan.get("global_steps", []):
            if gstep.get("action") == "validate_referential_integrity":
                from_parts = gstep.get("from", "").split(".")
                to_parts = gstep.get("to", "").split(".")
                if len(from_parts) == 2 and from_parts[0] == name and len(to_parts) == 2:
                    lines.append(f"\n    # ── Referential Integrity: {from_parts[1]} → {to_parts[0]}.{to_parts[1]} ──")
                    lines.append(
                        f"    # NOTE: join with df_{to_parts[0]} before calling this function\n"
                        f"    _valid = df_{to_parts[0]}.select('{to_parts[1]}').distinct()\n"
                        f"    df = df.join(_valid, df['{from_parts[1]}'] == _valid['{to_parts[1]}'], 'left_semi')\n"
                    )

        lines.append("\n    return df")
        return "\n".join(lines) + "\n"

    def _render_action(self, action: str, col: str, params: dict, dataset: str) -> str:
        if action == "trim":
            return f"    df = df.withColumn('{col}', F.trim(F.col('{col}')))"

        elif action == "fill_or_drop":
            return (
                f"    _mode_{col.replace(' ','_')} = df.groupBy('{col}').count().orderBy('count', ascending=False).first()\n"
                f"    if _mode_{col.replace(' ','_')}:\n"
                f"        df = df.fillna({{'{col}': _mode_{col.replace(' ','_')}['{col}']}})"
            )

        elif action == "drop_nulls":
            return f"    df = df.dropna(subset=['{col}'])"

        elif action == "coerce_numeric":
            return f"    df = df.withColumn('{col}', F.col('{col}').cast(DoubleType()))"

        elif action == "coerce_integer":
            return f"    df = df.withColumn('{col}', F.col('{col}').cast(LongType()))"

        elif action == "parse_dates":
            return (
                f"    df = df.withColumn('{col}', F.to_timestamp(F.col('{col}')))"
            )

        elif action == "sanitize_email":
            return (
                f"    df = df.withColumn('{col}', F.lower(F.trim(F.col('{col}'))))\n"
                f"    df = df.withColumn('{col}', F.when(\n"
                f"        F.col('{col}').rlike(r'^[^@\\\\s]+@[^@\\\\s]+\\\\.[^@\\\\s]+$'),\n"
                f"        F.col('{col}')\n"
                f"    ).otherwise(F.lit(None)))"
            )

        elif action == "normalize_phone":
            return f"    df = df.withColumn('{col}', F.regexp_replace(F.col('{col}'), r'[^\\\\d+]', ''))"

        elif action == "standardize_boolean":
            return (
                f"    df = df.withColumn('{col}', F.when(\n"
                f"        F.lower(F.col('{col}').cast(StringType())).isin(['true','yes','1','y']), F.lit(True)\n"
                f"    ).when(\n"
                f"        F.lower(F.col('{col}').cast(StringType())).isin(['false','no','0','n']), F.lit(False)\n"
                f"    ).otherwise(F.lit(None)).cast(BooleanType()))"
            )

        elif action == "column_rename":
            new_name = params.get("new_name", col)
            return f"    df = df.withColumnRenamed('{col}', '{new_name}')"

        elif action in ("clip_or_flag", "range_clip"):
            min_val = params.get("min_val", 0)
            max_val = params.get("max_val", 999999)
            return (
                f"    df = df.withColumn('{col}', F.greatest(F.lit({min_val}), F.least(F.lit({max_val}), F.col('{col}'))))"
            )

        elif action in ("deduplicate", "deduplicate_all"):
            dedup_key = params.get("dedup_key", [col] if col else [])
            if dedup_key:
                key_str = str(dedup_key)
                return f"    df = df.dropDuplicates({key_str})"
            else:
                return f"    df = df.dropDuplicates()"

        elif action == "flatten_nested":
            return f"    df = df.withColumn('{col}', F.to_json(F.col('{col}')))"

        else:
            return f"    # TODO: manual action '{action}' on column '{col}'"

    def _main_block(self, dataset_names: list[str]) -> str:
        lines = [
            "\ndef main():",
            '    \"\"\"Initialize Spark and run all transforms. Edit source paths before executing.\"\"\"',
            "    spark = SparkSession.builder.appName('AgentDhara_ETL').getOrCreate()",
            "",
        ]
        for name in dataset_names:
            fn = f"transform_{name.lower().replace(' ', '_').replace('-', '_')}"
            var = name.lower().replace(" ", "_").replace("-", "_")
            lines.append(f"    # TODO: replace path with your actual data source")
            lines.append(f"    df_{var} = spark.read.csv('data/{name}.csv', header=True, inferSchema=True)")
            lines.append(f"    df_{var} = {fn}(df_{var})")

            if self.target_mode == "return":
                lines.append(f"    df_{var}.show(5)")
            elif self.target_mode == "new_file":
                out = self.target_path.rstrip("/") + f"/{name}_cleaned"
                lines.append(f"    df_{var}.write.mode('overwrite').parquet('{out}')")
                lines.append(f"    print('Saved cleaned {name} → {out}')")
            elif self.target_mode == "delta":
                out = self.target_path.rstrip("/") + f"/{name}_cleaned"
                lines.append(f"    df_{var}.write.format('delta').mode('overwrite').save('{out}')")
            elif self.target_mode == "hive":
                lines.append(f"    df_{var}.write.mode('overwrite').saveAsTable('{name}_cleaned')")
            lines.append("")

        lines.append("\nif __name__ == '__main__':")
        lines.append("    main()")
        return "\n".join(lines) + "\n"
