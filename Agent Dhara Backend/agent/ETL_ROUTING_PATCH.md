# ETL Routing Patch — Apply to `chat_graph.py`

Your ETL engine files are complete. The **only missing piece** is routing.
`_MASTER_SYSTEM` has a closed allowed-actions list — ETL intents silently fall to `help`.
Apply the 3 changes below to `chat_graph.py`.

---

## CHANGE 1 — Add ETL actions to `_MASTER_SYSTEM` allowed list

Find this line in `_MASTER_SYSTEM`:
```
show_selection_status
```

Add these lines **immediately after** it:
```
etl_capture_intent
etl_capture_business_rules
etl_classify_issues
etl_resolve_ambiguity
etl_plan
etl_validate_dag
etl_confirm_target_schema
etl_present_plan
etl_human_review
etl_generate_code
etl_validate_code
etl_output
```

---

## CHANGE 2 — Add ETL behavior rules to `_MASTER_SYSTEM`

Find the behavior rules section (near the bottom of `_MASTER_SYSTEM`).
Add these rules **before the closing `"""`**:

```
- If the user says "generate ETL code", "build ETL", "create transformation code",
  "generate transformations", "write cleaning code", or similar ETL intent,
  choose action=etl_capture_intent. Args: {"raw_message": <user message>}.
- If the user is in an ETL flow and provides business rules or constraints (e.g.
  "customer_id must not be null", "status must be ACTIVE or INACTIVE"),
  choose action=etl_capture_business_rules. Args: {"rules_text": <user message>}.
- If the user approves an ETL plan (says "approve", "yes generate", "looks good", "proceed"),
  choose action=etl_human_review. Args: {"decision": "approve"}.
- If the user rejects or cancels an ETL plan (says "cancel", "reject", "stop"),
  choose action=etl_human_review. Args: {"decision": "reject"}.
- If the user wants to modify an ETL plan (says "modify", "change", "skip X step"),
  choose action=etl_human_review. Args: {"decision": "modify", "changes": <user message>}.
- If the user selects a target for cleaned data ("overwrite", "new table", "save to file"),
  choose action=etl_confirm_target_schema. Args: {"target_choice": <user message>}.
```

---

## CHANGE 3 — Add ETL dispatcher in the action handler

Find the section in `chat_graph.py` where actions are dispatched
(look for `if action == "list_sources":` or similar dispatcher block).

Add this block **before the final `else: return _help_reply()` fallback**:

```python
# ── ETL Flow ──────────────────────────────────────────────────────────────
elif action == "etl_capture_intent":
    from agent.etl_graph_nodes import node_capture_etl_intent
    return node_capture_etl_intent(state)

elif action == "etl_capture_business_rules":
    from agent.etl_graph_nodes import node_capture_business_rules
    return node_capture_business_rules(state)

elif action == "etl_classify_issues":
    from agent.etl_graph_nodes import node_classify_issues
    return node_classify_issues(state)

elif action == "etl_resolve_ambiguity":
    from agent.etl_graph_nodes import node_resolve_ambiguity
    return node_resolve_ambiguity(state)

elif action == "etl_plan":
    from agent.etl_graph_nodes import node_etl_plan
    return node_etl_plan(state)

elif action == "etl_validate_dag":
    from agent.etl_graph_nodes import node_validate_dag
    return node_validate_dag(state)

elif action == "etl_confirm_target_schema":
    from agent.etl_graph_nodes import node_confirm_target_schema
    return node_confirm_target_schema(state)

elif action == "etl_present_plan":
    from agent.etl_graph_nodes import node_present_plan
    return node_present_plan(state)

elif action == "etl_human_review":
    from agent.etl_graph_nodes import node_human_review
    return node_human_review(state)

elif action == "etl_generate_code":
    from agent.etl_graph_nodes import node_generate_code
    return node_generate_code(state)

elif action == "etl_validate_code":
    from agent.etl_graph_nodes import node_validate_code
    return node_validate_code(state)

elif action == "etl_output":
    from agent.etl_graph_nodes import node_etl_output
    return node_etl_output(state)
# ── End ETL Flow ──────────────────────────────────────────────────────────
```

---

## CHANGE 4 — Add ETL state keys to `ChatState` TypedDict

Find the `ChatState` class in `chat_graph.py`. Add these keys:

```python
class ChatState(TypedDict, total=False):
    # ... existing keys ...
    etl_intent: Dict[str, Any]          # engine, target, assessment_id
    etl_business_rules: Dict[str, Any]  # rules per dataset
    etl_manifest: Dict[str, Any]        # loaded from transformation_suggester
    etl_classified: Dict[str, Any]      # auto / review / blocked buckets
    etl_plan: Dict[str, Any]            # ordered ETL plan JSON
    etl_plan_approved: bool             # human approved flag
    etl_target: str                     # overwrite | new_table | return_df
    etl_generated_code: Dict[str, str]  # {dataset: code_string}
    etl_validation_result: Dict[str, Any]  # pass/fail per dataset
```

---

## Quick Test After Applying

After applying all 4 changes, restart the backend and send:
```
"generate ETL code for my assessed data"
```
Expected: agent responds with engine preference question (Python/SQL/PySpark),
not a `help` fallback.

---

## Files Already Complete (no changes needed)

| File | Status |
|---|---|
| `agent/etl_planner.py` | ✅ Complete |
| `agent/etl_graph_nodes.py` | ✅ Complete |
| `agent/schema_lineage.py` | ✅ Complete |
| `agent/etl_codegen/python_codegen.py` | ✅ Complete |
| `agent/etl_codegen/sql_codegen.py` | ✅ Complete |
| `agent/etl_codegen/pyspark_codegen.py` | ✅ Complete |
| `agent/etl_codegen/code_validator.py` | ✅ Complete |
| `agent/chat_graph.py` | ⚠️ Needs Changes 1–4 above |
