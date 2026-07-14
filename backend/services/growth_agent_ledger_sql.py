"""Lakebase SQL statements for Mortgage Growth Agent run state."""

RUN_INSERT_SQL = """
INSERT INTO mip_app.growth_agent_runs (
  actor_email, request_id, workflow_id, workflow_title, criteria,
  broad_total, actionable_total, broad_avg_score, actionable_avg_score,
  avg_rate_spread_bps, avg_equity_pct, route, source_assets,
  tool_steps, policy_checks
  , trace_id, tool_result_hash, specialist_agent, agent_evidence, governance_chips
) VALUES (
  %(actor_email)s, %(request_id)s, %(workflow_id)s, %(workflow_title)s, %(criteria)s::jsonb,
  %(broad_total)s, %(actionable_total)s, %(broad_avg_score)s, %(actionable_avg_score)s,
  %(avg_rate_spread_bps)s, %(avg_equity_pct)s, %(route)s, %(source_assets)s,
  %(tool_steps)s::jsonb, %(policy_checks)s::jsonb
  , %(trace_id)s, %(tool_result_hash)s, %(specialist_agent)s, %(agent_evidence)s::jsonb, %(governance_chips)s::jsonb
)
ON CONFLICT (actor_email, request_id) WHERE request_id IS NOT NULL DO NOTHING
RETURNING run_id, workflow_id, criteria, broad_total, actionable_total,
          broad_avg_score, actionable_avg_score, avg_rate_spread_bps, avg_equity_pct,
          route, source_assets, tool_steps, policy_checks, trace_id, tool_result_hash,
          specialist_agent, agent_evidence, governance_chips, audit_event_id, created_at
"""

RUN_ATTACH_AUDIT_SQL = """
UPDATE mip_app.growth_agent_runs
SET audit_event_id = %(audit_event_id)s
WHERE run_id = %(run_id)s
RETURNING run_id, workflow_id, criteria, broad_total, actionable_total,
          broad_avg_score, actionable_avg_score, avg_rate_spread_bps, avg_equity_pct,
          route, source_assets, tool_steps, policy_checks, trace_id, tool_result_hash,
          specialist_agent, agent_evidence, governance_chips, audit_event_id, created_at
"""

RUN_SELECT_BY_REQUEST_ID_SQL = """
SELECT run_id, workflow_id, criteria, broad_total, actionable_total,
       broad_avg_score, actionable_avg_score, avg_rate_spread_bps, avg_equity_pct,
       route, source_assets, tool_steps, policy_checks, trace_id, tool_result_hash,
       specialist_agent, agent_evidence, governance_chips, audit_event_id, created_at
FROM mip_app.growth_agent_runs
WHERE actor_email = %(actor_email)s
  AND request_id = %(request_id)s
LIMIT 1
"""

MONITOR_UPSERT_SQL = """
INSERT INTO mip_app.growth_agent_monitors (
  actor_email, workflow_id, name, cadence, criteria, route,
  actionable_total, source_assets, last_run_id, updated_at
) VALUES (
  %(actor_email)s, %(workflow_id)s, %(name)s, %(cadence)s, %(criteria)s::jsonb, %(route)s,
  %(actionable_total)s, %(source_assets)s, %(last_run_id)s, now()
)
ON CONFLICT (actor_email, workflow_id, name) DO UPDATE SET
  cadence = EXCLUDED.cadence,
  criteria = EXCLUDED.criteria,
  route = EXCLUDED.route,
  actionable_total = EXCLUDED.actionable_total,
  source_assets = EXCLUDED.source_assets,
  last_run_id = EXCLUDED.last_run_id,
  status = 'active',
  updated_at = now()
RETURNING monitor_id, workflow_id, name, cadence, status, criteria, route,
          actionable_total, source_assets, last_run_id, created_at, updated_at
"""

MONITOR_REFRESH_BY_ID_SQL = """
UPDATE mip_app.growth_agent_monitors
SET workflow_id = %(workflow_id)s,
    name = %(name)s,
    cadence = %(cadence)s,
    criteria = %(criteria)s::jsonb,
    route = %(route)s,
    actionable_total = %(actionable_total)s,
    source_assets = %(source_assets)s,
    last_run_id = %(last_run_id)s,
    status = 'active',
    updated_at = now()
WHERE actor_email = %(actor_email)s
  AND monitor_id = %(monitor_id)s
  AND status = 'active'
RETURNING monitor_id, workflow_id, name, cadence, status, criteria, route,
          actionable_total, source_assets, last_run_id, created_at, updated_at
"""

MONITOR_LIST_SQL = """
SELECT monitor_id, workflow_id, name, cadence, status, criteria, route,
       actionable_total, source_assets, last_run_id, created_at, updated_at
FROM mip_app.growth_agent_monitors
WHERE actor_email = %(actor_email)s
ORDER BY updated_at DESC
LIMIT %(limit)s
"""

