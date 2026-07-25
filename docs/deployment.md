# Deployment guide

## Local

```bash
cp .env.example .env.local
pip install -r requirements.txt
npm --prefix frontend install
npm --prefix frontend run dev
uvicorn backend.main:app --reload
```

## Build frontend

```bash
npm --prefix frontend run build
```

FastAPI serves `frontend/dist` automatically if present.

## Databricks App

1. Fill `.env.local` with workspace, warehouse, lender/runtime values, and the
   deployment-owned resource namespace (or set the corresponding GitHub
   Actions variables). The exact lender-name/NMLS pair must already exist in
   the source-controlled reviewed identity registry in
   `backend/schemas/lender_identity.py`; customer onboarding adds that pair in
   an independently reviewed PR before these deployment variables are set:

```bash
MIP_LENDER_NAME="<exact reviewed customer legal lender name>"
MIP_LENDER_NMLS_ID="<matching reviewed customer lender NMLS id>"
# Optional; defaults from MIP_LENDER_NAME when unset.
MIP_TENANT_ID="<customer_lender_slug>"
MIP_DEFAULT_CATALOG="acme_mip"
# Use a deployment-owned namespace in any isolated staging/customer workspace.
MIP_APP_NAME="acme-mip-app"
MIP_LAKEBASE_INSTANCE="acme-mip-state"
MIP_LAKEBASE_SYNC_CATALOG="acme_mip_state"
LAKEBASE_DATABASE="mip_app_state"
MIP_GENIE_SPACE_NAME="Acme Mortgage Lead Intelligence"
MIP_RUNTIME_SECRET_SCOPE="acme-mip-runtime"
MIP_APP_ROLLBACK_SECRET_SCOPE="acme-mip-app-rollback"
# Required for every deployed runtime, including the dev sandbox.
MIP_COTALITY_ID_MASK_SECRET="<deployment-scoped-random-secret>"
MIP_GENIE_ACTION_SECRET_CURRENT="<deployment-scoped-random-secret>"
MIP_GENIE_ACTION_SECRET_KID="v2"
# Set both previous-key values only for a bounded rotation grace period.
# MIP_GENIE_ACTION_SECRET_PREVIOUS="<prior-deployment-secret>"
# MIP_GENIE_ACTION_SECRET_PREVIOUS_KID="v1"
```

   See [`docs/se-onboarding.md`](se-onboarding.md) for the full
   one-workspace-one-lender tenancy checklist.
2. For a customer fork, rebind the bundle's single workspace-host anchor
   before any deploy:

```bash
./scripts/configure-workspace.sh https://<customer-workspace>.cloud.databricks.com
```

   The `databricks.yml` host anchor, `DATABRICKS_HOST`, and the repository or
   environment configuration used by GitHub Actions must identify that same
   workspace. Before the first deployment into an isolated workspace, run:

```bash
python tools/databricks/bundle_env.py validate -t dev
python tools/databricks/bundle_env.py plan -t dev
```

   The first plan must report only additions (`0 to change`, `0 to delete`). A
   change or deletion means the local bundle state or resource namespace still
   points at another installation; stop before deployment and reconcile it.
   `MIP_LAKEBASE_INSTANCE`/`LAKEBASE_INSTANCE_NAME` and
   `LAKEBASE_DATABASE`/`MIP_LAKEBASE_DATABASE_NAME` are compatibility alias
   pairs and must match when both names are present.

3. Run the deployment script. It builds the frontend, provisions Genie if
   needed, validates/plans/deploys the direct bundle through the env-aware wrapper,
   promotes the uploaded source to the running Databricks App, and runs
   the refresh/smoke steps.

```bash
./scripts/deploy.sh -t dev
```

The command of record deploys only an exact committed revision. Before any
workspace mutation, and again immediately before bundle upload, it refuses
staged or unstaged tracked changes and untracked non-ignored files. Standard
ignored artifacts such as `.env.local`, `frontend/dist/`, `sql/_rendered/`,
and `.databricks/` are preserved and do not block deployment. Run the gate by
itself with:

```bash
./scripts/deploy.sh --verify-source-only
```

The SHA printed by this gate is the value advertised as `MIP_GIT_SHA` and
verified by the live smoke test.

### Per-run M2M identities

Live app validation and agent ownership use seven service principals and store only their OAuth
client credentials. No bearer token is a GitHub secret or `.env.local` value:

One-shot OAuth secrets are written only to the repository detected from the
current `origin`. Customer automation without a local git remote must set
`MIP_M2M_GITHUB_REPOSITORY=owner/repository`; an explicit `--gh-repo` must
match that reviewed sink. Grant-only reconciliation with `--no-mint-secret`
does not require this binding and can run from a customer fork unchanged.

| Role | Default service principal | GitHub Actions secrets |
|---|---|---|
| normal app user | `mip-nightly-ci-sp` | `DATABRICKS_CLIENT_ID`, `DATABRICKS_CLIENT_SECRET` |
| second app operator | `mip-nightly-operator2-ci-sp` | `DATABRICKS_OPERATOR2_CLIENT_ID`, `DATABRICKS_OPERATOR2_CLIENT_SECRET` |
| app admin | `mip-nightly-admin-ci-sp` | `DATABRICKS_ADMIN_CLIENT_ID`, `DATABRICKS_ADMIN_CLIENT_SECRET` |
| candidate release probe | `mip-release-probe-ci-sp` | `DATABRICKS_RELEASE_PROBE_CLIENT_ID`, `DATABRICKS_RELEASE_PROBE_CLIENT_SECRET` |
| AI Gateway verifier | `mip-ai-gateway-verifier-ci-sp` | `DATABRICKS_VERIFIER_CLIENT_ID`, `DATABRICKS_VERIFIER_CLIENT_SECRET` |
| agent resource runtime | `mip-agent-runtime-ci-sp` | `DATABRICKS_AGENT_RUNTIME_CLIENT_ID`, `DATABRICKS_AGENT_RUNTIME_CLIENT_SECRET` |
| Supervisor proxy caller | `mip-agent-supervisor-proxy-ci-sp` | `DATABRICKS_AGENT_PROXY_CREDENTIAL_BUNDLE` |

