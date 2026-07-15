# M2M OAuth for nightly Playwright — one-time setup

**Purpose.** The nightly `playwright-e2e-live` GitHub Actions job needs to
reach the deployed Databricks App at
`https://mip-app-2543889327043640.aws.databricksapps.com` through the
Databricks Apps OAuth proxy. A fresh browser hit to `/` 302-redirects to
a consent page; the spec therefore attaches a workspace Bearer token
(`Authorization: Bearer …`) to every request, and the Apps middleware
short-circuits the redirect for that request.

CI cannot use a PAT for the Apps proxy — PATs authenticate against the
workspace API, not the per-app OAuth resource. The only mechanism that
works from an unattended runner is **Databricks M2M OAuth** (RFC 6749
client-credentials flow) against a service principal that has been
granted `CAN USE` on the deployed app.

**Audience.** A Databricks workspace admin. Everything in this doc is a
one-time setup; after the first run the CI secrets just need rotation
on the 90-day cadence in the "Rotation" section below.

**Canonical reference.**
[https://docs.databricks.com/en/dev-tools/auth/oauth-m2m.html](https://docs.databricks.com/en/dev-tools/auth/oauth-m2m.html)
is the authoritative Databricks doc as of Q1 2026. If Databricks
restructures their docs site and that URL 404s, search for "OAuth
machine-to-machine (M2M) authentication" from the Databricks docs
landing page — the flow and API surface have been stable since 2024.

---

## Setup path selector

Two paths exist for running this setup. Pick one:

- **SDK-scripted (canonical — zero click-ops).** Recommended. Creates
  the service principal, grants `CAN USE`, mints the OAuth secret, and
  pushes the three GitHub Actions secrets — all from one Python
  invocation. See §0 below.
- **Manual UI (appendix).** Click-through in the workspace admin
  console. Retained for workspaces whose policy disallows scripted SP
  creation. See §A in the appendix.

The remainder of this doc mirrors the same numbered-steps structure for
both paths, so the SDK flow and the UI flow are step-by-step comparable.

---

## 0. Zero-click SDK provisioning (canonical path)

The entire procedure — create the service principal, grant `CAN USE` on
the deployed App, mint the OAuth client_id + client_secret, write the
three secrets to the GitHub repo — runs from a single Python tool:

```bash
# Pre-reqs (one-time):
#   1. `databricks auth login` as a workspace admin (or set DATABRICKS_HOST +
#      an admin PAT in ~/.databrickscfg DEFAULT profile).
#   2. `gh auth login` against the repo owner. Used to push the three
#      GitHub secrets via stdin.
#   3. `./scripts/deploy.sh -t dev` has been run at least once, or the
#      lower-level bundle resource deploy plus app promotion has created the
#      deployed App resource (`mip-app`) in the workspace.

python tools/databricks/provision_m2m_oauth.py \
    --sp-name mip-nightly-ci-sp \
    --app-name mip-app \
    --gh-repo skyler-myers-db/mortgage-intelligence-platform \
    --set-gh-secrets
```

What this runs (in order, all via `databricks-sdk`):

1. `w.service_principals.list(filter="displayName eq 'mip-nightly-ci-sp'")`
   — idempotent lookup. If the SP exists, re-use it; else create it via
   `w.service_principals.create(...)`.
2. `w.apps.set_permissions("mip-app", access_control_list=[...CAN_USE...])`
   — grants the SP `CAN USE` on the deployed App resource.
3. `w.service_principal_secrets_proxy.create(service_principal_id=...)`
   — mints a one-shot OAuth client_secret. The secret is returned in
   the response's `.secret` field and cannot be retrieved later.
4. `gh secret set DATABRICKS_CLIENT_ID --repo ... <stdin>` (+ the
   secret and `MIP_APP_URL`) — piped via stdin so the value never
   appears in argv/ps. Each call is preceded by a GitHub Actions
   `::add-mask::` directive so any accidental echo downstream is
   redacted.

Flags of note:

| Flag                    | Default                                          | Purpose                                                                                           |
| ----------------------- | ------------------------------------------------ | ------------------------------------------------------------------------------------------------- |
| `--sp-name`             | `mip-nightly-ci-sp`                              | SCIM `displayName` for the SP.                                                                    |
| `--app-name`            | resolved from `databricks.yml`                   | Deployed App to grant on.                                                                         |
| `--gh-repo`             | inferred from `git remote get-url origin`        | Target GitHub repo for secret upload.                                                             |
| `--set-gh-secrets`      | off (explicit opt-in)                            | Required for secret upload. Without it, the tool prints the client_secret to stdout once.         |
| `--rotate`              | off                                              | If the SP exists, mint a fresh secret. Old secret remains valid until revoked in Accounts Console. |
| `--no-grant-can-use`    | grant is on                                      | Skip the CAN_USE grant (use when an admin grants it separately).                                  |
| `--dry-run`             | off                                              | Resolve defaults and validate arguments without touching the workspace.                           |

Rotation (replaces the "Rotation cadence" section below when you use
the SDK path): re-run with `--rotate --set-gh-secrets`. The old secret
is still valid until revoked in the Accounts Console — same zero-
downtime order as the UI flow (new secret first, revoke second).

Tests: `.venv/bin/pytest tests/unit/test_provision_m2m_oauth.py -q`
mocks the full SDK surface to pin the call-order contract; a future
SDK rename will break this test before it breaks production setup.

**If the SDK path fails with "403 Forbidden" / "PermissionDenied":**
your current workspace auth is not a workspace admin. Either run this
tool from an admin profile, or fall back to the manual UI path (§A).

---

## Appendix A — Manual UI setup (fallback)

Use this path when `tools/databricks/provision_m2m_oauth.py` cannot be
run (workspace policy, no admin shell access, emergency rotation
without local tooling, etc.). The end state is identical to the
SDK-scripted path; you are just performing the same four API calls
through the workspace admin console.

### A.1. Create the service principal

From the workspace admin console:

1. **Settings → Identity and access → Service principals → Add service principal**.
2. Name: `mip-nightly-ci-sp` (or your house convention — whatever you pick
   here will show up in the Apps audit log as the caller).
3. No workspace entitlements beyond the defaults are needed. Do **not**
   grant `Workspace access` on the sidebar if it's off by default; the
   SP only needs to traverse the Apps OAuth proxy, nothing else.

> **Why a dedicated SP and not a personal OAuth app:** CI should never
> run under a human identity. A dedicated SP gives us (a) an
> independently revokable credential, (b) a clean audit trail, and
> (c) a surface that's easy to scope to exactly `CAN USE` on one app.

### A.2. Mint a client_id + client_secret

With the SP selected in the admin console:

1. **OAuth secrets** tab → **Generate secret**.
2. Copy both values **immediately**. The `client_id` remains visible in
   the UI forever; the `client_secret` is shown exactly once. Losing it
   means deleting and re-minting.
3. Note the expiry. Databricks currently defaults OAuth secrets to a
   730-day lifetime; we rotate on a tighter 90-day cadence (see below)
   so a lost secret never has more than 90 days of blast radius.

Values you now have:

```
DATABRICKS_CLIENT_ID     = dbc-m2m-<opaque>
DATABRICKS_CLIENT_SECRET = dose_<opaque>
```

### A.3. Grant `CAN USE` on the deployed app

The App is a first-class resource in Unity Catalog / Apps permissions.
From the Databricks UI:

1. **Compute → Apps → mip-app → Permissions**.
2. **Add users and groups → Service principals → mip-nightly-ci-sp → CAN USE**.
3. Save.

`CAN USE` is the **minimum** grant required — it lets the SP traverse
the OAuth proxy and hit any route the app exposes, but **does not**
give the SP any workspace admin, UC read, or cluster-management rights.

> **Do not** grant `CAN MANAGE` or `IS OWNER`. That would let a leaked
> CI secret redeploy the app, which is wildly out of scope for a
> read-only nightly Playwright check.

If the SP also needs direct Unity Catalog reads for the parity job
(separate from the Apps OAuth proxy), grant `USE CATALOG` / `USE SCHEMA`
/ `SELECT` in SQL — but the Playwright job in this doc only needs
`CAN USE` on the app.

### A.4. Store the secrets in GitHub

From the GitHub repo:

1. **Settings → Secrets and variables → Actions → New repository secret**.
2. Add three secrets:

| Secret name                | Value                                                            |
| -------------------------- | ---------------------------------------------------------------- |
| `DATABRICKS_CLIENT_ID`     | The `client_id` from step 2.                                     |
| `DATABRICKS_CLIENT_SECRET` | The `client_secret` from step 2. (Redacted in all Actions logs.) |
| `MIP_APP_URL`              | `https://mip-app-2543889327043640.aws.databricksapps.com`        |

The nightly workflow requires all three for deployed-app proof. If
`DATABRICKS_CLIENT_ID` or `DATABRICKS_CLIENT_SECRET` is missing, the
`playwright-e2e-live` job fails before Playwright starts. It must not
fall back to localhost or to the admin PAT, because the release gate is
also the non-admin authorization proof.

**`DATABRICKS_HOST` is already in repo secrets** from the existing
parity-live job (`${{ secrets.DATABRICKS_HOST }}`); the mint helper
reuses it. You do not need to add it a second time.

## 1. Verify from your laptop first

Applies to both paths (SDK-scripted and manual UI).

Before touching CI, confirm the mint works with a local dry-run:

```bash
export DATABRICKS_HOST=https://<your-workspace>.cloud.databricks.com
export DATABRICKS_CLIENT_ID=<your-m2m-client-id>
export DATABRICKS_CLIENT_SECRET=<your-m2m-client-secret>

python tools/oauth_m2m_mint.py > /tmp/bearer.txt

# Expect: a JSON body from the deployed app (not a consent HTML page).
curl -sSf \
    -H "Authorization: Bearer $(cat /tmp/bearer.txt)" \
    https://mip-app-2543889327043640.aws.databricksapps.com/api/health \
    | head -c 200
```

If `curl` prints an HTML login page instead of JSON, the SP does not
have `CAN USE` on the app — go back to step 3.

## 2. Enable the CI path

Once the secrets are in place, the next nightly run (or a manual
`workflow_dispatch`) will automatically pick them up. The
`playwright-e2e-live` job emits explicit `::notice::` lines with the
deployed app URL and the non-admin M2M bearer path, so you can confirm
from the Actions run summary that it hit the live Databricks App rather
than a local runner.

---

## Token TTL and refresh

M2M tokens from Databricks have a **~1 hour TTL**. A Playwright run
takes 5–10 minutes end-to-end. A single mint at job start is therefore
enough — we don't need mid-run refresh. `tools/oauth_m2m_mint.py`
comments mirror this; if the spec ever grows past ~45 min, re-mint
before the long phase rather than caching across steps.

The token itself is written to `$GITHUB_ENV` as `MIP_BEARER_TOKEN`. It
lives only in the runner VM's process environment — GitHub does not
persist it, and it is **not** exposed in the Actions logs because the
mint helper writes the token to stdout while all diagnostics go to
stderr (GitHub only redacts declared secrets, so the helper's stderr
lines deliberately never include the token).

---

## Rotation cadence

Rotate `DATABRICKS_CLIENT_SECRET` **every 90 days**. Calendar the next
rotation in whatever system the team uses for ops chores.

Procedure:

1. Admin console → Service principals → `mip-nightly-ci-sp` → OAuth
   secrets → **Generate secret** (do not delete the old one yet).
2. GitHub → Secrets → edit `DATABRICKS_CLIENT_SECRET` → paste new value.
3. Trigger the nightly via `workflow_dispatch` and confirm it succeeds.
4. Admin console → **Revoke** the old secret.

This "new secret first, revoke second" order means zero-downtime: if
the new secret is wrong, the old one is still active until step 4.

---

## Credential-kill drill

Proves on demand that a revoked SP secret causes a hard, visible nightly
failure — not a silent fall-through to the localhost path or a cached
success.

**When to run.**
- Before every major release dry-run (paired with the existing
  `docs/credential-kill-drill.md` sweep for warehouse/Lakebase/Genie).
- After any change to `tools/oauth_m2m_mint.py` or the nightly workflow's
  OAuth mint step.
- Any time an auditor asks "what happens if the CI secret leaks?"

**Procedure.**

1. **Baseline.** Confirm the last nightly run used the deployed URL
   path (check the run's `::notice::` line). Record the run URL.

2. **Revoke in the Databricks UI.** Admin console → Service principals
   → `mip-nightly-ci-sp` → OAuth secrets → **Revoke** the active secret.
   **Do not** update `DATABRICKS_CLIENT_SECRET` in GitHub yet — the
   point of the drill is to see what happens when CI has a now-invalid
   secret.

3. **Re-run the mint helper locally with the now-invalid secret.**
   ```bash
   export DATABRICKS_HOST=https://<your-workspace>.cloud.databricks.com
   export DATABRICKS_CLIENT_ID=<the-same-client-id>
   export DATABRICKS_CLIENT_SECRET=<the-now-revoked-secret>
   python tools/oauth_m2m_mint.py
   echo "exit=$?"
   ```
   **Expected:** exit code `4` and a stderr line matching
   `ERROR authenticate() raised ... invalid_client` (or similar;
   Databricks' OAuth server returns a 401 with `error=invalid_client`
   for revoked credentials). **Not expected:** exit 0 with a stale
   cached token, or exit 2 with "missing env var". If either happens,
   the drill has found a real bug — stop and file an issue.

4. **Trigger the nightly via `workflow_dispatch`.** Watch the
   `playwright-e2e-live` job's "Mint workspace Bearer (M2M OAuth)" step.
   **Expected:** the step fails hard; the subsequent Playwright step
   does not run; the `notify-on-failure` job files a tracking issue.
   **Not expected:** the job silently falls back to the localhost path.
   The workflow's conditional is explicitly structured so that "mint
   failed" is a terminal failure, not a fall-through — see the
   `fail-on-mint-error` step in `.github/workflows/nightly.yml`.

5. **Restore.** Generate a new OAuth secret for the SP, update the
   `DATABRICKS_CLIENT_SECRET` repo secret, re-trigger the nightly, and
   confirm a green run with the deployed URL path.

**Evidence to capture.** Save the failed run URL + the mint step's
stderr excerpt into `tools/kill_drill/evidence/` (same directory the
existing drill uses) with a filename like
`YYYY-MM-DD-m2m-oauth-drill.md`. Governance review expects this
artifact alongside the warehouse/Lakebase/Genie drill evidence.

---

## Security invariants (do not regress)

- `DATABRICKS_CLIENT_SECRET` is **never** printed to stdout, embedded
  in a commit, pasted in `app.yaml`, or included in a screenshot.
- The mint helper writes diagnostics to stderr only; stdout carries
  exactly one payload (the token) so `TOKEN=$(python tools/oauth_m2m_mint.py)`
  works cleanly.
- The SP's scope is `CAN USE` on the deployed app and nothing else. If
  a future feature needs broader access (e.g. SQL warehouse reads), add
  a second purpose-built SP rather than widening this one.
- The AI Gateway verifier SP is separate from the app-access SP. It may
  receive only its scoped Lakebase role, serving-endpoint `CAN QUERY`, and
  SQL-warehouse `CAN USE`; it must have no direct, inherited, or effective
  app permission and no direct or nested membership in `mip-admin` or an
  app-authorized group.
- Verifier provisioning hydrates the workspace group graph and app ACL before
  granting resources or minting a secret. Any group or permission resolution
  error fails closed and requires an administrator to repair visibility before
  retrying.
- The workflow's mint step uses `run: |` with inline shell, not a
  third-party GitHub Action. No marketplace dependency is introduced.
