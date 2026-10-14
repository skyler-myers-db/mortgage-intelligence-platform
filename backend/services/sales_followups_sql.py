"""The ONE shared open-item CTE of the Follow-ups tray (D-shell-deviations-a).

The rail's dot (``SUMMARY_SQL``), the tray (``LIST_SQL``) and the snooze
ownership check (``BORROWER_ITEM_SQL``) read the same resolution, so the dot,
the tray and the snooze can never disagree about what is open.

Candidates first: borrowers actively assigned to the actor, UNION borrowers
with an approve follow-up the actor decided (``lower(btrim(actor_email))``,
the one column stored as the raw forwarded header). Each candidate is then
resolved with LATERAL reads, so no query scans every disposition:

(i)   the active assignment (``released_at IS NULL``, at most one per borrower);
(ii)  the latest disposition (occurred_at, created_at, disposition_id DESC);
(iii) the latest approval (decided_at, approval_id DESC): any later approve,
      hold, reject or revoke supersedes an earlier follow-up;
(iv)  closed_by_outcome(t): a lead_outcomes row at or after t, or the active
      assignment moved to outcome_recorded at or after t.

Kinds, one row per (actor, borrower), in precedence order:

- callback: the latest disposition is callback_scheduled with a callback_at,
  the lead is actively assigned TO THE ACTOR (the owner is the active
  assignee, never the disposition's logger) and no outcome closed it;
- approval_follow_up: the latest approval is an approve with follow_up_at, no
  disposition was logged at or after the decision, no outcome closed it, and
  the active assignee is the actor;
- requested_follow_up: the same open follow-up decided by the actor, owned by
  someone else and lapsed (follow_up_at in the past), or routed but not yet
  assigned. ``approvals.assigned_to_email`` is never read: without an active
  assignment that person cannot log a disposition, so it would never resolve;
- new_assignment: an active assignment to the actor in status 'assigned' from
  the last 7 days.

A snooze hides an item only while it is open and unexpired and was made at or
after the item's ``since_at``: a newer callback, follow-up or assignment always
shows. The list returns hidden rows with ``snoozed`` set (the service
re-surfaces a hidden row that is withheld for a non-frequency-cap reason); the
summary and the ownership check exclude or ignore them as documented below.

Every value compared to the actor is stored lower-cased (sales_team.email,
lead_assignments.assigned_to_email, lead_snoozes.actor_email), so the actor is
bound once, lower-cased by the caller.
"""

from __future__ import annotations

from typing import Final

#: At most this many rows per kind reach the tray; the list fetches one more
#: to know whether it cut.
PER_KIND_CAP: Final = 50

#: Summary horizon: an item overdue by more than this never lights the dot.
SUMMARY_OVERDUE_HORIZON_DAYS: Final = 14

#: A new assignment stays in the tray for this many days.
NEW_ASSIGNMENT_DAYS: Final = 7

