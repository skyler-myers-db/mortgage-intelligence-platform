# GitHub Actions — Workflow map + required secrets

This repo runs five workflows:

| File | Trigger | Credentials |
|---|---|---|
| [`ci.yml`](ci.yml) | `pull_request`, `push` to `main` / `feature/*` | **None.** Every job is credential-free. |
| [`deps-report.yml`](deps-report.yml) | `schedule` (Mondays 13:17 UTC) + `workflow_dispatch` | **GITHUB_TOKEN only:** contents and actions read in `report`, issues write in `publish`. Edits one `dependency-report` issue in place; never pushes, opens no branch or pull request. |
| [`nightly.yml`](nightly.yml) | `workflow_dispatch` only | **Parked:** its first job refuses every dispatch until M1-PR8 rebuilds it per client environment. Otherwise required — see below. |
| [`deploy-dev.yml`](deploy-dev.yml) | `workflow_dispatch` only | **One client environment per dispatch (M1-PR7):** the `build` job holds no ID token; the `deploy` job runs in the client's GitHub Environment with GitHub OIDC (`id-token: write`) for the client's deployer service principal, which has no stored credential, and reads the whole client profile from that environment's variables and secrets. |
| [`deploy-prod.yml`](deploy-prod.yml) | `workflow_dispatch` | **GITHUB_TOKEN (actions: read) only, for the live-validation age check. Non-deploying scaffold gate only.** |

The PR workflow (`ci.yml`) is designed to stay green for any contributor
including fork-based PRs: it uses placeholder BUNDLE_VARs and pytest
fixtures that explicitly install in-process repositories where needed.
No real workspace is touched.

**`deploy-dev.yml` deploys one client environment per dispatch (M1-PR7);
`nightly.yml` stays parked.** `nightly.yml` addressed the CI staging install,
whose workspace was deleted on 2026-10-29: its first job, `parked`, refuses
every dispatch until M1-PR8 rebuilds it per client under a non-admin
validator identity. `deploy-dev.yml` takes `client` (the client's GitHub
Environment) and `phase` (`plan`, `release`, `data`, `adopt` or `unadopt`);
docs/deployment.md, "CI deploy (deploy-dev.yml): one client environment per
dispatch", holds its jobs, its preflight and every variable and secret a
client environment holds.

The live validation workflow (`nightly.yml`, retained under its historical
filename) intentionally talks to a real Databricks workspace. It runs there so
drift between the SQL UDFs + Python scoring mirrors, Lakebase, Genie,
deployed-app auth, and the degraded-banner proof is caught before a release.
It is manual only because the Genie prompts and browser matrix burn real
customer/workspace compute. It fails loudly: live jobs check their own required
secrets and exit non-zero instead of silently skipping release gates.

The dev deploy workflow (`deploy-dev.yml`) is also manual-only. This repository
currently has one maintainer, so its required environment review cannot provide
separation of duties. Every dispatch must explicitly set
`acknowledge_single_maintainer_break_glass=true`, and the same maintainer must
approve the client's protected environment while recording the governing PR or
issue in the approval comment. This is an audited self-approval exception, not
independent review. The environment must restrict deployment to `main` and
forbid administrator bypass; the dispatch preflight reads those rules and
refuses a mutating phase when they differ (spike S8 is owed: if the job's token
cannot read them, a mutating phase needs the exact `protections_unverified`
ack). The original workflow run stays bound to the `workflow_dispatch` SHA; it
is not re-dispatched through a moving branch reference. The `build` job, which
holds no ID token, builds the frontend, stages the App source and downloads a
hash-pinned wheelhouse under a sha256 manifest. After approval, the `deploy`
job re-hashes that artifact, installs offline from it, resolves the client
profile from its environment (masking every identifying value first, then
writing an ephemeral mode-0600 `.env.local` and a `DEFAULT` Databricks CLI
profile that holds no credential: `auth_type = github-oidc` for the profile's
deployer service principal), proves that identity read-only, runs the dispatch
preflight and then `scripts/deploy.sh -t client --phase <phase>
--prebuilt-release <dir>`. Its deployer's federation subject is
`repo:<owner>/<repo>:environment:<client>`. Each client gets its own
non-cancelling concurrency lane (`mip-client-<client>`).

