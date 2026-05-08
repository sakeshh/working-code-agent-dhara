"""
LLM Router — fallback intent classifier using the LLM.

Called ONLY when keyword matching in conversational_intents.py fails.
Sends ~100-150 tokens per call. Never sends raw dataset rows.

Integrates with the existing model_config.py LLM client.
"""
from __future__ import annotations
import json
import logging
from typing import Any, Dict, Optional

from agent.agent_system_prompt import (
    ROUTER_SYSTEM_PROMPT,
    OUT_OF_SCOPE_REPLY,
    ADVERSARIAL_REPLY,
)

logger = logging.getLogger(__name__)

# Tool name → intent ID mapping (matches conversational_intents.py)
TOOL_TO_INTENT: Dict[str, int] = {
    "report_generate": 1,
    "top_issues":      2,
    "issue_filter":    3,
    "triage":          4,
    "cross_dataset":   5,
    "none":            7,
}


def _get_llm_client():
    """Lazy-import LLM client from existing model_config to avoid circular imports."""
    try:
        from agent.model_config import get_llm_client
        return get_llm_client()
    except Exception as exc:
        logger.warning("LLM client unavailable: %s", exc)
        return None


def llm_classify_intent(message: str) -> Optional[Dict[str, Any]]:
    """
    Send the user message to the LLM for intent classification.

    Returns:
        dict with keys: intent (int), tool (str), reason (str), source="llm"
        None if LLM is unavailable or response is unparseable.
    """
    client = _get_llm_client()
    if client is None:
        return None

    try:
        response = client.invoke([
            {"role": "system", "content": ROUTER_SYSTEM_PROMPT},
            {"role": "user",   "content": f'Classify this message: "{message}"'},
        ])

        # Handle both string and message-object responses
        raw = response if isinstance(response, str) else getattr(response, "content", str(response))

        # Strip markdown code fences if present
        raw = raw.strip().strip("```json").strip("```").strip()

        parsed = json.loads(raw)
        tool   = parsed.get("tool", "none")
        reason = parsed.get("reason", "llm classified")
        intent = TOOL_TO_INTENT.get(tool, 7)

        logger.info("LLM router → tool=%s intent=%d reason=%s", tool, intent, reason)
        return {"intent": intent, "tool": tool, "reason": reason, "source": "llm"}

    except json.JSONDecodeError as exc:
        logger.warning("LLM router returned non-JSON: %s | raw=%s", exc, raw[:200])
        return None
    except Exception as exc:
        logger.error("LLM router error: %s", exc)
        return None


def get_out_of_scope_reply() -> str:
    return OUT_OF_SCOPE_REPLY


def get_adversarial_reply() -> str:
    return ADVERSARIAL_REPLY
