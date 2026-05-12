"""
python_codegen.py — Agent Dhara Phase 2
Generates pandas Python ETL scripts from an ETL Plan JSON.

Input:  etl_plan dict (produced by etl_planner.py)
Output: Python source code string (validated by code_validator.py before delivery)
"""

import ast
import json
from datetime import datetime
from typing import Any


# ── Action → pandas code template map ──────────────────────────────────────────
ACTION_TEMPLATES: dict[str, str] = {
    "trim": "    df['{col}'] = df['{col}'].astype(str).str.strip()\n    df['{col}'] = df['{col}'].replace('nan', None)\n",
    "fill_or_drop": "    df['{col}'] = df['{col}'].fillna(df['{col}'].mode()[0] if not df['{col}'].mode().empty else None)\n",
    "drop_nulls": "    df = df.dropna(subset=['{col}'])\n",
    "coerce_numeric": "    df['{col}'] = pd.to_numeric(df['{col}'], errors='coerce')\n",
    "coerce_integer": "    df['{col}'] = pd.to_numeric(df['{col}'], errors='coerce').astype('Int64')\n",
    "parse_dates": "    df['{col}'] = pd.to_datetime(df['{col}'], infer_datetime_format=True, errors='coerce')\n",
    "sanitize_email": (
        "    df['{col}'] = df['{col}'].astype(str).str.lower().str.strip()\n"
        "    _mask_{col} = ~df['{col}'].str.match(r'^[^@\\s]+@[^@\\s]+\\.[^@\\s]+$', na=False)\n"
        "    df.loc[_mask_{col}, '{col}'] = None\n"
    ),
    "normalize_phone": "    df['{col}'] = df['{col}'].astype(str).str.replace(r'[^\\d+]', '', regex=True)\n",
    "standardize_boolean": (
        "    _bool_map_{col} = {{'true': True, 'false': False, '1': True, '0': False,\n"
        "                       'yes': True, 'no': False, 'y': True, 'n': False}}\n"
        "    df['{col}'] = df['{col}'].astype(str).str.lower().map(_bool_map_{col})\n"
    ),
    "regex_replace": "    df['{col}'] = df['{col}'].astype(str).str.replace(r'{pattern}', '{replacement}', regex=True)\n",
    "clip_or_flag": "    df['{col}'] = df['{col}'].clip(lower={min_val}, upper={max_val})\n",
    "range_clip": "    df['{col}'] = df['{col}'].clip(lower={min_val}, upper={max_val})\n",
    "column_rename": "    df = df.rename(columns={{'{col}': '{new_name}'}})\n",
    "deduplicate": "    df = df.drop_duplicates(subset={dedup_key}, keep='first').reset_index(drop=True)\n",
    "deduplicate_all": "    df = df.drop_duplicates().reset_index(drop=True)\n",
    "flatten_nested": "    df['{col}'] = df['{col}'].apply(lambda x: json.dumps(x) if isinstance(x, (dict, list)) else x)\n",
    "validate_referential_integrity": (
        "    # Referential integrity: {from_col} → {to_dataset}.{to_col}\n"
        "    # NOTE: Load '{to_dataset}' df before calling this function\n"
        "    _valid_{col} = set(df_{to_dataset}['{to_col}'].dropna())\n"
        "    _orphan_mask = ~df['{from_col}'].isin(_valid_{col}) & df['{from_col}'].notna()\n"
        "    if _orphan_mask.any():\n"
        "        print(f\"⚠ {{_orphan_mask.sum()}} orphan rows in '{from_col}' not found in {to_dataset}.{to_col}\")\n"
    ),
}