For a fresh workspace, create credentials **before** running deploy. The
credentials-only mode never lists or grants an App, Lakebase instance, Gateway
endpoint, or SQL warehouse; it creates/resolves the reserved service principal,
optionally creates/joins the reviewed admin group, and sends only that role's
client ID and one-shot client secret to the repository bound to `origin`.
Export the verifier proof signing key and its derived public verify key before
these commands. Each operation holds its outer signed App deployment lease and
also acquires the fixed global `mip-oauth-credential-mutations` lease before its
first provider inventory read. The global name is independent of App and
principal names, so alternate valid App names cannot create an overlapping
bootstrap, rotation, audit, or temporary identity probe.
Every standalone bootstrap or rotation also requires `HEAD` to equal the
configured source SHA (when set) and a completely clean tracked and untracked
worktree. A deploy-owned mutation instead borrows the already-acquired signed
outer lease and requires its explicit preflighted source SHA.
Bootstrap `agent_runtime` first: it is the only service-principal reader on the
shared signed-lease root and the delegated lease writer for every later
credential mutation.
The live workspace does not initially contain `mip-admin`, so creating that
group remains a separately reviewed, explicit action:

```bash
python tools/databricks/provision_m2m_oauth.py \
  --pre-app-bootstrap --identity-role agent_runtime --set-gh-secrets \
  --gh-repo skyler-myers-db/mortgage-intelligence-platform
python tools/databricks/provision_m2m_oauth.py \
  --pre-app-bootstrap --identity-role normal --set-gh-secrets \
  --gh-repo skyler-myers-db/mortgage-intelligence-platform
python tools/databricks/provision_m2m_oauth.py \
  --pre-app-bootstrap --identity-role operator2 --set-gh-secrets \
  --gh-repo skyler-myers-db/mortgage-intelligence-platform
python tools/databricks/provision_m2m_oauth.py \
  --pre-app-bootstrap --identity-role admin --create-group \
  --set-gh-secrets --gh-repo skyler-myers-db/mortgage-intelligence-platform
python tools/databricks/provision_m2m_oauth.py \
  --pre-app-bootstrap --identity-role release_probe \
  --set-gh-secrets --gh-repo skyler-myers-db/mortgage-intelligence-platform
python tools/databricks/provision_m2m_oauth.py \
  --pre-app-bootstrap --identity-role verifier \
  --set-gh-secrets --gh-repo skyler-myers-db/mortgage-intelligence-platform
python tools/databricks/provision_m2m_oauth.py \
  --pre-app-bootstrap --identity-role agent_proxy \
  --set-gh-secrets --gh-repo skyler-myers-db/mortgage-intelligence-platform
```

Pre-App bootstrap is creation-only and refuses every existing reserved
principal. If a failed bootstrap was fully compensated, prove that the
credentialless principal is removed before repeating the bootstrap command; do
not add `--rotate` to a bootstrap retry. After the App exists, a reviewed
rotation uses normal mode with that role's canonical
`--expected-application-id`, `--rotate`, and `--set-gh-secrets`.
Before provider create, the provisioner writes and reads back a signed,
immutable intent containing the exact authority, principal, sink descriptor,
global lease generation, and prior credential IDs. It records the returned
credential ID before exposing the one-shot secret and arms the exact GitHub
sink before its first write. No journal phase contains the secret.
If exact restoration or delivery is unproven, an operation-bound quarantine
record under `/.mip-deployment-leases` blocks every later credential baseline,
even under another App name. A quarantine is cleared only by a signed terminal
resolution whose provider inventory and sink disposition validate against the
intent; lease expiry or deleting a record is never recovery proof.
Recovery is an explicit deploy mode, not a fresh-create retry. First inspect the
signed record with
`python -m tools.databricks.oauth_credential_recovery_cli inspect
--intent-path <workspace-path>`. After governance review, set all four
`MIP_OAUTH_CREDENTIAL_RECOVERY_INTENT_PATH`,
`MIP_OAUTH_CREDENTIAL_RECOVERY_PRINCIPAL_ID`,
`MIP_OAUTH_CREDENTIAL_RECOVERY_AUTHORITY_IDENTITY`, and
`MIP_OAUTH_CREDENTIAL_RECOVERY_PROVIDER_API`, then rerun `scripts/deploy.sh`.
The deploy takes over only the intent's signed recovery root after expiry,
re-proves the exact provider identity and full credential inventory, revokes
only the observed or sole attributable delta, deletes only the armed GitHub
secret names and proves their repeated absence, appends a signed
resolver-lease resolution, and releases the resolver lease. A zero-delta intent
without a durable observation remains quarantined: a bounded inventory read
cannot prove that a delayed provider create will never commit. It may be
resolved only with provider-specific durable non-commit evidence, which the
current Databricks credential APIs do not expose. Multiple deltas, missing
prior IDs, a delete exception, or mismatched operator confirmation likewise
remain quarantined.
If failure occurred after the global mutation lease was acquired but before an
intent was durably committed, inspect it with
`python -m tools.databricks.oauth_credential_recovery_cli
inspect-orphan-lease`. Only when `intent_present` is false may a reviewer set
both `MIP_OAUTH_CREDENTIAL_ORPHAN_LEASE_ID` and
`MIP_OAUTH_CREDENTIAL_ORPHAN_RECOVERY_ROOT_LEASE_ID` to those exact reviewed
coordinates and rerun `scripts/deploy.sh`. The deploy first acquires and
heartbeats a new signed outer App lease, then invokes
`recover-orphan-lease` with both confirmations before any later deployment
mutation. Directly invoking the recovery subcommand after the failed deploy is
unsupported because no live outer deployment lease exists. Orphan recovery and
intent recovery are mutually exclusive, and an authoritative intent always
requires the intent-recovery path.
Create the separate account-SCIM OAuth principal and store
`DATABRICKS_ACCOUNT_ID`, `DATABRICKS_ACCOUNT_CLIENT_ID`, and
`DATABRICKS_ACCOUNT_CLIENT_SECRET` independently; it must not reuse any of the
seven workspace client IDs. A first install with an approved group owner requires
account-admin authority because the bundle-created target App cannot be
delegated in advance. After that install, downscope the account principal to
Service Principal Manager on every normal, operator2, admin, release-probe,
verifier, agent-runtime, agent-proxy, and now-existing target-App principal so later deploys
can create and revoke each five-minute target-identity proof credential. Also
configure the two distinct Ed25519 private keys
`MIP_AI_GATEWAY_PROOF_SIGNING_KEY` and
`MIP_GATEWAY_MODEL_ATTESTATION_SIGNING_KEY`, plus the runtime HMAC/masking
secrets documented below. Store its derived public key separately as the
`MIP_GATEWAY_MODEL_ATTESTATION_VERIFY_KEY` GitHub Actions variable; nightly
read-only jobs never receive the private key. Only then run
`./scripts/deploy.sh -t dev` or dispatch `deploy-dev.yml`. Bundle apply first
creates only non-App dependencies. Deploy then resolves their concrete IDs and
applies the App resource bindings without a source deployment: a new App is
created stopped with `--no-compute`, while an existing App update must preserve
its exact active/pending deployment and compute state. Only then does deploy
re-resolve the App service principal and grant `CAN_USE` to the exact normal,
operator2, and admin client IDs with `--no-mint-secret`. The release probe,
verifier, and runtime retain no persistent App access; verifier/runtime resource
reconciliation remains later, after their resources exist.

