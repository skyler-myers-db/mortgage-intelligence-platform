---
name: Genie regression registry format is verification-gated on ## heading count
description: The spec for the 50-prompt Genie regression registry checks `reg.count('## ')` ≥ 50, forcing a one-heading-per-prompt markdown layout rather than tables.
type: project
---

`genie/regression_suite.md` uses one `## ` heading per prompt (S1..S25 and
A1..A25), not a table format, because the verification gate in the task
spec is literally `reg.count('## ')` ≥ 50. Table-driven markdown (as the
original 22-prompt file used) is valid markup but fails the gate.

Why: The verification hook is how the harness confirms no prompts got
silently dropped during an expansion. A per-prompt `## ` heading also
gives each prompt a stable GitHub anchor (e.g., `#s17--in-the-money-offer-mix`)
that the parametrised test IDs in
`tests/integration/test_genie_regression.py` can hyperlink to in
failure reports.

How to apply: When expanding or editing the registry, keep the
"## <PID> — <short label>" heading shape. If the gate changes
(unlikely), update this memo and the grep check together.

Parallel data: `tests/integration/test_genie_regression.py` has a
cred-free `test_registry_size_matches_regression_suite_md` that asserts
the Python `SAMPLE_PROMPTS`/`ADVERSARIAL_PROMPTS` lists stay in sync
with the markdown cohort sizes. Both the count gate AND this test
must pass — one catches the markdown, the other catches the Python
registry.