MONITOR_SELECT_BY_RUN_ID_SQL = """
SELECT monitor_id, workflow_id, name, cadence, status, criteria, route,
       actionable_total, source_assets, last_run_id, created_at, updated_at
FROM mip_app.growth_agent_monitors
WHERE actor_email = %(actor_email)s
  AND last_run_id = %(last_run_id)s
ORDER BY updated_at DESC
LIMIT 1
"""

MONITOR_SELECT_BY_ID_SQL = """
SELECT monitor_id, workflow_id, name, cadence, status, criteria, route,
       actionable_total, source_assets, last_run_id, created_at, updated_at
FROM mip_app.growth_agent_monitors
WHERE actor_email = %(actor_email)s
  AND monitor_id = %(monitor_id)s
  AND status = 'active'
LIMIT 1
"""

DUE_MONITOR_LIST_SQL = """
SELECT monitor_id, workflow_id, name, cadence, status, criteria, route,
       actionable_total, source_assets, last_run_id, created_at, updated_at
FROM mip_app.growth_agent_monitors AS m
WHERE m.actor_email = %(actor_email)s
  AND status = 'active'
  AND (
    updated_at <= now() - CASE
      WHEN cadence = 'weekly' THEN INTERVAL '7 days'
      ELSE INTERVAL '1 day'
    END
    OR EXISTS (
      SELECT 1
      FROM unnest(%(channels)s::text[]) AS requested(channel)
      WHERE m.last_run_id IS NOT NULL
        AND NOT EXISTS (
          SELECT 1
          FROM mip_app.growth_agent_notification_drafts AS d
          WHERE d.monitor_id = m.monitor_id
            AND d.run_id = m.last_run_id
            AND d.channel = requested.channel
        )
    )
  )
ORDER BY updated_at ASC
LIMIT %(limit)s
"""

DUE_MONITOR_LIST_ALL_SQL = """
SELECT actor_email, monitor_id, workflow_id, name, cadence, status, criteria, route,
       actionable_total, source_assets, last_run_id, created_at, updated_at
FROM mip_app.growth_agent_monitors AS m
WHERE m.status = 'active'
  AND (
    updated_at <= now() - CASE
      WHEN cadence = 'weekly' THEN INTERVAL '7 days'
      ELSE INTERVAL '1 day'
    END
    OR EXISTS (
      SELECT 1
      FROM unnest(%(channels)s::text[]) AS requested(channel)
      WHERE m.last_run_id IS NOT NULL
        AND NOT EXISTS (
          SELECT 1
          FROM mip_app.growth_agent_notification_drafts AS d
          WHERE d.monitor_id = m.monitor_id
            AND d.run_id = m.last_run_id
            AND d.channel = requested.channel
        )
    )
  )
ORDER BY actor_email ASC, updated_at ASC
LIMIT %(limit)s
"""

NOTIFICATION_DRAFT_INSERT_SQL = """
INSERT INTO mip_app.growth_agent_notification_drafts (
  actor_email, monitor_id, run_id, channel, title, body, generation_mode,
  generator_label, strategy_summary, request_id, intent_payload, intent_hash, updated_at
) VALUES (
  %(actor_email)s, %(monitor_id)s, %(run_id)s, %(channel)s, %(title)s, %(body)s,
  %(generation_mode)s, %(generator_label)s, %(strategy_summary)s, %(request_id)s,
  %(intent_payload)s, %(intent_hash)s, now()
)
ON CONFLICT DO NOTHING
RETURNING draft_id, actor_email, monitor_id, run_id, channel, title, body,
          generation_mode, generator_label, strategy_summary, status, request_id,
          intent_payload, intent_hash, audit_event_id, created_at, updated_at
"""

NOTIFICATION_DRAFT_SELECT_BY_REQUEST_ID_SQL = """
SELECT draft_id, actor_email, monitor_id, run_id, channel, title, body,
       generation_mode, generator_label, strategy_summary, status, request_id,
       intent_payload, intent_hash, audit_event_id, created_at, updated_at
FROM mip_app.growth_agent_notification_drafts
WHERE request_id = %(request_id)s
LIMIT 1
"""

NOTIFICATION_DRAFT_SELECT_ACTIVE_SQL = """
SELECT draft_id, actor_email, monitor_id, run_id, channel, title, body,
       generation_mode, generator_label, strategy_summary, status, request_id,
       intent_payload, intent_hash, audit_event_id, created_at, updated_at
FROM mip_app.growth_agent_notification_drafts
WHERE actor_email = %(actor_email)s
  AND monitor_id = %(monitor_id)s
  AND run_id = %(run_id)s
  AND channel = %(channel)s
  AND status = 'draft'
LIMIT 1
"""

NOTIFICATION_DRAFT_ATTACH_AUDIT_SQL = """
UPDATE mip_app.growth_agent_notification_drafts
SET audit_event_id = %(audit_event_id)s
WHERE draft_id = %(draft_id)s
  AND audit_event_id IS NULL
RETURNING draft_id, actor_email, monitor_id, run_id, channel, title, body,
          generation_mode, generator_label, strategy_summary, status, request_id,
          intent_payload, intent_hash, audit_event_id, created_at, updated_at
"""
