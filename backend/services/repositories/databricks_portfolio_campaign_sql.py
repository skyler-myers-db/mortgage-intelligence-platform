"""Campaign persistence SQL for the Databricks portfolio repository."""

from __future__ import annotations

from backend.services.repositories.databricks_portfolio_campaign_mappers import (
    _NORMALIZED_CAMPAIGN_VARIANTS_SQL,
)


class _PortfolioCampaignSql:
    """The campaign INSERT / lookup / list / get / PATCH statements."""

    _CAMPAIGN_INSERT_SQL = """
    WITH inserted_campaign AS (
      INSERT INTO mip_app.campaigns (
        name, owner_email, status, criteria, suppression_policy,
        message_variants, channel_cascade, send_window, holdout,
        roi_assumptions, household_dedup, household_summary,
        idempotency_key, request_payload_hash, creation_response, updated_at
      )
      VALUES (
        %(name)s, %(owner_email)s, 'draft', %(criteria)s::jsonb,
        %(suppression_policy)s::jsonb, %(message_variants)s::jsonb,
        %(channel_cascade)s::jsonb, %(send_window)s::jsonb,
        %(holdout)s::jsonb, %(roi_assumptions)s::jsonb,
        %(household_dedup)s::jsonb, %(household_summary)s::jsonb,
        %(idempotency_key)s, %(request_payload_hash)s,
        %(creation_response)s::jsonb, now()
      )
      ON CONFLICT (owner_email, idempotency_key)
        WHERE idempotency_key IS NOT NULL
      DO NOTHING
      RETURNING campaign_id, request_payload_hash, creation_response
    ),
    inserted_audit AS (
      INSERT INTO mip_app.action_audit (
        event_type, actor_email, entity_type, entity_id,
        request_id, correlation_id, evidence_ids, metadata
      )
      SELECT
        'PORTFOLIO_CREATE',
        %(owner_email)s,
        'campaign',
        inserted_campaign.campaign_id::text,
        %(request_id)s,
        %(correlation_id)s,
        ARRAY[]::TEXT[],
        %(metadata)s::jsonb
      FROM inserted_campaign
      RETURNING audit_id
    ),
    inserted_variants AS (
      INSERT INTO mip_app.campaign_message_variants (
        campaign_id, variant_name, channel, subject, body, weight_pct,
        generation_mode, generator_label, provenance_key_id,
        provenance_issued_at, provenance_expires_at, provenance_copy_hash,
        provenance_criteria_fingerprint, provenance_performance_fingerprint,
        provenance_token_digest
      )
      SELECT
        inserted_campaign.campaign_id,
        variant.variant_name,
        variant.channel,
        variant.subject,
        variant.body,
        variant.weight_pct,
        variant.generation_mode,
        variant.generator_label,
        variant.provenance_key_id,
        variant.provenance_issued_at,
        variant.provenance_expires_at,
        variant.provenance_copy_hash,
        variant.provenance_criteria_fingerprint,
        variant.provenance_performance_fingerprint,
        variant.provenance_token_digest
      FROM inserted_campaign
      CROSS JOIN jsonb_to_recordset(%(variant_rows)s::jsonb) AS variant(
        variant_name TEXT,
        channel TEXT,
        subject TEXT,
        body TEXT,
        weight_pct NUMERIC,
        generation_mode TEXT,
        generator_label TEXT,
        provenance_key_id TEXT,
        provenance_issued_at TIMESTAMPTZ,
        provenance_expires_at TIMESTAMPTZ,
        provenance_copy_hash TEXT,
        provenance_criteria_fingerprint TEXT,
        provenance_performance_fingerprint TEXT,
        provenance_token_digest TEXT
      )
      RETURNING campaign_id
    )
    SELECT
      inserted_campaign.campaign_id,
      inserted_audit.audit_id,
      inserted_campaign.request_payload_hash,
      inserted_campaign.creation_response,
      TRUE AS created,
      (SELECT COUNT(*) FROM inserted_variants) AS variant_count
    FROM inserted_campaign
    LEFT JOIN inserted_audit ON TRUE
    """

    _CAMPAIGN_IDEMPOTENCY_LOOKUP_SQL = """
    SELECT
      c.campaign_id,
      c.request_payload_hash,
      c.creation_response,
      (
        SELECT a.audit_id
        FROM mip_app.action_audit a
        WHERE a.entity_type = 'campaign'
          AND a.entity_id = c.campaign_id::text
          AND a.event_type = 'PORTFOLIO_CREATE'
        ORDER BY a.event_at ASC
        LIMIT 1
      ) AS audit_id
    FROM mip_app.campaigns c
    WHERE c.owner_email = %(owner_email)s
      AND c.idempotency_key = %(idempotency_key)s
    LIMIT 1
    """

    # Re-audit #4 (2026-06-12): 'archived' is the "hide from the product"
    # status, but the default listing showed every status — so a governed
    # PATCH-to-archived (which sets updated_at=now()) bumped the archived
    # row to the TOP of Saved Campaigns instead of removing it. Default
    # listing now excludes archived; an explicit status='archived' query
    # still returns them (admin/audit). This is also what makes the booth
    # Saved Campaigns panel show only real builds after dev detritus
    # (load-test + Genie-draft rows) is archived.
    _CAMPAIGN_LIST_SQL = f"""
    SELECT c.campaign_id::text, c.name, c.owner_email, c.status, c.json_contract_version,
           c.treatment_state,
           c.criteria,
           c.suppression_policy, c.message_variants AS legacy_message_variants,
           {_NORMALIZED_CAMPAIGN_VARIANTS_SQL.format(campaign_id_ref="c.campaign_id")},
           c.channel_cascade, c.send_window, c.holdout, c.roi_assumptions,
           c.household_dedup, c.household_summary, c.created_at, c.updated_at
    FROM mip_app.campaigns AS c
    WHERE (%(owner_email)s::text IS NULL OR c.owner_email = %(owner_email)s::text)
      AND (
        CASE
          WHEN %(status)s::text IS NULL THEN c.status <> 'archived'
          ELSE c.status = %(status)s::text
        END
      )
    ORDER BY c.updated_at DESC, c.created_at DESC
    LIMIT %(limit)s
    """

    _CAMPAIGN_GET_SQL = f"""
    SELECT c.campaign_id::text, c.name, c.owner_email, c.status, c.json_contract_version,
           c.treatment_state,
           c.treatment_build_lease_until,
           (
             c.treatment_build_lease_until IS NOT NULL
             AND c.treatment_build_lease_until <= now()
           ) AS treatment_build_lease_expired,
           c.criteria,
           c.suppression_policy, c.message_variants AS legacy_message_variants,
           {_NORMALIZED_CAMPAIGN_VARIANTS_SQL.format(campaign_id_ref="c.campaign_id")},
           c.channel_cascade, c.send_window, c.holdout, c.roi_assumptions,
           c.household_dedup, c.household_summary, c.created_at, c.updated_at
    FROM mip_app.campaigns AS c
    WHERE c.campaign_id = %(campaign_id)s::uuid
    LIMIT 1
    """

    _CAMPAIGN_PATCH_SQL = f"""
    WITH updated_campaign AS (
      UPDATE mip_app.campaigns
      SET status = %(status)s,
          treatment_state = CASE
            WHEN %(status)s = 'archived'
             AND treatment_state = 'building'
             AND treatment_build_lease_until IS NOT NULL
             AND treatment_build_lease_until <= now()
            THEN 'failed'
            ELSE treatment_state
          END,
          treatment_build_lease_until = CASE
            WHEN %(status)s = 'archived'
             AND treatment_state = 'building'
             AND treatment_build_lease_until IS NOT NULL
             AND treatment_build_lease_until <= now()
            THEN NULL
            ELSE treatment_build_lease_until
          END,
          updated_at = %(transition_at)s::timestamptz
      WHERE campaign_id = %(campaign_id)s::uuid
        AND status = %(current_status)s
        AND treatment_state = %(current_treatment_state)s
        AND (
          treatment_state = 'ready'
          OR (
            %(status)s = 'archived'
            AND (
              treatment_state IN ('legacy_unbound', 'failed')
              OR (
                treatment_state = 'building'
                AND treatment_build_lease_until IS NOT NULL
                AND treatment_build_lease_until <= now()
              )
            )
          )
        )
      RETURNING campaign_id::text, name, owner_email, status, json_contract_version, criteria,
                treatment_state,
                suppression_policy, message_variants AS legacy_message_variants,
                channel_cascade, send_window,
                holdout, roi_assumptions, household_dedup, household_summary, created_at, updated_at
    ),
    inserted_audit AS (
      INSERT INTO mip_app.action_audit (
        event_type, actor_email, entity_type, entity_id,
        request_id, correlation_id, evidence_ids, metadata, event_at
      )
      SELECT
        'CAMPAIGN_STATUS_UPDATE',
        %(actor)s,
        'campaign',
        updated_campaign.campaign_id,
        %(request_id)s,
        %(correlation_id)s,
        %(evidence_ids)s::TEXT[],
        %(metadata)s::jsonb,
        %(transition_at)s::timestamptz
      FROM updated_campaign
      RETURNING audit_id
    )
    SELECT updated_campaign.*,
           {_NORMALIZED_CAMPAIGN_VARIANTS_SQL.format(campaign_id_ref="updated_campaign.campaign_id::uuid")},
           inserted_audit.audit_id
    FROM updated_campaign
    LEFT JOIN inserted_audit ON TRUE
    """

    _CAMPAIGN_STATUS_IDEMPOTENCY_LOOKUP_SQL = f"""
    SELECT c.campaign_id::text, c.name, c.owner_email, c.json_contract_version,
           c.treatment_state,
           COALESCE(NULLIF(a.metadata->>'status', ''), c.status) AS status,
           c.criteria,
           c.suppression_policy, c.message_variants AS legacy_message_variants,
           {_NORMALIZED_CAMPAIGN_VARIANTS_SQL.format(campaign_id_ref="c.campaign_id")},
           c.channel_cascade,
           c.send_window, c.holdout, c.roi_assumptions, c.household_dedup,
           c.household_summary, c.created_at, a.event_at AS updated_at,
           a.audit_id, a.metadata AS audit_metadata
    FROM mip_app.campaigns c
    JOIN mip_app.action_audit a
      ON a.entity_type = 'campaign'
     AND a.entity_id = c.campaign_id::text
    WHERE c.campaign_id = %(campaign_id)s::uuid
      AND a.actor_email = %(actor)s
      AND a.request_id = %(request_id)s
      AND a.event_type = 'CAMPAIGN_STATUS_UPDATE'
    ORDER BY a.event_at ASC
    LIMIT 1
    """
