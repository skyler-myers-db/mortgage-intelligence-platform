---
name: genie-visible-text-titlecase-trap
description: Title-Case two-word phrases in server-authored Genie strings trip genie_visible_text_unsafe's human-name-shape heuristic — always probe new strings before shipping
metadata:
  type: project
---

Any string rendered on the Ask Genie surface is scanned by
`genie_visible_text_unsafe` (backend/services/genie_message_policy.py), which
includes a Title-Case human-name-shape heuristic. **Server-authored** strings
trip it just as easily as model text: `"Live Genie drafted a SQL plan."` is
rejected; `"The live Genie turn drafted a SQL plan."` passes. Asset names
(`mip.gold.borrower_360`), `PII`, and `SQL` are all fine.

**Why:** the heuristic cannot tell a product noun phrase from a person's name,
and the guard is deliberately fail-closed — the alternative (leaking a borrower
name into proof UI) is worse. Only `structured_value=True` callers skip the
title-case pass, and that flag is reserved for already-key-redacted gold table
cells.

**How to apply:** when authoring any new visible Genie string (reasoning-trace
steps, degraded messages, canonical answers, viz titles), probe it first:

```
.venv/bin/python -c "from backend.services.genie_message_policy import genie_visible_text_unsafe as U; print(U('your string'))"
```

Prefer lowercase mid-sentence phrasing. `GenieProcessTrace._add`
(backend/services/repositories/databricks_genie_trace.py) also runs the scan
per step and drops or falls back rather than shipping unsafe text — follow that
fail-closed pattern for any new builder. See [[genie-live-first]].
