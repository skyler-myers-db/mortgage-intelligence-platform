---
name: CI OAuth mint failure must not fall through to localhost
description: In the nightly Playwright job, a failed M2M Bearer mint is a hard job failure, never a silent fall-back to the localhost-on-runner path.
type: feedback
---

When the nightly Playwright workflow attempts the deployed-URL path
(all three of `DATABRICKS_CLIENT_ID`, `DATABRICKS_CLIENT_SECRET`,
`MIP_APP_URL` are set as GitHub secrets), a mint failure from
`tools/oauth_m2m_mint.py` MUST cause the job to fail hard. Do not
architect a fall-through that re-runs the spec against localhost on
mint failure.

**Why:** The whole point of paying to hit the deployed App is to catch
the class of regression that only manifests behind the OAuth proxy —
credential rotation, grant drift, proxy config changes. A silent
localhost fallback would mask exactly the failures the credential-kill
drill is designed to prove are catchable. The localhost fallback is
only acceptable when the SECRETS ARE ABSENT (pre-admin-setup state),
not when they're present-but-revoked.

**How to apply:** Two-path selection (`use_deployed=true|false`) lives
in a preflight step (`Detect deployed-URL path`) that only checks
secret presence. The mint step runs under `if: use_deployed == true`
and fails the job on any error. Future edits must preserve this split;
if someone adds a `|| echo 'falling back'` or `continue-on-error: true`
around the mint step, that is a governance regression.
