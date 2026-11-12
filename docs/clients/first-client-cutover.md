# First-client cutover runbook

Client #1's live install predates the client target: one person deployed it by
hand, so that person owns its Unity Catalog objects, its Lakebase database and
its bundle state, and it ships only through the emergency recipe
(docs/deployment.md, "The emergency recipe, command by command"). This runbook
moves it, in place and without a redeploy from scratch, onto the governed
per-client path: the deployer's bundle root, the client's GitHub Environment
and the `deploy-dev.yml` dispatch. It is the ordered list of the plan's cutover
steps (revision 2, `cutover_steps`), one section per step, each with who runs
it, its preconditions, the exact commands, the proof to read, the refusals it
can hit and where to stop or roll back.

The reference for every tool is [docs/deployment.md](../deployment.md); this
page does not repeat it. The general onboarding order is
[onboarding.md](onboarding.md); review modes are [review-modes.md](review-modes.md).

## Placeholders, and where the real values live

This page names no client value. Every one is a placeholder:

| Placeholder | Meaning |
|---|---|
| `<client>` | client #1's client key (`MIP_CLIENT`), also its GitHub Environment |
| `<owner-profile>` | the owner's own Databricks CLI profile (U2M) for client #1's workspace |
| `<workspace-host>` | the workspace origin (`DATABRICKS_HOST`) |
| `<app-name>` | the live App's name (`MIP_APP_NAME`) |
| `<prefix>` | `MIP_IDENTITY_PREFIX` |
| `<catalog>` | the governed catalog, `MIP_DEFAULT_CATALOG` (`mip`) |
| `<legacy-owner>` | the identity that deployed the legacy install and owns it today |
| `<legacy-root>` | its bundle root, `MIP_LEGACY_BUNDLE_ROOT` (`/Workspace/Users/<legacy-owner>/.bundle/mortgage-intelligence-platform/<leaf>`) |
| `<private-dir>` | a mode-0700 directory outside the checkout for reports and journals |
| `<sha256>`, `<profile-ack>` | a digest the named read-only plan printed |

The real values live in the operator's profile (the owner's gitignored
`.env.local` and `~/.databrickscfg`) and in the `<client>` GitHub
Environment, never in git, an issue, a PR or a shared log. Reports and
journals the tools write name resources: keep them in `<private-dir>`.

## The state this runbook starts from (2026-11-11)

- **The live App is STOPPED.** The owner stopped it on 2026-11-09. A
  read-only read on 2026-11-11 found no active and no pending deployment; the
  newest deployment is SUCCEEDED and the App's default source is that
  deployment's source folder. Leave it stopped until step 10A.
- **Step 3 is done** (the owner, 2026-11-06).
- **Code on `main`:** waves 1-6 of the plan are merged, PR #334 (spike S7:
  start proofs and the stopped-App blue) and PR #337 (spike S2: binding
  updates and the `MIP_SALESFORCE_BINDINGS` switch) included. Steps 10 to 12
  need both. The gaps below remain.

## Blockers on main (code follow-ups, not runbook steps)

