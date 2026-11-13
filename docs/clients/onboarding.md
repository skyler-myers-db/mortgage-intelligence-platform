# Client onboarding: from nothing to a working App

A new client (a lender install in its own Databricks workspace, possibly in
another account) is onboarded by configuration only: no repository edit, no
client value in the tree. Everything client-specific lives in two places
outside git, the operator's profile and the client's GitHub Environments.
This page is the order of operations; each step links the reference that
documents its tool in full ([docs/deployment.md](../deployment.md),
[bootstrap.md](bootstrap.md), [github-environment.md](github-environment.md)).

An install that already exists (deployed by hand, outside the client target)
is not onboarded from nothing: it is **adopted**. Client #1 is that case, and
[first-client-cutover.md](first-client-cutover.md) is its runbook.

## Status: what runs today

| Stage | State on `main` |
|---|---|
| Profile, bootstrap, GitHub Environments | Built, and tested against fakes only (their guides record no live run yet). |
| `phase=plan` dispatch | Built, CI only. A new client's deployer has no bundle state yet, so its live plan and release take their SQL warehouse from the profile's `MIP_BOOTSTRAP_WAREHOUSE_ID` (B1, section 4), checked read-only before anything else reads it, until the first release's bundle apply creates the bundle's own; after that, from the deployer's bundle state. Without the field, a live plan or release whose state binds no warehouse refuses `this deployer's bundle state binds no SQL warehouse yet (a first install): set the client profile's MIP_BOOTSTRAP_WAREHOUSE_ID ...` (exit 2, nothing changed). `phase=adopt` and `phase=unadopt` read no warehouse at all: they run no SQL (#344). An existing install that is not adopted yet refuses earlier, at the live checks (section 5). |
| **First install through `deploy-dev.yml`** | **No longer blocked by the warehouse** (B1 is fixed: the profile's `MIP_BOOTSTRAP_WAREHOUSE_ID`, section 4). Still open: the first-install phase order: the release's gold-dependent tail (Genie trusted assets, the smoke) reads gold that only a later data phase writes (docs/deployment.md, "Data phase on a first install"). The metastore's owner is read from its summary, which a non-admin receives (spike S0(b); the live checks' check 4b measures it for each client). A deployer that is not a metastore admin cannot read the App's metastore privileges (spike S0(b′)). The first release stops with exit 3 for the owner bootstrap and keeps the App (section 5); the metastore owner then attests the kept App as well (`metastore_app_grants`, docs/deployment.md "Metastore authority"), and the resumed release carries that ack, without which it refuses `metastore_authority` before the lease. |
| Adopting an existing install through `deploy-dev.yml` | **No known code refusal is left on its path**, the adopt dispatches and the adopted install's first release included; it has not run live yet. S0(b) is fixed in code and measured by plan #1; a deployer that is not a metastore admin needs the metastore owner's `metastore_app_grants` attestation on every adopt, plan, unadopt and release run (S0(b′)) ([first-client-cutover.md](first-client-cutover.md), "Blockers"). `operation_acks` now carries adopt-live's acks and the supersede selector (B2, fixed) and the Genie pin and accepted-exposure acks in the adopt phases (B4(a) and B4(b), fixed); while no signed record pins the Genie space, the adopt phases' bundle plan, binds, unbinds and summary bind the one title match whose pin plan that exact `genie_pin` names (B4(c), fixed). The warehouse refusal (B1) no longer applies: the adopt phases read no warehouse, and the plan and release after the bind take the adopted one from the deployer's bundle state. The deploy job's shallow checkout (B5) is fixed: the adopt phase checks out main's full history, so adopt-live resolves a live health sha that `main` holds, and still refuses one it does not (`health_sha_not_on_main`; the remedy is an emergency ship of a `main` commit). The adopted install's first release carries the `restore_unverified_frontend` ack its plan prints (B3, fixed: CI adopts with an `unverified` frontend), and a plan before the adoption reports blue's env-name parity, the gate before the ownership handover: only `holds` passes it, and a newest deployment whose env names hold `MIP_APP_DEPLOYMENT_LEASE_ID` (`LEASE-BOUND`) stops there, because its CI adoption could not be released. |
| Data phase, per-client nightly | Built. The nightly validates only a client whose catalog is `mip`. |
| Owner hardware-key attestation (plan M1-PR5) | **Deferred.** Not built. See "Deferred" below. |

