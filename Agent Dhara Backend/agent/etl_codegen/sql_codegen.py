"""
sql_codegen.py — Agent Dhara Phase 2
Generates ANSI SQL transformation scripts from an ETL Plan JSON.

Dialect support: 'ansi' | 'postgresql' | 'mysql' | 'mssql' | 'sqlite'
Input:  etl_plan dict (produced by etl_planner.py)
Output: SQL script string per dataset
"""

from datetime import datetime
from typing import Any


class SQLCodegen:
    """Generates SQL ETL scripts from an approved ETL Plan JSON."""

    SUPPORTED_DIALECTS = {"ansi", "postgresql", "mysql", "mssql", "sqlite"}

    def __init__(self, etl_plan: dict[str, Any], dialect: str = "ansi"):
        self.plan = etl_plan
        self.dialect = dialect.lower() if dialect.lower() in self.SUPPORTED_DIALECTS else "ansi"
        self.generated_at = datetime.now().strftime("%Y-%m-%d %H:%M:%S")

    # ── Public API ──────────────────────────────────────────────────────────────

    def generate(self) -> dict[str, str]:
        """Returns {dataset_name: sql_script_string}."""
        results = {}
        for dataset_name, dataset_plan in self.plan.get("datasets", {}).items():
            results[dataset_name] = self._generate_dataset_sql(dataset_name, dataset_plan)
        return results

    def generate_combined(self) -> str:
        """Returns a single SQL file with all datasets separated by comments."""
        scripts = self.generate()
        header = self._file_header()
        sections = []
        for name, sql in scripts.items():
            sections.append(f"-- ═══════════════════════════════════════")
            sections.append(f"-- Dataset: {name}")
            sections.append(f"-- ═══════════════════════════════════════")
            sections.append(sql)
        return header + "\n" + "\n\n".join(sections)

    # ── Private helpers ─────────────────────────────────────────────────────────

    def _file_header(self) -> str:
        plan_id = self.plan.get("plan_id", "unknown")
        return (
            f"-- =================================================================\n"
            f"-- Agent Dhara — Auto-Generated SQL ETL Script\n"
            f"-- Plan ID  : {plan_id}\n"
            f"-- Dialect  : {self.dialect}\n"
            f"-- Generated: {self.generated_at}\n"
            f"-- WARNING  : Review all statements before running on production.\n"
            f"-- =================================================================\n\n"
        )

    def _generate_dataset_sql(self, name: str, dataset_plan: dict) -> str:
        steps = dataset_plan.get("steps", [])
        if not steps:
            return f"-- No auto-fixable steps for '{name}'\n"

        statements = []
        dedup_step = None

        for step in sorted(steps, key=lambda s: s.get("order", 99)):
            action = step.get("action", "")
            col = step.get("column") or ""
            params = step.get("params", {})

            if action in ("deduplicate", "deduplicate_all"):
                dedup_step = (name, col, params)
                continue

            sql = self._render_action(action, name, col, params)
            if sql:
                statements.append(sql)

        # Dedup always last
        if dedup_step:
            statements.append(self._render_dedup(*dedup_step))

        # Global RI checks
        for gstep in self.plan.get("global_steps", []):
            if gstep.get("action") == "validate_referential_integrity":
                from_parts = gstep.get("from", "").split(".")
                to_parts = gstep.get("to", "").split(".")
                if from_parts[0] == name:
                    statements.append(self._render_ri_check(name, from_parts[1], to_parts[0], to_parts[1]))

        return "\n\n".join(statements) + "\n"

    def _render_action(self, action: str, table: str, col: str, params: dict) -> str:
        q = self._quote

        if action == "trim":
            return (
                f"UPDATE {q(table)}\n"
                f"SET {q(col)} = TRIM({q(col)})\n"
                f"WHERE {q(col)} IS NOT NULL;"
            )

        elif action == "fill_or_drop":
            return (
                f"-- Fill nulls in {q(table)}.{q(col)} with mode (run in application layer)\n"
                f"-- OR delete nulls:\n"
                f"-- DELETE FROM {q(table)} WHERE {q(col)} IS NULL;"
            )

        elif action == "drop_nulls":
            return (
                f"DELETE FROM {q(table)}\n"
                f"WHERE {q(col)} IS NULL;"
            )

        elif action in ("coerce_numeric", "coerce_integer"):
            target_type = "INTEGER" if action == "coerce_integer" else "DECIMAL(18,4)"
            if self.dialect == "mssql":
                return (
                    f"UPDATE {q(table)}\n"
                    f"SET {q(col)} = TRY_CAST({q(col)} AS {target_type})\n"
                    f"WHERE {q(col)} IS NOT NULL;"
                )
            elif self.dialect in ("postgresql", "ansi"):
                return (
                    f"UPDATE {q(table)}\n"
                    f"SET {q(col)} = CAST({q(col)} AS {target_type})\n"
                    f"WHERE {q(col)} ~ '^[0-9]+(\\.[0-9]+)?$';"
                )
            else:
                return (
                    f"-- MANUAL: Cast {q(table)}.{q(col)} to {target_type} — dialect '{self.dialect}' requires manual type migration"
                )

        elif action == "parse_dates":
            if self.dialect in ("postgresql", "ansi"):
                return (
                    f"UPDATE {q(table)}\n"
                    f"SET {q(col)} = TO_DATE({q(col)}::TEXT, 'YYYY-MM-DD')\n"
                    f"WHERE {q(col)} IS NOT NULL;"
                )
            elif self.dialect == "mssql":
                return (
                    f"UPDATE {q(table)}\n"
                    f"SET {q(col)} = TRY_CONVERT(DATE, {q(col)})\n"
                    f"WHERE {q(col)} IS NOT NULL;"
                )
            elif self.dialect == "mysql":
                return (
                    f"UPDATE {q(table)}\n"
                    f"SET {q(col)} = STR_TO_DATE({q(col)}, '%Y-%m-%d')\n"
                    f"WHERE {q(col)} IS NOT NULL;"
                )
            else:
                return f"-- MANUAL: Parse date in {q(table)}.{q(col)}"

        elif action == "sanitize_email":
            return (
                f"UPDATE {q(table)}\n"
                f"SET {q(col)} = LOWER(TRIM({q(col)}));\n\n"
                f"-- Set invalid emails to NULL\n"
                f"UPDATE {q(table)}\n"
                f"SET {q(col)} = NULL\n"
                f"WHERE {q(col)} NOT LIKE '%@%.%';"
            )

        elif action == "normalize_phone":
            return (
                f"-- Normalize phone: remove non-digit chars (application layer recommended)\n"
                f"-- MANUAL: UPDATE {q(table)} SET {q(col)} = REGEXP_REPLACE({q(col)}, '[^0-9+]', '')"
            )

        elif action == "standardize_boolean":
            return (
                f"UPDATE {q(table)}\n"
                f"SET {q(col)} = CASE\n"
                f"    WHEN LOWER(CAST({q(col)} AS VARCHAR)) IN ('true','yes','1','y') THEN TRUE\n"
                f"    WHEN LOWER(CAST({q(col)} AS VARCHAR)) IN ('false','no','0','n') THEN FALSE\n"
                f"    ELSE NULL\n"
                f"END;"
            )

        elif action == "column_rename":
            new_name = params.get("new_name", col)
            return f"ALTER TABLE {q(table)} RENAME COLUMN {q(col)} TO {q(new_name)};"

        elif action in ("clip_or_flag", "range_clip"):
            min_val = params.get("min_val", 0)
            max_val = params.get("max_val", 999999)
            return (
                f"UPDATE {q(table)}\n"
                f"SET {q(col)} = GREATEST({min_val}, LEAST({max_val}, {q(col)}))\n"
                f"WHERE {q(col)} IS NOT NULL;"
            )

        else:
            return f"-- TODO: manual action '{action}' on {q(table)}.{q(col)}"

    def _render_dedup(self, table: str, col: str, params: dict) -> str:
        q = self._quote
        dedup_key = params.get("dedup_key", [col] if col else ["id"])
        key_cols = ", ".join(q(c) for c in dedup_key)

        if self.dialect in ("postgresql", "ansi"):
            return (
                f"-- Deduplicate {q(table)} keeping first occurrence per ({key_cols})\n"
                f"DELETE FROM {q(table)}\n"
                f"WHERE ctid NOT IN (\n"
                f"    SELECT MIN(ctid)\n"
                f"    FROM {q(table)}\n"
                f"    GROUP BY {key_cols}\n"
                f");"
            )
        elif self.dialect == "mssql":
            return (
                f"-- Deduplicate {q(table)}\n"
                f"WITH _CTE AS (\n"
                f"    SELECT *, ROW_NUMBER() OVER (PARTITION BY {key_cols} ORDER BY (SELECT NULL)) AS _rn\n"
                f"    FROM {q(table)}\n"
                f")\n"
                f"DELETE FROM _CTE WHERE _rn > 1;"
            )
        elif self.dialect == "mysql":
            pk = dedup_key[0] if dedup_key else "id"
            return (
                f"-- Deduplicate {q(table)}\n"
                f"DELETE t1 FROM {q(table)} t1\n"
                f"INNER JOIN {q(table)} t2\n"
                f"WHERE t1.{q(pk)} > t2.{q(pk)}\n"
                f"  AND {' AND '.join(f't1.{q(c)} = t2.{q(c)}' for c in dedup_key)};"
            )
        else:
            return f"-- TODO: Deduplicate {q(table)} on ({key_cols})"

    def _render_ri_check(self, table: str, fk_col: str, ref_table: str, ref_col: str) -> str:
        q = self._quote
        return (
            f"-- Referential integrity check: {table}.{fk_col} → {ref_table}.{ref_col}\n"
            f"SELECT {q(fk_col)}, COUNT(*) AS orphan_count\n"
            f"FROM {q(table)}\n"
            f"WHERE {q(fk_col)} NOT IN (SELECT {q(ref_col)} FROM {q(ref_table)})\n"
            f"  AND {q(fk_col)} IS NOT NULL\n"
            f"GROUP BY {q(fk_col)};\n"
            f"-- If orphan_count > 0, resolve manually or add FK constraint."
        )

    def _quote(self, identifier: str) -> str:
        """Wraps identifier in dialect-appropriate quotes."""
        if self.dialect == "mysql":
            return f"`{identifier}`"
        return f'"{identifier}"'
