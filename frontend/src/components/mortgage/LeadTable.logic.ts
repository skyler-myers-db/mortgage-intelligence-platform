import type { CampaignSummary, LeadSummary } from '../../types';
import type { SortKey } from './LeadTable.types';

export interface CampaignBinding {
  campaign_id: string;
  variant_name: string;
}

/** Accept a URL binding only when the server returned the same campaign and email variant. */
export function verifiedCampaignBinding(
  campaign: CampaignSummary | null | undefined,
  requested: CampaignBinding | null,
): CampaignBinding | null {
  if (!campaign || !requested || campaign.campaign_id !== requested.campaign_id) return null;
  const variantMatches = (campaign.message_variants ?? []).some((raw) => (
    raw.variant_name === requested.variant_name && raw.channel === 'email'
  ));
  return variantMatches ? requested : null;
}

/**
 * Return true when `el` is an editable element that the window-level
 * hotkey handler must skip over. Exported for unit tests.
 */
export function isEditableTarget(el: Element | null | undefined): boolean {
  if (!el) return false;
  const tag = (el as HTMLElement).tagName;
  if (tag === 'INPUT' || tag === 'TEXTAREA' || tag === 'SELECT') return true;
  return (el as HTMLElement).isContentEditable === true;
}

/**
 * Chunk a list into groups of `size`. Used by the bulk-approve loop to
 * bound in-flight POSTs to the approve endpoint without inventing a
 * server-side bulk API.
 */
export function chunk<T>(items: T[], size: number): T[][] {
  if (size <= 0) return [items];
  const out: T[][] = [];
  for (let i = 0; i < items.length; i += size) {
    out.push(items.slice(i, i + size));
  }
  return out;
}

/**
 * Where keyboard focus should land after a bulk approve/reject action
 * settles. Pure so it can be unit-tested under the node environment
 * (the repo deliberately does not mount components in Vitest).
 *
 * The bulk-action toolbar only renders while `selectionCount > 0`. On a
 * full success the selection is cleared, so the toolbar — and the button
 * the user clicked — unmounts; refocusing that button would drop focus to
 * `<body>`. On a partial outcome (failed/aborted rows stay selected) the
 * toolbar persists, so the triggering button is still in the DOM and is
 * the least-surprising place to return focus for a retry.
 *
 * @param remainingSelectionCount selected rows left AFTER the action settles.
 * @returns `'trigger'`     — refocus the button that launched the action.
 *          `'tableRegion'` — focus the always-mounted table scroll region.
 */
export function bulkActionFocusTarget(
  remainingSelectionCount: number,
): 'trigger' | 'tableRegion' {
  return remainingSelectionCount > 0 ? 'trigger' : 'tableRegion';
}

export function _newBulkId(): string {
  const c = typeof globalThis !== 'undefined' ? globalThis.crypto : undefined;
  if (c && typeof c.randomUUID === 'function') return c.randomUUID();
  return 'xxxxxxxx-xxxx-4xxx-yxxx-xxxxxxxxxxxx'.replace(/[xy]/g, (ch) => {
    const n = ch === 'x' ? Math.floor(Math.random() * 16) : 8 + Math.floor(Math.random() * 4);
    return n.toString(16);
  });
}

export function relationshipLabel(lead: LeadSummary): string {
  if (lead.is_current_customer) return 'Current';
  if (lead.is_former_customer) return 'Former';
  if (lead.is_competitor_lien) return 'Competitor';
  return 'Other';
}

export function relationshipVariant(lead: LeadSummary): 'success' | 'warning' | 'neutral' {
  if (lead.is_current_customer) return 'success';
  if (lead.is_competitor_lien) return 'warning';
  return 'neutral';
}

