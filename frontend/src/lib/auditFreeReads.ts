/**
 * The reviewed GET routes that write no audit row, in their `/api/` form.
 *
 * A read the server answered can still fail on the client: a 200 whose body
 * was lost mid-transfer fails offline or unreachable but keeps its status
 * (apiTransport `_readJson`). An audited read the server answered has already
 * written its row (the VIEW_* writes run in background tasks after the
 * response is sent), so recovering it on its own would add a second row with
 * no act behind it. A read on this list writes none, so a recovery may
 * complete it (connectionState `refetchableAfterNetworkLoss`).
 *
 * Fail-closed: a path that is not on the list is treated as audited, so a
 * new route stays out of every automatic re-read until someone reviews it
 * here. `tests/unit/test_audit_free_reads_registry.py` pins each entry from
 * the server side: a mounted GET route whose dependencies never take the
 * audit store or a background task, and no audited GET route matches one.
 *
 * Matching: the request path loses its query string and `/api/v1` becomes
 * `/api`; each `{name}` matches one path segment that is not `.` or `..`.
 */
export const AUDIT_FREE_GET_ROUTES: readonly string[] = [
  '/api/activation/destinations',
  '/api/activation/outbox',
  '/api/activation/summary',
  '/api/admin/assets/{asset_key}/metadata',
  '/api/admin/capabilities',
  '/api/admin/field-performance',
  '/api/admin/health',
  '/api/admin/rules',
  '/api/admin/settings',
  '/api/admin/sources',
  '/api/admin/sse-probe',
  '/api/admin/sse-probe/{probe_id}',
  '/api/analytics/economics',
  '/api/analytics/economics/points',
  '/api/analytics/executive',
  '/api/analytics/funnel',
  '/api/analytics/funnel/loan-officers/{loan_officer_id}',
  '/api/analytics/geography',
  '/api/analytics/rate-window',
  '/api/analytics/segments',
  '/api/analytics/signals',
  '/api/assets/{asset_key}/freshness',
  '/api/borrowers/search',
  '/api/borrowers/{borrower_id}/decisions',
  '/api/borrowers/{borrower_id}/evidence',
  '/api/borrowers/{borrower_id}/lifecycle',
  '/api/campaigns',
  '/api/campaigns/{campaign_id}',
  '/api/config/footprint',
  '/api/config/options',
  '/api/data-estate',
  '/api/genie/sessions',
  '/api/genie/sessions/{conversation_id}',
  '/api/geo/assignment-overlay',
  '/api/geo/county-rollups',
  '/api/geo/rate-sensitivity',
  '/api/geo/state-rollups',
  '/api/geo/zip-rollups',
  '/api/growth-agent',
  '/api/growth-agent/agent/plan/progress',
  '/api/growth-agent/monitors',
  '/api/growth-agent/monitors/summary',
  '/api/growth-agent/runs',
  '/api/health',
  '/api/home/summary',
  '/api/home/summary/attribution',
  '/api/kpi-proof',
  '/api/leads/count',
  '/api/leads/facets',
  '/api/leads/{borrower_id}/assignment',
  '/api/lineage/manifest',
  '/api/loan-officers',
  '/api/loan-officers/assignments',
  '/api/outreach/approval-requests',
  '/api/portfolio',
  '/api/portfolio/{portfolio_id}',
  '/api/sales/aging',
  '/api/sales/campaign-performance',
  '/api/sales/conversion',
  '/api/sales/followups',
  '/api/sales/followups/summary',
  '/api/sales/outcomes/summary',
  '/api/sales/standup',
  '/api/sales/team',
  '/api/sales/workload',
  '/api/segments',
  '/api/segments/combinations',
  '/api/session',
  '/api/workspace',
  '/api/workspace/queue-version',
  '/api/workspace/saved-views',
];

const DOT_SEGMENT = /^(?:\.|%2e){1,2}$/i;

function templatePattern(template: string): RegExp {
  const source = template
    .split('/')
    .map((segment) => (/^\{[a-z_]+\}$/.test(segment) ? '[^/]+' : segment.replace(/[.*+?^${}()|[\]\\]/g, '\\$&')))
    .join('/');
  return new RegExp(`^${source}$`);
}

const AUDIT_FREE_PATTERNS: readonly RegExp[] = AUDIT_FREE_GET_ROUTES.map(templatePattern);

/** The request path as the list spells it: no query string, `/api/v1` as `/api`. */
export function logicalApiPath(path: string): string {
  const bare = path.split(/[?#]/, 1)[0] ?? '';
  return bare.replace(/^\/api\/v1(?=\/|$)/, '/api');
}

/** True only when `path` (an ApiError's request path) is a reviewed audit-free GET. */
export function isAuditFreeRead(path: string): boolean {
  const logical = logicalApiPath(path);
  if (logical.split('/').some((segment) => DOT_SEGMENT.test(segment))) return false;
  return AUDIT_FREE_PATTERNS.some((pattern) => pattern.test(logical));
}
