/**
 * The lead CSV itself: its columns, its `#` metadata lines, the confirmation
 * line and the download. Loaded on the Export click (useLeadCsvExport), not
 * with the table: the render-time plan and counts live in LeadTable.csvPlan,
 * re-exported here so one import path still names the whole export.
 */
import type { LeadSummary } from '../../types';
import type { LeadExportContext } from './LeadTable.types';
import { formatCount } from '../../lib/formatters';
import { offerDisplayLabel } from '../../lib/offerLanguage';
import { safeSegmentName } from '../../lib/segmentMetadata';
// The formula-injection gate lives in lib/csv.ts, shared with the audit
// explorer's page export (flow-04).
import { csvEscape, downloadCsvText } from '../../lib/csv';
import { exportMatchingRows, isLeadCsvExportable, type LeadCsvExportPlan } from './LeadTable.csvPlan';

export {
  exportMatchingRows,
  isLeadCsvExportable,
  loadedExportTruncatedOf,
  planLeadCsvExport,
  type LeadCsvExportPlan,
} from './LeadTable.csvPlan';

function csvValue(raw: unknown): string {
  if (raw === null || raw === undefined) return '';
  if (typeof raw === 'boolean') return raw ? 'true' : 'false';
  if (typeof raw === 'number') return Number.isFinite(raw) ? String(raw) : '';
  return String(raw);
}

/** The confirmation line: the real row count, scope, order and exclusions. */
export function describeLeadCsvExport(plan: LeadCsvExportPlan, rowOrder: string): string {
  const count = plan.rows.length;
  const what = `${formatCount(count)} ${plan.scope === 'selected_rows' ? 'selected ' : ''}`
    + `lead${count === 1 ? '' : 's'}`;
  const order = rowOrder === 'rank' ? 'in rank order' : `sorted by ${rowOrder}`;
  const excluded = plan.excluded > 0
    ? ` ${formatCount(plan.excluded)} excluded by the marketing-eligibility gate.`
    : '';
  return `Exported ${what} ${order}.${excluded}`;
}

/** Anchor-download the CSV. Client-side only: no server export endpoint. */
export function downloadLeadCsv(csv: string): void {
  downloadCsvText(csv, `mip-leads-${new Date().toISOString().slice(0, 10)}.csv`);
}

export function buildLeadCsv(
  leads: LeadSummary[],
  approvals: Record<string, string | undefined> = {},
  context: LeadExportContext = {},
): string {
  const header = [
    'borrower_id',
    'property_ref',
    'city',
    'state',
    'zip',
    'segments',
    'equity_estimate',
    'rate_spread_bps',
    'opportunity_score',
    'confidence',
    'primary_offer',
    'approval_status',
    'outreach_status',
    'approved_at',
    'outreach_at',
    'assigned_to_email',
    'assigned_at',
    'latest_disposition_outcome',
    'latest_disposition_at',
    'latest_callback_at',
    'aging_days',
    'is_owner_occupied',
    'is_investor',
    'is_current_customer',
    'is_former_customer',
    'is_competitor_lien',
    'current_lender_ref',
    'current_lien_balance',
    'second_pos_amount',
    'filed_permit_signal',
    'listed_for_sale',
    'related_property_count',
    'marketing_eligible',
    'consent_status',
    'suppression_reason',
    'last_touch_at',
    'eligible_recontact_at',
    'dnc',
    'eligibility_source',
  ];
  // Re-applied here even though callers plan with the same predicate: the
  // gate must hold for ANY caller of buildLeadCsv.
  const exportableLeads = leads.filter(isLeadCsvExportable);
  const metadata = [
    ['generated_at', context.generatedAt ?? new Date().toISOString()],
    ['filters', context.filters ?? 'none'],
    ['export_scope', context.scope ?? 'loaded_rows'],
    ['row_order', context.rowOrder ?? 'rank'],
    ['exported_rows', exportableLeads.length],
    ['matching_rows', exportMatchingRows(context.matchingRows, exportableLeads.length) ?? 'unknown'],
    ['suppression_policy', 'eligible_only_default; non-eligible visible rows are excluded from client CSV'],
    ['contact_policy', 'human_approval_required; only rows with approval_status=approved are cleared for outreach'],
    ['consent_provenance', 'synthetic-by-design demo consent fields; eligibility_source column carries the per-row source'],
    ['refreshed_at', context.refreshedAt ?? 'unknown'],
    ['rules_version', context.rulesVersion ?? 'unknown'],
  ].map(([key, value]) => `# ${key}=${String(value).replace(/\r?\n/g, ' ')}`);
  const rows = exportableLeads.map((l) =>
    [
      l.borrower_id,
      l.clip ?? '',
      l.city,
      l.state,
      l.zip,
      l.segment_codes.map((code) => safeSegmentName(code) ?? 'Unknown segment').join('|'),
      l.equity_estimate,
      l.rate_spread_bps,
      l.opportunity_score,
      l.confidence,
      offerDisplayLabel(l.recommended_offer_code, l.recommended_offer),
      approvals[l.borrower_id] ?? l.approval_status ?? 'pending',
      l.outreach_status ?? 'none',
      l.approved_at ?? '',
      l.outreach_at ?? '',
      l.assigned_to_email ?? '',
      l.assigned_at ?? '',
      l.latest_disposition_outcome ?? '',
      l.latest_disposition_at ?? '',
      l.latest_callback_at ?? '',
      l.aging_days ?? '',
      l.is_owner_occupied,
      l.is_investor,
      l.is_current_customer,
      l.is_former_customer,
      l.is_competitor_lien,
      l.current_lender_ref,
      l.current_lien_balance,
      l.second_pos_amount,
      l.has_permit,
      l.listed_for_sale,
      l.related_property_count,
      l.marketing_eligible,
      l.consent_status ?? 'unknown',
      l.suppression_reason ?? '',
      l.last_touch_at ?? '',
      l.eligible_recontact_at ?? '',
      l.dnc ?? false,
      l.eligibility_source ?? 'synthetic_seed',
    ]
      .map((value) => csvEscape(csvValue(value)))
      .join(','),
  );
  return [...metadata, header.join(','), ...rows].join('\n');
}