The deployment-wide App fence is an append-only signed generation chain under
`/.mip-deployment-leases`. Acquire, renewal, release, and authorized expired
takeover each contend on the one deterministic successor path with Workspace
Files create-without-overwrite; no authoritative generation is overwritten or
deleted. A signed head file is only a read accelerator, so stale, missing, or
corrupt hint state falls back to the canonical chain and cannot select a
winner. Active leases have a six-hour absolute lifetime. Keep every retired
public key, oldest first, in the public GitHub Actions variable
`MIP_AI_GATEWAY_PROOF_HISTORICAL_VERIFY_KEYS`; private keys are still destroyed
at retirement. Each successor records the public-key epoch and rejects epoch
regression. The signed chain retains its original recovery root through every
release by the same holder. A new holder with no signed history begins a
separate root, while a returning holder discovers its newest historical root
without inheriting another holder's authority. A killed deploy can therefore
present its exact root for an expired takeover even when no first-install
journal exists. A stranded legacy v2 fence is migrated append-only only after
its expiry plus the five-minute quiescence margin and that exact durable
recovery authority.

Before the first Apps API create, deploy writes a signed, lease-bound ownership
journal under `/.mip-deployment-leases/<app>.first-install.json`. Journal v3
records the workspace, deployer identity, source revision, original lease and
bounded creation window, exact resolved resource bindings, and a random marker
embedded in the new App description. The successful Apps create response is
captured to a mode-`0600` file; before any bundle bind, deploy adds its
immutable App object ID plus service-principal client and SCIM IDs to the signed
journal and proves the live App still has those exact IDs. If the process is
lost after create but before that claim, the next deployment queries the
server-owned `system.access.audit` `apps/createApp` event. Recovery requires one
successful event in the signed workspace/actor/time window, an exact parsed
request payload, and an audited result App ID equal to the current live App ID;
only then are the live service-principal IDs signed. Missing or delayed audit
evidence is polled and authorizes no App mutation. The App-create authorization
lasts 15 minutes and the audit settlement interval lasts one additional hour,
keeping reconciliation inside the four-hour deploy job limit. An absent App
cannot make its intent disappear until both intervals have closed. During settlement, deploy
rechecks both live App inventory and the exact audit query every 30 seconds. A
zero-row query before that boundary is delayed evidence, never negative proof;
the journal is retired only after the final post-boundary query and App
recheck are both still absent.

If the runner is interrupted after either identity-claim path but before the
first signed last-good capture, an ordinary later deployment stops and
quarantines the App, quiesces its treatment authority, removes any partial
bundle binding, and deletes it only when the live creator, marker, resources,
and all three immutable App identity fields still match that signed journal.
The bundle-binding compensation is armed before the remote bind, and an
ambiguous unbind leaves both the stopped App and signed journal intact for a
later lease to reconcile. App deletion itself is performed only by the journal
verifier; a commit-then-timeout is resolved by re-reading exact App absence. An
App without the exact journal is never inferred to be owned by the failed run;
it remains on the explicit unsigned-App rebase path. Immediately after signed
last-good capture, deploy disarms first-install deletion and advances to the
signed-green compensation state before retiring the journal. A failed or
ambiguous journal retirement therefore preserves the signed customer App and is
retried by the next deployment. The journal is cleared only after audit-bound
proof that App creation did not commit or after signed rollback state is
verified, so a runner crash cannot strand an automatically trusted unsigned
App.

An explicit unsigned-App rebase stops the App and removes all direct non-manager
`CAN_USE` before any restart. After the green candidate deploy, the script first
proves the agent-runtime identity cannot reach the App, then temporarily grants
only the release probe `CAN_USE` for authenticated release checks. Normal,
second-operator, and admin access is restored only after the treatment authority
and exact last-good contract are durably captured. A pre-capture failure stops
the App and leaves operator access withheld.

Databricks Apps v2 accepts stop, start, resource update, snapshot deployment,
and App-permission mutations only by App name; it exposes no immutable-App-ID or
conditional-version parameter for those writes. App `CAN_MANAGE` principals and
workspace admins are therefore an explicit trusted control-plane boundary: they
must not delete/recreate or mutate the target App out of band while the signed
deployment lease is held. Deploy captures the App object/client/SCIM identity
triplet immediately after acquiring that lease, supplies it to every helper
that can accept an identity, and checks it before and after name-addressed
mutations. Any observed drift aborts the release and forbids a success claim,
but an authorized manager violating this boundary is a governance incident that
client-side code cannot atomically prevent. The supported operator path is
`scripts/deploy.sh`; manual App click-ops during a deployment are unsupported.