The second-client portability proof (`tests/unit/test_client_portability_proof.py`,
docs/deployment.md "Second-client portability proof") proves the plan and
release **dry runs** of a synthetic client need zero code edits. A dry run
reads no bundle state and never checks the bootstrap warehouse; a fresh
client's live `--phase release` on it runs offline in
`tests/unit/test_bootstrap_warehouse_release_sandbox.py`.

## Placeholders, and where the real values live

| Placeholder | Meaning | Real value lives in |
|---|---|---|
| `<client>` | the client key, `MIP_CLIENT` (2-31 lowercase letters, digits or `-`, from a letter); also the GitHub Environment's name and the federation subject | the operator's profile and the environment name |
| `<prefix>` | `MIP_IDENTITY_PREFIX` (default `mip`) | the operator's profile |
| `<workspace-host>` | `DATABRICKS_HOST` | the operator's profile; the environment secret |
| `<app-name>` | `MIP_APP_NAME` | the operator's profile |
| `<catalog>` | `MIP_DEFAULT_CATALOG`; must be `mip` today | the operator's profile |
| `<owner>/<repo>` | this GitHub repository | `git remote` |
| `<sha256>`, `<profile-ack>` | a digest a read-only plan printed | that plan's output |

**The operator's profile** is the gitignored `.env.local` in the operator's
checkout (or one file named with `--env-file <path> --env-file-only`), plus
the operator's own Databricks CLI profiles. In CI the client's GitHub
Environment `<client>` holds the same profile: public fields as variables,
identifying and secret fields as secrets. Never commit a value, put one in a
command line that a log keeps, or paste one into an issue or PR:
`tests/unit/test_no_client_identifiers_committed.py` holds the tree at zero,
and every mutating dispatch scans the tree for the resolved identifying values
(`tree_scan`).

## 1. The client profile

The schema is `tools/clients/schema.py` (version 1). The template is
[`deploy/clients/example.env`](../../deploy/clients/example.env): every client
value is a `<placeholder>`, grouped by class (`FIELD_CLASS`):

| Class | CI location | Local location | In logs |
|---|---|---|---|
| public | environment variable | `.env.local` | may print |
| identifying | environment secret (masked by the runner) | `.env.local` | never |
| secret | environment secret | `.env.local` | never |

Copy the fields into `.env.local`, fill them, and check:

```bash
python -m tools.clients check --env-file-only --env-file deploy/clients/example.env --allow-placeholders
python -m tools.clients check
python -m tools.clients digest
```

`check` prints the client key, install class, tier and profile digest, or
refuses (exit 2) naming fields and rules, never values. The rules that most
often refuse a first profile:

- `MIP_DEFAULT_CATALOG` must be `mip`: the SQL still names the catalog
  literally, so a rename is refused until the catalog-label slice lands
  ([runbook-multi-catalog.md](../runbook-multi-catalog.md)).
- `MIP_REVIEW_MODE` must be `single_maintainer_audited`
  ([review-modes.md](review-modes.md)).
- `MIP_IDENTITY_IDS_JSON` may be absent or partial until the bootstrap has
  run; the bootstrap pins each identity into it.
