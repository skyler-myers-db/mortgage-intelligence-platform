---
name: verify-scaffold-live-call
description: tools/verify_scaffold.py shells out to `databricks apps get mip-app` from the repo cwd (bundle host, no -p) whenever the CLI is on PATH
metadata:
  type: project
---

`python tools/verify_scaffold.py` is listed as an offline gate, but it runs `databricks apps get mip-app -o json` with cwd = the checkout and no profile. Inside the bundle the CLI resolves the anchor host (paychex since PR #184) and the matching profile, so on this Mac it reads the live paychex App (it printed the App SP's client id). Read-only, but it is a live call made without the "from /tmp with -p paychex" form (observed 2026-10-27, deploy-hardening lane).

**Why:** lanes with a "no live Databricks call except from /tmp -p paychex" rule trip it silently through a gate the brief itself lists.

**How to apply:** when a lane forbids live calls, run it with the CLI off PATH, e.g. `env PATH=/usr/bin:/bin "$VENV_PY" tools/verify_scaffold.py`, or disclose the read in the report. Related: [[app-sql-gold-only]].