The live mutation gate encodes the GitHub run and attempt into a reviewed,
non-PII campaign name. It removes abandoned marked fixtures before pytest and
uses a fresh operator/admin OAuth pair in an `if: always()` postflight to
archive the current run and prove three active-inventory absences. An
interrupted build remains immutable until its five-minute lease expires; only
then may the admin transition atomically quarantine it as `failed` and
`archived` through the public campaign API.

The `rebase_unverified_app` input is a one-time adoption control, defaulting to
false. Select it only on a release whose existing App predates the signed server-owned
rollback record; that run stops the unverified App and must prove/capture a new
green contract before service resumes. Routine roll-forwards leave it false.

No workflow mints or rotates a credential. The former normal-credential
repair step, which wrote a rotated OAuth secret into GitHub with a
secrets-write token, was removed; a missing or invalid credential is owner
work outside CI.

Despite its legacy filename, `deploy-prod.yml` does not deploy or authenticate
to a production workspace. It is an explicit scaffold-only placeholder. The
only implemented mutable workflow is the governed dev deployment above;
production requires a separately reviewed environment contract before a real
workflow may call `scripts/deploy.sh -t client`.

Its second job, `live-validation-age`, is the release gate on live evidence
(audit quality-03 item 3): it runs `python -m tools.live_validation_gate
--release-sha "$GITHUB_SHA" --max-age-days 14` with the workflow's own
read-only `GITHUB_TOKEN` (`contents: read`, `actions: read`) and no other
credential. It passes only when the newest completed `nightly.yml` run on
`main` whose code is runtime-equivalent to the release commit is green, at
most 14 days old, and its parity, Playwright and simulated-drill jobs all
succeeded. A FAIL names the fix: dispatch `deploy-dev.yml` on that commit
(for the client environment, `phase` plan, release and data), then
`nightly.yml` on the same commit.

---

## Required repo secrets (live validation)

