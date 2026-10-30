# Client GitHub Environments

`python -m tools.clients.github_environment` sets up one client's two GitHub
Environments in this repository and fills the deploy one from the client's
local profile. The repository's maintainer runs it by hand from a terminal,
never from CI. It works in the same plan, review, ack and apply loop as the
account bootstrap ([bootstrap.md](bootstrap.md)): nothing changes until you
hand it the exact digest of a plan you have read.

> **Status.** Built and tested against a fake `gh` only. No environment has
> been created by it yet; the CI workflows that would run in one are parked
> until M1-PR7.

## The two environments

| Environment | Used by | Holds |
|---|---|---|
| `<client>` | the deploy job (the deployer's federation subject `…:environment:<client>`) | the profile: public fields as variables, identifying fields and credentials as secrets |
| `<client>-validate` | the validator's federation subject `…:environment:<client>-validate` | protections only, for now; the validator job (M1-PR8) decides its values |

`<client>` is `MIP_CLIENT`. Both get exactly the protections of the review
mode `MIP_REVIEW_MODE` names:

| Protection | `single_maintainer_audited` |
|---|---|
| Required reviewers | one: the GitHub user signed in to the tool's fresh `gh` session |
| `prevent_self_review` | `false`: the one maintainer approves their own runs |
| `can_admins_bypass` | `false` |
| Deployment branches | custom branch policies, protected branches off, exactly one policy: the branch `main` |

**What the approval is.** In `single_maintainer_audited` mode the
environment approval is an audit trail, not an independent review: the same
person who dispatches a run approves it. The control is the record GitHub
keeps of who approved what.

`independent` (a second, human-only GitHub identity and
`prevent_self_review=true`) is modelled and refused: it needs the deferred
owner-attestation layer.

The plan converges drift: a missing reviewer, an admin bypass, a
self-review setting or an extra branch policy (another branch, or a tag)
becomes an update or a delete of that policy.

## What it writes into `<client>`

- **Variables:** every public profile field, `MIP_CLIENT` and
  `MIP_REVIEW_MODE` among them, set where missing or different.
- **Secrets:** every identifying profile field (hosts, account, workspace
  and metastore ids, principals, emails, the lender identity, accepted
  exposure), set **only where missing**. GitHub cannot read a secret back, so
  the tool cannot tell an old value from a new one; `--rotate` rewrites them
  all from the local profile. Before it moves `MIP_CLIENT_PROFILE_SHA256`
  past a secret it keeps, the plan proves that secret current: the
  environment's variables plus your local identifying values must hash to
  the digest the environment already records. If they do not (you changed an
  identifying value, re-pinned `MIP_IDENTITY_IDS_JSON`, or an apply stopped
  before the digest), the plan refuses and asks for `--rotate`. So the digest
  never vouches for a value the environment does not hold.
- **Signing keys:** `MIP_AI_GATEWAY_PROOF_SIGNING_KEY`, and with
  `MIP_AGENTIC_TIER=on` also `MIP_GATEWAY_MODEL_ATTESTATION_SIGNING_KEY`. Each
  is a fresh Ed25519 key, generated in memory and piped straight into the
  secret on stdin: it never touches disk or argv, and nobody sees it. Each is
  generated **once**. `--rotate` never replaces one, because a new key
  orphans every record the old one signed; rotate a proof key with the
  reviewed procedure in [deployment.md](../deployment.md) ("Per-run M2M
  identities").
- **Verify keys:** the public half of each generated key,
  `MIP_AI_GATEWAY_PROOF_VERIFY_KEY` and `MIP_GATEWAY_MODEL_ATTESTATION_VERIFY_KEY`,
  as a variable. Read-only jobs (the nightly) verify with it and never
  receive the private key. It is derived in the same step, exactly as the
  deploy derives it, and written **before** the key: GitHub never returns
  the private key, so no later run could derive it, and if the key's write
  fails the next plan generates both again. A key already set without its
  verify key is named in the notes, never regenerated.
- **`MIP_CLIENT_PROFILE_SHA256`:** the profile digest (`python -m
  tools.clients digest`) of the local profile, as a public variable. It is
  the stand-in for the deferred owner profile attestation: a deploy that
  resolves a different profile from the environment can refuse.

It never writes:

- a **runtime secret**: the Cotality id mask, the Genie action keys and the
  Salesforce credentials stay in the App's runtime secret scope,
  presence-only, and nothing here reads or imports them (attack G10);
- an **M2M or account-principal secret**: `bootstrap_account
  --rotate-adopted --sink github-environment` mints those and hands them
  over. A secret in your local file is dropped on read and never sent;
- **`MIP_IDENTITY_IDS_JSON`**: the bootstrap's sink owns it.

Until the whole local profile resolves (every public and identifying field,
with `MIP_IDENTITY_IDS_JSON` complete), the plan writes only the skeleton:
both environments and their protections, `MIP_CLIENT`, `MIP_REVIEW_MODE`,
the signing keys and their verify keys. Its notes name what is missing, by field.

The plan refuses an identifying field stored as a variable, or a public one
stored as a secret: the deploy's resolver would refuse it too. Delete it by
hand. It names, and leaves alone, a profile variable or identifying secret
your local profile no longer sets. The client-id names the M2M writer and
the bootstrap's sink store (`DATABRICKS_CLIENT_ID` and the others that alias
a `MIP_IDENTITY_IDS_JSON` role) are theirs, so the plan never calls them
stale. An alias variable such as `APP_ENV` counts as set by its canonical
field; the plan names it only when it disagrees, which the deploy refuses.

## The loop

```bash
# 1. Plan (read-only). Prints the steps, the canonical plan and its sha256.
python -m tools.clients.github_environment --plan

# 2. Review the steps, then apply exactly that plan.
python -m tools.clients.github_environment --apply --ack-plan <sha256 from step 1>
```

- `--apply` recomputes the plan read-only first and refuses, changing
  nothing, if the digest differs by one byte.
- After applying, it re-plans and reports `converged`, or exits 1 with the
  steps that remain.
- A second `--apply` makes zero mutations.
- `--rotate` is part of the plan: pass it to both `--plan` and `--apply`.
- `--github-repository <owner>/<repo>` names the repository; it defaults to
  the git origin.
- Inputs come from the shell, then `.env.local`, or one file with
  `--env-file <path> --env-file-only`.

**Human presence.** Every action, `--plan` too, refuses when
`GITHUB_ACTIONS` or `CI` is set, or when stdin is not a terminal, before it
reads any input.

**A fresh `gh` login, every run.** The tool never uses your own `gh`
login. Each run makes a new private temporary directory (mode 0700), points
`GH_CONFIG_DIR` and `HOME` into it and asks you to `gh auth login` there in
the browser. The token is stored in that directory (`--insecure-storage`), not
your keychain, and is deleted with it: when the run ends, and also when you
close the terminal (SIGHUP) or the run is terminated (SIGTERM). A run that
is killed outright (SIGKILL, a crash) cannot clean up, so each new run first
deletes your session directories whose process is gone; a live run's is
never touched. If a directory survives its deletion, the run names it on
stderr: delete it by hand. Every `GH_*` and `GITHUB_*` variable
in your shell (`GH_TOKEN`, `GITHUB_TOKEN`, `GH_DEBUG`, …) is removed from
every `gh` call. Every call names `--env` and never `--repo`: the repository
reaches `gh` through `GH_REPO`. Every value reaches `gh` on stdin, never on
the command line.

## Onboarding order

1. `python -m tools.clients.github_environment --plan`, then `--apply`: the
   environments, their protections and the signing keys (the skeleton is
   enough here).
2. `python -m tools.clients.bootstrap_account --plan`, then `--apply --sink
   github-environment --ack-plan <sha256>`: the identities, pinned into
   `MIP_IDENTITY_IDS_JSON`. The sink refuses unless `<client>` is protected
   exactly as its review mode requires.
3. `bootstrap_account --plan --for rotate-adopted`, then `--rotate-adopted
   --sink github-environment --ack-plan <sha256>`: each M2M and
   account-principal secret, minted and handed to `<client>` only.
4. With the whole profile in your local file, including
   `MIP_IDENTITY_IDS_JSON`, run `github_environment` again: the profile's
   values and `MIP_CLIENT_PROFILE_SHA256`.

## What the plan prints

Environments, review mode, protections, field names, public values and
policy ids. Never an identifying value, a credential, a key or the
maintainer's GitHub id. The identifying values and the id are folded into
one `binding` sha256, so the ack pins the values a `set_secret` step writes
and the one reviewer the plan installs: an `--apply` under another gh login
refuses the digest you reviewed.

The binding covers **every** identifying value of the resolved profile, not
only those the plan writes. So a plan that writes one guessable secret (a
host, an email) never prints a hash of that value alone: it is always hashed
beside the metastore and account UUIDs. Before the profile resolves (the
skeleton), the binding covers the maintainer's id only, and an id can be
recovered from it by enumeration. That hides nothing: GitHub returns any
user's id from their public login.

## Deferred

- **Owner hardware-key `mint` attestation and `MIP_OWNER_PROFILE_ATTESTATION`
  (plan §4.2): deferred.** The stand-ins are the exact plan-digest ack, the
  public `MIP_CLIENT_PROFILE_SHA256` variable and the human-presence checks.
- **The `independent` review mode:** refused until that layer exists.
