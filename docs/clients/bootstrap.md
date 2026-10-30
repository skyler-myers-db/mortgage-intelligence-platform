# Client account bootstrap

`python -m tools.clients.bootstrap_account` sets up one client's identities
before that client's first governed deploy. The client's account admin runs
it by hand from a terminal, never from CI. It works in a plan, review, ack
and apply loop: nothing changes until you hand it the exact digest of a plan
you have read.

> **Status.** The tool is built and tested against fake account and
> workspace clients only. A real `--apply` that would create, adopt or mint
> anything stops before its first mutation until the GitHub-environment
> credential sink lands (M2-PR2): the default sink refuses. `--plan` and
> `--verify` are read-only and work today.

## What it creates or adopts

It works in this order (plan §4.3). `<prefix>` is `MIP_IDENTITY_PREFIX`
(default `mip`), and `<client>` is `MIP_CLIENT`.

| # | Identity | What the bootstrap ensures |
|---|---|---|
| 1 | `<prefix>-deployer` | No OAuth secret, no PAT or OBO token, and exactly one federation policy: issuer GitHub Actions, subject `repo:<owner>/<repo>:environment:<client>`, the workspace OIDC audience. The policy is set **before** the workspace `ADMIN` assignment. |
| 2 | `<prefix>-jobs` | No secret, no policy, a workspace `USER` assignment. The deployer gets Service Principal User on it. |
| 3 | `<prefix>-data-owners` | An account group holding deployer and jobs, plus `--interim-member` if you give one. |
| 4 | `<prefix>-validator` | No secret. One federation policy with subject `…:environment:<client>-validate`. A `USER` assignment. |
| 5 | Reserved M2M identities | normal, operator2, admin and release-probe; with `--tier agentic` also verifier, runtime and proxy. Each gets a `USER` assignment. |
| 6 | Account principal | Service Principal Manager on the reserved M2M identities only. Never on deployer, jobs or validator, and never account admin. |
| 7 | Source grants | `USE_CATALOG` on the Cotality source catalog, and `USE_SCHEMA` and `SELECT` on its schema, for the data-owners group. If the source is not mounted yet, this step is deferred. |

**Pinned by application id.** `MIP_IDENTITY_IDS_JSON` records every identity
by application id. An identity it pins is the bootstrap's own.

- If an existing identity has an expected name but nothing pins it, the plan
  is refused.
- Check where that identity came from. Then name it with
  `--adopt <role>=<application id>`. For the group, use
  `--adopt data_owners=<group id>`.

After each create or adopt, the bootstrap hands the grown pin map to the
credential sink.

## Inputs

The tool reads the shell, then `.env.local`, or one file with
`--env-file <path> --env-file-only`. It uses the fields below, and keeps no
secret from those sources.

- **Public:** `MIP_CLIENT`, `MIP_CLOUD`, `MIP_IDENTITY_PREFIX`, and optionally
  `MIP_AGENTIC_TIER` (it must agree with `--tier`), `MIP_APP_NAME` (needed by
  `--post-first-install`), `MIP_COTALITY_SOURCE_CATALOG` and
  `MIP_COTALITY_SOURCE_SCHEMA`. When the last two are unset, the canonical
  names apply.
- **Identifying:** `DATABRICKS_HOST`, `MIP_EXPECT_WORKSPACE_ID`,
  `DATABRICKS_ACCOUNT_ID`, and optionally `MIP_EXPECT_METASTORE_ID`,
  `DATABRICKS_ACCOUNT_HOST` (derived from the cloud when unset) and
  `MIP_IDENTITY_IDS_JSON`. That last one may be absent or partial before the
  first run.

`--github-repository <owner>/<repo>` names the repository whose environments
the policies trust. It defaults to the git origin.

## The loop

Each run opens a fresh interactive login (U2M, in the browser) inside a new
private temporary directory:

- its own `DATABRICKS_CONFIG_FILE`;
- an empty token cache;
- no inherited `DATABRICKS_*` or cloud auth variables.

Your own `~/.databrickscfg` is never read, whatever profiles it holds. A
cached token in the session directory is refused. The directory is deleted
when the run ends.

