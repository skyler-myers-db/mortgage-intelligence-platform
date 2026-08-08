---
name: growth-agent-supervisor-selection
description: Growth Agent Supervisor now SELECTS the workflow (not proof-only); governance model is allowlist-bounded selection with deterministic execution
metadata:
  type: project
---

As of commit 5859d61 (~2026-07), the Databricks Supervisor Agent in `backend/agents/mortgage_growth_copilot.py` SELECTS one workflow (shift from prior proof-only/advisory posture where its response was discarded).

**Why:** product wants a visible real agentic-selection path, not a discarded attestation. The change returns `selected = WORKFLOWS.get(decision.workflow_id)` as the driving workflow.

**How to apply — the governance model that makes this safe (verify it still holds before trusting any future edit):**
- Selection is allowlist-bounded: `_supervisor_decision_from_response` rejects any `workflow_id not in WORKFLOWS` → `None` → clean deterministic fallback. Agent output is an enum pick, never free text into SQL/route/audit.
- Downstream stays deterministic: after selection, `_run_workflow` in `backend/api/growth_agent.py` owns criteria/predicates/counts/route/audit from the workflow def. No agent free-text reaches SQL or the audit payload. Only the agent's *choice* (workflow id) and non-PII hashes/thoughts are persisted in `agent_evidence`.
- PII/protected-class/injection guard runs BEFORE the Supervisor call: `GrowthAgentPromptRunRequest._prompt` validator (pydantic) rejects at 422 before `plan_growth_agent_prompt` is reached. The prompt is never sent raw to the Supervisor — only an objective hash + reviewed signal summary.
- Explicit `segment_codes` bypass the Supervisor entirely (CUSTOM_WORKFLOW_ID early-return), so user-pinned cohorts can't be overridden.

**Residual divergence gap — CLOSED as of commit 6bcfd15 (range 5859d61..13cd0b3, ~2026-07-01), verified by audit at 13cd0b3.** When the Supervisor's valid choice DIVERGES from the deterministic candidate, `mortgage_growth_copilot._agent_framework_plan` now sets `diverged = selected.id != deterministic_workflow.id` and `workflow_override_review_required=diverged` on the evidence. `growth_agent_runtime.py` propagates that flag into THREE visible surfaces: the "Interpret objective" tool step goes `review_required`, a "Supervisor workflow selection" policy check is appended as `review_required`, and the "Multi-agent framework" governance chip goes `review_required`. All three are persisted into the audit row (`backend/api/growth_agent.py` writes `tool_steps`/`policy_checks`/`governance_chips` into `payload_json` ~L741-742) AND into `agent_evidence` (`workflow_override_review_required`, `deterministic_workflow_id`, `supervisor_workflow_id`). The frontend run card (`ask-genie.growth-run-card.tsx`) renders all chips/checks unconditionally, so a diverged run shows a visible "Review" chip. Replay-by-request_id reconstructs the stored chips, so the flag survives idempotent re-fetch.

**Why safe:** there is NO approve endpoint on the growth-agent router — a run is a recommendation (audited Lead Queue deep-link); outreach/approval is a separate downstream human action, and the review flag travels with the run + audit row.

**Tests that pin it** (`tests/unit/test_growth_agent_orchestrator.py`): diverged=True case (agent picks listing_watch vs deterministic daily_refi_brief) asserts selection check + framework chip `review_required` and persisted `workflow_override_review_required is True`; diverged=False case asserts the check is `passed` ("agreed on ..."). Adversarial-but-valid selection discards the free-text reason (only the workflow-id enum is used); non-allowlisted picks (`send_email`) fall back cleanly (no divergence flag needed since nothing was selected). Verify the flag still propagates through all three surfaces before trusting any future edit to the runtime.