Without `--create-group`, admin provisioning fails closed when `mip-admin` is
missing. Re-running is idempotent: existing principals, group membership, and
the verifier Lakebase OAuth role are reused. Client ID and secret sink flags
are assertions for the reserved role-owned names; custom or cross-role sink
names are rejected. The release probe is admin-capable inside the product but
receives no persistent Databricks App permission; its temporary candidate access
is converged directly by the signed-release gate and removed after capture. The
verifier is never added to `mip-admin` and receives no
Databricks App `CAN_USE` permission by default. It receives only `CAN_USE`
on the named SQL warehouse, `CAN_QUERY` on the AI Gateway endpoint, read-only
access to the exact inference tables, and verifier-only Lakebase proof-ledger
privileges. Provisioning fails closed if the verifier has a direct App grant
or if any non-admin automation identity remains in `mip-admin`. A reused
Lakebase verifier role must be reported by the SDK with
`identity_type=SERVICE_PRINCIPAL`; `USER`, other identity types, and absent type
metadata are rejected before endpoint, warehouse, or verifier grants.
The agent-runtime identity is also excluded from the App and `mip-admin`; it
gets no Lakebase role, SQL warehouse grant, borrower-table access, or verifier
authority. Deployment re-audits those exclusions on every run. It grants
persistent `USE CATALOG`, `USE SCHEMA`, and `EXECUTE` only for the three
reviewed UC functions plus direct `CAN_RUN` on the one Genie space. `CREATE
MODEL` and `CREATE TABLE` on `mip.audit` exist only while it owns/updates the
exact registered proxy model and Gateway inference table; the deploy EXIT
compensation revokes both privileges on success or failure. Databricks makes
the endpoint creator the inference-table owner, so this runtime necessarily
retains owner capabilities on that exact payload table. That does not expand
the data it can observe: the same runtime already processes those request and
response payloads in flight, and it receives no access to any other audit or
borrower table.
The agent-proxy identity is independently excluded from every App, Lakebase
instance, SQL warehouse, and direct serving endpoint ACL. It receives
`CAN_QUERY` through exactly one endpoint-bound managed group whose sole member
is the proxy, direct `CAN_RUN` on one Genie
space, and direct `USE CATALOG`, `USE SCHEMA`, and `EXECUTE` only for the three
reviewed `mip.gold` functions. A target-authenticated dual-authority audit
enumerates every visible UC catalog, schema, function, table, volume, and
registered model before cutover and again after blue retirement; inherited,
foreign, ownership, or fourth-function authority fails deployment.
The proxy's own OAuth SCIM projection must expose the exact managed group before
the query gate runs. Deployment retries only `PermissionDenied` during bounded
provider propagation; deadline exhaustion fails closed. Removing the group's
sole member atomically revokes query authority before group retirement.
Before green activation, a principal-pinned workspace-admin global audit
enumerates every admin-visible Genie space and customer-created serving
endpoint. ID-less, creator-less Databricks foundation-model endpoints are
routed to the fixed `system.ai` Unity Catalog inventory instead of being
mistaken for customer ACL securables. The audit requires runtime direct
`CAN_MANAGE` only on the exact green Supervisor/Gateway pair (plus any pinned,
runtime-owned blue endpoint during side-by-side cutover) and direct `CAN_RUN`
only on the reviewed Genie space. Query-only identities, including verifier and
agent proxy, receive `CAN_QUERY` only through an endpoint-bound managed group
whose immutable name, external ID, group ID, endpoint ID, and sole member are
re-proven. Unrelated group-derived, inherited, or broader access and access to
any unrelated resource fail the deploy. The same endpoint audits run again
after blue retirement.
Isolation provisioning likewise enumerates every visible Databricks App and
every Lakebase instance rather than checking only the named deployment.
For an existing principal whose prior client secret is unavailable or being
replaced, add `--rotate`; without it, the existing secret remains unchanged.

For local `scripts/deploy.sh`, provide the normal, admin, release-probe,
verifier, agent-runtime, and agent-proxy client credential pairs plus the
second-operator client ID and active proxy credential ID through the process
environment or `.env.local`. The script rejects reused
client IDs, mints distinct normal/admin bearers at preflight, and uses only the
release-probe bearer while an unsigned rebase remains quarantined. It remints
the active automation bearers immediately before Agent Evaluation and the final
smoke sweep. GitHub workflows use `tools/oauth_m2m_mint.py` to append
fresh values directly to `$GITHUB_ENV`; minted values are not printed.
The second-operator credential pair is intentionally required only by the
on-demand `nightly.yml` isolation gate; deployment does not impersonate that
operator.

The deploy script's live smoke now requires agentic capability proof by
default (`MIP_EXPECT_AGENTIC_CAPABILITIES=1`). The admin proof uses the fresh
admin M2M bearer; it never falls back to the normal identity or deployment PAT.
Do not disable this gate for customer-release signoff. Only override it for an
explicitly documented partial validation where agentic capabilities are out of
scope.
AI Gateway claimability is stricter than endpoint/table configuration: deploy
runs `tools/databricks/verify_ai_gateway_exact_proof.py send --wait` under the
dedicated verifier M2M identity after agentic provisioning. It first reconciles
the verifier's Lakebase role and proof-ledger grants; the runtime app retains
read-only proof-ledger access. The capability row becomes available only when
Lakebase contains a fresh `mip_app.ai_gateway_proof_ledger` row proving an exact
inference-log row for the current `MIP_GIT_SHA`. Set
`MIP_REQUIRE_AI_GATEWAY_CLAIMABLE=1` for release signoff when the deploy must
fail if that exact proof has not landed yet. Proof and inference-row timestamps
are bounded below by the request/freshness window and above by a five-minute
clock tolerance. Future-dated evidence beyond that tolerance is rejected by the
verifier, runtime lookup, and Lakebase write trigger.
Without strict mode, a proof submission or verification failure is surfaced as
a deploy warning and the App remains honestly configured/unavailable; it never
becomes claimable without the exact signed row. This permits non-release
diagnostics and browser validation while preserving the release gate.
Inference-log tables are delivered asynchronously, so deploy waits up to
`MIP_AI_GATEWAY_GRANT_TIMEOUT_S` (1200 seconds by default, bounded at 3600)
before treating their table-level grants as pending. Non-strict deploys may
continue in configured/unavailable state; the manual live-validation workflow
reconciles the app and verifier grants again before exact proof verification.
Strict deploys fail if grant convergence is incomplete.

