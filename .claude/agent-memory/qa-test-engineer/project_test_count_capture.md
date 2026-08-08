---
name: test-count-capture
description: How to get exact pytest/vitest counts reliably in this repo's non-TTY agent shell (junit XML, isolated vitest)
metadata:
  type: project
---

Getting exact test counts for regression sweeps in this repo (non-TTY agent shell).

**Fact 1 — pytest `-q` summary line is suppressed here.** Running `.venv/bin/pytest -q` in the background/redirected agent shell ends output on the "-- Docs: ... capture-warnings.html" warnings line; the terse `N passed, M skipped in Xs` bar does NOT appear in captured stdout (TTY-only terminal reporter). Exit code is still authoritative (0 = no failures).
**Why:** repeatedly lost the count line to both `tail` and `grep` during the 2026-07 adversarial sweep even after redirecting to a file.
**How to apply:** to get exact passed/skipped/failed, run with `--junit-xml=<path>` and parse the `<testsuite tests= failures= errors= skipped=>` attributes (passed = tests - failures - errors - skipped). Do NOT trust `--collect-only -q | grep test | wc -l` (format gives a wrong number, e.g. 136 for a 2290-test suite). Full suite as of 3256faf: total=2290, passed=2106, skipped=184, failures=0.

**Fact 2 — vitest flakes under CPU contention with worker-timeout, NOT assertion, errors.** Running the full `npm --prefix frontend run test` concurrently with the ~2100-test pytest suite (and/or frontend build) starves vitest's forks pool, producing "Timeout waiting for worker to respond" / "Failed to start forks worker" and a depressed file count (73 files instead of the real ~79). These are infra flakes, not regressions.
**Why:** first pass of the 2026-07 sweep reported "3 failed | 70 passed" test files purely from running everything at once on one machine.
**How to apply:** run the full vitest suite in ISOLATION (nothing else CPU-heavy running). For per-file flake rechecks use `npm --prefix frontend run test -- --run <path>` which is cheap and safe to run alongside other work. See [[async-ai-gateway-proof-inversion]] for the sweep this came from.