class PythonCodegen:
    """Generates pandas Python ETL scripts from an approved ETL Plan JSON."""

    def __init__(self, etl_plan: dict[str, Any], target_mode: str = "return"):
        """
        Args:
            etl_plan:    ETL Plan dict from etl_planner.py
            target_mode: 'return'     — function returns cleaned df (default)
                         'overwrite'  — saves df back to source path
                         'new_file'   — saves df to etl_plan['target_path']
                         'sql_table'  — writes df to SQL table
        """
        self.plan = etl_plan
        self.target_mode = target_mode
        self.target_path = etl_plan.get("target_path", "output/cleaned/")
        self.generated_at = datetime.now().strftime("%Y-%m-%d %H:%M:%S")

    # ── Public API ──────────────────────────────────────────────────────────────

    def generate(self) -> dict[str, str]:
        """Returns {dataset_name: python_code_string} for every dataset in the plan."""
        results = {}
        for dataset_name, dataset_plan in self.plan.get("datasets", {}).items():
            results[dataset_name] = self._generate_dataset_script(dataset_name, dataset_plan)
        return results

    def generate_combined(self) -> str:
        """Returns a single Python file containing all dataset transform functions + a main() block."""
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
            f"# Agent Dhara — Auto-Generated ETL Script\n"
            f"# Plan ID  : {plan_id}\n"
            f"# Datasets : {datasets}\n"
            f"# Generated: {self.generated_at}\n"
            f"# Target   : {self.target_mode}\n"
            f"# WARNING  : Review before running on production data.\n"
            f"# =================================================================\n"
        )

    def _imports(self) -> str:
        return (
            "import json\n"
            "import pandas as pd\n"
            "from pathlib import Path\n"
        )

    def _generate_dataset_script(self, name: str, dataset_plan: dict) -> str:
        steps = dataset_plan.get("steps", [])
        fn_name = f"transform_{name.lower().replace(' ', '_').replace('-', '_')}"
        lines = []
        lines.append(f"def {fn_name}(df: pd.DataFrame) -> pd.DataFrame:")
        lines.append(f'    """Auto-generated ETL transform for dataset: {name}"""')

        if not steps:
            lines.append("    return df  # No auto-fixable steps found")
            return "\n".join(lines) + "\n"

        prev_action_group = None
        for step in sorted(steps, key=lambda s: s.get("order", 99)):
            action = step.get("action", "")
            col = step.get("column") or ""
            params = step.get("params", {})

            # Section comment when action group changes
            action_group = action.split("_")[0]
            if action_group != prev_action_group:
                lines.append(f"\n    # ── {action.replace('_', ' ').title()} ──")
                prev_action_group = action_group

            code = self._render_action(action, col, params, name)
            if code:
                lines.append(code.rstrip())

        # Global steps (referential integrity etc.)
        for gstep in self.plan.get("global_steps", []):
            if gstep.get("action") == "validate_referential_integrity":
                from_parts = gstep.get("from", "").split(".")
                to_parts = gstep.get("to", "").split(".")
                if from_parts[0] == name and len(from_parts) == 2 and len(to_parts) == 2:
                    lines.append(f"\n    # ── Referential Integrity ──")
                    ri_code = ACTION_TEMPLATES["validate_referential_integrity"].format(
                        col=from_parts[1],
                        from_col=from_parts[1],
                        to_dataset=to_parts[0],
                        to_col=to_parts[1],
                    )
                    lines.append(ri_code.rstrip())

        lines.append("\n    return df")
        return "\n".join(lines) + "\n"

    def _render_action(self, action: str, col: str, params: dict, dataset: str) -> str:
        template = ACTION_TEMPLATES.get(action)
        if not template:
            return f"    # TODO: manual action '{action}' on column '{col}' — not auto-generated\n"

        safe_col = col.replace(" ", "_").replace("-", "_")

        try:
            rendered = template.format(
                col=col,
                safe_col=safe_col,
                new_name=params.get("new_name", col),
                pattern=params.get("pattern", ""),
                replacement=params.get("replacement", ""),
                min_val=params.get("min_val", 0),
                max_val=params.get("max_val", 999999),
                dedup_key=json.dumps(params.get("dedup_key", [col])),
                from_col=params.get("from_col", col),
                to_dataset=params.get("to_dataset", dataset),
                to_col=params.get("to_col", "id"),
            )
        except KeyError:
            rendered = f"    # ERROR rendering action '{action}' for col '{col}' — check params\n"

        return rendered

    def _main_block(self, dataset_names: list[str]) -> str:
        lines = ["\ndef main():", "    \"\"\"Run all transforms. Edit source paths before executing.\"\"\"", ""]
        for name in dataset_names:
            fn = f"transform_{name.lower().replace(' ', '_').replace('-', '_')}"
            var = name.lower().replace(" ", "_").replace("-", "_")
            lines.append(f"    # TODO: replace path below with your actual data source")
            lines.append(f"    df_{var} = pd.read_csv('data/{name}.csv')")
            lines.append(f"    df_{var} = {fn}(df_{var})")

            if self.target_mode == "return":
                lines.append(f"    print(df_{var}.head())")
            elif self.target_mode in ("new_file", "overwrite"):
                out = self.target_path.rstrip("/") + f"/{name}_cleaned.csv"
                lines.append(f"    df_{var}.to_csv('{out}', index=False)")
                lines.append(f"    print(f'Saved cleaned {name} → {out}')")
            elif self.target_mode == "sql_table":
                lines.append(f"    # df_{var}.to_sql('{name}_cleaned', con=engine, if_exists='replace', index=False)")
            lines.append("")

        lines.append("\nif __name__ == '__main__':")
        lines.append("    main()")
        return "\n".join(lines) + "\n"


# ── Standalone validation helper ────────────────────────────────────────────────

def validate_python(code: str) -> tuple[bool, str]:
    """Parses generated Python code with ast.parse. Returns (ok, error_message)."""
    try:
        ast.parse(code)
        return True, ""
    except SyntaxError as e:
        return False, f"SyntaxError at line {e.lineno}: {e.msg}"