Product requests use the MIP-owned `mip-growth-agent-gateway` Agent Model
endpoint. Its MLflow `ResponsesAgent` delegates the unchanged bounded input to
the managed Mortgage Growth Agent Supervisor. Both the managed Supervisor and
outer Gateway endpoint are created by the stable `mip-agent-runtime-ci-sp`, not
a human PAT. The logged model declares the upstream serving endpoint, exact
Genie space, and all three reviewed UC functions as non-user-delegated MLflow
resources. Those resource names and the pinned runtime requirements participate
in the reviewed source hash. The App is
granted `CAN_QUERY` only on the outer Agent Model endpoint; it does not receive a
direct grant on the managed Supervisor endpoint. The runtime separately proves
the configured Supervisor ID-to-managed-endpoint mapping, the immutable creator
of both endpoints and the Supervisor, and the outer endpoint's
`agent/v1/responses` task before labeling a response as Supervisor-generated.

Every Supervisor or proxy-contract change is blue/green and resumable,
including the first migration from a human-owned Supervisor. Deployment derives
contract-hashed Supervisor and outer-Gateway candidate names, creates and proves
those runtime-owned resources without updating live endpoints in place, grants
the App only the green outer endpoint,
and deploys an authenticated-health-verified App snapshot on the green contract
while the old Supervisor remains available. Only then does it re-read the old
agent's pinned ID, endpoint, creator, and create time, revoke the old bypass,
delete the exact old agent and any pinned orphan endpoint, delete a replaced
outer Gateway only when its journaled ID and runtime creator still match, and
rename the Supervisor replacement. The durable journal also resumes
Gateway-only cleanup after interruption. Unexpected tools, examples,
instructions, duplicate names, creator drift, model/source/inference drift, or
a changed old identity stop the cutover before the destructive step.
Each immutable proxy model version also carries an Ed25519 contract
attestation binding its source hash, Supervisor object ID, managed Supervisor
endpoint ID, runtime application ID, upstream name, Genie space, catalog functions,
experiment family, inference-table family, model name, and immutable MLflow
logged-model URI/ID (`models:/m-...`). Run, registered-version, and alias URIs
are rejected because they do not prove the same logged-model identity.
The v3 signed payload is the first governed durable schema; the unshipped v2
draft is deliberately not accepted because it did not bind the immutable
managed-endpoint ID. Unity Catalog model-version metadata stores that payload as
16 fixed, underscore-only `mip_proxy_*` fields: the 13 contract values plus its
algorithm, signature, and public verification key. Every key and value is
validated against Unity Catalog's 256-character limits before registration.
The earlier dotted single-envelope transport never produced a model version and
is rejected; dotted tags remain only on the separate Serving Endpoint API.
Registration attaches the complete signed field set atomically to the newly
created version. Retained
historical versions are verified against their own signed source contract, not
against today's proxy bytes, so a reviewed source upgrade can allocate a new
blue/green family without making earlier evidence unverifiable. The model
attestation public key is itself part of the allocation hash. A key rotation
therefore creates a distinct current-key model, experiment, inference-table
family, and Gateway endpoint while leaving signed blue resources untouched.
Retained non-candidate models may remain signed by the bounded previous key
only when their allocation suffix recomputes from that attestation record key;
the exact release candidate must use the current key. Verification is
read-only: deploy never rewrites retained model-version tags, and unsigned,
source-drifted, or wrong-epoch versions fail closed.

The deployed proxy authenticates the signed resource envelope and re-reads the
immutable Supervisor ID, creator,
description, instructions, exact four-tool set, zero-example contract, and the
live Unity Catalog metadata/body hashes for all three reviewed SQL functions
before every inference, using only the dedicated proxy OAuth identity. The
model declares no automatic private Databricks resources, so Model Serving
creator credentials are not a second caller. A post-deploy mutation therefore
fails the product request closed instead of remaining a nominally verified
Supervisor path. The deployment verifier separately re-proves the immutable
private endpoint ID and the outer Gateway/model/experiment allocation.

App activation has a separate blue/green rollback contract. Before changing an
existing App, deploy requires a server-owned
`mip-app-rollback/app-last-good-v6-mip-app` secret whose Ed25519 signature and
digest bind the full environment payload, exact health SHA, App service-
principal client and SCIM IDs, Gateway binding, succeeded deployment ID,
immutable `/Workspace/Users/.../src/...` source artifact, and the complete live
Supervisor/Gateway/model/experiment/inference-table resource proof. The v6
record scope is deterministically App-bound: names ending in `-app` append
`-rollback`, while an environment suffix after `-app-` is preserved after the
inserted `-rollback-` segment (for example,
`mip-app-pr105-staging` → `mip-app-rollback-pr105-staging`). It contains an
App/deployer ownership marker, and is re-audited before every record read,
write, or legacy-key deletion. Its direct ACL must be exactly the original
deployer plus `admins` at `MANAGE`, and its only permitted non-marker keys are
that App's v5/v6 rollback records. An unmarked pre-existing scope, foreign ACL,
or unrelated key blocks deployment without adoption or cleanup.
The only bounded adoption path is an unmarked, exact-current-deployer scope
containing solely that App's v5 key; deploy verifies the v5 Ed25519 signature,
schema, digests, and Lakebase binding before writing the ownership marker, then
the normal signed-blue verifier re-proves all live resources before any
rollback authority is exercised.
The v6
payload must also bind matching `MIP_LAKEBASE_INSTANCE` and
`LAKEBASE_INSTANCE_NAME` values to the current deployment target. An existing,
signed v5 record is accepted only through its original proxyless field set,
digest, signature algorithm, and exact live-resource verifier. The first
durable v6 capture deletes that legacy key. Older v4 records are intentionally
not rollback authority; an operator must use the explicit stopped-App rebase
path once before the next governed capture. Before any App start, endpoint ACL
grant, rollback deployment, or treatment restoration,
the rollback tool re-reads those immutable resource IDs, owners, exact endpoint
configuration, signed model envelope/source, runtime-owned experiment name/ID,
the experiment's normalized Workspace ACL (runtime `CAN_MANAGE` inherited only
from the independently resolved `/Users/<runtime-client-id>` directory object,
`admins` inherited only from the workspace root, and no other principal),
catalog, App resource target IDs, and Genie input and rejects any drift. Capture
additionally resolves the source-declared App bindings from
`databricks bundle summary` and rejects every
extra, missing, wrong-target, wrong-kind, or wrong-permission live binding
before it can become signed last-good authority. It repeats that proof after
health verification to close deployment races.

