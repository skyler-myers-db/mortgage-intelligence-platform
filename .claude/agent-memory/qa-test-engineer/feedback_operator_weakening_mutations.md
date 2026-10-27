---
name: feedback-operator-weakening-mutations
description: A "N/N mutants killed" battery that only deletes whole checks misses half-pinned == invariants and mirrored checks; weaken operators one side at a time, drop each conjunct alone, and pin mirrored suites to one bad-shape id list
metadata:
  type: feedback
---

Deleting whole checks is the easiest mutant to kill, so a battery made only of
those over-reports. In live-gate-geo-drill (e1fa965e, 2026-10-27) a 17/17
battery passed, yet the adversarial verifier showed the reconciliation
`sum(zip) == state.addressable - zip_unassigned_count` was pinned only for an
over-sum in BOTH suites: `==` -> `<=` (smoke jq) and `!=` -> `>` (verify_live)
stayed green. The under-sum is the realistic regression (a ZIP layer that
loses borrowers to truncation or a predicate drift). Two checks mirrored
across smoke jq and the verify_live Python module were each tested only in
the OTHER suite.

**Why:** an equality or range check is proven only when tests hit both sides;
a conjunction is proven only when each conjunct alone refuses some case; a
mirrored check is proven only in the suite that owns it.

**How to apply:**
- For every `==` / `!=`, run both one-sided weakenings (`<=`, `>=` / `>`, `<`).
- For every `a and b and c`, remove each conjunct on its own (make each
  negative / fractional / wrong-type case sum to the passing total, so only
  that conjunct can refuse it).
- For mirrored suites, name cases with `pytest.param(..., id=...)` and pin the
  id sets equal with an explicit allow-list of one-sided ids; prove that pin
  with test-side mutants (drop one case from either suite -> the parity test
  fails).
- Restore with backup copies + sha256, never `git checkout --`; see
  [[project_test_count_capture]] for JUnit counts.
