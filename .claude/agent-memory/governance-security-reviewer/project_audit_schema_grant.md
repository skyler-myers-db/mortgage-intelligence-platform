---
name: audit-schema-grant
description: mip.audit grant for AI Gateway probe is schema-wide SELECT, not table-scoped; least-privilege + doc gap
metadata:
  type: project
---

Historical issue: `scripts/deploy.sh` once granted the app SP
`GRANT USE SCHEMA, SELECT ON SCHEMA mip.audit` to support AI Gateway
inference-log proof. Current deploy uses
`tools/databricks/grant_ai_gateway_inference_table.py` to discover concrete
MIP-owned prefix tables (default prefix `mip.audit.mip_agent_gateway_sonnet`)
and grants table-level `SELECT` only.

**Why:** least-privilege — schema-wide SELECT means the app SP can read every current and FUTURE table in `mip.audit`, not just the gateway inference log. If other sensitive audit data lands there, the app gains unintended read.

**How to apply:** Recommend narrowing to `GRANT SELECT ON TABLE mip.audit.mip_agent_gateway_sonnet` (plus `USE SCHEMA mip.audit`). Also: `docs/security/GRANTS.md` was NOT updated with a §N section or §9 smoke query for the new `mip.audit` schema, violating the doc's own closing rule (line ~401: "Every new schema needs its own §N ... and a smoke query in §9"). GRANTS.md claims to be the authoritative audit-readable matrix (lines 7-8) but is now out of sync with deploy.sh.

Related: [[ai-gateway-probe-cost]]