Green activation remains treatment-quiesced through hosted tool proof, exact
Gateway proof, evaluation, smoke, and authenticated health. Durable capture
first persists and verifies the signed last-good contract while treatment is
still quiesced, then restores treatment authority and repeats App health,
resource, and lease proof. Deployment acquires an atomic, signed workspace
lease under `/.mip-deployment-leases`; the root-level location avoids
`/Shared`'s inherited `users`-group management permission. Its directory ACL is
pinned to the exact workspace-admin deployer, with one direct `CAN_READ` grant
for the signed agent-runtime writer and only the inherited `admins` group also
retaining management. The writer identity is part of the signed lease record;
an unrelated identity cannot assert the lease, and the delegated writer cannot
release or replace it. Because Databricks restricts ACL reads to managers, the
deployer revalidates the exact directory ACL on every heartbeat and the
read-only runtime requires that signed manager attestation to be no more than
three heartbeat intervals old. Both identities revalidate the exact signed
lease before Lakebase Sync creation, Gateway logging, journal mutation, model
registration or cleanup, endpoint creation, App ACL convergence, Supervisor
rename, and blue-resource deletion. Its one-minute heartbeat renews the signed
fence. A losing contender reads the existing lease without changing that ACL,
and a heartbeat exits without renewal as soon as it is no longer a child of the
deployer that launched it. If process death follows the first signed generation
but precedes its ACL postflight, recovery discovery remains read-only. After the
signed lease expires (with renewal bounded by the six-hour cap), only the exact
signed holder and writer may present that durable root and contend on the one
deterministic non-overwriting successor path. Only the immutable successor
winner converges the exact new holder/writer ACL and then revalidates the
authoritative head; a losing historical holder never changes the shared ACL.
No journal, delete, or overwrite is involved, so an active, unrelated, or
changed fence cannot be replaced. The
lease UUID is injected into the App payload and authenticated
health response, and capture revalidates the live signed lease before and after
activation. Capture records the
`green_treatment_pending_capture` compensation state and advances to
`green_verified` only after the signed secret read-after-write matches and blue
cleanup/global ACL postflights finish. Normal
shell interruption in that window stops/quiesces green and restores signed blue
while still quiesced; every subsequent deploy also quiesces before trusting a
saved candidate. `--skip-smoke`, a missing smoke script, or
`ALLOW_SMOKE_FAILURE=1` never certifies green. With signed blue, rollback proves
blue while quiesced and restores authority only after exact blue health; on a
first install it leaves the unproven App stopped and quiesced and exits nonzero.

An existing legacy installation without this signed record cannot truthfully be
treated as exact blue state because Databricks redacts historical environment
values. Its one-time migration must set `MIP_REBASE_UNVERIFIED_APP=1`; deploy
first proves no last-good record already exists, then identity-pins and stops
that unverified App and uses first-install fail-closed compensation until the
rebased App passes the hosted-tool gate and is captured as the first signed
last-good contract. Remove the flag only after the first successful signed
capture; a failed attempt that remains unsigned requires another explicit
recovery invocation. Once a record exists, the exceptional path refuses to run
even if the flag is mistakenly selected. Routine CI must never set it. New
installations capture their first record automatically.
`MIP_APP_ROLLBACK_SECRET_SCOPE` is a reviewed non-secret setting, but its value
must match that deterministic transformation; arbitrary shared scopes are
rejected before workspace mutation.

Databricks Agent Model endpoints currently support AI Gateway payload logging,
but not Gateway rate limiting or usage tracking. The provisioner therefore
creates each immutable endpoint with its own contract-hashed inference-table
family under `<catalog>.audit.mip_agent_gateway_growth_agent_<resource-hash-12>`.
The resource hash binds source, model family, experiment family, schema, table
family, and the model-attestation public-key epoch. Databricks
does not support pointing two endpoint creations at an existing inference
table, so blue and green never reuse a table prefix. Historical table families
are retained for audit; App and verifier grants converge to the exact current
family. Request budgets remain enforced by the application's authenticated
backpressure middleware. Do not add unsupported Gateway rate-limit fields to
this Agent endpoint or describe them as live proof.

Interrupted model registration uses an append-only MLflow experiment journal.
Databricks exposes experiment-tag set/update but no delete operation, so the
runtime writes a SHA-256-addressed journal tag and later appends an exact
digest-bound retirement tag. The reader rejects malformed reserved tags,
orphans, multiple journals, reuse after retirement, and any journal whose
canonical JSON, signed model contract, model source, run, or experiment binding
does not verify—even when a matching retirement tag exists. Journal writes,
retirement, registration cleanup, and final endpoint creation are all fenced by
the signed deployment lease above.

Exact-row proof also requires an Ed25519 attestation from the verifier. Store
`MIP_AI_GATEWAY_PROOF_SIGNING_KEY` only in deploy/verifier automation. It signs
exact-row, App rollback, and cutover-journal evidence, but never model artifacts. The deploy
script derives `MIP_AI_GATEWAY_PROOF_VERIFY_KEY` and injects only that public key
into the App. Lakebase proof writers therefore cannot make AI Gateway claimable
by inserting or editing a proof row; the runtime verifies the signature over
the deployment SHA, request id, endpoint, inference table, and timestamps.
Rotate the key by setting the new signing key and temporarily setting
`MIP_AI_GATEWAY_PROOF_PREVIOUS_VERIFY_KEY` to the old public key. Early deploy
reconciliation verifies the signed last-good record with that bounded previous
key and immediately re-signs it with the new current key; the promoted App then
receives only the new public key and generates a fresh exact-row proof. Remove
the previous-key setting after that successful deploy, but first append its
public key oldest-first to `MIP_AI_GATEWAY_PROOF_HISTORICAL_VERIFY_KEYS` so the
append-only lease and first-install journal chains remain readable. Historical
keys are accepted only for those chain-verification paths; exact-row promotion
does not accept them, and proof rows signed by the prior key then fail closed.

