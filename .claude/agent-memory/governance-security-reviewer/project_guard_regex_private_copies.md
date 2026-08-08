---
name: guard-regex-private-copies
description: Round-6 persona-audit guard fixes landed only on the Genie surface; five other files carry stale private copies of the same regexes — audit them together
metadata:
  type: project
---

The Ask Genie persona-audit fixes (unrounded-average `(?<!\d\.)` lookbehind,
bare-numeric-cell exemption, geo-strip + `include_titlecase=False`,
`assume_reviewed_read_only_analytics`) were applied to the shared modules and to
`backend/services/genie_message_policy.py` only. Five other surfaces carry
**private copies** of the same title-case / 9-digit regexes and did not inherit
the fixes: `backend/schemas/common.py`, `backend/schemas/portfolio_campaign.py`,
`backend/schemas/sales.py`, `backend/schemas/campaign_status.py`,
`backend/services/audit_store.py`.

**Why:** confirmed by executed repro on 2026-08-07 (audit 2, findings-only). The
copies both over-refuse (city names `El Paso`/`Fort Worth`, product labels
`Home Equity`/`Purchase Mortgage`, unrounded UC averages — blocking approve/reject
rationale, disposition notes, campaign evidence chips, and 409-ing stored campaign
variants on the READ path) and under-refuse (lowercase `john smith` reaches the
append-only audit ledger through `DispositionRequest.notes`, because
`scrub_free_text` redacts SSN/phone/email/address but never names).

**How to apply:** when changing any guard in
`backend/schemas/_validators_*.py`, grep `'A-Z\]\[a-z\]{1,30}'` and `'d{9,}'`
across `backend/` first and fix every copy in the same commit. The shared
`contains_human_name_shape` already exempts product labels via
`_NON_PERSON_TITLECASE_SUFFIXES` but has **no** US-city gazetteer — geography
needs the `GENIE_GEO_LOCATION_RE` strip
(`backend/services/genie_message_policy.py`), not another suffix. The
sentence-prefixed audience-criterion FP (`Rank in-the-money refi candidates`)
clears with `assume_reviewed_read_only_analytics=True`.

Related: [[genie-live-first-doctrine]], [[r6-09-health-disclosure-scope]].
