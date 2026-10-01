/**
 * What a bulk run would decide, counted for its gate: per primary offer (the
 * approve gate and the reject gate) and per state (the reject gate). Lives
 * beside the lazy bulk chunk's two gates so neither imports the other.
 */
import type { LeadSummary } from '../../types';
import { offerDisplayLabel } from '../../lib/offerLanguage';

export interface GateCount {
  label: string;
  count: number;
}

function sortedCounts(labels: readonly string[]): GateCount[] {
  const counts = new Map<string, number>();
  for (const label of labels) counts.set(label, (counts.get(label) ?? 0) + 1);
  return [...counts.entries()]
    .map(([label, count]) => ({ label, count }))
    .sort((a, b) => b.count - a.count || a.label.localeCompare(b.label));
}

export function offerCounts(leads: readonly LeadSummary[]): GateCount[] {
  return sortedCounts(leads.map((lead) => offerDisplayLabel(lead.recommended_offer_code, lead.recommended_offer)));
}

export function stateCounts(leads: readonly LeadSummary[]): GateCount[] {
  return sortedCounts(leads.map((lead) => lead.state?.trim() || 'No state'));
}
