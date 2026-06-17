# Genie eval

Recurring quality check for the Genie space. Hits a fixed question
set after every gold refresh and scores the answers against the
trusted-asset citations + forbidden-keyword + required-keyword +
table-row-shape contracts encoded in
[`tools/genie_eval_questions.yml`](../../tools/genie_eval_questions.yml).

There are two packs:

- `tools/genie_eval_questions.yml` — lightweight canonical release gate.
- `tools/genie_stress_questions.yml` — broader app-boundary stress matrix for
  free-form phrasing, source gaps, PII, prompt injection, off-topic questions,
  cross-lender requests, and SQL/schema-sniffing attempts.

## Run it

```bash
# local backend
python tools/genie_eval.py --base http://localhost:8000

# deployed app (Databricks Apps OAuth gate)
python tools/genie_eval.py \
  --base https://mip-app-2543889327043640.aws.databricksapps.com \
  --token "$(databricks auth token --host $DATABRICKS_HOST | jq -r .access_token)"

# deployed app stress pack
python tools/genie_eval.py \
  --base "$MIP_APP_URL" \
  --token "$MIP_BEARER_TOKEN" \
  --questions tools/genie_stress_questions.yml \
  --report-dir /tmp/mip-genie-stress
```

Each run writes:

- `<UTC-stamp>.md` — human-readable scorecard with per-question
  pass/fail, latency, and the failure notes
- `latest.json` — machine-readable summary; downstream dashboards
  / alerting read this

## Regression check

`baseline.json` is the committed score floor. A run whose
`overall_score` drops more than 10 points below the baseline fails
the release gate. Any canonical question failure also returns non-zero.
Use `--soft` only for exploratory report-only runs. When the new floor
is intentional, refresh the baseline:

```bash
python tools/genie_eval.py --base $URL --update-baseline
git add docs/genie_eval/baseline.json
git commit -m "chore(genie-eval): bump baseline to N.N"
```

## What the eval catches

- Hallucinated "no candidates" answers when the data has plenty
  (`forbid_keywords`)
- Missing source citations (`must_cite`)
- Refusal-path violations: questions about protected-class
  attributes must hit the fair-lending refusal; questions about
  out-of-footprint geos must mention scope
  (`require_keywords`)
- Answer-shape regressions: top-N questions must return at least
  N rows (`min_rows`)
- Unsafe guardrail regressions: refusal/source-gap questions must return
  allowed response sources, no SQL, no rows, and known-gap/refusal proof
  when the question pack requests those checks
- Latency regressions: per-question soft budgets that surface in
  the scorecard but don't fail the run

## What the eval does NOT catch

- Subtle factual errors when a count is "in the right ballpark"
  but wrong by a few percent. The `min_rows` check is a shape
  signal, not a value signal.
- Drift in the prose phrasing (we score keyword presence, not
  semantic match). Use the human-readable markdown report to spot
  changes that look right by the rules but read wrong to a human.
