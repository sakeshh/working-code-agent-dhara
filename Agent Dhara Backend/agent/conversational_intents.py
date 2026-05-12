"""
Conversational intent classification for post-assessment chat.

Intent IDs:
  1 REPORT_GENERATE
  2 ISSUE_LIST
  3 ISSUE_DETAIL / ISSUE_FILTER
  4 PRIORITIZE / TRIAGE
  5 CROSS_DATASET
  6 CLARIFY
  7 OUT_OF_SCOPE
  8 ADVERSARIAL
  9 ETL_GENERATE          ← NEW: generate ETL/transformation code
"""
from __future__ import annotations
import re
from typing import Any, Dict, List, Optional


def select_best_response(specialist_outputs: List[str], message: str = "") -> str:
    del message
    for s in specialist_outputs or []:
        if isinstance(s, str) and s.strip():
            return s.strip()
    return ""


def fallback_router_intent(message: str, context: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    low = (message or "").lower().strip()
    if not low:
        return None
    if any(t in low for t in (" this", "this ", " this.", "fix this", " too", "too.")) and len(low) < 90:
        return {"intent": 6, "reason": "fallback_short_deictic"}
    if any(w in low for w in ("stock price", "reliance", "quantum", " ipl", "ipl ", "president", "fastapi")):
        return {"intent": 7, "reason": "fallback_keyword_ood"}
    return None


def _peek_assessment(context: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    r = context.get("last_assessment_result")
    return r if isinstance(r, dict) else None


def _is_adversarial(low: str) -> bool:
    patterns = (
        "ignore the dataset", "ignore data", "tell me everything is clean",
        "everything is clean", "invent some", "invent issues", "fabricate",
        "don't analyze", "do not analyze", "dont analyze", "just say ready",
        "without checking any files", "without checking", "override the report",
        "say it's safe", "want you to say", "contradict the report",
    )
    return any(p in low for p in patterns)


def _is_etl_generate(low: str) -> bool:
    """
    Intent 9 — ETL / transformation code generation.
    MUST be checked BEFORE _is_ood() because _is_ood() blocks these keywords.
    Triggers when the user asks Agent Dhara to produce ETL/cleaning code
    based on the current assessment.
    """
    etl_keys = (
        "generate etl code",
        "generate etl",
        "etl code",
        "write etl",
        "create etl",
        "build etl",
        "generate transformation",
        "generate transformations",
        "write transformation",
        "create transformation code",
        "build transformation",
        "generate cleaning code",
        "write cleaning code",
        "create cleaning code",
        "generate python etl",
        "generate sql etl",
        "generate pyspark",
        "generate pyspark etl",
        "write pyspark etl",
        "generate adf",
        "generate pipeline code",
        "write pipeline code",
        "build pipeline code",
        "code to fix the data",
        "code to clean the data",
        "code to clean this",
        "code to fix this",
        "auto fix",
        "autofix",
        "generate the fix",
        "generate fix",
        "produce etl",
        "produce transformation",
        "produce cleaning",
    )
    return any(k in low for k in etl_keys)


def _is_ood(low: str) -> bool:
    """
    Out-of-domain detection — catches anything not related to DQ assessment.
    NOTE: ETL code generation is intentionally excluded here — it is handled
    by _is_etl_generate() (intent 9) which runs BEFORE this check.
    """
    # ── General knowledge / off-domain ──────────────────────────────────────
    general_keys = (
        "stock price", "share price", "nifty", "sensex", "nyse", "nasdaq",
        "fastapi", "django app", "flask app",
        "quantum computing", "quantum ", "explain quantum",
        "president of the", "prime minister",
        "ipl match", "ipl ", "world cup", "super bowl",
        "latest news", "who won",
        "write a poem", "tell me a joke", "what is the weather",
        "how to cook", "recipe for",
        # Generic coding help not related to this data
        "write a python script", "write a python", "how to code",
        "coding tutorial", "python tutorial",
        "write airflow", "generate airflow", "airflow dag",
        "write dag", "generate dag",
        "write dbt", "generate dbt", "dbt model",
    )
    return any(k in low for k in general_keys)


def _is_clarify(low: str, raw: str) -> bool:
    if len(raw.strip()) <= 28:
        tiny = {
            "fix this.", "fix this", "is it okay?", "is it okay",
            "compare these.", "compare these", "why is this bad?", "why is this bad",
            "what's the issue here?", "what is the issue here",
            "check this one too", "check this one too."
        }
        if raw.strip().lower() in tiny:
            return True
    phrases = (
        "check this one too", "what should i do next", "what's the issue here",
        "what is the issue here", "use the same logic as before", "again but better",
        "do the report again", "is it okay", "why is this bad",
        "can you make this ready", "make this ready", "do this again",
        "same logic as before", "check this too",
    )
    return any(p in low for p in phrases)


def _is_cross_dataset(low: str) -> bool:
    return any(k in low for k in (
        "compare ", "between these", "cross-dataset", "cross dataset",
        "orphan foreign", "foreign keys between", "relationships between",
        "across files", "across datasets", "schema naming",
        "naming problems across", "customers.csv", "orders.csv",
    ))


def _is_triage(low: str) -> bool:
    return any(k in low for k in (
        "2 hours", "two hours", "fix first", "clean first", "before loading",
        "before warehouse", "highest priority", "prioritize", "production-ready",
        "production ready", "warehouse-ready", "warehouse ready", "etl risk",
        "riskiest", "most important", "dashboard errors", "business-team",
        "business team", "which dataset is blocked", "blocked and why",
        "safest to load", "manual-review", "manual review burden",
        "source-system", "source system", "user-entry", "user entry",
        "what should i fix", "what to fix first", "where do i start",
        "where should i start", "fix order", "order of fixing",
        "what needs fixing first", "most urgent", "urgent issues",
        "critical first", "fix critical", "tackle first",
    ))


def _is_issue_filter(low: str) -> bool:
    return any(k in low for k in (
        "null-related", "null related", "null issues", "missing values",
        "duplicate-related", "duplicate issues", "duplicate only",
        "duplicates only", "email issues", "invalid email", "phone issues",
        "identifier", "primary key",
        "only nulls", "just nulls", "show nulls", "show null",
        "only duplicates", "just duplicates", "show duplicates",
        "only email", "just email issues", "show email",
        "only phone", "just phone", "show phone issues",
        "format issues only", "type issues only",
    ))


def _is_issue_list(low: str) -> bool:
    if re.search(r"top\s*\d+", low):
        return True
    if re.search(r"\btop\s+five\b", low) or re.search(r"\btop\s+5\b", low):
        return True
    keys = (
        "list issues", "red flags", "red flag", "what's wrong", "what is wrong",
        "whats wrong", "issues only", "main problems", "biggest problems",
        "most risky", "risky for etl", "broken pipelines",
        "break downstream pipelines", "suspicious", "clean enough to load",
        "clean enough", "production-ready or not", "use this dataset directly",
        "rows should worry", "worry me the most", "auto-fixable", "manual review",
        "which columns", "business risks", "business risk", "data engineer",
        "data engineer-focused", "auto fixable",
        "what problems", "what are the problems", "what issues",
        "what are the issues", "show me issues", "show issues",
        "what went wrong", "tell me the issues", "list the problems",
        "what should i know", "what do i need to fix",
        "give me a summary", "summarise issues", "summarize issues",
        "how bad is", "how clean is", "is this data clean",
        "is my data clean", "is the data good", "is it clean",
        "what's the status", "status of the data", "data status",
        "give me an overview", "overview of issues",
        "focused on", "should i focus", "focus on",
        "what to be aware of", "be aware of",
    )
    if any(k in low for k in keys):
        return True
    if any(k in low for k in ("analyze this", "inspect this", "check this data", "scan this")):
        if not any(x in low for x in ("full report", "markdown", "html report", "detailed report", "entire report")):
            return True
    return False


def _is_full_report(low: str) -> bool:
    return any(k in low for k in (
        "executive summary",
        "full narrative",
        "full report",
        "detailed report",
        "entire report",
        "markdown report",
        "html report",
        "narrative summary",
        "summarize the report",
        "summary of the report",
        "plain english summary of the report",
        "engineer-focused summary",
        "rank issues by severity",
        "generate a report",
        "generate dq report",
        "generate quality report",
        "generate data quality report",
        "create a report",
        "create dq report",
        "build a report",
        "give me a report",
        "show me a report",
        "produce a report",
    ))


def classify_intent(message: str, context: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    raw = (message or "").strip()
    if not raw:
        return None
    low = raw.lower()

    if low.startswith("select ") or low.startswith("insert ") or low.startswith("update "):
        return None
    if raw.strip().startswith("```"):
        return None

    # 1. Safety checks always run first
    if _is_adversarial(low):
        return {"intent": 8, "reason": "adversarial_policy"}

    # 2. ETL generation — checked BEFORE OOD so it is never blocked
    if _is_etl_generate(low):
        return {"intent": 9, "reason": "etl_generate"}

    # 3. OOD check runs before report/issue checks
    if _is_ood(low):
        return {"intent": 7, "reason": "out_of_domain"}

    has_assessment = _peek_assessment(context) is not None

    if _is_clarify(low, raw):
        return {"intent": 6, "reason": "underspecified"}
    if _is_cross_dataset(low) and not has_assessment:
        return {"intent": 6, "reason": "cross_dataset_needs_selection"}

    if has_assessment:
        if _is_cross_dataset(low):
            return {"intent": 5, "reason": "cross_dataset"}
        if _is_triage(low):
            return {"intent": 4, "reason": "prioritize"}
        if _is_issue_filter(low):
            return {"intent": 3, "reason": "issue_slice"}
        if _is_issue_list(low):
            return {"intent": 2, "reason": "issue_list"}
        if _is_full_report(low):
            return {"intent": 1, "reason": "full_report"}

    if has_assessment and len(low.split()) >= 4 and "?" in raw:
        return {"intent": 2, "reason": "nl_question_with_assessment"}

    return None
