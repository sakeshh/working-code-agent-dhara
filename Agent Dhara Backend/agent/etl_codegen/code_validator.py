"""
Code Validation Node (Node 13: code_validation_node)

Validates generated ETL code before delivery to the user.
Supports Python (AST), SQL (sqlparse), and PySpark schema checks.
"""
from __future__ import annotations

import ast
import re
from typing import Any, Dict, List, Optional, Tuple

try:
    import sqlparse  # type: ignore
    HAS_SQLPARSE = True
except ImportError:
    HAS_SQLPARSE = False


# ---------------------------------------------------------------------------
# Python validator
# ---------------------------------------------------------------------------

def validate_python(code: str, schema_columns: Optional[List[str]] = None) -> Tuple[bool, List[str]]:
    """
    Validate Python ETL code.
    - AST parse check (syntax)
    - Column reference check against schema
    Returns (is_valid, list_of_errors)
    """
    errors: List[str] = []

    # 1. Syntax check
    try:
        tree = ast.parse(code)
    except SyntaxError as e:
        errors.append(f"SyntaxError on line {e.lineno}: {e.msg}")
        return False, errors

    # 2. Column reference check
    if schema_columns:
        # Find all string literals that look like column names inside df['...']
        col_refs = re.findall(r"df\[['\"](.*?)['\"]\]", code)
        unknown = [c for c in col_refs if c not in schema_columns]
        if unknown:
            errors.append(
                f"Column(s) referenced in code not found in schema: {unknown}. "
                f"Available: {schema_columns}"
            )

    # 3. Danger patterns — no DROP TABLE / os.system etc.
    danger_patterns = [
        (r"os\.system", "Dangerous: os.system call detected"),
        (r"subprocess", "Dangerous: subprocess call detected"),
        (r"DROP TABLE", "Dangerous: DROP TABLE detected"),
        (r"__import__", "Dangerous: __import__ call detected"),
        (r"exec\s*\(", "Dangerous: exec() call detected"),
        (r"eval\s*\(", "Dangerous: eval() call detected"),
    ]
    for pattern, msg in danger_patterns:
        if re.search(pattern, code, re.IGNORECASE):
            errors.append(msg)

    return len(errors) == 0, errors


# ---------------------------------------------------------------------------
# SQL validator
# ---------------------------------------------------------------------------

def validate_sql(code: str) -> Tuple[bool, List[str]]:
    """
    Validate SQL ETL code.
    - sqlparse parse check
    - Danger pattern check
    Returns (is_valid, list_of_errors)
    """
    errors: List[str] = []

    if HAS_SQLPARSE:
        try:
            parsed = sqlparse.parse(code)
            if not parsed or all(str(s).strip() == "" for s in parsed):
                errors.append("SQL appears to be empty or unparseable.")
        except Exception as e:
            errors.append(f"sqlparse error: {e}")
    else:
        # Fallback: basic semicolon check
        if not code.strip():
            errors.append("SQL code is empty.")

    # Danger patterns
    danger_sql = [
        (r"DROP\s+DATABASE", "Dangerous: DROP DATABASE detected"),
        (r"DROP\s+SCHEMA",   "Dangerous: DROP SCHEMA detected"),
        (r"TRUNCATE\s+TABLE", "Warning: TRUNCATE TABLE detected — confirm this is intentional"),
    ]
    for pattern, msg in danger_sql:
        if re.search(pattern, code, re.IGNORECASE):
            errors.append(msg)

    return len(errors) == 0, errors


# ---------------------------------------------------------------------------
# PySpark validator
# ---------------------------------------------------------------------------

VALID_SPARK_TYPES = {
    "StringType", "IntegerType", "LongType", "FloatType", "DoubleType",
    "BooleanType", "DateType", "TimestampType", "ShortType", "ByteType",
    "BinaryType", "DecimalType", "ArrayType", "MapType", "StructType",
}


def validate_pyspark(code: str, schema_columns: Optional[List[str]] = None) -> Tuple[bool, List[str]]:
    """
    Validate PySpark ETL code.
    - AST syntax check
    - Spark type check
    - Column reference check
    Returns (is_valid, list_of_errors)
    """
    errors: List[str] = []

    # Syntax
    try:
        ast.parse(code)
    except SyntaxError as e:
        errors.append(f"SyntaxError on line {e.lineno}: {e.msg}")
        return False, errors

    # Spark type references
    used_types = re.findall(r"(\w+Type)\(\)", code)
    for t in used_types:
        if t not in VALID_SPARK_TYPES:
            errors.append(f"Unknown Spark type: {t}")

    # Column references
    if schema_columns:
        col_refs = re.findall(r"F\.col\(['\"]([^'\"]+)['\"]\)", code)
        col_refs += re.findall(r"withColumn\(['\"]([^'\"]+)['\"]", code)
        unknown = [c for c in col_refs if c not in schema_columns]
        if unknown:
            errors.append(
                f"Column(s) referenced in PySpark code not in schema: {unknown}"
            )

    return len(errors) == 0, errors


# ---------------------------------------------------------------------------
# ADF JSON validator
# ---------------------------------------------------------------------------

def validate_adf_json(code: str) -> Tuple[bool, List[str]]:
    """Basic ADF Data Flow JSON structure check."""
    import json
    errors: List[str] = []
    try:
        obj = json.loads(code)
        required_keys = ["name", "properties"]
        for k in required_keys:
            if k not in obj:
                errors.append(f"ADF JSON missing required key: '{k}'")
    except json.JSONDecodeError as e:
        errors.append(f"Invalid JSON: {e}")
    return len(errors) == 0, errors


# ---------------------------------------------------------------------------
# Main dispatcher
# ---------------------------------------------------------------------------

def validate_generated_code(
    code: str,
    engine: str,
    schema_columns: Optional[List[str]] = None,
) -> Dict[str, Any]:
    """
    Validate generated ETL code by engine type.

    Args:
        code: The generated code string
        engine: 'python' | 'sql' | 'pyspark' | 'adf'
        schema_columns: Optional list of valid column names for reference checks

    Returns:
        {
          "valid": bool,
          "errors": [str, ...],
          "engine": str,
          "warnings": [str, ...]
        }
    """
    engine = engine.lower().strip()
    warnings: List[str] = []

    if engine == "python":
        valid, errors = validate_python(code, schema_columns)
    elif engine == "sql":
        valid, errors = validate_sql(code)
    elif engine == "pyspark":
        valid, errors = validate_pyspark(code, schema_columns)
    elif engine == "adf":
        valid, errors = validate_adf_json(code)
    else:
        valid, errors = False, [f"Unknown engine: '{engine}'. Supported: python, sql, pyspark, adf"]

    # General warnings (not failures)
    if "TODO" in code or "FIXME" in code:
        warnings.append("Code contains TODO/FIXME markers — review before production use.")
    if "hardcoded" in code.lower() or "password" in code.lower():
        warnings.append("Possible hardcoded credential — use environment variables.")

    return {
        "valid": valid,
        "errors": errors,
        "warnings": warnings,
        "engine": engine,
    }