```bash
# 1. Plan (read-only). Prints the steps, the canonical plan and its sha256.
python -m tools.clients.bootstrap_account --plan

# 2. Review the steps, then apply exactly that plan.
python -m tools.clients.bootstrap_account --apply --ack-plan <sha256 from step 1>

# 3. Re-check read-only at any time (exit 1 on drift, 2 on a refusal).
python -m tools.clients.bootstrap_account --verify
```

`--apply` recomputes the plan read-only first. It refuses, changing
nothing, if the digest differs by one byte: for example, if anything in the
account moved since you planned, or if you changed a flag or an input.

- After applying, it re-plans and reports `converged` when nothing remains.
- A second `--apply` makes zero mutations.
- A mutating action refuses when `GITHUB_ACTIONS` or `CI` is set, or when
  stdin is not a terminal.

Keep the same inputs and flags (`--tier`, `--adopt`, `--interim-member`,
`--github-repository`) between the plan and the apply. They are part of the
digest.

### Credentials: `--rotate-adopted`

```bash
python -m tools.clients.bootstrap_account --plan --for rotate-adopted
python -m tools.clients.bootstrap_account --rotate-adopted --ack-plan <sha256>
```

Every identity that holds a secret must already be pinned. That means the
reserved M2M identities and the account principal; deployer, jobs and
validator are never touched. For each such identity the tool:

1. mints one new secret;
2. hands it to the credential sink only;
3. confirms the sink stored it;
4. proves it authenticates (an account-level client-credentials token);
5. revokes every older secret the plan listed.

For a newly created identity this is its first secret, and nothing is
revoked.

If the sink or the proof fails, every older secret is kept. A secret minted
after you planned changes the digest, so the run refuses before minting.

The secret value never reaches stdout, stderr, a log, the plan or an error
message. The `databricks` loggers are held at WARNING for the session,
because the SDK's debug log does not redact a `secret` field.

### After the first install: `--post-first-install`

The first governed release creates the App, and with it the App's own
service principal. Once it exists, grant the account principal Service
Principal Manager on it:

```bash
python -m tools.clients.bootstrap_account --plan --for post-first-install
python -m tools.clients.bootstrap_account --post-first-install --ack-plan <sha256>
```

## What the plan prints

The plan names roles, actions, public display names, the federation
subjects, privileges and the source catalog. It prints no identifier: no
host, no account, workspace or metastore id, no application or SCIM id, no
email.

Those facts are folded into one `binding` sha256 inside the plan, so the ack
still pins them exactly.

## What it never does

- transfer metastore or object ownership;
- revoke a grant, remove a group member, or drop a rule-set entry;
- change an identity's roles, so it never grants account admin;
- lower a workspace permission;
- delete anything, except older secrets of a pinned M2M identity during
  `--rotate-adopted`.

Two kinds of tests pin this:

- `tests/unit/test_bootstrap_account_safety.py`: an AST allowlist of every
  SDK call the tool can make;
- every bootstrap test: a run-time call spy in the fake account.

When a precondition fails, the tool refuses and tells you what to fix by
hand. Examples: a deployer that holds a secret, a second federation policy,
or an account principal that holds account admin.

## Recovering from an interrupted apply

A failed step stops the run and names only the step and the exception
class. Re-run `--plan`; the bootstrap is idempotent.

Say a service principal was created but its pin never reached the sink.
The next plan refuses its name as "not pinned". Confirm it is the one this
run created, then pin it with `--adopt <role>=<application id>`.

## Deferred and pending

- **Owner hardware-key `mint` attestation (plan §4.2): deferred.** The
  review mode is `single_maintainer_audited`. Its stand-in is the exact
  plan-digest ack plus the human-presence checks above.
- **GitHub-environment sink (M2-PR2): pending.** Until it lands, `--apply`
  refuses any create, adopt or mint, and `--rotate-adopted` refuses outright.
- **`CREATE_CATALOG` on the metastore** for the deployer is not granted
  here. The first install needs it; it is a metastore-wide grant and stays
  an owner step.
