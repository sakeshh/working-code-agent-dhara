"""
Router Orchestrator — unified entry point for all intent routing.

Layered routing strategy:
  Layer 1 → Adversarial / safety guard        (rule-based, unbypassable)
  Layer 1b→ Code generation guard             (rule-based, unbypassable)
  Layer 2 → Keyword matching                  (existing code, free, 0ms)
  Layer 3 → LLM Router                        (fallback, ~100-150 tokens)
  Layer 4 → Final fallback                    (return None)

Usage:
    from agent.router_orchestrator import route_message
    result = route_message(user_message, context)
"""
from __future__ import annotations
import logging
from typing import Any, Dict, Optional

from agent.conversational_intents import (
    classify_intent,
    fallback_router_intent,
    _is_adversarial,
    _is_ood,
)
from agent.llm_router import llm_classify_intent
from agent.agent_system_prompt import OUT_OF_SCOPE_REPLY, ADVERSARIAL_REPLY

logger = logging.getLogger(__name__)

# ── Hardcoded code-generation guard (triple safety net) ─────────────────────
_CODE_KEYWORDS = (
    "generate code", "write code", "etl code", "generate etl code",
    "generate etl", "write etl", "create etl", "build etl",
    "python code", "python script", "write python", "write a python",
    "write sql", "generate sql", "sql script",
    "write script", "generate script", "create script",
    "write a script", "give me code", "give code", "show me code",
    "write me code", "code for this", "code to fix", "code to clean",
    "write pipeline", "build pipeline", "create pipeline",
    "generate pipeline", "build a pipeline", "write a pipeline",
    "pyspark", "spark code", "write pandas", "pandas code",
    "write dbt", "generate dbt", "dbt model",
    "write airflow", "generate airflow", "airflow dag",
    "write dag", "generate dag",
    "automate this", "automate the fix", "write automation",
)

_CODE_OOS_REPLY = (
    "I can't generate ETL code or scripts. "
    "I only analyse data quality issues from your assessment.\n\n"
    "Try asking:\n"
    "- 'What are the top issues in my data?'\n"
    "- 'Which datasets should I fix first?'\n"
    "- 'Show me null issues only'\n"
    "- 'Generate a DQ report'"
)


def route_message(
    message: str,
    context: Dict[str, Any],
    use_llm_fallback: bool = True,
) -> Optional[Dict[str, Any]]:
    """
    Route a user message through all intent layers.

    Returns dict with: intent, tool, reason, source
    Returns None if no layer matched.
    """
    if not message or not message.strip():
        return None

    low = message.lower().strip()

    # ── Layer 1a: Adversarial guard ──────────────────────────────────────────
    if _is_adversarial(low):
        logger.info("Router: adversarial detected")
        return {
            "intent": 8,
            "tool": "none",
            "reason": "adversarial_policy",
            "source": "safety_guard",
            "reply": ADVERSARIAL_REPLY,
        }

    # ── Layer 1b: Code generation guard (before keyword matching) ────────────
    if any(k in low for k in _CODE_KEYWORDS):
        logger.info("Router: code generation request blocked")
        return {
            "intent": 7,
            "tool": "none",
            "reason": "code_generation_not_supported",
            "source": "code_guard",
            "reply": _CODE_OOS_REPLY,
        }

    # ── Layer 1c: General OOD keyword guard ──────────────────────────────────
    if _is_ood(low):
        logger.info("Router: out-of-domain keyword detected")
        return {
            "intent": 7,
            "tool": "none",
            "reason": "out_of_domain_keyword",
            "source": "safety_guard",
            "reply": OUT_OF_SCOPE_REPLY,
        }

    # ── Layer 2a: Primary keyword classifier ─────────────────────────────────
    result = classify_intent(message, context)
    if result:
        result.setdefault("source", "keyword")
        logger.info("Router: keyword match → intent=%d", result.get("intent"))
        return result

    # ── Layer 2b: Fallback keyword heuristics ────────────────────────────────
    result = fallback_router_intent(message, context)
    if result:
        result.setdefault("source", "keyword_fallback")
        logger.info("Router: keyword fallback → intent=%d", result.get("intent"))
        return result

    # ── Layer 3: LLM Router (fires only on keyword miss) ─────────────────────
    if use_llm_fallback:
        logger.info("Router: keyword missed → calling LLM router for: %s", message[:80])
        result = llm_classify_intent(message)
        if result:
            if result.get("tool") == "none":
                result["reply"] = OUT_OF_SCOPE_REPLY
            return result

    # ── Layer 4: No match ────────────────────────────────────────────────────
    logger.info("Router: no layer matched → message: %s", message[:80])
    return None


def route_and_get_reply(
    specialist_fn,
    message: str,
    context: Dict[str, Any],
    assessment: Dict[str, Any],
    use_llm_formatter: bool = True,
) -> str:
    """
    Convenience wrapper: route → call specialist → optionally format with LLM.
    """
    raw = specialist_fn(assessment, message)

    if use_llm_formatter and raw:
        try:
            from agent.llm_formatter import format_specialist_output
            return format_specialist_output(raw, message)
        except Exception as exc:
            logger.warning("Formatter error, using raw output: %s", exc)

    return raw