`deploy-dev.yml` reads none of the repository-level names below: since M1-PR7
it reads its client's environment only, and no repository- or
organization-level secret or variable may carry a client profile field name
(GitHub would merge it into the environment's contexts). The table is the
parked `nightly.yml`'s until M1-PR8.

All listed below must be set under **Settings → Secrets and variables
→ Actions → Repository secrets**. Missing any one of them aborts the
job that needs it with a clear `::error::` pointing at the offending
var.

| Secret | What it is | How to obtain |
|---|---|---|
| `DATABRICKS_HOST` | The URL of the workspace the workflow addresses. It still names the deleted CI staging workspace; each client environment holds its own as a secret (M1-PR7). | `nightly.yml` is parked, so nothing reads the repository-level one today. |
| `DATABRICKS_TOKEN` | Repository-level workspace-admin Personal Access Token for the deleted CI staging workspace, which the parked `nightly.yml` jobs still name. `deploy-dev.yml` does not read it: its deployer authenticates through GitHub OIDC (the profile's deployer role). It is not reused as an app bearer or treated as the product app-admin identity. | Dead with that workspace. Delete the `dev` environment copy; M1-PR8 replaces the repository-level one with a validator identity (docs/deployment.md, owner step 5). |
| `DATABRICKS_WAREHOUSE_ID` | ID of the dev serverless SQL warehouse. Same value `databricks bundle validate` resolves from `databricks.yml`. | Databricks UI → SQL Warehouses → click the warehouse → copy from URL or Connection Details. |
| `GENIE_SPACE_ID` | Retired as a deploy input (M1-PR3): `deploy-dev.yml` no longer reads it, because the deploy resolves the governed Genie space by its pin. The parked `nightly.yml` still names it for its live checks until M1-PR8. | Delete the `dev` environment copy once `nightly.yml` stops naming it. |
| `DATABRICKS_CLIENT_ID` | Non-admin service-principal OAuth client ID used by deployed Playwright and the non-admin RBAC smoke. | Provision with `tools/databricks/provision_m2m_oauth.py` or `docs/security/m2m-oauth-setup.md`. |
| `DATABRICKS_ACCOUNT_ID` | Databricks account UUID used by deploy identity proof and the verifier's account-admin denial probe. | Copy from the Databricks account console; this is an identifier, not a credential. |
| `DATABRICKS_ACCOUNT_HOST` | Databricks account-console host for exact SCIM identity proof and the verifier's read-only account-admin denial probe. | Optional on AWS (`https://accounts.cloud.databricks.com` is the workflow default); set explicitly for another cloud. |
| `DATABRICKS_ACCOUNT_CLIENT_ID` | Dedicated account-SCIM OAuth client used for exact identity/role inspection and bounded owner exclusion. | Must retain account-admin authority and differ from every app-facing M2M and target App. Account-admin inventory is required on every deploy because accessible UC owners can legitimately exist at account scope without MIP workspace assignment; Service Principal Manager alone is insufficient. Also grant Service Principal Manager on every forbidden normal, operator2, admin, verifier, agent-runtime, and target App so five-minute target credentials can be created and revoked. Keep this identity deploy/verifier-only. Deployment fails closed if credentials or proof are absent or inconclusive. |
| `DATABRICKS_ACCOUNT_CLIENT_SECRET` | Secret for the dedicated account-SCIM OAuth client. | Used only by bounded identity/role verification and the group-owner target-identity probes; workspace PAT credentials are never reused. |
| `DATABRICKS_CLIENT_SECRET` | Secret for the non-admin OAuth client. Live validation fails if this is absent; it must not fall back to the admin PAT. | Rotate with the same cadence as the workspace token. |
| `DATABRICKS_OPERATOR2_CLIENT_ID` | Dedicated second non-admin operator client ID for the live per-actor recovery/isolation proof. | Must differ from the normal, admin, and verifier client IDs. Provision with `--identity-role operator2`. |
| `DATABRICKS_OPERATOR2_CLIENT_SECRET` | Secret for the second non-admin operator OAuth client. | Used only to mint the short-lived `MIP_OPERATOR2_BEARER_TOKEN` in the on-demand live gate. |
| `DATABRICKS_ADMIN_CLIENT_ID` | Dedicated app-admin service-principal OAuth client ID. | Must differ from the normal and verifier client IDs. |
| `DATABRICKS_ADMIN_CLIENT_SECRET` | Secret for the dedicated app-admin OAuth client. | The workflow mints a fresh `MIP_ADMIN_BEARER_TOKEN`; no bearer is stored as a repository secret. |
| `DATABRICKS_VERIFIER_CLIENT_ID` | Dedicated AI Gateway verifier service-principal OAuth client ID. | Must have no App permission or admin membership. |
| `DATABRICKS_VERIFIER_CLIENT_SECRET` | Secret for the dedicated AI Gateway verifier OAuth client. | Used only for exact inference-row and verifier-identity proof. |
| `DATABRICKS_AGENT_RUNTIME_CLIENT_ID` | Dedicated runtime-owner service-principal application ID. | Owns only the reviewed Supervisor, Gateway, model, and per-runtime MLflow experiment; it must have no App, Lakebase, or warehouse access. |
| `DATABRICKS_AGENT_RUNTIME_CLIENT_SECRET` | Secret for the isolated runtime-owner OAuth client. | Exposed only to allowlisted provisioning/verification subprocesses and never to the App. |
| `DATABRICKS_AGENT_PROXY_CREDENTIAL_BUNDLE` | Canonical JSON containing the managed-Supervisor caller client ID, immutable credential ID, and one-shot client secret. | This is the sole GitHub source of truth and is written atomically at mint time. Live deploy and nightly jobs derive the public IDs from this bundle before writing the credential-versioned Databricks secret or verifying the runtime contract. |
| `MIP_AI_GATEWAY_PROOF_SIGNING_KEY` | Ed25519 private signing key for exact inference-row, App rollback, and destructive cutover-journal attestations. | Store only in deploy/verifier automation. The App and agent runtime receive only the derived public key; neither receives this private key. |
| `MIP_AI_GATEWAY_PROOF_PREVIOUS_VERIFY_KEY` | Optional prior proof/rollback Ed25519 public key during a bounded rotation. | Remove after every signed App and exact-proof record has converged to the current key. Never store a prior private key. |
| `MIP_GATEWAY_MODEL_ATTESTATION_SIGNING_KEY` | Independent Ed25519 private key for immutable proxy-model registration attestations. | Must differ from `MIP_AI_GATEWAY_PROOF_SIGNING_KEY`. Expose it only to the bounded runtime-owner model-registration command. |
| `MIP_GATEWAY_MODEL_ATTESTATION_VERIFY_KEY` | Public Ed25519 verification key derived from the model-attestation signing key. | Store as a GitHub Actions variable; nightly read-only jobs must never receive the private signing key. |
| `MIP_GATEWAY_MODEL_ATTESTATION_PREVIOUS_VERIFY_KEY` | Optional prior model-attestation public key during a bounded model-key rotation. | Keep it while any retained non-candidate model remains signed by that epoch. Deploy allocates a distinct current-key green family and never rewrites retained model tags. |

The Gateway endpoint, Agent Model version, managed Supervisor identity, and
inference table are not repository secrets. Live validation discovers them
from the source-owned resource names and verifies their immutable runtime
contract before it mutates data or spends browser/evaluation compute. Remove
any historical `MIP_AI_GATEWAY_ENDPOINT` or
`MIP_AI_GATEWAY_INFERENCE_TABLE` repository secrets; the workflow deliberately
does not consume them.

## Repository variables (the parked nightly.yml)

`deploy-dev.yml` reads none of these. It reads only its client environment's
own variables and secrets, each profile name bound one by one
(tools/clients/workflow_contexts.py), and its `check-dispatch` job refuses
every dispatch while any profile field or alias name (a name in
tools/clients/schema.py) is set as a repository- or organization-level
variable or secret: GitHub would merge it into every client environment's
contexts and silently fill a field the environment omits. So each profile
name in the table below, and every repository-level secret above that is a
profile field (`DATABRICKS_HOST`, `DATABRICKS_ACCOUNT_ID`, the client
secrets and signing keys), is deleted before the first per-client dispatch;
the parked `nightly.yml` that names them is rebuilt per client by M1-PR8.

`MIP_ADMIN_EMAILS`, `MIP_ADMIN_GROUP_NAME` and `MIP_APPROVER_GROUP_NAME` are
identifying profile fields: a client environment holds them as **secrets**,
never as variables at any scope (a variable prints in clear in a public run
log).

The "Defaults to" values below are `nightly.yml`'s fallbacks, which are the
live install's names. `deploy-dev.yml` has no fallback: every install name is
a variable of the client's environment (M1-PR7). Its deployer is the profile's
`MIP_IDENTITY_IDS_JSON.deployer` (a secret), federated with subject
`repo:<owner>/<repo>:environment:<client>`.

| Variable | What it is | Notes |
|---|---|---|
| `MIP_AI_GATEWAY_PROOF_HISTORICAL_VERIFY_KEYS` | Repository variable: every retired AI Gateway proof public key, oldest first, comma-separated. | No client profile field, so `deploy-dev.yml` no longer passes it (M1-PR7); a rotation adds it to the schema first. The key registry must end at the current key, and each signed lease record's `key_epoch` must be its key's index (docs/deployment.md, owner step 6). Public keys only. |
| `MIP_ADMIN_EMAILS` | Comma-separated app-admin email allowlist. | Identifying: a client environment **secret**, never a variable. Delete any repository- or `dev`-environment variable of this name. |
| `MIP_ADMIN_GROUP_NAME` | Optional local/test compatibility group name. | Identifying: a client environment **secret**, never a variable. Deployed automation uses the exact `MIP_ADMIN_IDENTITIES` value derived from `DATABRICKS_ADMIN_CLIENT_ID`; group headers are not authoritative in sandbox/production. |
| `MIP_DEFAULT_CATALOG` | Target Unity Catalog name. | Defaults to `mip`, the live catalog. Deploy and every nightly proof/render step use the same value. |
| `MIP_APP_NAME` | Bundle-managed Databricks App name. | Defaults to `mip-app`; use a unique DNS-style value for every isolated staging/customer workspace. |
| `MIP_LAKEBASE_INSTANCE` | Bundle-managed Lakebase instance name. | Defaults to `mip-app-state`; it must match `LAKEBASE_INSTANCE_NAME` if that compatibility alias is also set. |
| `MIP_LAKEBASE_SYNC_CATALOG` | Bundle-managed Lakebase synced-table catalog. | Defaults to `mip_app_state`, the live catalog. |
| `LAKEBASE_DATABASE` | Database inside the Lakebase instance that owns `mip_app`. | Defaults to `mip_app_state`; it must match `MIP_LAKEBASE_DATABASE_NAME` if that compatibility alias is also set. |
| `MIP_GENIE_SPACE_NAME` | Human-readable governed Genie space title. | Defaults to `Mortgage Lead Intelligence`; use an environment-qualified title when several spaces share an account. |
| `MIP_RUNTIME_SECRET_SCOPE` | Databricks secret scope for App runtime bindings. | Defaults to `mip-runtime`; isolate it per deployment. |
| `MIP_APP_ROLLBACK_SECRET_SCOPE` | Databricks secret scope for signed last-good App state. | Defaults to `mip-app-rollback`; isolate it per deployment. |
| `MIP_REVIEWED_FUNCTION_OWNER` | Exact owner returned unanimously by the live reviewed UC function inventory. | Set only from the deployed catalog after the deployer's authenticated owner capture succeeds. Nightly pins this value and rejects owner drift. |
| `MIP_LIFECYCLE_REPLAY_REVIEW_SHA256` | SHA-256 of the committed canonical lifecycle replay MERGE sequence. | Regenerate `docs/validation/lifecycle-replay-sql-2026-07-29.json` with `python -m tests.integration.test_lifecycle_delta_replay_live --catalog <catalog> --out <artifact>` and update this variable only after independent governance review. Before any Delta DDL, the live test validates the artifact's self-digest and requires both runtime SQL and this variable to equal that committed review. |

Optional non-secret Gateway family variables are `MIP_AI_GATEWAY_AGENT_MODEL_FAMILY`
(default `<catalog>.audit.mortgage_growth_supervisor_proxy`),
`MIP_AI_GATEWAY_AGENT_EXPERIMENT_BASE` (default
`mip-agent-runtime-gateway-proxy`), and `MIP_AI_GATEWAY_TABLE_PREFIX` (default
`mip_agent_gateway_growth_agent`). The experiment value is a family name, not
a `/Shared` path. Provisioning derives the runtime-owned `/Users/<runtime-id>/...`
experiment and contract-hashed endpoint, model, and table-family names. Do not
store those generated names or their binding/resource digests as secrets; the
deploy and nightly jobs export and verify them from live immutable resources.

## Optional secrets (live validation, gated features)

| Secret | What it enables | Notes |
|---|---|---|
| `MIP_APP_URL` | Explicit deployed Databricks App URL for live Playwright and real-infra drill jobs. | If unset for Playwright, the workflow resolves `MIP_APP_NAME` through the Databricks Apps API. The opt-in real-infra drill still requires this secret. |

`nightly.yml` still reads the historical `LAKEBASE_DATABASE` secret as a
temporary compatibility fallback, but new installations should use the
non-secret repository/environment variable above. The app and live tests use
workspace-identity Lakebase credentials, not static database passwords.

## No-secret jobs (run on every PR)

The PR workflow intentionally has no `secrets:` references. If you add
a job that needs a secret, consider whether that job belongs in
manual live validation instead; PR jobs that fail for fork-based PRs due to
missing secrets create friction with external contributors.

## Triage

- Parity-live red → `docs/runbook.md` §3.
- Missing-secret errors → rotate / re-add per the table above.
- Cold-warehouse timeouts on first run → expected once every ~2 weeks
  depending on workspace activity; the failure issue auto-filed by the
  `notify-on-failure` job links directly to the workflow run.

The auto-filed issue is labelled `ci-failure` + `live-validation`; close it
once the next manual live validation run is green.
