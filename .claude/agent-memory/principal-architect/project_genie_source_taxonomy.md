---
name: genie-source-taxonomy
description: GenieAnswerShape.source taxonomy — which values are trusted data answers vs degraded/blocked, and the canonical denylist to gate UI on
metadata:
  type: project
---

`GenieAnswerShape.source` (the field that flows from backend genie services to the frontend) has multiple values, and several **trusted** ones are NOT `'genie'`.

Backend source literals (grep `source=` in backend/):
- Trusted data answers: `genie`, `trusted_sql`, `sales_ops`
- Canonical booth answers (top borrowers by state, ITM/HELOC/cash-out top ZIPs, retention/competitor-lien lists) resolve to **`trusted_sql`**, NOT `genie` — see `backend/services/repositories/databricks_genie.py` (`_CANONICAL_*_SQL`).
- Non-trusted / degraded: `degraded`, `policy_blocked`, `refused`, `data_gap`, `out_of_footprint` (also transient: `idle`, `new`).

**Why:** A UI gate that allowlists `source === 'genie'` silently excludes the canonical demo answers (most likely to be shown at the booth). This bit the `feat/genie-pin-followups` pin button.

**How to apply:** Gate "is this a trusted answer" with the established **denylist**, not a `'genie'` allowlist. The canonical pattern already exists in `frontend/src/components/mortgage/GenieChat.tsx`: `NON_PERSISTABLE_SOURCES` Set = {degraded, policy_blocked, refused, data_gap, out_of_footprint} plus `warningLabelForSource()`. Reuse those rather than re-deriving trust polarity. Related: [[buyer-wow-audit-cadence]] if that memory exists.
