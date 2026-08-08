---
name: count-delta-reconciliation
description: How to reconcile a confusing pytest passed/total delta (e.g. "+4 new test funcs but only +1 net collected") — diff JUnit test-id SETS, not counts, and check the baseline commit's actual position in the graph.
metadata:
  type: project
---

When a QA signoff quotes a baseline like "2151 passed / 184 skipped" and HEAD differs in a way that doesn't obviously add up (e.g. `git diff` shows +5/-1 `def test_` lines but the net collected total only moved +1), reconcile with **test-id SET diffs from the JUnit XMLs**, not raw counts.

**Why:** raw count deltas conflate three independent effects that all move the numbers: (1) genuinely new/removed tests, (2) skip↔pass flips on environment-gated tests, and (3) tests that a `git diff` attributes to the scope but that actually landed in a commit *before* the baseline the counts were measured at. Counting `def test_` lines in `git diff e9b4671..HEAD` gives the full-scope view; the passed/total numbers are measured at a *later* baseline commit (the "split"). Those two windows don't match, so the arithmetic looks broken when it isn't.

**How to apply:**
1. Generate JUnit XML at HEAD (`pytest -q --junit-xml=...`) and at the baseline commit (checkout in a throwaway `git worktree add -f --detach <scratchpad> <sha>`, run the SAME `.venv` pytest, remove the worktree after).
2. Diff the sets of `(classname::name, skipped_bool)` from `<testcase>` elements. This gives you exactly: IDs added, IDs removed, and skip-status flips — the three effects separated.
3. Cross-check commit order with `git log --oneline --graph e9b4671..HEAD`. A test that `git diff` shows as "added" may sit in a commit *before* the split baseline, so it's already in the baseline count — not part of the split-baseline delta.
4. Environment-gated "when present / fail-closed artifact" tests (e.g. `test_demo_first_party_feeds::...fail_closed_when_present`) legitimately flip skip↔pass between machines; a 1-test pass/skip split difference vs a quoted baseline is usually this, not a regression.

**Gotcha in this shell:** `pytest --collect-only -q` prints `path/to/file.py: N` (per-file counts), NOT `::`-delimited node IDs — so grepping for `::` returns 0. Use the JUnit XML `<testcase>` elements for id-level work. See [[project_test_count_capture]] for the `-q` summary-suppression / `--junit-xml` workaround.