Proxy-model provenance uses an independent Ed25519 authority:
`MIP_GATEWAY_MODEL_ATTESTATION_SIGNING_KEY`. The deploy preflight rejects a
model key whose derived public key equals the proof key. Only the bounded
runtime-owner registration command receives the model private key; exact
resource exporters and the deployed model receive
`MIP_GATEWAY_MODEL_ATTESTATION_VERIFY_KEY` instead. Rotate model attestations
with `MIP_GATEWAY_MODEL_ATTESTATION_PREVIOUS_VERIFY_KEY`, independently of the
proof/rollback rotation. The new current public key allocates a separate green
resource family; retained historical versions and blue endpoints are never
re-signed or updated in place. The ordinary UC postflight is public-key-only
and accepts the previous key only for a non-candidate model whose name suffix
is bound to that exact record key. Keep the previous public key configured
while any governed retained version still depends on it, or retire that
historical family through the reviewed retention process before removing it.

The App service principal does not receive direct Supervisor, registered-model,
or MLflow-experiment access. App-side capability and generation checks
authenticate the canonical signed resource envelope and re-read only the outer
Gateway endpoint the App is authorized to query. The runtime-owned served proxy
re-proves the private Supervisor definition, registered model/version,
experiment owner/ACL, and UC functions before every inference. This separation
keeps the product path functional without widening the App around the governed
Gateway boundary.

This attestation proves what the separated verifier observed at verification
time; it is not a tamper-independent audit of the inference-table owner.
Databricks makes the endpoint creator (`mip-agent-runtime-ci-sp`) the owner of
the endpoint's inference tables, so that identity can modify those tables. The
claimable row therefore means “a verifier-credentialed process observed and
signed a timely exact delivery row after a tool-bearing live response,” not
“the runtime owner was cryptographically unable to alter the source table.”
Customers requiring owner-independent retention must export Gateway logs to a
separately governed immutable sink; Module 0 does not claim that stronger
control.

`MIP_COTALITY_ID_MASK_SECRET` and `MIP_GENIE_ACTION_SECRET_CURRENT` are
mandatory for sandbox, staging, customer, and production app payloads. Only
`APP_ENV=local` and `APP_ENV=test` may use compatibility keys. During action-key
rotation, deploy the new current key/KID together with both prior-key values.
The prior secret without `MIP_GENIE_ACTION_SECRET_PREVIOUS_KID` is rejected.
When the prior secret is absent, it is not injected into the App payload. The
bundle's static optional-resource declaration still requires the backing
Databricks Secret key to exist, so the provisioner replaces it with a random
disabled sentinel that the runtime cannot read through an environment binding.
Keep the real prior key available for at least two hours so
existing action confirmations and one-hour generated-copy/cohort provenance
tokens can expire. The previous key is verification-only; new tokens always
use the current key.

After that bounded grace period, remove
`MIP_GENIE_ACTION_SECRET_PREVIOUS` and
`MIP_GENIE_ACTION_SECRET_PREVIOUS_KID` from the deployment environment and
`.env.local`, retire the Databricks secret, and deploy the no-previous-key
payload:

```bash
unset MIP_GENIE_ACTION_SECRET_PREVIOUS
unset MIP_GENIE_ACTION_SECRET_PREVIOUS_KID
python tools/databricks/provision_runtime_secrets.py \
  --scope "${MIP_RUNTIME_SECRET_SCOPE:-mip-runtime}" \
  --retire-previous
./scripts/deploy.sh -t dev
```

Retirement is idempotent and does not require the current or Cotality secret
values. It replaces `genie-action-previous` with a fresh disabled sentinel,
which immediately invalidates old tokens while keeping the static Databricks
App resource valid. An ordinary deploy with no configured previous key does
the same before emitting a payload without the previous environment binding.

`make bundle-validate` and `make bundle-plan` remain read-only diagnostics.
The mutable `make bundle-deploy` and `make bundle-deploy-dev` aliases are
retired and fail closed. The internal bundle wrapper accepts mutation only
with exact non-App selectors supplied by `scripts/deploy.sh`; it rejects
unrestricted or App selectors. Always rerun `./scripts/deploy.sh` for recovery
so complete resource bindings, durable secrets, the signed source contract,
refreshes, and smoke gates are preserved. A direct Apps promotion bypasses the
required full deployment payload and is unsupported.

For disaster-recovery rollback paths, including Lakebase point-in-time
restore, prior app snapshots, source-regression rollback via
`git checkout <prior-good-sha>`, Genie space re-provisioning, audit archival,
and governed-action HMAC key rotation, use
[`docs/disaster-recovery.md`](disaster-recovery.md).

## Resources

Databricks App resources expected by `app.yaml`:

- `sql_warehouse`: SQL warehouse resource, `CAN_USE`.
- `genie_space`: Genie space resource, `CAN_RUN`.
- `database`: Lakebase database resource, `CAN_CONNECT_AND_CREATE` for binding
  compatibility; `mip_lakebase_migrate` immediately revokes effective
  database `CREATE` and fails unless postflight proves connect-only access plus
  the exact reviewed `mip_app` table/sequence matrix in the internal security
  grant runbook.

## Optional Salesforce activation

Salesforce delivery is disabled unless the full connector is configured. In
addition to the OAuth values, the customer must create a unique External ID
text field on the configured sObject and set
`SALESFORCE_EXTERNAL_ID_FIELD` (for example `MIP_Activation_Id__c`). MIP uses
the immutable activation UUID as that field's value and performs a Salesforce
External-ID `PATCH` upsert; ordinary non-idempotent `POST` delivery is not
used. This makes exact request retries and recovery after an ambiguous local
commit reuse one remote record.

The dev deploy workflow accepts the non-secret connector values as GitHub
environment variables and the client secret, integration-user password, and
optional security token as GitHub environment secrets. `deploy.sh` writes
those secret values directly to the `mip-runtime` Databricks secret scope and
the App payload uses `value_from` bindings. A partial connector remains
honestly staged and cannot claim delivery.

## Optional OTLP log export

The shipped default is stdout-safe JSON only. To prove durable
off-platform logs, configure the collector endpoint before promoting the
snapshot, plus headers when the collector requires auth:

```bash
MIP_OTEL_ENDPOINT=https://<otlp-collector>/v1/logs
# MIP_OTEL_HEADERS is stored in Databricks Secrets and referenced by
# the Databricks App resource named otel_headers. Do not paste it into
# app.yaml, bundle JSON, screenshots, or shell history.
```

The endpoint must be a customer-owned collector reachable from the
Databricks App container. Without an endpoint, `/api/v1/admin/health` should honestly
report `"log_export": "stdout-only"`. With them configured and the
OpenTelemetry exporter wheels present in `requirements.txt`, it should
report `"log_export": "otlp"`; then verify the collector has a fresh
deployed app `correlation_id`.

