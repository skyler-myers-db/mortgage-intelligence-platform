import type { LeadSummary } from '../../types';
import type { LeadExportContext } from './LeadTable.types';
import { offerDisplayLabel } from '../../lib/offerLanguage';

function csvEscape(raw: string): string {
  const v = /^[=+\-@]/.test(raw) ? `'${raw}` : raw;
  return /[",\n]/.test(v) ? `"${v.replace(/"/g, '""')}"` : v;
}

function csvValue(raw: unknown): string {
  if (raw === null || raw === undefined) return '';
  if (typeof raw === 'boolean') return raw ? 'true' : 'false';
  if (typeof raw === 'number') return Number.isFinite(raw) ? String(raw) : '';
  return String(raw);
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
  ];
  const exportableLeads = leads.filter((lead) => lead.marketing_eligible === true);
  const metadata = [
    ['generated_at', context.generatedAt ?? new Date().toISOString()],
    ['filters', context.filters ?? 'none'],
    ['suppression_policy', 'eligible_only_default; non-eligible visible rows are excluded from client CSV'],
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
      l.segment_codes.join('|'),
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
    ]
      .map((value) => csvEscape(csvValue(value)))
      .join(','),
  );
  return [...metadata, header.join(','), ...rows].join('\n');
}
