---
name: growth-agent-monitor-name-pii
description: Growth-agent monitor_name PII containment — where it lands, what the validator does and does not catch
metadata:
  type: project
---

The Mortgage Growth Agent (`backend/api/growth_agent.py`, commit bbda202) lets an operator name a saved monitor (`monitor_name`). Containment facts a future reviewer should not re-derive from scratch:

- `monitor_name` is stored ONLY in `mip_app.growth_agent_monitors.name` (api line ~296: `payload.monitor_name or workflow.title`). It NEVER reaches `mip_app.action_audit` / `payload_json`. The audit ledger uses `workflow_title = workflow.title`, a fixed `_WorkflowDef` enum constant, so the append-only compliance ledger is PII-clean by construction.
- The validator chain is `_monitor_name` (schemas/growth_agent.py:100-118) -> `contains_pii_marker` + `_RAW_IDENTIFIER_RE` + `_WORKFLOW_MONITOR_TITLE_RE` OR `_MONITOR_NAME_RE` + `validate_public_campaign_label`.
- What it CATCHES: email/SSN/US-phone-shaped, `clip_ref_*`/`owner link`/`raw clip`/`B-xxx`, street addresses, two-capitalized-word human/brand names ("Quicken Loans", "Wells Fargo", "Bob Smith").
- What SLIPS PAST (accepted, low residual risk because it lands only in a private actor-scoped name column, never the audit ledger): single-token brand names ("Chase", "PennyMac", "UWM", "loanDepot", "Nationstar", "Better"); 7-digit or no-separator number strings ("5551234", "callme5551234"); "X for FirstName" patterns ("Refi for John"); obfuscated emails ("john at acme dot com").

**Why:** distinguishes a genuine ledger-poisoning hole (none found) from acceptable cosmetic leakage into a private name column.
**How to apply:** if a future change routes `monitor_name` (or the monitor `name` read-back) into the audit ledger, a Genie prompt, or any export, the single-token-brand and number-string gaps become real and need `validate_public_campaign_label`-grade tightening. Until then they are accept-with-note.