Current Entrada dev status (2026-05-15): external OTLP is not configured
on `mip-app`. CLI/API evidence shows active deployment
`01f14ff2c4cd10b99ebad8f8785c307f` is `SUCCEEDED`, the app is `RUNNING`,
and only the warehouse, Genie, Lakebase, and lifecycle job resources are
attached; no app secret resource is attached for collector headers.
`/api/v1/admin/health` returns
`log_export: stdout-only`. The Databricks CLI `apps deploy` command in
use here (`0.299.1`) has no top-level `--env` flag, but the underlying
API accepts deployment `env_vars` via `--json`. That list replaces the
app.yaml env list. The historical proof used that API directly; current
operators must use `scripts/deploy.sh`, which attaches the `otel_headers` App
secret resource and emits the full runtime contract plus
`value_from: otel_headers`. Do not paste collector tokens into `app.yaml`, a
bundle var, process environment, or shell history.

App-side proof was completed on 2026-05-14 with temporary deployment
`01f14fe0164a1b6388f9d240679492db`: `/api/v1/admin/health` reported
`log_export: otlp`, a sanitized RUM request returned correlation id
`b07b2f0825a043188fda96e041a14d19`, and the external collector received
the matching OTLP HTTP protobuf payload. The sandbox was then restored to
stdout-only so it is not left exporting logs to the temporary collector.

Secret-backed header proof was completed on 2026-05-14/15 with temporary
deployment `01f14ff1cb5513a9824b1e040701141d`: `MIP_OTEL_HEADERS` was
resolved through Databricks App resource `value_from: otel_headers`,
backed by a temporary non-sensitive `mip/otel-headers` secret. The
external collector received request `d64260ce-232a-498e-bb9a-4bf65306c090`
with proof header `x-mip-otel-proof=proof-20260515T000246Z` and a
matching `/api/v1/telemetry/rum` log body for correlation id
`c864cd1bbbf44779874fdb235ae7c6bf`. The sandbox was restored to
deployment `01f14ff2c4cd10b99ebad8f8785c307f` with no OTLP env vars, the
temporary app secret resource removed, and the temporary Databricks
Secret deleted.

For customer production, store the collector headers in a dedicated Databricks
Secret, then set `MIP_OTEL_ENDPOINT`, `MIP_OTEL_HEADERS_SECRET_SCOPE`, and
`MIP_OTEL_HEADERS_SECRET_KEY` for the ordinary governed `prod` deployment.
`scripts/deploy.sh` adds the `otel_headers` resource to the existing App target
and overlays `MIP_OTEL_ENDPOINT` plus `value_from: otel_headers` onto the same
complete signed payload used by every release. It rejects partial settings,
plaintext `MIP_OTEL_HEADERS`, and credential-bearing endpoints before mutation.
Do not add collector values to `app.yaml` or deploy the App separately.

All MIP refresh and automation schedules deploy paused by default, including
`prod`. The app's Admin **Data operations** panel is the
normal customer-facing refresh surface. The Growth Agent monitor scheduler is
also paused by default; when an operator deliberately runs or unpauses it, it
only refreshes saved reviewed watchlists and creates Slack/Teams review drafts.
It does not send messages or activate connectors. Only unpause FRED,
lifecycle, or Growth Agent schedules after the customer approves a recurring
cadence and confirms the target writes to an isolated catalog; otherwise
multiple bundle targets can contend on the same Unity Catalog tables and burn
avoidable compute.

Adding OTLP to an existing App uses the same target that already owns its
Lakebase and warehouse resources. Re-run `scripts/deploy.sh -t prod`; its
source-free resource reconciliation preserves the active signed deployment,
adds `otel_headers`, and only then runs the normal migration, cutover, signed
rollback, and smoke gates.

## Release checklist

The app runs on live Unity Catalog + Lakebase in every environment — there is no mock-mode runtime toggle (see [CLAUDE.md](../CLAUDE.md) "Negative prompting"). Flakiness is handled by the resilience layer (retry, warehouse warm-start, SWR cache, circuit breaker, degraded-state banner), never by silent mock fallback.

- Warehouse ID + Genie space ID are set (`BUNDLE_VAR_sql_warehouse_id`, `BUNDLE_VAR_genie_space_id`).
- App name, Lakebase instance/catalog/database, Genie title, and runtime/rollback
  secret scopes are unique to the intended workspace; the configured bundle
  host and `DATABRICKS_HOST` identify that same workspace.
- Lakebase schema + `mip_app.action_audit` table exist.
- Genie space is curated against `mip.semantics.*` metric views only.
- `/api/v1/health` returns `status: ok`; `/api/v1/admin/health` reports
  `warehouse: up`, `genie: up`, `lakebase: up`, all circuits `closed`,
  and the current `log_export` posture.
- Resilience is observable: degraded banner renders when a dependency drops; Approve writes a real row to `mip_app.action_audit`.
- Live outreach smoke persists an email draft, then approves that exact draft
  with its generation ID, response hash, source-freshness timestamp, subject,
  body, and canonical borrower evidence IDs.
- `./scripts/deploy.sh --verify-source-only` passes for the exact committed SHA.
- `tools/databricks/bundle_env.py validate -t dev` passes.
- `tools/databricks/bundle_env.py plan -t dev` shows the expected direct
  deployment changes; a first isolated-workspace plan has zero changes and
  zero deletions.
- Optional production observability gate: if external log durability is
  required, `MIP_OTEL_ENDPOINT` and any required `MIP_OTEL_HEADERS` are
  configured, `/api/v1/admin/health` reports `log_export: otlp`, and the
  collector has a fresh deployed-app log line. Validate the customer
  evidence packet with
  `python tools/databricks/otlp_customer_retention_gate.py <evidence.json>`
  before claiming durable customer retention. If any customer-owned
  collector, real secret reference, retention/ACL proof, or fresh
  collector query proof input is missing, record the gate as bounded
  stdout-only or transport-only instead of treating local mocked OTLP
  tests as external collector proof.
- Frontend build passes (`npm --prefix frontend run build`).
- Python tests pass (`pytest -q`).
- Talk track rehearsed.