Read against `main` (which holds #334 and #337), these gaps stop the governed path, and
two metastore reads gate it (S0(b) and S0(b′)). Each gap is a code change; nothing in this
runbook works around one. B2, B4 (all three parts), B5 and B6 are fixed on `main` (their
rows stay for the record), and B1 no longer stops this cutover: its adopt-phase
part has landed (#344), and what remains of it blocks a new client's first
install only. **Do not start step 7 until B3 has landed or
is about to land, plan #1 (step 6) has answered S0(b), and the owner can take
S0(b′)'s attestation (below):** steps 7 to 12
run as one sequence inside a 14-day window (step 7), and a sequence started
while 10B cannot run leaves the install half handed over.

| # | Blocks | What happens | The follow-up |
|---|---|---|---|
| B1 | a new client's plan and first install (step 10B no longer) | Before adoption the deployer's bundle state binds no SQL warehouse, and `DATABRICKS_WAREHOUSE_ID` is no client profile field, so `deploy-dev.yml` carries none. **Adopt phases cleared by #344:** `--phase adopt` and `--phase unadopt` run no SQL, so the preflight reads no warehouse for them, leaves `DATABRICKS_WAREHOUSE_ID` unset and prints `sql warehouse: none (--phase adopt runs no SQL)`; 10B no longer refuses here. A live `plan` or `release` whose deployer state binds no warehouse still refuses `this deployer's bundle state binds no SQL warehouse yet` (exit 2, nothing changed). | A new client's first install still needs the follow-up: a bootstrap warehouse carried through the profile ([onboarding.md](onboarding.md)). |
| B2 | fixed | `operation_acks` carries adopt-live's acks: `adopt_live` (`MIP_ACK_ADOPT_LIVE`, phase `adopt`), `supersede` (`MIP_ACK_SUPERSEDE`, `adopt`) with its selector `supersedes_deployment` (`MIP_ADOPT_LIVE_SUPERSEDE`, `adopt`: the sha256 of the adopted record's deployment id, a digest and never a switch), and `unadopt` (`MIP_ACK_UNADOPT`, `unadopt`). The carrier checks each entry alone: the bind's `adopt_plan` without adopt-live's ack still refuses before the lease (`the adopt needs the adopt-live ack as well`), and a selector that names no adopted record refuses in adopt-live (`supersede_mismatch`). | Fixed on `main`. Step 10B's dispatches, its supersede remedy and its rollback carry them. |
| B3 | step 12 | adopt-live in CI cannot build a reference `frontend/dist` (the deploy job has no Node, and `MIP_ADOPT_BUILD_REFERENCE_DIST` is no dispatch input), so the adopted record's frontend is `unverified`. The release's pre-activation proof then refuses green activation (`unverified_frontend_unacked`) unless `MIP_ACK_RESTORE_UNVERIFIED_FRONTEND` or `MIP_ACK_NO_ROLLBACK_FOR` is given, and neither has a dispatch carrier. It refuses after the non-App bundle apply and any admitted window, with blue still serving. | Add `restore_unverified_frontend` (and, if the owner wants that route, `no_rollback_for`) to `OPERATION_ACKS` for phase `release`. |
| B4 | fixed | (a) and (b): `genie_pin` (`MIP_GENIE_PIN_ACK`) and `accepted_exposure_plan` (`MIP_ACK_ACCEPTED_EXPOSURE_PLAN`) are admitted in `plan`, `release`, `adopt` and `unadopt` (never `data`), for the two readers the adopt phases reach: the step-0 Genie pin check (`provision_genie_space --plan-pin`, no phase guard, `scripts/lib/deploy_step_preflight_gates.sh`) and the pre-stop probes in their adopt mode. So 10B-0 refuses at the pin check and prints the pin plan's sha256, and 10B-1 carries it. (c): before an adoption no pin record and no signed rollback record names the governed space (an older one, if any, is signed with another key than the one step 4 generates, and refuses as well), so the adopt phases' bundle plan (`install_state_adopt`'s `bundle_env plan`), its binds and unbinds and adopt-live's bundle summary refused `no pin names the governed Genie space yet` (exit 2) whatever `genie_pin` said. Those calls, and no others, now pass `bundle_env --adopt-acked-genie-pin`: while nothing pins a space, bundle_env binds the one title match whose pin plan sha256 is that exact `genie_pin`, through the step-0 check's own resolution (a pin always wins; no ack, another plan's ack, no title match or two of them refuse). bundle_env builds the deployer's client the provisioner's way, so it recomputes the very digest 10B-0 printed. Nothing writes a pin: adopt-live's signed v7 record then names the space (`genie_space_id`), and no later dispatch needs `genie_pin`. Each of those calls re-resolves the match, so a direct `CAN_MANAGE` change mid-run refuses (10B, Refusals). | Fixed on `main`: (a) and (b) by the carrier fix, (c) by the acked title pin. `phase=unadopt` reads the same `genie_pin` while no record names the space (10B, Rollback). |
| B5 (fixed: #343) | step 10B | The deploy job's checkout was shallow (`actions/checkout@v6` at depth 1), so adopt-live refused `health_sha_not_on_main` for every live health `git_sha` older than the run's own commit. `deploy-dev.yml`'s adopt phase now checks out main's full history (`fetch-depth` 2147483647, git's infinite depth: the run's commit at `refs/remotes/origin/main`, unshallow, and no other branch or tag); every other phase stays at depth 1, and a shallow checkout refuses saying so. adopt-live still refuses, by design, a health sha `main` does not hold: in CI, `the health sha resolves to no single commit in this checkout's history of main` (step 10A's source check finds it first). | Done. Keep the two history rules in "Decisions taken". |
| B6 | step 11 | Fixed. Plan #3 plans run 1's WR2 window, and it always exited 2: `quiesce_window` was release-only, so a plan could not carry the window's ack and the pre-lease window plan refused `window_unacked`; and the window's admission was judged against the plan job's own 30 minutes, which admit no window (each needs about an hour), so an acked plan would still refuse `window_budget_refused`. Plan #3 never printed the active deployment's sha256 or published the PlanReport step 12 needs. | Fixed on `main`: a plan carries `quiesce_window` (read-only) and forecasts the window's admission for a release dispatched now; step 11 is two dispatches. A release refuses an unacked or unadmitted window exactly as before. |
| S0(b) | steps 10B, 11 and 12 | **Fixed in code; plan #1 measures it.** The pre-stop owner probe, the owner-equivalent probe, the treatment-access converger and the client live checks read the metastore's owner from `metastores.summary()` through one reader (`tools/databricks/uc_metastore_read.py`), never the metastore-admin-only `metastores.get` (docs/deployment.md, "Metastore owner"). A throwaway non-admin service principal received the owner on 2026-11-11. Check 4b of the live checks reads it as the real CI deployer, so step 6's plan #1 answers S0(b) for this client. | Read plan #1's live-check lines with the decision rule in step 6. If the summary withholds the owner from the CI deployer, stop before step 7: a follow-up must read the owner another way (`system.information_schema.metastores` through a warehouse, after B1, or an owner attestation). |
| S0(b′) | steps 10B, 11 and 12, and `phase=unadopt` | **A proof route is in code; it needs the owner.** Once the owner is read, the converger, adopt-live's `treatment_unverified` fact and the pre-stop owner probe prove that the App's service principal holds no metastore privilege outside `USE_MARKETPLACE_ASSETS`. That read (`grants.get_effective` on the metastore for the App) is denied to a deployer that is not a metastore admin (HTTP 403, measured 2026-11-11), and the CI deployer is not one. So every `plan`, `adopt`, `unadopt` and `release` run refuses before the lease, `[mip-prestop] REFUSED: metastore_authority: the deploying identity is not a metastore admin ...`, unless it carries the metastore owner's attestation: `metastore_app_grants=<sha256>` in `operation_acks` (docs/deployment.md, "Metastore authority"). Closing S0(b) does not clear this. Under `single_maintainer_audited` the ack is the dispatcher's unauthenticated assertion that the App holds only reviewed metastore privileges, not a proof that the owner read them (review-modes.md). | The owner takes the attestation (step 10B, first block) at most 29 days before each run that reads it, and again after any change to the App's metastore grants. Only the pre-stop probe adds the run's 24-hour margin, so a run that starts before the printed last start keeps the ack through its last converger after the lease. |

The order a live `phase=adopt` run meets them: the client live checks (S0(b),
check 4b), the
warehouse preflight (which passes the adopt phases since B1's adopt part
landed), then the Genie pin check (10B-0's expected stop; 10B-1's
`genie_pin` clears it), then the pre-stop probes (`accepted_exposure_plan`
clears the exposure check; `metastore_app_grants` clears S0(b′)'s
`metastore_authority`), then the bind plan (which binds the title match
`genie_pin` names: B4(c) is fixed), then adopt-live's plan
(S0(b′) again, as `treatment_unverified`, without the attestation). Nothing of B2 is left on the acked
dispatch. A fix that clears one gap only moves the run
to the next.

**10B walked with #344, #345, #346 and B4(c) all on `main`** (read offline,
against the code of all four; B5 is on `main` as well, #343). B1 no longer
stops it: the adopt phases read no SQL warehouse (#344). The pin check stops
10B-0 by design and prints the pin plan; `genie_pin` clears it. The pre-stop
probes read the metastore's owner from its summary (#346, S0(b)), and
`metastore_authority` (S0(b′)) passes with the owner's `metastore_app_grants`
attestation, which every 10B dispatch line below carries. The bind
plan binds the acked title match (B4(c)); adopt-live's plan resolves the live
health sha in main's full history (B5). Under the lease, the binds, the
post-bind plan and adopt-live's bundle summary bind the same match, and
adopt-live signs the v7 record that pins the space. **No known code gap is
left on 10B's path:** 10B-1 ends with the adopt plan's and adopt-live's
digests, and 10B-2 runs to the signed record. What can still refuse is the
install's own state, each named at 10B: a Genie holder the owner-equivalent
probe does not approve (`owner_equivalent`, which 10B-0's review counts), a
holder that changes after 10B-0 (`the ack names another plan`), a Genie turn
over budget (the adopt phases' probes ask one real turn), and adopt-live's
checks of the live App (`bindings_differ`, `tree_drift`, `closure_differs`,
...). B3 does not stop 10B (the record's frontend reads `unverified`); it
stops step 12.

Two smaller gaps are noted at their steps: the freeze marker needs a signing
key no shell holds (step 1), and an open quiesce window that the owner
restarts by hand stays refused (docs/deployment.md, "Owner break-glass").

## Decisions taken (the runbook's defaults)

- **The client key** is `<client>`; the real key is in the operator's profile.
- **Review mode** `single_maintainer_audited`: the environment approval is an
  audited self-approval, not human-only review ([review-modes.md](review-modes.md)).
- **Core tier for run 1** (`MIP_AGENTIC_TIER=off`), **refresh off**
  (`MIP_SCHEDULED_REFRESH=off`) until seven green days (step 15).
- **Keep the third-party grants that exist today.** Never revoke another
  team's grant, never change the owner of anything outside this install's own
  objects, never write an `ALTER ... OWNER` on a live object by hand (owner
  changes happen only through the two plan-first handover tools in steps 7 and
  8, plus the one database-owner move step 8 names if its plan refuses
  `target_will_not_own_database`), and **never transfer metastore
  ownership**. The reviewed third-party managers stay: they are listed as
  approved principals (step 4), not revoked.
- **No Salesforce bindings**: `MIP_SALESFORCE_BINDINGS` stays unset (off).
  The live App binds 11 resources and none of the three Salesforce secrets.
- **`MIP_SEED_OPERATOR_EMAIL`** is set, before the first governed migrate, to
  the address the install was seeded with (step 4). Unset, the seed adds a
  synthetic admin row and the Lakebase schema digest changes.
- **Run 1 plans one bounded window** with its migration job, which revokes the
  CREATE ON DATABASE re-grant a binding send leaves. The online `REVOKE` spike
  S2b proposed is not used. Plan #3 names the window's actual reasons (step 11).
- **Pipeline-managed datasets** belong permanently to the pipeline's run_as,
  the jobs identity. There is no re-own exit. After the release:
  `handover_uc_ownership --verify --released`, then unset
  `MIP_UC_INTERIM_OWNER_PAIRS_JSON` (step 13).
- **Dashboards stay pinned** in their legacy folder: `parent_path` is
  immutable (spike S4), so `MIP_DASHBOARD_PARENT_PATH` names that folder for
  good. The alternative, a one-time recreate that loses the dashboards' ids,
  URLs and published history, is the owner's choice only.
- **The App start before step 10** is the owner's raw `apps start` (step 10A).
- **Until 10B, no emergency ship from a commit `main` does not hold.** This
  rule, not adopt-live, keeps a branch ship out. adopt-live refuses
  `health_sha_not_on_main`, by design, a health `git_sha` that `main` does not
  hold at the adopt dispatch: a commit of a side or unmerged branch, or of a
  branch later squash- or rebase-merged. A branch commit `main` later merges
  with a merge commit, this repository's usual merge, becomes an ancestor of
  `main` and is admitted, even one that was never `main`'s tip; step 10A's
  source check passes it too. A refused ship's remedy is another emergency
  ship, of a `main` commit, then step 10A's source check again.
- **No git-history rewrite before 10B, nor before any later supersede** of an
  adopted record. The public-history rewrite is an open owner decision; a
  rewrite gives every commit a new sha, so the live health sha leaves `main`
  and adopt-live refuses `health_sha_not_on_main`. If the owner rewrites first,
  an emergency redeploy of a rewritten `main` commit comes before 10A, and the
  source check runs in a clone fetched after the rewrite.
- **Owner break-glass for an open window**: a raw `apps start`
  (docs/deployment.md, "Owner break-glass").

## Steps at a glance

| Step | What | Who | State |
|---|---|---|---|
| 1 | Freeze and snapshot | agent (read-only), owner | ready |
| 2 | Owner decisions | owner | mostly decided (above) |
| 3 | Supervisor rename and App `CAN_QUERY` revokes | owner | **done 2026-11-06** |
| 4 | Bootstrap: identities, environments, profile | owner | ready |
| 5 | Profile attestation | owner | deferred; the exact profile ack stands in |
| 6 | Plan #1 | CI | ready |
| 7 | Unity Catalog ownership handover | owner | **wait** for the blockers (it starts the 14-day sequence) |
| 8 | Lakebase ownership handover | owner | **wait** (part of that sequence) |
| 9 | Plan #2 | CI | **wait** (part of that sequence) |
| 10 | Start the App (10A), then adopt (10B) | owner, CI | needs the S0(b′) attestation |
| 11 | Plan #3 | CI | after 10; needs the S0(b′) attestation |
| 12 | Release (run 1) | CI | **blocked (B3)**; needs the S0(b′) attestation |
| 13 | Post-release verification | agent, owner | after 12 |
| 14 | Data phase | CI | after 13 |
| 15 | Refresh on, after 7 green days | owner | later |
| 16 | Archive the legacy root | owner | after 13 |
| 17 | Owner legacy deletions | owner | after 16 |
| 18 | Run 2 prerequisites (agentic tier) | owner | optional; not wired (step 18) |
| 19 | Run 2 | CI | optional |

The plan's revision 1 listed 17 steps. Revision 2, the reviewed plan this
runbook follows, moved adopt after both ownership handovers (attack O8) and
added step 5 (the profile attestation) and step 9 (plan #2). Revision 1's steps
1-4, 7 and 8 keep their numbers; its 5 (plan #1) is step 6 here, its 6 (adopt)
is 10, its 9 is 11, its 10 (release) is 12, and its 11 to 17 are 13 to 19.

Every dispatch below is the command of record, a `deploy-dev.yml` (or
`nightly.yml`) dispatch on `main`, approved by the environment's reviewer in
the run's page. Approve only the run you dispatched, after reading its inputs.

---

## Step 1. Freeze and snapshot

**Who:** an agent (read-only) for the snapshot; the owner for the freeze.

**Preconditions:** none.

**Actions.**

1. Freeze piecewise deploys: message every peer session that the emergency
   recipe is frozen for client #1 until step 13. The owner keeps the live App
   stopped.
2. The signed freeze marker (`python -m tools.databricks.app_deployment_lease
   freeze --app-name <app-name> --until <epoch>`) is **not available here**: it
   needs the deploy's proof-signing key pair in the shell, and that key does
   not exist until step 4, where `github_environment` generates it in memory
   into a write-only environment secret that no shell can read. The freeze is
   therefore the message and the owner's rule; a CI-run freeze is a follow-up.
3. Take the read-only snapshot, kept in the session's private scratch (never
   committed), hashed: the App's deployments (ids, states, source paths, the
   newest one's env-name set) and its ACL; the legacy bundle state's 16 keys;
   Unity Catalog owners and grants on the governed catalog, the Lakebase sync
   catalog and the metastore; the Lakebase owners and roles (`datdba`,
   `relowner`, `proowner`, `pg_auth_members`); the job, pipeline, warehouse,
   experiment and dashboard settings; serving-endpoint and secret-scope ACLs;
   the Genie space's serialized spec diffed against the repo spec.

```bash
databricks apps get <app-name> -p <owner-profile> -o json | jq '{compute: .compute_status.state, active: (.active_deployment != null), pending: (.pending_deployment != null)}'
databricks apps list-deployments <app-name> -p <owner-profile>
```

**Proof:** the snapshot is hashed; the newest deployment's env-name set has no
`MIP_AGENT_*` or Gateway name; one real turn in the governed Genie space (its
own UI works with the App stopped) answers, and a `BUDGET_EXCEEDED` means the
owner restores the budget before step 12.

**Rollback:** read-only. Lift the freeze by message.

## Step 2. Owner decisions

**Who:** the owner. Most decisions are taken (above). Still to write down,
each in the client profile (step 4), never in git:

- **Approved principals.** `MIP_UC_APPROVED_OWNER_PRINCIPALS`: the
  `<prefix>-data-owners` group plus the reviewed third-party principal that
  holds `MANAGE` on the governed catalog today. `MIP_GENIE_MANAGER_PRINCIPALS`:
  the reviewed third-party principal that holds `CAN_MANAGE` on the governed
  Genie space, and the space's creator, which holds a direct `CAN_MANAGE` from
  creation, for as long as it keeps that grant (unless the creator is the
  deployer, which the probe approves anyway). Without them the pre-stop
  owner-equivalent probe refuses (docs/deployment.md, "Pre-stop probes"). The
  Genie API names no creator, so this list, not a count, is what names it:
  10B-0's review counts every holder this list (with the approved owners, the
  deployer and the platform's `admins` group) leaves out.
- **Accepted exposure** (`MIP_ACCEPTED_EXPOSURE_JSON`): one exact entry per
  Genie grant reaching the App outside the governed space (the workspace's
  `users` grants): read class for the `CAN_RUN` ones, explicit `elevated`,
  expiring within 30 days, for the `CAN_MANAGE` ones (re-review at every
  expiry). List the candidates read-only, then pin the reviewed policy:

```bash
DATABRICKS_CONFIG_PROFILE=<owner-profile> python -m tools.databricks.estate.cli --emit-exposure-inventory --app-name <app-name> --expected-inventory-principal <owner-user-name> --genie-space-name "<genie-space-title>" --agentic-tier off
python -m tools.databricks.estate.cli --plan
```

  The first lists candidate entries read-only, as the workspace admin it names
  (`<owner-user-name>`, the caller's own user name), with placeholder `reason`
  and `expires_at` for the owner to fill. The second prints the
  `accepted_exposure_plan` sha256 that plans, releases and the adopt phases
  carry (`operation_acks`, phases `plan`, `release`, `adopt` and `unadopt`). It reads
  `MIP_ACCEPTED_EXPOSURE_JSON` and `MIP_GENIE_SPACE_NAME` from the shell only,
  never from `.env.local`: export the profile's two values in that shell for
  that one command, or it prints the empty policy's digest, which 10B-1 then
  refuses (`accepted_exposure`). The deploy reads both from the profile. If you
  skip the local run, take the digest from the `accepted_exposure` refusal line
  of the first dispatch whose pre-stop probes run (10B-1), which names the
  exact `MIP_ACK_ACCEPTED_EXPOSURE_PLAN` value (one extra dispatch). See docs/deployment.md, "Accepted shared-estate
  exposure".
- **Dashboards:** pinned (default) or the one-time recreate.
- Confirm: no metastore or catalog ownership transfer, no revoke of another
  team's grant.

**Proof:** each decision is written down; `python -m tools.clients check`
validates the profile, the exposure JSON included.

**Rollback:** revise a decision before step 4.

## Step 3. Supervisor rename and App `CAN_QUERY` revokes (done)

**Done by the owner on 2026-11-06.** The legacy, human-created Supervisor was
renamed to a non-reserved name, and the App service principal's `CAN_QUERY` was
removed from the legacy Supervisor's serving endpoint and the legacy agent
gateway. The before-ACLs and the revert notes are in the owner's private
scratch. Nothing on another team's object was touched.

**Proof still to read:** the pre-stop probes (run in steps 10 and 12) find no
Supervisor under a reserved name; in the core tier they skip the Supervisor
clauses. The owner-equivalent probe passes for the catalog and the governed
space once step 2's approved principals are in the profile.

**Rollback:** rename back and re-grant from the step-1 snapshot.

## Step 4. Bootstrap: identities, environments, profile

**Who:** the owner (the client's account admin and the repository's
maintainer), by hand, in a terminal. The tools refuse under CI or without a
terminal, and each run logs in fresh, never reading `~/.databrickscfg`
([bootstrap.md](bootstrap.md), [github-environment.md](github-environment.md)).

**Preconditions:** steps 1 and 2.

**The profile.** Fill `.env.local` from `deploy/clients/example.env` with the
**live install's own names**, because adoption binds by name and refuses a
rename (`renamed`): the App, Lakebase instance and database, sync catalog,
secret scopes, Genie title and Supervisor name. Also, for an adopted install:

- `MIP_LEGACY_BUNDLE_ROOT=<legacy-root>`, and `MIP_DASHBOARD_PARENT_PATH` set
  to the legacy dashboards' live folder inside it (pinned);
- the install class and App env the live install runs with (the emergency
  recipe's `--install-class demo --app-env sandbox`);
- `MIP_AGENTIC_TIER=off`, `MIP_SCHEDULED_REFRESH=off`,
  `MIP_SALESFORCE_BINDINGS` unset;
- `MIP_SEED_OPERATOR_EMAIL=<the address the install was seeded with>`
  (identifying: `.env.local` and the environment secret);
- `MIP_UC_METASTORE_OWNER`, `MIP_UC_APPROVED_OWNER_PRINCIPALS`,
  `MIP_GENIE_MANAGER_PRINCIPALS`, `MIP_ACCEPTED_EXPOSURE_JSON` (step 2).

**Commands,** in this order:

```bash
python -m tools.clients check
python -m tools.clients.github_environment --plan
python -m tools.clients.github_environment --apply --ack-plan <sha256>
python -m tools.clients.bootstrap_account --plan --interim-member <legacy-owner-email>
python -m tools.clients.bootstrap_account --apply --sink github-environment --interim-member <legacy-owner-email> --ack-plan <sha256>
```

**Then pin by hand, before the next bootstrap run.** The sink writes
`MIP_IDENTITY_IDS_JSON` to an environment secret, which GitHub never returns,
and the tool prints no id. The next bootstrap run reads its pins from the shell
or `.env.local` only, and refuses an identity under an expected name that
nothing pins ([bootstrap.md](bootstrap.md)). Copy the application id of each
identity the apply created or adopted, from the account console, into
`MIP_IDENTITY_IDS_JSON` in `.env.local`. The data-owners group needs no pin:
the bootstrap proves it by its deployer and jobs members. Steps 7 and 8 read
the same local pins (`deployer`, `jobs`).

```bash
python -m tools.clients.bootstrap_account --plan --for rotate-adopted --interim-member <legacy-owner-email>
python -m tools.clients.bootstrap_account --rotate-adopted --sink github-environment --interim-member <legacy-owner-email> --ack-plan <sha256>
python -m tools.clients.bootstrap_account --verify --interim-member <legacy-owner-email>
python -m tools.clients.github_environment --plan
python -m tools.clients.github_environment --apply --ack-plan <sha256>
```

- `--interim-member` puts the legacy owner into `<prefix>-data-owners` for the
  cutover, so jobs that still run as the legacy owner keep working after the
  UC handover. Pass it to every bootstrap run while the membership exists: the
  bootstrap refuses a data-owners group holding any member other than the
  deployer, jobs and the named interim identity. It is removed by hand after
  the release (step 13); the bootstrap never removes a member.
- An identity that already exists under an expected name (a deployer the owner
  created earlier, the account principal, reserved M2M identities left from an
  earlier install) is refused as not pinned. Check its provenance, then pin it
  with `--adopt <role>=<application id>` on both the plan and the apply.
  `--rotate-adopted` then mints it a new secret, hands it to the environment
  only, proves it and revokes every older secret, including ones of unknown
  origin. Revoked secrets cannot come back.
- The deployer is refused if it holds any OAuth secret, PAT or OBO token, or
  more than one federation policy. Its subject is
  `repo:<owner>/<repo>:environment:<client>`, set before its `ADMIN`
  assignment.
- The last `github_environment` run writes the profile's values; if it asks
  for `--rotate`, pass it to both plan and apply.

**Then, by hand:**

- Fill `<client>-validate` with its five names ([onboarding.md](onboarding.md),
  step 3).
- Delete every repository- or organization-level variable or secret that
  carries a profile field or alias name, and those the retired parked nightly
  read: `check-dispatch` refuses every dispatch of either workflow while one
  exists and names each (docs/deployment.md, CI deploy owner steps 3 and 5).

**Proof:** a second `--apply` of each tool reports zero mutations;
`bootstrap_account --verify` is clean; `gh secret list --env <client>` shows the
schema's names; no tool printed a value.

**Refusals:** see [bootstrap.md](bootstrap.md) (a holder that is not an account
admin, a second federation policy, an account principal with account admin, a
sink environment without the review mode's protections) and
[github-environment.md](github-environment.md) (an identifying field stored as a
variable, a stale digest asking for `--rotate`).

**Rollback:** federation, assignments, group membership and environment
settings are restored by hand from the step-1 snapshot; revoked secrets are
replaced by the newly minted ones, never restored.

## Step 5. Profile attestation (deferred)

The owner-signed profile attestation (plan M1-PR5) is **deferred and not
built**: nothing signs or verifies a profile. Its stand-ins, both exact
digests:

- `MIP_CLIENT_PROFILE_SHA256`, the profile digest `github_environment` records
  (`python -m tools.clients digest` prints it locally);
- the `profile` ack, `owner_attestation=<profile-ack>`, which every mutating
  dispatch needs and every plan prints. A changed profile changes the ack: the
  pairs added at step 7 and removed at step 13, and the refresh switch at step
  15, each need the ack of a plan dispatched after the change.

**Who:** the owner reads the plan's profile ack before passing it. **Proof:**
the dispatch preflight reports `profile_ack` exact. **Refusal:** `profile_ack`
(dispatch with the printed value after reviewing the profile).

## Step 6. Plan #1

**Who:** CI, dispatched and approved by the owner.

```bash
gh workflow run deploy-dev.yml --ref main -f client=<client> -f phase=plan -f acknowledge_single_maintainer_break_glass=true
```

**Proof (step summary):** the OIDC proof passes (the profile's deployer, in
`admins`, on the profile's workspace and metastore: this also answers spike
S5, the federation audience); the dispatch preflight prints the profile ack
and the environment-protection result; then the client live checks refuse,
and the run stops there (exit 2, nothing changed). The expected set:

- `adoption: existing install not adopted` (the App exists, the deployer's
  state binds none);
- the install marker as the deployer can read it. Before step 7 the deployer
  holds no privilege on `<catalog>` (the legacy owner owns it, with no
  deployer grant), and the marker check reads the catalog as the deployer, so
  expect `install marker: could not be read (PermissionDenied); refusing
  rather than reading it as absent` (the error class may differ). If the API
  hides the catalog instead (`NotFound`), there is no marker line, and the
  storage-root check runs as for a first install: unless
  `MIP_UC_CATALOG_STORAGE_ROOT` is set, a metastore without a default storage
  root then adds `storage root: the metastore has no default storage root
  ...`. Here that line judges a create that never happens, and it goes away
  at plan #2. If the deployer can read the catalog after all, the marker line
  is the one plan #2 shows (below); find out which grant allows it before
  step 7.

**S0(b), answered here.** The live checks never short-circuit, so plan #1
also runs check 4b, the metastore owner, as the real CI deployer and through
the very reader the pre-stop probe and the converger use. Read it with this
rule, and only when its positive control holds: the same run shows the
`adoption: existing install not adopted` line (the install checks ran) and no
`live checks could not read the workspace` line. Then:

- **no `metastore owner:` line**: the summary returned the owner to the
  deployer and it is exactly `MIP_UC_METASTORE_OWNER`. S0(b) is closed. S0(b′)
  is not: the step 10B attestation is still needed for 10B, 11 and 12;
- `metastore owner: the metastore summary returns no owner to the deployer
  (spike S0(b)) ...`: **stop before step 7**; the owner-read follow-up must
  land first (Blockers, S0(b));
- `metastore owner: the metastore summary returns an owner that is not a
  canonical principal name ...`: a fault in the metastore's own data, not a
  withheld read; the metastore's owner reads and corrects it, then plan #1
  again;
- `metastore owner: the metastore's owner is not exactly
  MIP_UC_METASTORE_OWNER ...`: correct the profile value after review (never a
  metastore transfer), then plan #1 again;
- `metastore owner: the metastore summary names another metastore ...` or
  `metastore owner: could not be read (...)`: stop and find out why before
  step 7.

The two-line set of an unadopted install from before the marker (`adoption:
...` plus `install marker: MIP_DEFAULT_CATALOG exists without
mip.install_key`, "adopt it first") first appears at plan #2, once step 7 has
made `<prefix>-data-owners`, which holds the deployer, the catalog's owner.
On `main` these are the only refusals plan #1 can show: the live checks run
before the pre-stop probes, so the plan's other expected refusals (UC owner
policy, Lakebase executor) are not reached until the adopt phase. The public
summary leaks no identifier.

**Refusals and what to do:** `check-dispatch` names a repository-level profile
name (delete it, step 4); `environment_protections_drift` (re-run
`github_environment`); `environment_protections_unreadable` (spike S8(a) owed:
a mutating phase then needs `protections_unverified_ack=<sha256>`, the exact
ack the refusal prints); an OIDC proof mismatch, `tree_scan`, or any live-check
line outside the expected set above (the Cotality share, the account
principal, a marker naming another install, a storage root that differs from
an existing catalog's, a `metastore owner:` line): **stop the cutover** and fix the cause.

**Rollback:** read-only.

## Step 7. Unity Catalog ownership handover

**Who:** the owner, as the legacy owner (an agent only on the owner's explicit
go-ahead, with the owner's profile). **Announce** steps 7 to 12 to the owner
and peers as one contiguous sequence; the App stays stopped until step 10A.

**Preconditions:**

- **Every blocker of steps 10B and 12 has landed on `main`** (B3, or B3 is
  about to land before step 12; B1's adopt part, B2, B4 (all three parts)
  and B5 have landed); plan #1 showed no
  `metastore owner:` line beside its positive control (step 6), so S0(b) is
  closed; and the owner is ready to take S0(b′)'s attestation before 10B
  (Blockers, above). Closing S0(b) alone does not clear this step. This step starts a sequence that must finish inside the
  pairs' 14 days; started while 10B cannot run, it leaves UC (and, after
  step 8, Lakebase) handed over with no adoption to follow.
- Step 6 showed only the expected set; the interim membership (step 4) is in
  place.
- Step 10A's source check passes (it is read-only and reads the stopped App):
  a deployment adopt-live will refuse must not start the 14-day sequence.

**The 14-day window.** Pairs expire at most 14 days ahead (`--interim-days`,
1 to 14). An expired pair refuses `handover_uc_ownership --plan` and `--verify`
(`interim_pair_expired`) and the pre-stop owner policy. If the sequence will
overrun, renew before the expiry:

1. re-run `handover_uc_ownership --plan --report-out <private-dir>/uc-plan.json`
   (the commands below; the report is written even when the plan refuses), and
   take its fresh `interim_owner_pairs_json`;
2. replace `MIP_UC_INTERIM_OWNER_PAIRS_JSON` in `.env.local` with it, and
   re-run `github_environment --plan` and `--apply --ack-plan <sha256>` (with
   `--rotate` on both if the plan asks for it) so the environment secret and
   the recorded profile digest follow;
3. dispatch a plan and use the profile ack it prints: the profile changed, so
   the old ack no longer matches.

```bash
DATABRICKS_CONFIG_PROFILE=<owner-profile> python -m tools.clients.handover_uc_ownership --plan --report-out <private-dir>/uc-plan.json
DATABRICKS_CONFIG_PROFILE=<owner-profile> python -m tools.clients.handover_uc_ownership --apply --ack-plan <sha256> --journal <private-dir>/uc-journal.json
DATABRICKS_CONFIG_PROFILE=<owner-profile> python -m tools.clients.handover_uc_ownership --verify
DATABRICKS_CONFIG_PROFILE=<owner-profile> python -m tools.clients.handover_verify privileges --principal <legacy-owner-email> --report-out <private-dir>/privileges.json
```

- The plan moves the governed catalog, its schemas, tables, views, volumes and
  functions, and the sync catalog at catalog level, to `<prefix>-data-owners`.
  It never touches the metastore, the share catalog, a foreign or system
  catalog, or a System-user object.
- Pipeline-managed streaming tables and materialized views are excluded for
  good (spike S4): their owner is always the pipeline's run_as. Copy the
  report's `interim_owner_pairs_json` into `MIP_UC_INTERIM_OWNER_PAIRS_JSON`
  (`.env.local` and the environment secret, then re-run `github_environment`)
  before `--verify`.

**Proof:** the owner counts match the plan; `--verify` passes;
`handover_verify privileges` shows the legacy owner's privileges on every
lifecycle target unchanged (through the group). The App service principal's
grants are untouched.

**Refusals:** `metastore`, `share_catalog`, `foreign_catalog`,
`foreign_securable`, `system_catalog` (never moved, by design);
`interim_pair_expired` (renew or release the pair); exit 3, a partial apply:
the journal names what moved; revert.

**Rollback:**

```bash
DATABRICKS_CONFIG_PROFILE=<owner-profile> python -m tools.clients.handover_uc_ownership --revert --journal <private-dir>/uc-journal.json
DATABRICKS_CONFIG_PROFILE=<owner-profile> python -m tools.clients.handover_uc_ownership --revert --journal <private-dir>/uc-journal.json --ack-plan <sha256>
```

The first prints the revert plan's digest; the second applies exactly it. The
legacy owner can also reclaim ownership as metastore owner.

## Step 8. Lakebase ownership handover

**Who:** the owner, as the legacy owner, connected to the App database.

**Preconditions:** step 7. Unset `LAKEBASE_HOST`, `LAKEBASE_USER`,
`LAKEBASE_PASSWORD` and `LAKEBASE_PORT` in the shell (the tool refuses those
overrides).

**The mechanism (spike S3):** L-A role setup plus the per-object
`ALTER ... OWNER TO` at `lock_timeout = '3s'` and `statement_timeout = '30s'`.
Not `REASSIGN OWNED`, not L-B (it deletes the legacy role). First print the
preconditions, read-only:

```bash
DATABRICKS_CONFIG_PROFILE=<owner-profile> python -m tools.clients.lakebase_handover --preconditions --report-out <private-dir>/lakebase-preconditions-report
```

Run in `psql`, as the legacy owner, **exactly** the SQL it prints, then re-run
`--preconditions` until it prints `-- Every precondition holds.` Connect with
values local to the `psql` command, never with the four `LAKEBASE_*` names
(exported in this shell, they make the next `--preconditions` refuse): read the
instance's host, then connect with a fresh OAuth token from your own CLI
profile, which never reaches the shell history or the screen:

```bash
databricks database get-database-instance <lakebase-instance> -p <owner-profile> -o json | jq -r .read_write_dns
PGPASSWORD="$(databricks database generate-database-credential --json '{"instance_names":["<lakebase-instance>"],"request_id":"mip-cutover-step-8"}' -p <owner-profile> -o json | jq -r .token)" PGSSLMODE=require psql -h <read-write-dns> -U <legacy-owner-email> -d <lakebase-database>
```

`<lakebase-instance>` and `<lakebase-database>` are the profile's
`MIP_LAKEBASE_INSTANCE` and `LAKEBASE_DATABASE`; `<read-write-dns>` is what the
first command printed. The token is short-lived: reconnect the same way when it
expires. `--preconditions` prints, in order, only the
steps that do not hold yet; their shapes, with `<deployer>` the deployer's
Postgres role (its application id), which the tool fills from the profile:

```sql
CREATE EXTENSION IF NOT EXISTS databricks_auth;
SELECT databricks_create_role('<deployer-application-id>', 'SERVICE_PRINCIPAL');
GRANT "<deployer>" TO "<legacy-owner>" WITH INHERIT TRUE, SET TRUE;
GRANT CREATE ON SCHEMA "mip_app" TO "<deployer>";
GRANT USAGE ON SCHEMA "<schema>" TO "<deployer>";
GRANT <one privilege> ON <object> TO "<deployer>";
```

- Create the deployer's role only this way, by the legacy owner: a role made
  through the REST API or the UI has no ADMIN edge, and `--preconditions`
  refuses `target_role_without_admin_edge`.
- **Never hand-write `GRANT USAGE ON SCHEMA public`.** The deployer uses
  `public` as the database owner; the grants job's exact public-schema gate
  refuses any extra row there, so that grant breaks every later release.
- `target_will_not_own_database`: the App database belongs to neither the
  legacy owner nor the deployer. Its owner first gives the App database to the
  legacy owner; then re-run `--preconditions`.
- `target_needs_object_privilege`: the object's owner grants the deployer a
  column-capable privilege (`SELECT` on a table, view or sequence). `TRIGGER`,
  `DELETE` or `TRUNCATE` alone, or `USAGE` alone on a sequence, is not enough.

Then check that no session holds a long transaction (one older than the
per-object budget, about 120 s, exhausts it), plan and apply:

```sql
SELECT state, now() - xact_start FROM pg_stat_activity WHERE datname = current_database() AND xact_start IS NOT NULL ORDER BY 2 DESC;
```

```bash
DATABRICKS_CONFIG_PROFILE=<owner-profile> python -m tools.clients.lakebase_handover --plan
DATABRICKS_CONFIG_PROFILE=<owner-profile> python -m tools.clients.lakebase_handover --apply --ack-plan <sha256> --journal <private-dir>/lakebase-journal.json
```

**Proof:** the apply reports `applied` with its counts; a new `--plan` has
nothing left to move. The legacy role is kept (never run `--retire-check`
toward a drop yet: jobs still run as it). The plan's other proofs (the
executor probe as the deployer, the App role still SAFE with no
`pg_auth_members` rows) are read by the governed migration plan in plan #3
(step 11). Synced tables advancing and `/health` wait for step 10A.

**Refusals:** `target_role_missing`, `target_role_not_settable`,
`caller_cannot_revert`, `target_needs_schema_create`,
`caller_needs_database_create`, `caller_needs_createdb`,
`target_needs_schema_usage`, `target_needs_object_privilege`,
`target_grant_unrevertible`, `name_has_control_character`: fix the named
precondition (re-run `--preconditions`), then plan again. Exit 4: the lock
budget ran out with nothing half-applied. The WR5 window is not available while
the App is stopped (it refuses `window_app_not_active`), so the exit is the
revert below, then a new plan. Exit 3: a partial apply; revert.

**Rollback:**

```bash
DATABRICKS_CONFIG_PROFILE=<owner-profile> python -m tools.clients.lakebase_handover --revert --journal <private-dir>/lakebase-journal.json
DATABRICKS_CONFIG_PROFILE=<owner-profile> python -m tools.clients.lakebase_handover --revert --journal <private-dir>/lakebase-journal.json --ack-plan <sha256>
```

## Step 9. Plan #2

**Who:** CI.

```bash
gh workflow run deploy-dev.yml --ref main -f client=<client> -f phase=plan -f acknowledge_single_maintainer_break_glass=true
```

**Proof:** exactly the two not-adopted lines, `adoption: existing install not
adopted` and `install marker: MIP_DEFAULT_CATALOG exists without
mip.install_key`. The marker line is new since step 6 (the deployer can now
read the catalog through `<prefix>-data-owners`), and any step-6 marker read
refusal or storage-root line is gone. Nothing else refuses: the handovers
changed no profile field the live checks read. A `plan` cannot show more of an
unadopted install. The adoption's own
read-only readout (the bind plan, the pre-stop probes in their adopt mode and
adopt-live's preconditions) is step 10B's first dispatch, which needs the
running App of step 10A.

**Rollback:** read-only. If anything else refuses, revert steps 7 and 8 or fix
the cause.

## Step 10. Start the App (10A), then adopt (10B)

### 10A. Start the stopped App (owner)

adopt-live signs only a **running** App: a stopped App has no active
deployment and refuses `deployment_unstable`. Immediately before 10B the owner
starts it, raw, with their own profile. **Do not start it while any 10B
blocker stands** (an unanswered S0(b), or no S0(b′)
attestation the owner can take): 10B would refuse
before the lease, and the App, which the owner keeps stopped until 10B can
follow, would be up with nothing to adopt it. The start creates a new SNAPSHOT
deployment of the App's default-source folder (spike S7), which adopt-live then
checks (`tree_drift`: the folder and its snapshot must digest equal).

**First, the source check (read-only; the App stays stopped).** adopt-live
adopts only a deployment whose health `git_sha` is a commit `main` holds, and
the start deploys the App's default-source folder, the newest deployment's:

```bash
databricks apps list-deployments <app-name> -p <owner-profile> -o json | jq 'sort_by(.create_time) | last | {state: .status.state, leaf: (.source_code_path | split("/") | last)}'
databricks apps get <app-name> -p <owner-profile> -o json | jq '{default_leaf: (.default_source_code_path | split("/") | last)}'
```

The newest deployment is `SUCCEEDED`, and both leaves are the same
`<sha12>-<digest12>`, the folder a governed emergency ship publishes
(`app-src/<sha12>-<digest12>`). Then, in a full clone of this repository (never
the CI checkout, and never one fetched before a history rewrite), run
`git fetch origin`; `git rev-parse --is-shallow-repository` prints `false`,
`git rev-parse --verify <sha12>^{commit}` prints one commit that starts with
`<sha12>`, and `git merge-base --is-ancestor <that commit> origin/main` exits 0.
Keep the sha12 in your terminal; the repository records none. **Stop** if
the leaf has another shape, the two leaves differ, or the commit is not on
`origin/main`: do not start or adopt. The remedy is an emergency redeploy of a
`main` commit (docs/deployment.md, "The emergency recipe, command by command"),
then this check again.

Then start it:

```bash
databricks apps start <app-name> -p <owner-profile>
databricks apps get <app-name> -p <owner-profile> -o json | jq '{compute: .compute_status.state, pending: (.pending_deployment != null), active_state: .active_deployment.status.state, default_is_active_source: (.default_source_code_path == .active_deployment.source_code_path)}'
databricks apps list-deployments <app-name> -p <owner-profile>
```

Wait until the compute is `ACTIVE`, nothing is pending, the active deployment
is `SUCCEEDED` and the newest of any state, and `default_is_active_source` is
true. Then measure, read-only, what spike S7's follow-up needs (#334's
stranded-start check compares env values, which Databricks may redact): for the
new start's deployment and for the previous newest one (both ids from
`list-deployments`, kept in your terminal), record only these counts:

```bash
databricks apps get-deployment <app-name> <deployment-id> -p <owner-profile> -o json | jq '{env_vars: ((.env_vars // []) | length), with_a_value: ([(.env_vars // [])[] | select((.value // "") != "")] | length)}'
```

Also now, with the App up: one real Genie turn (BUDGET_EXCEEDED means the owner
restores the budget before step 12), `/health` ok with a `git_sha` (authenticated
`/api/v1/health`) that starts with the source check's sha12 (with none, 10B
refuses `health_unreadable`), synced tables advancing, and
the next App-triggered lifecycle run, watched read-only and never run-now:

```bash
DATABRICKS_CONFIG_PROFILE=<owner-profile> python -m tools.clients.handover_verify watch-next-run --job-id <lifecycle-job-id> --report-out <private-dir>/lifecycle-run.json
```

**Stop point:** if the start's deployment FAILED, do not adopt. Stop the App
again if the owner wants it stopped, and fix the folder first (a fresh
emergency deploy of a current `main` commit is the remedy for `closure_differs`
and for a health sha `main` does not hold).

### 10B. Adopt (CI)

**Preconditions:** 10A, its source check included; steps 7 and 8 done; S0(b)
closed by plan #1 (step 6). Every dispatch below
carries the `accepted_exposure_plan` digest of step 2 and the
`metastore_app_grants` digest of the attestation that comes first.

**First, the owner attests the App's metastore privileges (S0(b′)).** The CI
deployer is not a metastore admin, so it cannot read them; every run of steps
10B, 11 and 12 (and `phase=unadopt`) reads this attestation instead. As the
metastore's owner (the legacy owner), read-only:

```bash
DATABRICKS_CONFIG_PROFILE=<owner-profile> python -m tools.databricks.metastore_app_grants --app-name <app-name>
```

It prints the App's effective metastore privileges (expect
`USE_MARKETPLACE_ASSETS`, from `account users`), the UTC date, the last run
start that accepts it (29 days after that UTC day starts: the pre-stop probe
judges the run's end, and the converger after the lease its own moment, so a
run started before it keeps the ack to its end), and
`metastore_app_grants=<sha256>`. It
names no principal or id. It refuses, printing no digest, if the caller is not
the metastore's owner (or in its owning group), or if the App holds any other
metastore privilege (revoking that is the owner's decision). Pass the digest in
`operation_acks` below and in steps 11 and 12. Take it again if a run would
start after that date, or after any change to the App's metastore grants.

**10B-0, read-only: the Genie pin plan.**

```bash
gh workflow run deploy-dev.yml --ref main -f client=<client> -f phase=adopt -f owner_attestation=<profile-ack> -f operation_acks="accepted_exposure_plan=<sha256> metastore_app_grants=<sha256>" -f acknowledge_single_maintainer_break_glass=true
```

It refuses at the step-0 Genie pin check, before the lease (`one Genie space
carries the governed title but no pin names it, and no ack names it`, then
`the governed Genie space pin was refused before the lease`, exit 2), and
prints the read-only pin plan: `candidate space`, `creator (direct
CAN_MANAGE; ...)`, `deploying identity` and `pin plan sha256`. In the job log
the first three are runner masks (`***`, one per direct holder on the creator
line); the digest is printed as it is, but it covers the CI deployer, so the
plan cannot be read from the log. Review it as the owner, read-only, before
passing the digest on, with the profile's `MIP_IDENTITY_IDS_JSON` (step 4's
local pins), `MIP_UC_APPROVED_OWNER_PRINCIPALS` and
`MIP_GENIE_MANAGER_PRINCIPALS` (step 2) exported in that shell for that one
command (each is read from the shell only, never from `.env.local`; without
the deployer pin it refuses):

```bash
DATABRICKS_CONFIG_PROFILE=<owner-profile> python -m tools.clients.genie_pin_review --app-name <app-name> --genie-space-name "<genie-space-title>"
```

It prints counts and one digest, never an id or a principal name
(`tools/clients/genie_pin_review.py`): how many spaces carry the governed title
(the pin check pins only exactly one, so anything else is a stop); how many
principals hold a direct `CAN_MANAGE` on that one and whether the owner is one
of them; how many of the reviewed managers are among the holders and how many
holders are outside that list; how many principals with `CAN_EDIT` or
`CAN_MANAGE` on the space, direct or inherited, the pre-stop owner-equivalent
probe does not approve (counted by the probe's own functions); and the pin plan
sha256 recomputed from that space, its holders and the profile's deployer. Go
on only if:

- the title count is 1;
- the recomputed `pin plan sha256` equals the CI log's. Only then did CI see
  this space, these holders and that deployer: a stranger in the creator's
  place counts the same but gives another digest;
- the holder count equals the masked entries on the CI creator line, and every
  reviewed manager is among the holders;
- no principal is outside the probe's approved principals. 10B-1 refuses each
  one (`[mip-prestop] REFUSED: owner_equivalent`) before the lease. A count
  never says who a holder is, so a creator the list leaves out counts like a
  stranger: approve the reviewed one in `.env.local` (step 2), re-run
  `github_environment --plan` and `--apply --ack-plan <sha256>` so the deploy
  reads it, and take the new profile ack from a plan dispatch (the profile
  changed); or remove its grant with its owner's consent. Then review again.
  If the owner created the space, the review says the caller is one of the
  holders, and the owner's own user is then the creator to approve.

The digest binds that candidate, those holders and the deployer: if a holder
changes before 10B-1, 10B-1 refuses `the ack names another plan`; review again.
The adopt phases' bundle calls recompute it too, each one afresh (the bind
plan, every bind, the post-bind plan, adopt-live's bundle summary and an
undo's unbinds), until adopt-live's signed record pins the space: keep the
space's grants as they are from this review until 10B-2 has ended.

**10B-1, read-only: the adopt plans.**

```bash
gh workflow run deploy-dev.yml --ref main -f client=<client> -f phase=adopt -f owner_attestation=<profile-ack> -f operation_acks="genie_pin=<sha256> accepted_exposure_plan=<sha256> metastore_app_grants=<sha256>" -f acknowledge_single_maintainer_break_glass=true
```

Its bind plan binds the title match that `genie_pin` names (B4(c), fixed),
so the run gives the readout plan #2 cannot: it
prints the adopt plan's sha256 and adopt-live's `adopt_live` plan sha256 and
stops before the lease. adopt-live's plan line names the 40-hex `git_sha` it
resolved, which starts with the source check's sha12. Expect 16 legacy keys
to bind, 2 planned creates, no delete, recreate or instance change, the
dashboards `pinned_permanent`, and
`s7_default_source: not_settable`. Keep the adopt-live plan's
`deployment_sha256`: it is the `supersedes_deployment` selector should a
supersede ever be needed.

**10B-2, the adoption.**

```bash
gh workflow run deploy-dev.yml --ref main -f client=<client> -f phase=adopt -f owner_attestation=<profile-ack> -f operation_acks="genie_pin=<sha256> accepted_exposure_plan=<sha256> adopt_plan=<sha256> adopt_live=<sha256> metastore_app_grants=<sha256>" -f acknowledge_single_maintainer_break_glass=true
```

It binds the 16 legacy keys into the deployer's state, judges the post-bind
plan in adopt mode, then copies the live folder create-only into the
deployer's `app-src/adopted-<id12>-<digest12>`, re-hashes it and signs one v7
core record, which from then on names the space the App binds (`genie_space_id`),
so no later dispatch needs `genie_pin`. It never stops, starts or deploys the
App. Do not pass `expected_active_deployment_sha256`: it is release-only, and
an adopt dispatch with it refuses.

**Proof:** the gate shows 0 delete, 0 recreate, no instance change, creates =
the 2 jobs the legacy install never had; the active deployment is unchanged (the
10A start); the record verifies; `frontend_provenance` is reported
(`unverified` in CI, see B3).

**Refusals:** before the bind plan: the Genie pin (exit 2: `the governed
Genie space pin was refused before the lease`; 10B-0's expected stop, and at
10B-1 `the ack names another plan` when the plan moved), and the
pre-stop probes (`[mip-prestop] REFUSED: <code>`; `accepted_exposure` names the
exact digest when the ack is missing or another plan's; `metastore_authority`
is S0(b′): the attestation is missing, too old, or for another App or owner,
so take it again; `owner_policy` with `the metastore summary returned no
owner` is S0(b) and means stop); bind plan: `governed Genie binding verification failed` with `the ack
names another plan` (a direct holder of the space changed after 10B-0: review
again), `no Genie space carries the governed title` or `2 Genie spaces carry
the governed title` (the title match is gone, or doubled),
`missing`, `renamed`, `foreign_bound`,
`bound_elsewhere`, `duplicate_id`, `deployer_state_orphan`,
`legacy_key_unknown`, `dashboard_parent_moved`, `unresolved_name`,
`state_mismatch`, `malformed_state`, `read_failed`; post-bind gate:
`instance_change`, `create_set_mismatch`, `dashboard_recreate`,
`dashboard_move`, `rename_collision`; adopt-live: `deployment_unstable`,
`env_names_not_proxyless`, `bindings_invalid`, `bindings_differ`,
`acl_unreadable`, `treatment_unverified` (with `cannot prove the App's
metastore privileges`: S0(b′), as above), `health_unreadable`,
`main_unresolved`, `source_not_on_main`, `health_sha_not_on_main` (a health
sha `main` does not hold; 10A's source check finds it first),
`tree_drift`, `app_yaml_differs`, `closure_differs`, `record_present` (over
this install's adopted record of another deployment it prints the
`supersedes_deployment` value), `tier_agentic`, and an acked dispatch
without its `adopt_live` pair: `the adopt needs the adopt-live ack as well`.
Each is in docs/deployment.md ("Adopting an existing install",
"Signing the live deployment as an adopted blue"). Exit 3 from the bind: its
undo is unproven; dispatch `phase=unadopt`.

**A Genie holder that changes mid-run.** Until adopt-live signs, every bundle
call of 10B-2 re-resolves the Genie title match (the space list, the space's
permissions and the deploying identity): each bind, the post-bind plan,
adopt-live's bundle summary, and the undo's unbinds. A direct `CAN_MANAGE`
holder added or removed while 10B-2 runs makes the pin plan another one, so
the next of those calls refuses `the ack names another plan`. Among the binds,
that refusal starts the undo, whose unbinds refuse the same way: the run ends
exit 3, **half-bound** (the private report names the keys the state may still
bind), with the App untouched and no record written. Recover with
`phase=unadopt` and a **fresh** `genie_pin`: review the space again as in
10B-0 (the unadopt's read-only dispatch without `genie_pin` refuses at the pin
check and prints the new digest), then run the unadopt dispatches of the
rollback below with that digest. A change that lands at adopt-live's bundle
summary, after the binds, refuses `bindings_differ` (`the bundle summary
failed`): no record is written and the binds stay as acknowledged, so re-run
10B-2, or unadopt, each with a fresh `genie_pin`.

**Supersede** (the adopted record names an older deployment than the one the
App now serves, for example after the owner break-glass; `record_present` and
the pre-stop `rollback_record_drift` remedy print the selector): first
read-only, which prints the adopt plan's and the `supersede` plan's sha256,
then acked:

```bash
gh workflow run deploy-dev.yml --ref main -f client=<client> -f phase=adopt -f owner_attestation=<profile-ack> -f operation_acks="accepted_exposure_plan=<sha256> supersedes_deployment=<sha256> metastore_app_grants=<sha256>" -f acknowledge_single_maintainer_break_glass=true
gh workflow run deploy-dev.yml --ref main -f client=<client> -f phase=adopt -f owner_attestation=<profile-ack> -f operation_acks="accepted_exposure_plan=<sha256> supersedes_deployment=<sha256> adopt_plan=<sha256> supersede=<sha256> metastore_app_grants=<sha256>" -f acknowledge_single_maintainer_break_glass=true
```

**Rollback:** dispatch `phase=unadopt`, read-only first (it prints its unbind
plan's sha256 and adopt-live's `unadopt` plan sha256), then acked: the exact
unbind plus a tombstone record version. Nothing is deleted. Both carry
`metastore_app_grants` for the unadopt's pre-stop probes (S0(b′)).

```bash
gh workflow run deploy-dev.yml --ref main -f client=<client> -f phase=unadopt -f owner_attestation=<profile-ack> -f operation_acks="accepted_exposure_plan=<sha256> metastore_app_grants=<sha256>" -f acknowledge_single_maintainer_break_glass=true
gh workflow run deploy-dev.yml --ref main -f client=<client> -f phase=unadopt -f owner_attestation=<profile-ack> -f operation_acks="accepted_exposure_plan=<sha256> adopt_plan=<sha256> unadopt=<sha256> metastore_app_grants=<sha256>" -f acknowledge_single_maintainer_break_glass=true
```

While no signed record names the space (an adopt that stopped before
adopt-live signed, the bind's exit-3 undo included), the unadopt needs
`genie_pin` as 10B does: the step-0 pin check refuses without it, and its
unbind plan and unbinds bind the acked title match (B4(c)). Review a fresh pin
plan as in 10B-0 (a new one whenever a holder changed, as in the exit-3 case
above) and add its `genie_pin=<sha256>` to both dispatches, the acked one then
being:

```bash
gh workflow run deploy-dev.yml --ref main -f client=<client> -f phase=unadopt -f owner_attestation=<profile-ack> -f operation_acks="genie_pin=<sha256> accepted_exposure_plan=<sha256> adopt_plan=<sha256> unadopt=<sha256> metastore_app_grants=<sha256>" -f acknowledge_single_maintainer_break_glass=true
```

## Step 11. Plan #3

**Who:** CI. Two dispatches when the plan predicts a window, as run 1's does
(B6, fixed): the first prints the window's sha256, the second carries it.

```bash
gh workflow run deploy-dev.yml --ref main -f client=<client> -f phase=plan -f operation_acks="accepted_exposure_plan=<sha256> metastore_app_grants=<sha256>" -f acknowledge_single_maintainer_break_glass=true
gh workflow run deploy-dev.yml --ref main -f client=<client> -f phase=plan -f operation_acks="quiesce_window=<sha256> accepted_exposure_plan=<sha256> metastore_app_grants=<sha256>" -f acknowledge_single_maintainer_break_glass=true
```

The first plans the window and refuses `window_unacked` before the lease
(exit 2, read-only), printing `window sha256 <sha256>: acknowledge with
MIP_ACK_QUIESCE_WINDOW` with the window's reasons, predicted length and
admission forecast. Review them (below), then dispatch the second with that
digest: the same plan completes. A plan that predicts no window completes on
the first dispatch.

**Proof (the completing dispatch):** 0 refusals; blue is the adopted record; the bundle plan gate shows
0 delete and 0 recreate; the migration plan is a noop (inputs unchanged since
the seeded install, which needs `MIP_SEED_OPERATOR_EMAIL` set: otherwise the
plan applies a seed change, a contract WR3); the window plan states its reasons,
predicted length and budget admission, read as a forecast for a release
dispatched now (`admission (forecast for a release dispatched now) admitted`:
the plan job's own 30 minutes admit no window, and a plan opens none); the
EXECUTE-gap prediction is shown; the step summary prints the profile ack and
the active deployment's sha256.

**The window.** Plan for one window in run 1 and announce it. On the shape
spike S2 read, #337's set comparison predicts no WR1 (order and a binding's own
description are not reasons; the empty App description is fixed online), and
the jobs identity's `jobs_reader` Lakebase role, which no earlier run created,
makes it a WR2 (docs/deployment.md, WR2). A live App role that already holds
CREATE ON DATABASE is a WR2 too. Whatever reasons plan #3 prints are the ones
the release acks; a reason that appears only at the release's recheck
refuses. The release carries the same `quiesce_window` digest: its own
pre-lease plan and the recheck under the lease refuse any other.

**Refusals:** `window_unacked` (the first dispatch's expected stop; on the
second, the digest moved: dispatch again with the new one),
`window_budget_refused`, `window_migration_refused`,
`window_start_source_drift`, `window_journal_open`, `window_facts_incomplete`
(docs/deployment.md, "Keep blue serving"); `accepted_exposure` (the ack is
missing or names another plan: the refusal line names the exact digest);
`metastore_authority` (S0(b′): the attestation from 10B is missing, too old or
stale; take it again); `owner_policy` with `the metastore summary returned no
owner` (S0(b)'s worst case, stop). No Genie
pin refusal is expected: after 10B the adopted v7 record names the space the
App binds (`genie_space_id`), and the pin check uses it. A pin refusal here
(the record and a pin disagree, or the space is gone) means stop and read it.

**Rollback:** read-only.

## Step 12. Release (run 1): **blocked by B3**

**Who:** CI. **Preconditions:** step 11 clean; B3 fixed on `main`; the App
running; the Genie budget restored; the announcement made.

```bash
gh workflow run deploy-dev.yml --ref main -f client=<client> -f phase=release -f owner_attestation=<profile-ack> -f operation_acks="quiesce_window=<sha256> accepted_exposure_plan=<sha256> metastore_app_grants=<sha256>" -f expected_active_deployment_sha256=<sha256> -f acknowledge_single_maintainer_break_glass=true
```

Add `genie_pin=<sha256>` or `database_instance_update=<sha256>` to
`operation_acks` only when plan #3 printed them. `metastore_app_grants` is
the 10B attestation (S0(b′)): the pre-stop probe and the treatment converger
both read it, so a run that would start after its last accepted start needs a
fresh one. Never set
`rebase_unverified_app`: it stops an App that has no signed record, and this
App has one.

While blue serves, the run rechecks the plan, applies the bundle (names lose
the development prefix, run_as moves to the deployer and the jobs identity,
jobs stay PAUSED; the pipeline's datasets follow its run_as to the jobs
identity at once), writes the catalog marker, grants and lineage, publishes the
release, then opens the one admitted window with the dead-man armed, starts and
re-pins blue, and activates green in one rollover with a signed core capture.
The data jobs are deferred to step 14.

**B3:** the pre-activation proof refuses green activation with
`unverified_frontend_unacked` because the adopted frontend is unverified and no
dispatch input carries `restore_unverified_frontend`. Blue keeps serving, but
the run has already applied the bundle and taken any window the plan admitted.
Do not dispatch step 12 until the follow-up lands.

**Proof:** green SUCCEEDED; health `git_sha` equals the dispatched commit; the
env-name diff equals the plan's; jobs run as the planned identities and are
PAUSED; the runtime secrets and the pii-salt were not rewritten; observed
downtime equals the predicted window plus one rollover.

**Refusals:** the pre-stop probe codes (`[mip-prestop] REFUSED: <code>`, before
the lease, App untouched; `metastore_authority` is S0(b′), as in step 11, and
`owner_policy` with `the metastore summary returned no owner` is S0(b)'s worst
case; the release's treatment-access converger reads the metastore and the
attestation the same way); `window_unacked` (the digest moved: plan and ack
again); the release postflight's live run_as check (exit 4, before any grant,
when a jobs-identity job or the pipeline does not run as the jobs identity);
`no_rollback_proof`, `unverified_frontend_unacked` (B3).

**Rollback:** before activation, blue keeps serving (`blue_serving`). Inside
the window, the run starts and re-pins blue, or the dead-man restarts it at the
deadline and the next run re-pins it (`repin --from-deadman`). If the outage
cannot wait for the dead-man: the owner break-glass (docs/deployment.md, "Owner
break-glass"). After activation, the tier-b restore of the adopted copy from the
record.

## Step 13. Post-release verification

**Who:** an agent for the checks; the owner for the grants and the membership.

1. The owner grants the validator its read-only set ([onboarding.md](onboarding.md),
   step 8), while still a data-owners member.
2. Dispatch the per-client nightly and read the gate:

```bash
gh workflow run nightly.yml --ref main -f client=<client>
python -m tools.live_validation_gate --release-sha <sha> --client <client>
```

3. In the App: an evidence drawer citing real rows; one human approval writing
   a Lakebase audit row; one Genie turn answering live-first. Announce the App
   is live.
4. Prove the UC end state, then unset the pairs:

```bash
DATABRICKS_CONFIG_PROFILE=<owner-profile> python -m tools.clients.handover_uc_ownership --verify --released
```

   It passes only when every pipeline dataset in the governed catalog is owned
   by the jobs identity, which its pipeline runs as. Then unset
   `MIP_UC_INTERIM_OWNER_PAIRS_JSON`: remove it from `.env.local`, delete the
   environment secret by hand (`github_environment` names a secret the profile
   no longer sets and leaves it), and re-run `github_environment` so the
   recorded profile digest follows.
5. With the owner's OK, remove the legacy owner's interim membership of
   `<prefix>-data-owners` in the account console, then dispatch a plan and watch
   the next App-triggered lifecycle run (now as the jobs identity) with
   `handover_verify watch-next-run`.

**Proof:** the nightly is green and the gate accepts the commit; `--verify
--released` passes; after the membership removal the plan shows 0 refusals and
the lifecycle run SUCCEEDS.

**Refusals:** `pipeline_owner_unapproved`, `pipeline_owner_not_run_as`,
`pipeline_run_as_unreadable`, `pipeline_unresolved` (a pipeline dataset not
owned by the jobs identity: stop, the release did not move the run_as).

**Rollback:** re-add the membership; dispatch a release of the previous green
commit, or restore through the record.

## Step 14. Data phase

**Who:** CI.

```bash
gh workflow run deploy-dev.yml --ref main -f client=<client> -f phase=data -f owner_attestation=<profile-ack> -f acknowledge_single_maintainer_break_glass=true
```

**Proof:** job digests match the bundle; `mip_scheduled_refresh` SUCCEEDED and
its outcome gate passed; the reviewed-function grants are reconciled and
verified; the data smoke is green; a second dispatch re-attaches or skips.

**Refusals:** exit 4 (a job still running at the deadline: dispatch again);
a stale or rerun ledger (`data_ledger_supersede` / `data_ledger_rerun` digest
in `operation_acks`, docs/deployment.md "Governed run phases").

**Rollback:** the jobs are idempotent; gold swaps are atomic per table.

## Step 15. Refresh on, after seven green days

**Who:** the owner. Set `MIP_SCHEDULED_REFRESH=on` and
`MIP_SCHEDULED_REFRESH_RECIPIENTS` (email recipients only), re-run
`github_environment`, dispatch a plan, then a release with the new profile ack.

**Proof:** `mip_scheduled_refresh` is UNPAUSED as the jobs identity, with email
recipients only. **Rollback:** set it back to `off` and release.

## Step 16. Archive the legacy root

**Who:** the owner (or an agent with the owner's approval and profile).

It copies top-level children of the legacy root into
`<deployer root>/legacy-archive/<leaf>/` create-only and byte-verified, and
never deletes. Its protected paths are the legacy root's `state` folder (always:
`phase=unadopt` reads it) and every workspace path a resource the deployer's
state binds still uses (each dashboard's file, so the pinned dashboards'
folder; the App's default and deployed source folders; the paths a bound job,
pipeline or experiment names). A child that holds or lies inside a protected
path refuses (`bound_path`), and **one such child refuses the whole plan**, so
a run without `--only` (every child) always refuses. First take the inventory:

```bash
DATABRICKS_CONFIG_PROFILE=<owner-profile> python -m tools.clients.archive_legacy_root --report-out <private-dir>/archive-inventory.json
```

It exits 2 with `bound_path` lines, as expected; its private report lists
every child (`moves`, in order) and names each refused one by its position
(`move.<n>`). Then plan and apply with one `--only <name>` for each child that
is not protected: never `state`, the child holding `MIP_DASHBOARD_PARENT_PATH`,
or a child holding an App source folder, and, until a governed capture
supersedes the adopted record, not the legacy deployment's source folder
either. The apply must repeat the plan's exact `--only` set: the digest covers
the moves.

```bash
DATABRICKS_CONFIG_PROFILE=<owner-profile> python -m tools.clients.archive_legacy_root --only <child-a> --only <child-b> --report-out <private-dir>/archive-plan.json
DATABRICKS_CONFIG_PROFILE=<owner-profile> python -m tools.clients.archive_legacy_root --only <child-a> --only <child-b> --apply --ack-plan <sha256>
```

Removing the originals is the owner's, from the plan's list.

**Proof:** the narrowed plan exits 0 with no refusal; the apply reports
`archived: <n> copied, <m> already archived; nothing was deleted`; a re-run
of the same plan is still clean; the active deployment and the record are
unaffected. **Rollback:** nothing was moved.

## Step 17. Owner legacy deletions

**Who:** the owner; each permanent, so each needs the owner's own go-ahead:

- the retired `dev` and `copilot` GitHub environments;
- repository-level identity secrets and both retired `DATABRICKS_TOKEN`
  secrets (anything that still blocks `check-dispatch` went in step 4);
- the federation audience of the deleted staging workspace, and dangling
  metastore assignments of deleted workspaces;
- the partial legacy `app-src` folder and the temporary and proof service
  principals of the retired staging install;
- the historical public CI run logs that printed identifying values;
- the throwaway spike resources on the owner's private delete list.

**Proof:** each deleted object returns not-found; a plan is still clean.
**Rollback:** none.

## Step 18. Run 2 prerequisites (agentic tier; optional)

Not part of run 1. The agentic tier needs the owner's estate inventory, taken as
the metastore's direct owner (`tools/clients/estate_inventory.py`,
docs/deployment.md "Estate inventory"), `bootstrap_account --tier agentic`, and
the owners of other teams' `account users` and elevated Genie grants narrowing
them, or the tier stays off. **Not wired into `deploy-dev.yml` today:** the
inventory (`MIP_ESTATE_INVENTORY_JSON` or `_FILE`) and its `estate` ack are read
from the deploy process's own environment only, and no dispatch input carries
them.

## Step 19. Run 2 (optional)

A plan and a release with `MIP_AGENTIC_TIER=on`, once step 18's wiring exists.
Before activation the core green keeps serving; after it, tier b restores the
core record.
