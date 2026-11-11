"""Read-only: every pipeline dataset is owned by an approved principal its pipeline runs as (spike S4).

Spike S4 measured, on throwaway resources, that a pipeline-published streaming
table or materialized view can never change owner by itself: the REST owner
PATCH and every ``ALTER ... OWNER TO`` form are refused for any actor,
metastore owner included (``CHANGE_OWNER_NOT_ALLOWED_FOR_PIPELINE_TABLE``).
Its owner is always the pipeline's run_as identity, and it flipped every time
the pipeline's run_as setting changed, immediately and before any run. Grants
on the datasets survived every flip.

So the governed pipeline's datasets are owned by whoever the pipeline runs as:
the legacy identity until the release's bundle apply, the jobs identity
permanently after it (``MIP_IDENTITY_IDS_JSON.jobs``; a group cannot be a
run_as, so never the data-owner group). The handover plan counted one owned
by anyone but the legacy owner as ``other_owner`` and asserted nothing about
it. :func:`owner_codes` is that assertion, for ``handover_uc_ownership
--verify``. Because the platform keeps a dataset's owner equal to its
pipeline's run_as, "owned by its run_as" alone would pass whatever identity a
pipeline runs as; so the owner must also be approved:

* the jobs principal, permanently; or
* the legacy owner, only while the caller admits it (``interim_owner``): the
  window from the UC handover to the release, in which ``--verify`` also
  requires the dataset's exact interim owner pair. ``--verify --released``
  admits no interim owner, so its PASS is the end state: every pipeline
  dataset in the governed catalog is the jobs principal's, and its pipeline
  runs live as the jobs principal.

The pipeline's live identity is its ``run_as_user_name``, the field
tools/databricks/live_run_as.py reads for the release postflight. The GET is
made here, not through that helper, so the tools/clients SDK allowlist
(tests/unit/test_bootstrap_account_safety.py) sees every call.

Codes, value-free, one per refused dataset, in this order:

* ``pipeline_unresolved``: a dataset names no pipeline (neither its listing nor
  its own GET carries ``pipeline_id``);
* ``pipeline_run_as_unreadable``: its pipeline is gone or answers a blank
  run_as, so whom the dataset should belong to is unproven;
* ``pipeline_owner_not_run_as``: the dataset's owner is another principal than
  its pipeline's live run_as;
* ``pipeline_owner_unapproved``: the owner is its pipeline's run_as, but
  neither the jobs principal nor an admitted interim owner (another identity
  runs a pipeline that publishes into the governed catalog, or the release is
  undone).

One GET per distinct pipeline, and one table GET only for a dataset whose
listing lacks its pipeline id. Any error but not-found propagates.
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from typing import Any

from databricks.sdk.errors import NotFound

from tools.databricks.live_run_as import same_identity

UNRESOLVED = "pipeline_unresolved"
UNREADABLE = "pipeline_run_as_unreadable"
NOT_RUN_AS = "pipeline_owner_not_run_as"
UNAPPROVED = "pipeline_owner_unapproved"
CODES = (UNRESOLVED, UNREADABLE, NOT_RUN_AS, UNAPPROVED)


def _pipeline_id(workspace: Any, dataset: Mapping[str, str]) -> str:
    listed = str(dataset.get("pipeline_id") or "").strip()
    if listed:
        return listed
    try:
        table = workspace.tables.get(dataset["full_name"])
    except NotFound:
        return ""
    return str(getattr(table, "pipeline_id", "") or "").strip()


def _run_as(workspace: Any, pipeline_id: str) -> str:
    """The pipeline's effective ``run_as_user_name``; ``""`` when blank or the pipeline is gone."""

    try:
        pipeline = workspace.pipelines.get(pipeline_id=pipeline_id)
    except NotFound:
        return ""
    return str(getattr(pipeline, "run_as_user_name", "") or "").strip()


def owner_codes(
    workspace: Any,
    datasets: Iterable[Mapping[str, str]],
    *,
    jobs_principal: str,
    interim_owner: str = "",
) -> list[str]:
    """One value-free code per dataset not owned by an approved principal its pipeline runs as; read-only.

    ``jobs_principal`` is always approved; ``interim_owner`` (the legacy owner,
    before the release) only when given. A blank jobs principal refuses the
    whole check rather than approving nothing silently.
    """

    if not jobs_principal.strip():
        raise ValueError("the jobs principal is required")
    approved = [jobs_principal] + ([interim_owner] if interim_owner.strip() else [])
    identities: dict[str, str] = {}
    codes: list[str] = []
    for dataset in datasets:
        pipeline_id = _pipeline_id(workspace, dataset)
        if not pipeline_id:
            codes.append(UNRESOLVED)
            continue
        if pipeline_id not in identities:
            identities[pipeline_id] = _run_as(workspace, pipeline_id)
        identity = identities[pipeline_id]
        owner = str(dataset.get("owner") or "")
        if not identity:
            codes.append(UNREADABLE)
        elif not same_identity(owner, identity):
            codes.append(NOT_RUN_AS)
        elif not any(same_identity(owner, principal) for principal in approved):
            codes.append(UNAPPROVED)
    return codes


__all__ = ["CODES", "NOT_RUN_AS", "UNAPPROVED", "UNREADABLE", "UNRESOLVED", "owner_codes"]
