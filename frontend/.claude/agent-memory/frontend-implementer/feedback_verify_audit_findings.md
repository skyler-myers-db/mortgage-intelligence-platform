---
name: verify-audit-findings
description: When auditing UX/UI, verify each finding against actual code before reporting — delegated Explore sweeps over-flag.
metadata:
  type: feedback
---

When running a UX/UI quirk audit of Module 0, delegated `Explore` agents reliably over-flag. Verify every finding against the actual source line before including it.

**Why:** In the 2026-06-13 audit, parallel Explore sweeps produced multiple false positives that would have wasted demo-prep time:
- Flagged "Clear filters" buttons as missing aria-label when they have visible text labels (not icon-only).
- Flagged "100+" stale count as hiding truncation when the very next line already discloses "showing first 100."
- Flagged hero Approve button as never showing approved state when it does render "Approved" + disabled.
- Flagged analytics as refetching on tab change when all queries already carry `staleTime: 60_000`.

**How to apply:** Treat delegated audit output as leads, not findings. Spot-read each cited line. Report a "false positives cleared" section so the reader trusts the list. Lead the final report with verification status. Cap real findings (~12) over exhaustiveness.

Known-good patterns in this app worth not re-flagging: KpiCard has a real skeleton (`kpi__value-skeleton`); routes use spacing tokens/utilities (no inline px/hex — grep confirmed). SegmentCard, by contrast, has NO loading skeleton — that gap is real. See [[appshell-design-contract]].
