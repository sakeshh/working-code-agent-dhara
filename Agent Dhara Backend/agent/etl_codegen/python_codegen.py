"""
python_codegen.py — Agent Dhara Phase 2
Generates pandas Python ETL scripts from an ETL Plan JSON.

Input:  etl_plan dict (produced by etl_planner.py)
Output: Python source code string (validated by code_validator.py before delivery)

Patches applied (2026-05-12):
  - Removed deprecated infer_datetime_format=True (pandas 2.2+ removed it)
  - Fixed unsafe variable names: use safe_col (no spaces/hyphens) for Python
    identifiers in _mask_*, _bool_map_*, df_* — was using raw col name before
  - Guarded global_steps 'from'/'to' split against empty/missing keys
"""

import ast
import json
from datetime import datetime
from typing import Any


# ── Action → pandas code template map ──────────────────────────────────────────
# IMPORTANT: {col} = actual DataFrame column name (for df['{col}']),
#            {safe_col} = Python-safe identifier (no spaces/hyphens) for variable names.
ACTION_TEMPLATES: dict[str, str] = {
    "trim": (
        "    df['{col}'] = df['{col}'].astype(str).str.strip()\n"
        "    df['{col}'] = df['{col}'].replace('nan', None)\n"
    ),
    "fill_or_drop": (
        "    df['{col}'] = df['{col}'].fillna("
        "df['{col}'].mode()[0] if not df['{col}'].mode().empty else None)\n"
    ),
    "drop_nulls": "    df = df.dropna(subset=['{col}'])\n",
    "coerce_numeric": "    df['{col}'] = pd.to_numeric(df['{col}'], errors='coerce')\n",
    "coerce_integer": (
        "    df['{col}'] = pd.to_numeric(df['{col}'], errors='coerce').astype('Int64')\n"
    ),
    # FIX #1: removed infer_datetime_format=True — deprecated and removed in pandas 2.2+
    "parse_dates": (
        "    df['{col}'] = pd.to_datetime(df['{col}'], errors='coerce')\n"
    ),
    # FIX #2: use {safe_col} for _mask_ variable name — raw col may have spaces/hyphens
    "sanitize_email": (
        "    df['{col}'] = df['{col}'].astype(str).str.lower().str.strip()\n"
        "    _mask_{safe_col} = ~df['{col}'].str.match("
        "r'^[^@\\s]+@[^@\\s]+\\.[^@\\s]+$', na=False)\n"
        "    df.loc[_mask_{safe_col}, '{col}'] = None\n"
    ),
    "normalize_phone": (
        "    df['{col}'] = df['{col}'].astype(str).str.replace("
        "r'[^\\d+]', '', regex=True)\n"
    ),
    # FIX #3: use {safe_col} for _bool_map_ variable name
    "standardize_boolean": (
        "    _bool_map_{safe_col} = {{'true': True, 'false': False, "
        "'1': True, '0': False,\n"
        "                        'yes': True, 'no': False, "
        "'y': True, 'n': False}}\n"
        "    df['{col}'] = df['{col}'].astype(str).str.lower()"
        ".map(_bool_map_{safe_col})\n"
    ),
    "regex_replace": (
        "    df['{col}'] = df['{col}'].astype(str).str.replace("
        "r'{pattern}', '{replacement}', regex=True)\n"
    ),
    "clip_or_flag": (
        "    df['{col}'] = df['{col}'].clip(lower={min_val}, upper={max_val})\n"
    ),
    "range_clip": (
        "    df['{col}'] = df['{col}'].clip(lower={min_val}, upper={max_val})\n"
    ),
    "column_rename": (
        "    df = df.rename(columns={{'{col}': '{new_name}'}})\n"
    ),
    "deduplicate": (
        "    df = df.drop_duplicates(subset={dedup_key}, keep='first')"
        ".reset_index(drop=True)\n"
    ),
    "deduplicate_all": (
        "    df = df.drop_duplicates().reset_index(drop=True)\n"
    ),
    "flatten_nested": (
        "    df['{col}'] = df['{col}'].apply("
        "lambda x: json.dumps(x) if isinstance(x, (dict, list)) else x)\n"
    ),
    # FIX #4: use {safe_to_dataset} for df_ variable name
    "validate_referential_integrity": (
        "    # Referential integrity: {from_col} → {to_dataset}.{to_col}\n"
        "    # NOTE: Load '{to_dataset}' df before calling this function\n"
        "    _valid_{safe_col} = set(df_{safe_to_dataset}['{to_col}'].dropna())\n"
        "    _orphan_mask_{safe_col} = "
        "~df['{from_col}'].isin(_valid_{safe_col}) & df['{from_col}'].notna()\n"
        "    if _orphan_mask_{safe_col}.any():\n"
        "        print(f\"\u26a0 {{_orphan_mask_{safe_col}.sum()}} orphan rows in "
        "'{from_col}' not found in {to_dataset}.{to_col}\")\n"
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

    @staticmethod
    def _safe_id(name: str) -> str:
        """Convert a column/dataset name to a safe Python identifier."""
        return name.strip().replace(" ", "_").replace("-", "_").replace(".", "_")

    def _generate_dataset_script(self, name: str, dataset_plan: dict) -> str:
        steps = dataset_plan.get("steps", [])
        fn_name = f"transform_{self._safe_id(name).lower()}"
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

        # FIX #5: guard global_steps 'from'/'to' split against empty/missing strings
        for gstep in self.plan.get("global_steps", []):
            if gstep.get("action") == "validate_referential_integrity":
                from_str = gstep.get("from", "") or ""
                to_str = gstep.get("to", "") or ""
                from_parts = from_str.split(".") if "." in from_str else []
                to_parts = to_str.split(".") if "." in to_str else []
                if (
                    len(from_parts) == 2
                    and len(to_parts) == 2
                    and from_parts[0] == name
                ):
                    to_ds = to_parts[0]
                    safe_to_ds = self._safe_id(to_ds)
                    lines.append("\n    # ── Referential Integrity ──")
                    ri_code = ACTION_TEMPLATES["validate_referential_integrity"].format(
                        col=from_parts[1],
                        safe_col=self._safe_id(from_parts[1]),
                        from_col=from_parts[1],
                        to_dataset=to_ds,
                        safe_to_dataset=safe_to_ds,
                        to_col=to_parts[1],
                    )
                    lines.append(ri_code.rstrip())

        lines.append("\n    return df")
        return "\n".join(lines) + "\n"

    def _render_action(self, action: str, col: str, params: dict, dataset: str) -> str:
        template = ACTION_TEMPLATES.get(action)
        if not template:
            return f"    # TODO: manual action '{action}' on column '{col}' — not auto-generated\n"

        safe_col = self._safe_id(col)
        to_ds = params.get("to_dataset", dataset)
        safe_to_ds = self._safe_id(to_ds)

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
                to_dataset=to_ds,
                safe_to_dataset=safe_to_ds,
                to_col=params.get("to_col", "id"),
            )
        except KeyError as e:
            rendered = (
                f"    # ERROR rendering action '{action}' for col '{col}' "
                f"— missing template key: {e}\n"
            )

        return rendered

    def _main_block(self, dataset_names: list[str]) -> str:
        lines = [
            "\ndef main():",
            '    """Run all transforms. Edit source paths before executing."""',
            "",
        ]
        for name in dataset_names:
            fn = f"transform_{self._safe_id(name).lower()}"
            var = self._safe_id(name).lower()
            lines.append("    # TODO: replace path below with your actual data source")
            lines.append(f"    df_{var} = pd.read_csv('data/{name}.csv')")
            lines.append(f"    df_{var} = {fn}(df_{var})")

            if self.target_mode == "return":
                lines.append(f"    print(df_{var}.head())")
            elif self.target_mode in ("new_file", "overwrite"):
                out = self.target_path.rstrip("/") + f"/{name}_cleaned.csv"
                lines.append(f"    df_{var}.to_csv('{out}', index=False)")
                lines.append(f"    print('Saved cleaned {name} → {out}')")
            elif self.target_mode == "sql_table":
                lines.append(
                    f"    # df_{var}.to_sql('{name}_cleaned', con=engine, "
                    "if_exists='replace', index=False)"
                )
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
