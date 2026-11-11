# Review modes

A client profile names how its governed runs are reviewed:
`MIP_REVIEW_MODE` (a public field of the client profile,
`tools/clients/schema.py`). The mode decides the protections of the client's
two GitHub Environments, `<client>` and `<client>-validate`, and what the
dispatch preflight (`tools/clients/dispatch_preflight.py`) accepts before a
deploy job runs.

**Single-maintainer approval is not human-only review.** In the one mode the
code accepts today, the person who dispatches a run is the same person who
approves it, and an agent session acting on that person's delegated
credentials can do both. The approval is an audit trail, not a second pair of
eyes.

## The modes

| Mode | Status | Who approves a run | What the approval proves |
|---|---|---|---|
| `single_maintainer_audited` | **The only mode accepted.** Every client profile uses it today. | The one maintainer, the environment's one required reviewer, approving their own dispatch (`prevent_self_review=false`). | Who approved which run, and when, in GitHub's record. Nothing about independence. |
| `independent` | **Refused.** The profile schema refuses it ("independent needs the owner-attestation layer, which is deferred"), and so does the dispatch preflight (`approval_provenance`). | Would need a second, human-only GitHub identity and `prevent_self_review=true`. | Would add approval provenance: the run's approval came from a reviewer other than the dispatching actor. |

`independent` is modelled and refused, not built. It depends on the owner
hardware-key attestation layer (plan M1-PR5), which is **deferred**: no
hardware-key enrollment, no `allowed_signers` file, no trust-root guard and
no signature check exists in this repository. Nothing verifies a signature
anywhere in a governed run. The dispatch preflight reports the trust-root
chain as "deferred (M1-PR5)" and approval provenance as "not applicable
(single_maintainer_audited)".

## What `single_maintainer_audited` does gate

These are real checks, and each one refuses. None of them is a human-only
review.

- **Environment protections.** `tools/clients/github_environment.py`
  creates both environments with exactly: one required reviewer (the
  maintainer signed in to the tool's fresh `gh` session),
  `prevent_self_review=false`, `can_admins_bypass=false`, and one deployment
  branch policy, `main`. Every dispatch reads them back with the job's token
  and refuses drift (`environment_protections_drift`). When the token cannot
  read them (spike S8(a) is owed), a mutating phase refuses
  `environment_protections_unreadable` unless the dispatch carries the exact
  `protections_unverified_ack` the refusal prints, for that client,
  environment and commit only ([github-environment.md](github-environment.md)).
- **The break-glass acknowledgement.** Every `deploy-dev.yml` dispatch must set
  `acknowledge_single_maintainer_break_glass=true`; without it the run ends in
  its `refuse-unacknowledged` job and deploys nothing. The input records that
  the dispatcher knows the approval is a self-approval.
- **Exact digest acks.** Every attestation the plan names is replaced by an
  exact sha256 ack over a purpose-named body that a read-only plan printed
  (`tools/clients/dispatch_acks.py`, `tools/databricks/operation_ack.py`):
  the `profile` ack (`owner_attestation`) on every mutating phase, and the
  operation acks a tool printed (`adopt_plan`, `adopt_live`, `supersede`,
  `unadopt`, `quiesce_window`, `database_instance_update`, `genie_pin`,
  `accepted_exposure_plan`, `data_ledger_supersede`, `data_ledger_rerun`,
  and the selector `supersedes_deployment`, a digest of the superseded
  record's deployment id and never a switch), each admitted only in the
  phases that read it. The run recomputes each body
  right before it relies on it and refuses on any difference. An ack for one
  purpose never satisfies another. An ack binds the reviewed bytes; it does
  not prove that anyone other than the dispatcher reviewed them.
- **The human-run tools.** `bootstrap_account` and `github_environment` refuse
  under CI or without a terminal, open a fresh interactive login every run,
  and apply only the exact plan digest the operator reviewed
  ([bootstrap.md](bootstrap.md)).
- **Workflow shape.** Only a manual dispatch of `deploy-dev.yml` on `main`
  reaches a client environment (`workflow_event`, `workflow_ref`), and the
  deployer's federation policy trusts exactly the subject
  `repo:<owner>/<repo>:environment:<client>`.

## What it does not protect against

- **An agent with the maintainer's credentials.** It can dispatch, approve and
  supply every ack a tool printed. The digests stop a run from doing anything
  other than the reviewed plan; they do not stop an agent from reviewing its
  own plan.
- **A repository-admin token.** Its holder can change environment protections,
  disable branch protection (main has none today) or merge an unreviewed
  change. GitHub's audit log records it; nothing here prevents it.

If a lender requires separation of duties, single-maintainer approval does not
provide it. The route is the deferred owner-attestation layer (M1-PR5) plus
`independent` mode with a second human-only reviewer; until that is built, say
so to the lender plainly.

Related: [onboarding.md](onboarding.md),
[first-client-cutover.md](first-client-cutover.md), and
[docs/deployment.md](../deployment.md), "CI deploy (deploy-dev.yml): one
client environment per dispatch" (the dispatch preflight).
