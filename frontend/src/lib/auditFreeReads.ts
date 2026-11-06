/**
 * The reviewed reads that write no audit row, per HTTP method, in their
 * `/api/` form.
 *
 * A read the server answered can still fail on the client: a 200 whose body
 * was lost mid-transfer fails offline or unreachable but keeps its status
 * (apiTransport `_readJson`). An audited read the server answered has already
 * written its row (the VIEW_* writes run in background tasks after the
 * response is sent), so recovering it on its own would add a second row with
 * no act behind it. A read on this list writes none, so a recovery may
 * complete it (connectionState `refetchableAfterNetworkLoss`).
 *
 * Fail-closed: a method and path that are not on the list are treated as
 * audited, so a new route stays out of every automatic re-read until someone
 * reviews it here. `tests/unit/test_audit_free_reads_registry.py` pins each
 * entry from the server side: it calls every entry on the mounted app with
 * the audit store and the Lakebase client recording, and asserts that the
 * call wrote no audit row, through the store or as raw SQL, and queued no
 * background task; and that no route outside the list serves a path that one
 * of its entries matches.
 *
 * Matching: the method must be the entry's; the request path loses its query
 * string and `/api/v1` becomes `/api`; each `{name}` matches one path segment
 * that is not `.` or `..` and holds no encoded `/` or `\`.
 */
export const AUDIT_FREE_GET_ROUTES: readonly string[] = [
  '/api/activation/destinations',
  '/api/activation/outbox',
  '/api/activation/summary',
  '/api/admin/assets/{asset_key}/metadata',
  '/api/admin/capabilities',
  '/api/admin/field-performance',
  '/api/admin/health',
  // A ledger read that writes no row (the registry pin calls it): the Admin
  // data-operations panel reads the last run of each job for its cooldown.
  '/api/admin/operations',
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
  // A ledger read that writes no row: "My recent activity" in the Console,
  // the events of the reader alone.
  '/api/audit/my-events',
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

/**
 * The reviewed POST reads that write no audit row: a query that reads with a
 * POST (a body the server needs), never a write. Ask Genie's starters and
 * trusted assets (lib/genieStartQuery; the route is audit-exempt), and the
 * cohort reads of Home and the Portfolio builder: the preview counts (Home's
 * KPI row and contactable preview, the builder preview) and the builder's
 * campaign recommendation (backend/api/portfolio.py; it writes no state).
 */
export const AUDIT_FREE_POST_READS: readonly string[] = [
  '/api/genie/start',
  '/api/portfolio/campaign-recommendation',
  '/api/portfolio/preview',
];

const DOT_SEGMENT = /^(?:\.|%2e){1,2}$/i;
/** An encoded separator: the server would decode it into another path. */
const ENCODED_SEPARATOR = /%(?:2f|5c)/i;

function templatePattern(template: string): RegExp {
  const source = template
    .split('/')
    .map((segment) => (/^\{[a-z_]+\}$/.test(segment) ? '[^/]+' : segment.replace(/[.*+?^${}()|[\]\\]/g, '\\$&')))
    .join('/');
  return new RegExp(`^${source}$`);
}

const AUDIT_FREE_PATTERNS: ReadonlyMap<string, readonly RegExp[]> = new Map([
  ['GET', AUDIT_FREE_GET_ROUTES.map(templatePattern)],
  ['POST', AUDIT_FREE_POST_READS.map(templatePattern)],
]);

/** The request path as the list spells it: no query string, `/api/v1` as `/api`. */
export function logicalApiPath(path: string): string {
  const bare = path.split(/[?#]/, 1)[0] ?? '';
  return bare.replace(/^\/api\/v1(?=\/|$)/, '/api');
}

/**
 * True only when `method` and `path` (an ApiError's) name a reviewed
 * audit-free read. An unknown method (null: not a transport error) never does.
 */
export function isAuditFreeRead(path: string, method: string | null): boolean {
  const patterns = method === null ? undefined : AUDIT_FREE_PATTERNS.get(method.toUpperCase());
  if (!patterns) return false;
  const logical = logicalApiPath(path);
  if (logical.split('/').some((segment) => DOT_SEGMENT.test(segment) || ENCODED_SEPARATOR.test(segment))) return false;
  return patterns.some((pattern) => pattern.test(logical));
}