_OPEN_ITEMS_CTE: Final = """
WITH candidates AS (
    SELECT la.borrower_id
    FROM mip_app.lead_assignments AS la
    WHERE la.released_at IS NULL
      AND la.assigned_to_email = %(actor)s
      {borrower_filter_la}
    UNION
    SELECT ap.borrower_id
    FROM mip_app.approvals AS ap
    WHERE ap.action = 'approve'
      AND ap.follow_up_at IS NOT NULL
      AND lower(btrim(ap.actor_email)) = %(actor)s
      {borrower_filter_ap}
),
facts AS (
    SELECT
        c.borrower_id,
        act.assigned_to_email AS owner_email,
        act.assigned_at,
        act.status AS assignment_status,
        act.status_updated_at,
        owner_member.display_label AS owner_display_label,
        disp.outcome AS disposition_outcome,
        disp.callback_at,
        disp.occurred_at AS disposition_at,
        disp.attempt_number,
        appr.action AS approval_action,
        appr.follow_up_at,
        appr.decided_at,
        appr.requester
    FROM candidates AS c
    LEFT JOIN LATERAL (
        SELECT la.assigned_to_email, la.assigned_at, la.status, la.status_updated_at
        FROM mip_app.lead_assignments AS la
        WHERE la.borrower_id = c.borrower_id
          AND la.released_at IS NULL
        ORDER BY la.assigned_at DESC
        LIMIT 1
    ) AS act ON TRUE
    LEFT JOIN mip_app.sales_team AS owner_member
        ON owner_member.email = act.assigned_to_email
    LEFT JOIN LATERAL (
        SELECT d.outcome, d.callback_at, d.occurred_at, d.attempt_number
        FROM mip_app.call_dispositions AS d
        WHERE d.borrower_id = c.borrower_id
        ORDER BY d.occurred_at DESC, d.created_at DESC, d.disposition_id DESC
        LIMIT 1
    ) AS disp ON TRUE
    LEFT JOIN LATERAL (
        SELECT a.action, a.follow_up_at, a.decided_at,
               lower(btrim(a.actor_email)) AS requester
        FROM mip_app.approvals AS a
        WHERE a.borrower_id = c.borrower_id
        ORDER BY a.decided_at DESC, a.approval_id DESC
        LIMIT 1
    ) AS appr ON TRUE
),
open_flags AS (
    SELECT
        f.*,
        COALESCE(
            f.disposition_outcome = 'callback_scheduled'
            AND f.callback_at IS NOT NULL
            AND f.owner_email IS NOT NULL
            AND NOT EXISTS (
                SELECT 1 FROM mip_app.lead_outcomes AS o
                WHERE o.borrower_id = f.borrower_id
                  AND o.occurred_at >= f.disposition_at
            )
            AND NOT COALESCE(
                f.assignment_status = 'outcome_recorded'
                AND f.status_updated_at >= f.disposition_at,
                FALSE
            ),
            FALSE
        ) AS callback_open,
        COALESCE(
            f.approval_action = 'approve'
            AND f.follow_up_at IS NOT NULL
            AND (f.disposition_at IS NULL OR f.disposition_at < f.decided_at)
            AND NOT EXISTS (
                SELECT 1 FROM mip_app.lead_outcomes AS o
                WHERE o.borrower_id = f.borrower_id
                  AND o.occurred_at >= f.decided_at
            )
            AND NOT COALESCE(
                f.assignment_status = 'outcome_recorded'
                AND f.status_updated_at >= f.decided_at,
                FALSE
            ),
            FALSE
        ) AS approval_open
    FROM facts AS f
),
kinds AS (
    SELECT
        o.*,
        CASE
            WHEN o.callback_open AND o.owner_email = %(actor)s
                THEN 'callback'
            WHEN o.approval_open AND o.owner_email = %(actor)s
                THEN 'approval_follow_up'
            WHEN o.approval_open
                 AND o.requester = %(actor)s
                 AND o.owner_email IS DISTINCT FROM %(actor)s
                 AND (o.owner_email IS NULL OR o.follow_up_at < now())
                THEN 'requested_follow_up'
            WHEN o.owner_email = %(actor)s
                 AND o.assignment_status = 'assigned'
                 AND o.assigned_at >= now() - interval '{new_assignment_days} days'
                THEN 'new_assignment'
        END AS kind
    FROM open_flags AS o
),
shaped AS (
    SELECT
        k.borrower_id,
        k.kind,
        CASE k.kind
            WHEN 'callback' THEN k.callback_at
            WHEN 'new_assignment' THEN NULL
            ELSE k.follow_up_at
        END AS due_at,
        CASE k.kind
            WHEN 'callback' THEN k.disposition_at
            WHEN 'new_assignment' THEN k.assigned_at
            ELSE k.decided_at
        END AS since_at,
        CASE WHEN k.kind = 'callback' THEN k.attempt_number END AS attempt_number,
        CASE
            WHEN k.kind = 'requested_follow_up' AND k.owner_email IS NOT NULL
                THEN k.owner_display_label
        END AS owner_label,
        CASE
            WHEN k.kind = 'requested_follow_up' AND k.owner_email IS NULL THEN 'unassigned'
            WHEN k.kind = 'requested_follow_up' THEN 'assigned'
        END AS owner_state
    FROM kinds AS k
    WHERE k.kind IS NOT NULL
),
visible AS (
    SELECT
        s.*,
        EXISTS (
            SELECT 1 FROM mip_app.lead_snoozes AS z
            WHERE z.actor_email = %(actor)s
              AND z.borrower_id = s.borrower_id
              AND z.woken_at IS NULL
              AND z.wake_at > now()
              AND z.snoozed_at >= s.since_at
        ) AS snoozed,
        EXISTS (
            SELECT 1 FROM mip_app.lead_snoozes AS z
            WHERE z.actor_email = %(actor)s
              AND z.borrower_id = s.borrower_id
              AND z.wake_reason = 'signal'
              AND z.woken_at >= s.since_at
        ) AS woke_on_signal
    FROM shaped AS s
)
"""

_BORROWER_FILTER_LA: Final = "AND la.borrower_id = %(borrower_id)s"
_BORROWER_FILTER_AP: Final = "AND ap.borrower_id = %(borrower_id)s"


def _open_items_cte(*, borrower_scoped: bool) -> str:
    return _OPEN_ITEMS_CTE.format(
        borrower_filter_la=_BORROWER_FILTER_LA if borrower_scoped else "",
        borrower_filter_ap=_BORROWER_FILTER_AP if borrower_scoped else "",
        new_assignment_days=NEW_ASSIGNMENT_DAYS,
    )


#: The dot: the earliest due time over visible callbacks and follow-ups due
#: no more than 14 days ago. A snoozed item never lights it. Params: actor.
SUMMARY_SQL: Final = (
    _open_items_cte(borrower_scoped=False)
    + f"""
SELECT min(v.due_at) AS next_due_at
FROM visible AS v
WHERE v.kind IN ('callback', 'approval_follow_up', 'requested_follow_up')
  AND NOT v.snoozed
  AND v.due_at >= now() - interval '{SUMMARY_OVERDUE_HORIZON_DAYS} days'
"""
)

#: The tray: items due within the window (overdue of any age included) plus
#: new assignments, at most PER_KIND_CAP + 1 per kind, snoozed rows ranked
#: last and returned with their flag. Params: actor, window_days.
LIST_SQL: Final = (
    _open_items_cte(borrower_scoped=False)
    + f"""
SELECT ranked.*
FROM (
    SELECT
        v.*,
        ROW_NUMBER() OVER (
            PARTITION BY v.kind
            ORDER BY v.snoozed, v.due_at ASC NULLS LAST, v.since_at DESC, v.borrower_id
        ) AS kind_rank
    FROM visible AS v
    WHERE v.kind = 'new_assignment'
       OR v.due_at <= now() + (%(window_days)s::int * interval '1 day')
) AS ranked
WHERE ranked.kind_rank <= {PER_KIND_CAP + 1}
ORDER BY ranked.due_at ASC NULLS LAST, ranked.since_at DESC, ranked.borrower_id
"""
)

#: The snooze ownership check: the actor's open item for ONE borrower, of any
#: kind and whether or not it is snoozed. Params: actor, borrower_id.
BORROWER_ITEM_SQL: Final = (
    _open_items_cte(borrower_scoped=True)
    + """
SELECT v.kind, v.since_at
FROM visible AS v
LIMIT 1
"""
)