- A customer install (`MIP_INSTALL_CLASS=customer`) needs the lender record
  `MIP_LENDER_IDENTITY_JSON` and its acknowledged digest
  `MIP_LENDER_IDENTITY_SHA256` (docs/deployment.md, "Customer lender
  identity"); `MIP_SEED_OPERATOR_EMAIL` is for demo installs only.

Start a new client in the core tier (`MIP_AGENTIC_TIER=off`) with the weekly
refresh off (`MIP_SCHEDULED_REFRESH=off`). The profile also takes
`MIP_SALESFORCE_BINDINGS` (`off` when unset): leave it
unset unless the client's App binds the three Salesforce connector secrets
(docs/deployment.md, "Optional Salesforce activation").

## 2. Account bootstrap (human-run)

The client's account admin runs this once, by hand, from a terminal: the
mutating actions refuse under CI or without a terminal, and every run logs in
fresh (U2M, in the browser) into a throwaway config that never reads
`~/.databrickscfg`. It is a plan, review, ack and apply loop: nothing changes
until you hand the tool the exact digest of a plan you read. The full guide,
including what it never does, is [bootstrap.md](bootstrap.md).

The order ([github-environment.md](github-environment.md), "Onboarding
order"): the environments' skeleton first, because the credential sink
refuses unless `<client>` already carries the review mode's protections.

```bash
python -m tools.clients.github_environment --plan
python -m tools.clients.github_environment --apply --ack-plan <sha256>
python -m tools.clients.bootstrap_account --plan
python -m tools.clients.bootstrap_account --apply --sink github-environment --ack-plan <sha256>
```

Before the next bootstrap run, pin by hand: copy the application id of each
identity the apply created or adopted, from the account console, into
`MIP_IDENTITY_IDS_JSON` in `.env.local`. The sink writes the map to an
environment secret, which GitHub never returns, and the tool prints no id; the
next run reads its pins from the shell or `.env.local` only and refuses an
identity under an expected name that nothing pins ([bootstrap.md](bootstrap.md)).
The data-owners group needs no pin: the bootstrap proves it by its deployer
and jobs members.

```bash
python -m tools.clients.bootstrap_account --plan --for rotate-adopted
python -m tools.clients.bootstrap_account --rotate-adopted --sink github-environment --ack-plan <sha256>
python -m tools.clients.bootstrap_account --verify
```

What it creates or adopts, each pinned by application id into
`MIP_IDENTITY_IDS_JSON`: `<prefix>-deployer` (no secret; one federation
policy, subject `repo:<owner>/<repo>:environment:<client>`, set before its
workspace `ADMIN` assignment), `<prefix>-jobs`, the `<prefix>-data-owners`
group, `<prefix>-validator` (no secret; subject `...:environment:<client>-validate`),
the reserved M2M identities and the account principal (Service Principal
Manager on the reserved identities only). An identity that already exists
under an expected name is refused until you check where it came from and name
it with `--adopt <role>=<application id>` on both the plan and the apply.
`--tier` (default `core`) must agree with `MIP_AGENTIC_TIER`.

Two things the bootstrap never does: grant the deployer `CREATE_CATALOG` on
the metastore (a first install needs it; it is a metastore-wide grant, so it
is an owner step), and transfer metastore or object ownership. Never transfer
metastore ownership for an install; nothing in this flow needs it.

## 3. GitHub Environments

| Environment | Used by | Filled by |
|---|---|---|
| `<client>` | `deploy-dev.yml`'s deploy job, and `nightly.yml`'s browser job | `github_environment` (variables, identifying secrets, signing keys, `MIP_CLIENT_PROFILE_SHA256`), the bootstrap's sink (`MIP_IDENTITY_IDS_JSON`, minted M2M secrets) |
| `<client>-validate` | `nightly.yml`'s validator jobs | the tool creates it with its protections; its five names are filled by hand |

With the whole profile in `.env.local` (`MIP_IDENTITY_IDS_JSON` complete),
run `github_environment` once more to write the profile's values:

```bash
python -m tools.clients.github_environment --plan
python -m tools.clients.github_environment --apply --ack-plan <sha256>
```

If the plan refuses and asks for `--rotate` (an identifying value changed since
the environment last recorded the digest), pass `--rotate` to both the plan and
the apply. Then fill `<client>-validate` with the same values `<client>`
holds. `gh secret set` reads the value from the terminal, so it never lands in
shell history:

```bash
gh variable set MIP_CLIENT --env <client>-validate --body <client>
gh variable set MIP_APP_NAME --env <client>-validate --body <app-name>
gh variable set MIP_DEFAULT_CATALOG --env <client>-validate --body mip
gh secret set DATABRICKS_HOST --env <client>-validate
gh secret set MIP_IDENTITY_IDS_JSON --env <client>-validate
```

It holds no M2M secret. Finally, delete every repository- or
organization-level variable or secret that carries a profile field or alias
name: `check-dispatch` in both workflows refuses every dispatch while one
exists, and names each one (docs/deployment.md, CI deploy owner step 3).

## 4. Plan (CI only)

**A new client's bootstrap warehouse (B1).** Until its deployer's bundle
state binds the bundle's own SQL warehouse (the first release's step-4 apply
creates it), a live plan and release run their early SQL on the warehouse
the profile names in `MIP_BOOTSTRAP_WAREHOUSE_ID`: the id (16 lowercase hex
characters) of a pro or serverless warehouse the deployer can use, for
example the workspace's serverless starter warehouse. Put it in `.env.local`
and run `github_environment --plan`, then `--apply --ack-plan <sha256>`
(section 3): the field is identifying, so it is stored as a `<client>`
environment secret, and it changes the profile ack. The preflight checks it
read-only (the deploy's own `bundle_state bootstrap-warehouse-id` step, no
owner command: no bundle state binds a warehouse or the App, no App of the
profile's name exists, and the warehouse is that id, not deleted and `PRO`)
and the log
says `sql warehouse: first-install-bootstrap`. The check proves the deployer
can read the warehouse, not that it holds `CAN_USE`: a warehouse it cannot
use fails at step 0a, after the lease, with no App created. After the first
release the field is not read (`bootstrap warehouse: not read`); deleting it
(`gh secret delete --env <client> MIP_BOOTSTRAP_WAREHOUSE_ID` and the
`.env.local` line) is optional and changes the profile ack once. Never set it
for an adopted install: the profile refuses it beside
`MIP_LEGACY_BUNDLE_ROOT`. Without it, a live plan refuses at the warehouse
preflight (exit 2, nothing changed). A `phase=data` dispatch before the first
release refuses there with or without the field: the data phase runs on the
bundle's own warehouse only, which the first release creates, so the release
comes first. The local dry run never reads it.

The command of record is the governed dispatch, one client and one phase per
run:

```bash
gh workflow run deploy-dev.yml --ref main -f client=<client> -f phase=plan -f acknowledge_single_maintainer_break_glass=true
```

Approve the pending deployment in the run (the environment's required
reviewer), then read the step summary: the dispatch preflight's facts
(including `owner_attestation=<profile-ack>`, the profile ack every mutating
phase needs, and the environment-protection result), the PlanReport's public
summary (refusal codes, counts, digests), the active deployment's sha256 and
each phase's predicted budget. The full PlanReport goes create-only to
`<deployer root>/plan-reports/<run id>-<attempt>.json` in the client
workspace. `plan` changes no governed resource.

A live phase is CI-only: `scripts/deploy.sh` refuses a `--phase` without
`--dry-run` outside GitHub Actions ("is CI-only; use --dry-run or make
bundle-validate"), because the deployer has no secret a person could use.
Locally:

```bash
./scripts/deploy.sh -t client --phase plan --dry-run
make bundle-validate
```

A plan that predicts a quiesce window (an existing signed blue) refuses
`window_unacked` and prints the window's sha256; dispatch the plan again with
`-f operation_acks="quiesce_window=<sha256>"` and it completes (read-only: its
budget admission is a forecast for a release dispatched now, and a plan opens
no window). The release then carries the same ack.

If GitHub cannot show the job token the environment's protection rules
(spike S8(a) is owed), the plan records `environment_protections_unreadable`
and every mutating phase then needs `-f protections_unverified_ack=<sha256>`,
the exact ack the refusal prints for that client, environment and commit.

## 5. Adopt, or install for the first time

- **The App `MIP_APP_NAME` names already exists** in the workspace and the
  deployer's bundle state does not bind it: every plan refuses `adoption:
  existing install not adopted` (and, for its catalog, `install marker: ...
  adopt it first`). Adopt it (`phase=adopt`, docs/deployment.md
  "Adopting an existing install" and "Signing the live deployment as an
  adopted blue"), in the order [first-client-cutover.md](first-client-cutover.md)
  gives, after both ownership handovers. Before those handovers, the owner
  runs the read-only adopt preflight (`tools.databricks.adopt_preflight`;
  first-client-cutover.md, step 6b), which predicts the bind plan's name and
  existence refusals from `databricks.yml` and the profile. Never set
  `rebase_unverified_app=true` on an install you mean to adopt: that one-time
  rebase stops the existing App.
- **Nothing exists yet**: the first `phase=release` is the first install. The
  warehouse preflight no longer stops it through CI (section 4: the profile's
  `MIP_BOOTSTRAP_WAREHOUSE_ID`); the first-install phase order is still open
  (Status, above). It creates the App
  stopped and source-free, then stops with **exit 3** and `awaiting owner
  bootstrap --post-first-install`, because the account principal does not yet
  manage the new App's own service principal. The App is kept. The account
  admin then runs:

```bash
python -m tools.clients.bootstrap_account --plan --for post-first-install
python -m tools.clients.bootstrap_account --post-first-install --ack-plan <sha256>
```

  and a second `phase=release` dispatch resumes at the same point
  (docs/deployment.md, "First install: awaiting owner bootstrap"). When the
  deployer is not a metastore admin, the metastore owner also attests the kept
  App now (`python -m tools.databricks.metastore_app_grants --app-name
  <app-name>`) and the resumed release carries `metastore_app_grants=<sha256>`
  (section 6).

## 6. First release

```bash
gh workflow run deploy-dev.yml --ref main -f client=<client> -f phase=release -f owner_attestation=<profile-ack> -f acknowledge_single_maintainer_break_glass=true
```

Add, only when the plan printed them:

- `-f operation_acks="quiesce_window=<sha256>"`: the window the plan
  predicted (WR1-WR4), with its reasons and length in the private plan;
- `genie_pin=<sha256>` in the same `operation_acks` input: an unpinned Genie
  space with the governed title (refused, exit 10, until acknowledged);
- `database_instance_update=<sha256>`, `accepted_exposure_plan=<sha256>`: an
  acknowledged Lakebase instance change, a non-empty accepted exposure;
- `metastore_app_grants=<sha256>`: when the deployer is not a metastore admin,
  the metastore owner's attestation of the App's metastore privileges
  (`python -m tools.databricks.metastore_app_grants --app-name <app-name>`,
  run read-only as the metastore's owner; docs/deployment.md, "Metastore
  authority"). Every plan, adopt, unadopt and release run of an existing App
  reads it. A first install's first release creates the App and stops with
  exit 3 before reading it (section 5); the owner attests the kept App
  alongside `--post-first-install`, and the resumed release carries the ack
  (without it, it refuses `metastore_authority` before the lease);
- `-f expected_active_deployment_sha256=<sha256>`: the active deployment the
  plan printed (release only), so the run refuses before its lease if the App
  moved.

A release keeps the signed last-good App (blue) serving. It stops the App only
inside one bounded window the plan predicted and the job budget admits, with a
runner-independent dead-man armed, then activates green in one rollover and
writes the signed capture (docs/deployment.md, "Keep blue serving" and "The job
budget and the dead-man"). A refusal before the lease exits 2 with nothing
changed; one after it leaves blue serving or restores it.

## 7. Data

```bash
gh workflow run deploy-dev.yml --ref main -f client=<client> -f phase=data -f owner_attestation=<profile-ack> -f acknowledge_single_maintainer_break_glass=true
```

Silver (unless `skip_silver`), then `mip_scheduled_refresh`, each gold-touching
job followed by the reviewed-function grant reconcile, then the data smoke. It
never writes the App. A job still running at the deadline exits 4: dispatch
`phase=data` again and it re-attaches. A stale or rerun ledger refuses with the
digest of its reset or rerun plan: pass it as `data_ledger_supersede=<sha256>`
or `data_ledger_rerun=<sha256>` in `operation_acks` (docs/deployment.md,
"Governed run phases").

## 8. Validation: the per-client nightly

`nightly.yml` validates one client per manual dispatch under the client's
non-admin `<prefix>-validator` (docs/deployment.md, "Live validation per
client"). Before its first dispatch the owner:

1. checks the validator exists (step 2 created it: no secret, a `USER`
   assignment, never in `admins` or the data-owners group);
2. fills `<client>-validate` (step 3);
3. grants the validator read-only access, nothing more: `CAN_USE` on the App
   and its SQL warehouse; `CAN_RUN` on the governed Genie space; `USE CATALOG`,
   `USE SCHEMA` and `SELECT` on `gold`, the metric views, `silver`, `raw` and
   `audit`, and on the raw Cotality source through its owner; `EXECUTE` on
   `fn_in_the_money`, `fn_lead_score`, `fn_next_best_offer`, `fn_rate_spread`,
   `fn_estimated_upb` and `fn_bounded_mortgage_rate`. Never `MODIFY`,
   `MANAGE`, `ALL PRIVILEGES` or `CAN_MANAGE`;
4. deletes the repository-level secrets and variables the retired parked
   nightly read (step 3's last paragraph).

```bash
gh workflow run nightly.yml --ref main -f client=<client>
python -m tools.live_validation_gate --release-sha <sha> --client <client>
```

The gate accepts a release only on a green run on `main` whose run-name names
that client and its own commit, which certifies runtime-equivalent code and is
at most 14 days old. Known limits: the validator session refuses a catalog
other than `mip`; a `prod` App skips the App API mutation suites (the verdict
says so); and `requirements-dev.txt` is version-pinned, not hash-pinned, which
is a blocker before a non-sandbox client's first nightly dispatch.

## 9. After go-live

After seven green days, turn the weekly refresh on: set
`MIP_SCHEDULED_REFRESH=on` and `MIP_SCHEDULED_REFRESH_RECIPIENTS` (email
addresses only) in `.env.local`, re-run `github_environment` (step 3) so the
environment and `MIP_CLIENT_PROFILE_SHA256` follow, dispatch `phase=plan`, and
release with the new profile ack the plan prints. Any profile change works the
same way: the profile ack covers the profile digest, so a changed profile needs
a new plan.

## Deferred: owner hardware-key attestation (plan M1-PR5)

The plan's owner FIDO2 hardware-key layer (an `allowed_signers` trust root, a
trust-root guard, and owner-signed profile, operation and mint attestations) is
**deferred and not built**. Nothing in this repository enrolls a key, signs an
attestation or checks a signature; the dispatch preflight reports the
trust-root chain as "deferred (M1-PR5)". Its stand-ins are:

- the exact digest acks: the `profile` ack (`owner_attestation`) and each
  operation ack, each recomputed right before it is relied on;
- `MIP_CLIENT_PROFILE_SHA256`, the profile digest `github_environment` records;
- the human-presence checks of the bootstrap tools;
- the environment approval, which in `single_maintainer_audited` mode is an
  audited self-approval, not human-only review ([review-modes.md](review-modes.md)).

If a lender requires separation of duties, this layer (and the `independent`
review mode it unlocks) is the milestone to build first.
