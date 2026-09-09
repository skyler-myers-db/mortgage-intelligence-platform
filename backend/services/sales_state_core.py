"""Read side of the sales lifecycle store: scope guards, Lakebase readers, and
the shared audit-event insert."""

from __future__ import annotations

from typing import Any, cast

from backend.schemas.sales import (
    CallDisposition,
    LeadAssignment,
    LeadOutcome,
    LeadOutcomeSourceSystem,
    SalesTeamMember,
)
from backend.services.audit_lakebase_store import _build_insert_params
from backend.services.lakebase import LakebaseClient, LakebaseError
from backend.services.sales_state_mappers import (
    _CONFIGURED_OUTCOME_SOURCE_STATUSES,
    _OUTCOME_SOURCE_LABELS,
    _OUTCOME_SOURCE_STATUS_BY_TYPE,
    _OUTCOME_SOURCE_STATUS_LIST,
    _OUTCOME_SOURCE_SYSTEMS,
    _SALES_AUDIT_INSERT_SQL,
    _assignment_from_row,
    _cache_get,
    _cache_set,
    _copy_assignment,
    _copy_disposition,
    _copy_member,
    _disposition_from_row,
    _outcome_from_row,
    get_sales_lakebase,
)


class _SalesStateCore:
    """Construction, scope guards, and read paths shared by every store mixin."""

    def __init__(self, client: LakebaseClient | None = None) -> None:
        self._client = client or get_sales_lakebase()

    def list_team(
        self,
        *,
        actor: str | None = None,
        use_cache: bool = False,
    ) -> list[SalesTeamMember]:
        cache_key = f"sales_state:list_team:{actor or '_all'}"
        if use_cache:
            cached = _cache_get(cache_key)
            if isinstance(cached, list) and all(
                isinstance(member, SalesTeamMember) for member in cached
            ):
                return [member.model_copy(deep=True) for member in cached]
        actor_member = (
            self.require_active_team_member(actor, use_cache=use_cache) if actor else None
        )
        rows = self._client.fetchall(
            """
            SELECT email, display_label, role, region, manager_email, capacity_per_day, active
            FROM mip_app.sales_team
            WHERE active = true
            ORDER BY role DESC, display_label ASC
            """,
            limit=200,
        )
        members = [SalesTeamMember(**row) for row in rows]
        if actor_member is None or actor_member.role == "admin":
            out = members
        elif actor_member.role == "sales_manager":
            out = [
                member
                for member in members
                if member.email == actor_member.email or member.manager_email == actor_member.email
            ]
        else:
            out = [member for member in members if member.email == actor_member.email]
        if use_cache:
            _cache_set(cache_key, out)
        return [member.model_copy(deep=True) for member in out]

    def require_active_team_member(self, email: str, *, use_cache: bool = False) -> SalesTeamMember:
        normalized_email = email.lower()
        cache_key = f"sales_state:team_member:{normalized_email}"
        if use_cache:
            cached = _cache_get(cache_key)
            if cached == "__missing__":
                raise KeyError(email)
            if isinstance(cached, SalesTeamMember):
                return _copy_member(cached)
        row = self._client.fetchone(
            """
            SELECT email, display_label, role, region, manager_email, capacity_per_day, active
            FROM mip_app.sales_team
            WHERE email = %(email)s AND active = true
            LIMIT 1
            """,
            {"email": normalized_email},
        )
        if row is None:
            if use_cache:
                _cache_set(cache_key, "__missing__")
            raise KeyError(email)
        member = SalesTeamMember(**row)
        if use_cache:
            _cache_set(cache_key, member)
        return _copy_member(member)

    def require_manager_actor(self, actor: str, *, use_cache: bool = False) -> SalesTeamMember:
        member = self.require_active_team_member(actor, use_cache=use_cache)
        if member.role not in {"admin", "sales_manager"}:
            raise PermissionError("sales manager or admin access required")
        return member

    def require_assignee_in_scope(
        self,
        *,
        actor: str,
        assigned_to_email: str,
        use_cache: bool = False,
    ) -> SalesTeamMember:
        actor_member = self.require_manager_actor(actor, use_cache=use_cache)
        assignee = self.require_active_team_member(assigned_to_email, use_cache=use_cache)
        if assignee.role != "loan_officer":
            raise KeyError(assigned_to_email)
        if actor_member.role != "admin" and assignee.manager_email != actor_member.email:
            raise PermissionError("loan officer is outside the manager scope")
        return assignee

    def require_visible_assignee(
        self,
        *,
        actor: str,
        assigned_to_email: str,
        use_cache: bool = False,
    ) -> SalesTeamMember:
        actor_member = self.require_active_team_member(actor, use_cache=use_cache)
        assignee = self.require_active_team_member(assigned_to_email, use_cache=use_cache)
        if assignee.role != "loan_officer":
            raise KeyError(assigned_to_email)
        if actor_member.role == "admin":
            return assignee
        if actor_member.role == "sales_manager" and assignee.manager_email == actor_member.email:
            return assignee
        if actor_member.role == "loan_officer" and assignee.email == actor_member.email:
            return assignee
        raise PermissionError("assigned_to is outside the actor scope")

    def require_disposition_scope(
        self,
        *,
        actor: str,
        lo_email: str,
        use_cache: bool = False,
    ) -> SalesTeamMember:
        actor_member = self.require_active_team_member(actor, use_cache=use_cache)
        lo = self.require_active_team_member(lo_email, use_cache=use_cache)
        if lo.role != "loan_officer":
            raise KeyError(lo_email)
        if actor_member.role == "admin":
            return lo
        if actor_member.role == "sales_manager" and lo.manager_email == actor_member.email:
            return lo
        if actor_member.role == "loan_officer" and lo.email == actor_member.email:
            return lo
        raise PermissionError("cannot log a disposition for another loan officer")

    def require_outcome_scope(
        self,
        *,
        actor: str,
        borrower_id: str,
        assigned_to_email: str | None,
    ) -> SalesTeamMember | None:
        actor_member = self.require_manager_actor(actor, use_cache=False)
        supplied_assignee = (
            self.require_visible_assignee(
                actor=actor,
                assigned_to_email=assigned_to_email,
                use_cache=False,
            )
            if assigned_to_email is not None
            else None
        )
        if actor_member.role == "admin":
            return supplied_assignee

        assignment = self.active_assignment_for(borrower_id, use_cache=False)
        if assignment is None:
            raise PermissionError("lead outcome requires an in-scope active assignment")

        try:
            active_assignee = self.require_visible_assignee(
                actor=actor,
                assigned_to_email=assignment.assigned_to_email,
                use_cache=False,
            )
        except KeyError as exc:
            raise PermissionError(
                "lead outcome active assignment is no longer assigned to an active loan officer"
            ) from exc
        if supplied_assignee is not None and supplied_assignee.email != active_assignee.email:
            raise PermissionError("lead outcome assigned_to must match the active assignment")
        return active_assignee

    def visible_lo_emails(self, *, actor: str, use_cache: bool = False) -> set[str] | None:
        """Return LO emails visible to actor; None means admin/all."""
        cache_key = f"sales_state:visible_lo_emails:{actor.lower()}"
        if use_cache:
            cached = _cache_get(cache_key)
            if cached == "__all__":
                return None
            if isinstance(cached, set):
                return set(cached)
        actor_member = self.require_active_team_member(actor, use_cache=use_cache)
        if actor_member.role == "admin":
            if use_cache:
                _cache_set(cache_key, "__all__")
            return None
        if actor_member.role == "loan_officer":
            out = {actor_member.email}
            if use_cache:
                _cache_set(cache_key, out)
            return set(out)
        members = self.list_team(actor=actor, use_cache=use_cache)
        out = {member.email for member in members if member.role == "loan_officer"}
        if use_cache:
            _cache_set(cache_key, out)
        return set(out)

    def _insert_audit_event(
        self,
        cur: Any,
        *,
        actor: str,
        action: str,
        entity_type: str,
        entity_id: str,
        payload_json: dict[str, Any],
        event_type: str,
        subject_clip: str | None = None,
        request_id: str | None = None,
    ) -> str:
        _, params = _build_insert_params(
            actor=actor,
            action=action,
            entity_type=entity_type,
            entity_id=entity_id,
            payload_json=payload_json,
            event_type=event_type,
            subject_clip=subject_clip,
            request_id=request_id,
        )
        cur.execute(_SALES_AUDIT_INSERT_SQL, params)
        row = dict(cur.fetchone() or {})
        audit_id = row.get("audit_id")
        if audit_id is None:
            raise LakebaseError("sales audit insert returned no row")
        return str(audit_id)

    def active_assignment_for(
        self, borrower_id: str, *, use_cache: bool = False
    ) -> LeadAssignment | None:
        cache_key = f"sales_state:active_assignment:{borrower_id}"
        if use_cache:
            cached = _cache_get(cache_key)
            if cached == "__none__":
                return None
            if isinstance(cached, LeadAssignment):
                return cached.model_copy(deep=True)
        assignment = _assignment_from_row(
            self._client.fetchone(
                """
                SELECT a.assignment_id, a.borrower_id, a.assigned_to_email,
                       t.display_label AS assigned_to_label, a.assigned_by,
                       a.assigned_at, a.expires_at, a.released_at, a.strategy,
                       COALESCE(a.status, 'assigned') AS status
                FROM mip_app.lead_assignments a
                LEFT JOIN mip_app.sales_team t ON t.email = a.assigned_to_email
                WHERE a.borrower_id = %(borrower_id)s
                  AND a.released_at IS NULL
                ORDER BY a.assigned_at DESC
                LIMIT 1
                """,
                {"borrower_id": borrower_id},
            )
        )
        if use_cache:
            _cache_set(cache_key, assignment if assignment is not None else "__none__")
        return _copy_assignment(assignment)

    def latest_disposition_for(self, borrower_id: str) -> CallDisposition | None:
        cache_key = f"sales_state:latest_disposition:{borrower_id}"
        cached = _cache_get(cache_key)
        if cached == "__none__":
            return None
        if isinstance(cached, CallDisposition):
            return cached.model_copy(deep=True)
        disposition = _disposition_from_row(
            self._client.fetchone(
                """
                SELECT disposition_id, borrower_id, lo_email, outcome, attempt_number,
                       occurred_at, callback_at, notes, audit_event_id
                FROM mip_app.call_dispositions
                WHERE borrower_id = %(borrower_id)s
                ORDER BY occurred_at DESC, created_at DESC, disposition_id::text DESC
                LIMIT 1
                """,
                {"borrower_id": borrower_id},
            )
        )
        _cache_set(cache_key, disposition if disposition is not None else "__none__")
        return _copy_disposition(disposition)

    def assignments_for(self, borrower_ids: list[str]) -> dict[str, LeadAssignment]:
        if not borrower_ids:
            return {}
        normalized = sorted({str(borrower_id) for borrower_id in borrower_ids})
        cache_key = "sales_state:assignments:" + ",".join(normalized)
        cached = _cache_get(cache_key)
        if isinstance(cached, dict) and all(isinstance(v, LeadAssignment) for v in cached.values()):
            return {str(k): v.model_copy(deep=True) for k, v in cached.items()}
        rows = self._client.fetchall(
            """
            SELECT a.assignment_id, a.borrower_id, a.assigned_to_email,
                   t.display_label AS assigned_to_label, a.assigned_by,
                   a.assigned_at, a.expires_at, a.released_at, a.strategy,
                   COALESCE(a.status, 'assigned') AS status
            FROM mip_app.lead_assignments a
            LEFT JOIN mip_app.sales_team t ON t.email = a.assigned_to_email
            WHERE a.borrower_id = ANY(%(borrower_ids)s)
              AND a.released_at IS NULL
            ORDER BY a.borrower_id, a.assigned_at DESC
            """,
            {"borrower_ids": normalized},
            limit=max(len(normalized), 1),
        )
        out: dict[str, LeadAssignment] = {}
        for row in rows:
            borrower_id = str(row["borrower_id"])
            out.setdefault(borrower_id, _assignment_from_row(row))  # type: ignore[arg-type]
        result = {k: v for k, v in out.items() if v is not None}
        _cache_set(cache_key, result)
        return {k: v.model_copy(deep=True) for k, v in result.items()}

    def assignments_for_request(self, request_id: str | None) -> list[LeadAssignment]:
        return [
            assignment
            for row in self.assignment_rows_for_request(request_id)
            if (assignment := _assignment_from_row(row)) is not None
        ]

    def assignment_rows_for_request(self, request_id: str | None) -> list[dict[str, Any]]:
        if not request_id:
            return []
        return self._client.fetchall(
            """
            SELECT a.assignment_id, a.borrower_id, a.assigned_to_email,
                   t.display_label AS assigned_to_label, a.assigned_by,
                   a.assigned_at, a.expires_at, a.released_at, a.strategy,
                   COALESCE(a.assignment_scope, 'single') AS assignment_scope,
                   COALESCE(a.status, 'assigned') AS status
            FROM mip_app.lead_assignments a
            LEFT JOIN mip_app.sales_team t ON t.email = a.assigned_to_email
            WHERE a.request_id = %(request_id)s
            ORDER BY a.assigned_at ASC
            """,
            {"request_id": request_id},
            limit=500,
        )

    def disposition_for_request(self, request_id: str | None) -> CallDisposition | None:
        if not request_id:
            return None
        return _disposition_from_row(
            self._client.fetchone(
                """
                SELECT disposition_id, borrower_id, lo_email, outcome, attempt_number,
                       occurred_at, callback_at, notes, audit_event_id
                FROM mip_app.call_dispositions
                WHERE request_id = %(request_id)s
                LIMIT 1
                """,
                {"request_id": request_id},
            )
        )

    def outcome_for_request(self, request_id: str | None) -> LeadOutcome | None:
        if not request_id:
            return None
        return _outcome_from_row(
            self._client.fetchone(
                """
                SELECT outcome_id, borrower_id, outcome_type, source_system,
                       source_record_ref, assigned_to_email, campaign_id,
                       loan_amount, competitor_lender_label, occurred_at,
                       request_id, audit_event_id, created_at
                FROM mip_app.lead_outcomes
                WHERE request_id = %(request_id)s
                LIMIT 1
                """,
                {"request_id": request_id},
            )
        )

    def outcome_for_source_record(
        self,
        *,
        source_system: LeadOutcomeSourceSystem,
        source_record_ref: str | None,
    ) -> LeadOutcome | None:
        if not source_record_ref:
            return None
        return _outcome_from_row(
            self._client.fetchone(
                """
                SELECT outcome_id, borrower_id, outcome_type, source_system,
                       source_record_ref, assigned_to_email, campaign_id,
                       loan_amount, competitor_lender_label, occurred_at,
                       request_id, audit_event_id, created_at
                FROM mip_app.lead_outcomes
                WHERE source_system = %(source_system)s
                  AND source_record_ref = %(source_record_ref)s
                LIMIT 1
                """,
                {"source_system": source_system, "source_record_ref": source_record_ref},
            )
        )

    def outcome_source_status(self, source_system: LeadOutcomeSourceSystem) -> dict[str, Any]:
        if source_system == "manual_import":
            return {
                "source_system": source_system,
                "display_name": _OUTCOME_SOURCE_LABELS[source_system],
                "status": "available",
                "configured": True,
            }
        row = self._client.fetchone(
            _OUTCOME_SOURCE_STATUS_BY_TYPE, {"source_system": source_system}
        )
        status = str(row.get("status") if row else "not_configured")
        return {
            "source_system": source_system,
            "display_name": str(
                row.get("display_name")
                if row
                else _OUTCOME_SOURCE_LABELS.get(source_system, source_system)
            ),
            "status": status,
            "configured": status in _CONFIGURED_OUTCOME_SOURCE_STATUSES,
        }

    def outcome_source_statuses(self) -> list[dict[str, Any]]:
        rows = self._client.fetchall(_OUTCOME_SOURCE_STATUS_LIST, limit=25)
        by_source = {str(row.get("source_system")): dict(row) for row in rows}
        out: list[dict[str, Any]] = []
        for source_system in _OUTCOME_SOURCE_SYSTEMS:
            if source_system == "manual_import":
                out.append(
                    {
                        "source_system": source_system,
                        "display_name": _OUTCOME_SOURCE_LABELS[source_system],
                        "status": "available",
                        "configured": True,
                    }
                )
                continue
            row = by_source.get(source_system)
            status = str(row.get("status") if row else "not_configured")
            out.append(
                {
                    "source_system": source_system,
                    "display_name": str(
                        row.get("display_name")
                        if row
                        else _OUTCOME_SOURCE_LABELS.get(source_system, source_system)
                    ),
                    "status": status,
                    "configured": status in _CONFIGURED_OUTCOME_SOURCE_STATUSES,
                }
            )
        return out

    def _require_configured_outcome_source(
        self, source_system: LeadOutcomeSourceSystem
    ) -> dict[str, Any]:
        status = self.outcome_source_status(source_system)
        if not status["configured"]:
            raise ValueError(f"{status['display_name']} outcome feed is not configured")
        return status

    def latest_dispositions_for(self, borrower_ids: list[str]) -> dict[str, CallDisposition]:
        if not borrower_ids:
            return {}
        normalized = sorted({str(borrower_id) for borrower_id in borrower_ids})
        cache_key = "sales_state:latest_dispositions:" + ",".join(normalized)
        cached = _cache_get(cache_key)
        if isinstance(cached, dict) and all(
            isinstance(v, CallDisposition) for v in cached.values()
        ):
            return {str(k): v.model_copy(deep=True) for k, v in cached.items()}
        rows = self._client.fetchall(
            """
            SELECT DISTINCT ON (borrower_id)
                   disposition_id, borrower_id, lo_email, outcome, attempt_number,
                   occurred_at, callback_at, notes, audit_event_id
            FROM mip_app.call_dispositions
            WHERE borrower_id = ANY(%(borrower_ids)s)
            ORDER BY borrower_id, occurred_at DESC, created_at DESC,
                     disposition_id::text DESC
            """,
            {"borrower_ids": normalized},
            limit=max(len(normalized), 1),
        )
        result = {
            str(row["borrower_id"]): _disposition_from_row(row)
            for row in rows
            if row.get("borrower_id")
        }
        result = {k: v for k, v in result.items() if v is not None}
        _cache_set(cache_key, result)
        return {k: cast(CallDisposition, v).model_copy(deep=True) for k, v in result.items()}

    def borrower_ids_for_assignee(self, assigned_to_email: str | None) -> list[str] | None:
        if assigned_to_email is None:
            return None
        rows = self._client.fetchall(
            """
            SELECT borrower_id
            FROM mip_app.lead_assignments
            WHERE assigned_to_email = %(assigned_to_email)s
              AND released_at IS NULL
            ORDER BY assigned_at DESC
            LIMIT 500
            """,
            {"assigned_to_email": assigned_to_email.lower()},
            limit=500,
        )
        return [str(row["borrower_id"]) for row in rows]