export function sortValue(lead: LeadSummary, key: SortKey): string | number {
  if (key === 'relationship') return relationshipLabel(lead);
  if (key === 'assignment') return lead.assigned_to_label ?? lead.assigned_to_email ?? 'Unassigned';
  if (key === 'outreach') return lead.outreach_status ?? 'none';
  if (key === 'equity') return lead.equity_estimate;
  if (key === 'rate') return lead.rate_spread_bps;
  if (key === 'score') return lead.opportunity_score;
  if (key === 'confidence') return lead.confidence;
  return 0;
}

/** S2 assignment lifecycle chip copy. Stages render as short labels in the
 *  Assigned-to column; unknown/absent statuses render nothing (caller skips). */
export function assignmentStatusLabel(status?: string | null): string {
  if (status === 'assigned') return 'Assigned';
  if (status === 'contact_drafted') return 'Contact drafted';
  if (status === 'approved') return 'Approved';
  if (status === 'actioned') return 'Actioned';
  if (status === 'outcome_recorded') return 'Outcome recorded';
  return '';
}

/** Prototype chip variants only (design_files/index.html .chip--*):
 *  entry stage neutral, in-review warning, human-approved/actioned success,
 *  closed-loop neutral. */
export function assignmentStatusVariant(
  status?: string | null,
): 'success' | 'warning' | 'neutral' {
  if (status === 'approved' || status === 'actioned') return 'success';
  if (status === 'contact_drafted') return 'warning';
  return 'neutral';
}

export function outreachLabel(status?: string | null): string {
  if (!status || status === 'none') return 'None';
  return status.charAt(0).toUpperCase() + status.slice(1);
}

export function outreachVariant(status?: string | null): 'success' | 'warning' | 'neutral' {
  if (status === 'sent' || status === 'replied' || status === 'actioned') return 'success';
  if (status === 'queued' || status === 'bounced') return 'warning';
  return 'neutral';
}

export function isTerminalApproval(status?: string | null): boolean {
  return status === 'approved' || status === 'rejected' || status === 'hold';
}

function isNonWorkableApproval(status?: string | null): boolean {
  return status === 'rejected' || status === 'hold';
}

export function isLeadMarketingActionable(
  lead?: Pick<LeadSummary, 'marketing_eligible' | 'consent_status' | 'dnc'> | null,
): boolean {
  if (!lead) return true;
  return (
    lead.marketing_eligible !== false &&
    lead.dnc !== true &&
    (lead.consent_status ?? 'opt_in') === 'opt_in'
  );
}

export function isLeadSelectableForSalesOps(
  status?: string | null,
  localStatus?: string | null,
  lead?: Pick<LeadSummary, 'marketing_eligible' | 'consent_status' | 'dnc'> | null,
): boolean {
  if (!isLeadMarketingActionable(lead)) return false;
  return !isNonWorkableApproval(status) && !isNonWorkableApproval(localStatus);
}

export function isLeadApprovalEligible(
  status?: string | null,
  localStatus?: string | null,
  lead?: Pick<LeadSummary, 'marketing_eligible' | 'consent_status' | 'dnc'> | null,
): boolean {
  if (!isLeadMarketingActionable(lead)) return false;
  return !localStatus && !isTerminalApproval(status);
}

export function dispositionLabel(outcome?: string | null): string {
  if (!outcome) return 'Untouched';
  return outcome.split('_').map((part) => part.charAt(0).toUpperCase() + part.slice(1)).join(' ');
}

export function dispositionVariant(outcome?: string | null): 'success' | 'warning' | 'neutral' | 'danger' {
  if (outcome === 'application_started' || outcome === 'callback_scheduled' || outcome === 'connected') return 'success';
  if (outcome === 'called_no_answer' || outcome === 'called_left_voicemail' || outcome === 'not_now') return 'warning';
  if (outcome === 'dead' || outcome === 'not_interested') return 'danger';
  return 'neutral';
}

export function formatDateTimeShort(value?: string | null): string {
  if (!value) return '—';
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) return value;
  return date.toLocaleString(undefined, {
    month: 'short',
    day: 'numeric',
    hour: 'numeric',
    minute: '2-digit',
  });
}
