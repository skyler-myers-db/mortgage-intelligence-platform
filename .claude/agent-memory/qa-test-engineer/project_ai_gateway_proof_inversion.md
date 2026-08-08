---
name: ai-gateway-proof-history
description: Historical AI Gateway async proof acceptance is superseded; current QA standard requires exact proof-ledger verification.
metadata:
  type: project
---

**Current QA standard (2026-07-02):** AI Gateway is claimable only when a
fresh live endpoint precheck is paired with a fresh
`mip_app.ai_gateway_proof_ledger` verified row for the current deployment SHA.
Runtime code must not use recent-row, prefix-count, or historical inference-log
evidence as a claimable fallback.

**Historical warning:** an earlier reviewed change accepted asynchronous
deployment-scoped inference-log evidence because AI Gateway table delivery can
lag. That standard is now explicitly rejected by the product owner. Keep this
file only as a regression warning: if a future diff turns a missing exact proof
into `available=True`, treat it as a blocker unless the proof ledger path is
still satisfied.

**Tests to check:** `tests/unit/test_capabilities.py` must keep rejecting
missing, stale, and wrong-SHA ledger rows. `scripts/smoke_live.sh` must keep
requiring "exact inference-row round-trip verified" for available AI Gateway
capability rows.
