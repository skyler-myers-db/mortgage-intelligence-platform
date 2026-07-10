import type { IconName } from '../components/Icon';
import type { SegmentCode } from '../types';

/**
 * Per-segment UI metadata — code, display name, accent color, description,
 * and icon. This is the canonical *presentation* definition of each
 * Module 0 segment; counts/deltas/avg_score come from the backend at
 * runtime (never hard-coded here).
 *
 * Safe to import from production code — no fake borrower attributes live
 * in this module. Use `safeSegmentName()` for user-visible API payloads
 * where an unknown code should be hidden rather than echoed back verbatim.
 */
export interface SegmentDefinition {
  code: SegmentCode;
  name: string;
  color: string;
  description: string;
  icon: IconName;
}

export const SEGMENT_DEFINITIONS: readonly SegmentDefinition[] = [
  {
    code: 'itm',
    name: 'Prime Refi Candidates',
    color: 'var(--seg-itm)',
    description: 'Lien rate ≥ 75 bps above par and equity ≥ 15%.',
    icon: 'money',
  },
  {
    code: 'listed',
    name: 'Listed for Sale',
    color: 'var(--seg-listed)',
    description: 'Current active or under-contract Cotality MLS listing.',
    icon: 'tag',
  },
  {
    code: 'permit',
    name: 'HELOC Intent',
    color: 'var(--seg-permit)',
    description: 'Cotality HELOC propensity score indicates equity-credit demand.',
    icon: 'equity',
  },
  {
    code: 'investor',
    name: 'Investor / Multi-Property',
    color: 'var(--seg-investor)',
    description: 'Owner Link shows 2+ properties or repeat behavior.',
    icon: 'investor',
  },
  {
    code: 'equity',
    name: 'Home Equity Candidate',
    color: 'var(--seg-equity)',
    description: 'Strong available equity without an active second-position balance.',
    icon: 'equity',
  },
  {
    code: 'retention',
    name: 'Retention Risk',
    color: 'var(--seg-retention)',
    description: 'Current-customer or recapture signals worth reviewing before the borrower shops alternatives.',
    icon: 'shield',
  },
  // S1.3 overlay segments — registry parity with the gold `meta` VALUES
  // table in sql/transformations/gold_segment_population.sql.
  {
    code: 'second_lien_itm',
    name: 'Second-Lien Consolidation',
    color: 'var(--seg-second-lien)',
    description: 'Open second lien clears governed spread and equity thresholds.',
    icon: 'layers',
  },
  {
    code: 'heloc_draw_to_payback',
    name: 'HELOC Draw Ending',
    color: 'var(--seg-heloc-draw)',
    description: 'Equity-loan lien near the standard 120-month draw reset.',
    icon: 'bell',
  },
  {
    code: 'home_equity_history',
    name: 'Home Equity History',
    color: 'var(--seg-equity-history)',
    description: 'Appreciation ≥ 40%, owned ≥ 36 months, current equity ≥ 20%.',
    icon: 'equity',
  },
  {
    code: 'refi_propensity',
    name: 'Refi Propensity',
    color: 'var(--seg-refi-propensity)',
    description: 'Transparent 0-100 heuristic; 60+ joins this segment.',
    icon: 'target',
  },
  {
    code: 'itm_on_related_property',
    name: 'ITM on Related Property',
    color: 'var(--seg-related-itm)',
    description: 'Owner Link also holds a different in-the-money property.',
    icon: 'link',
  },
  {
    code: 'payoff_loss_leads',
    name: 'Payoff Loss',
    color: 'var(--seg-payoff-loss)',
    description: 'Tenant lien released within 24 months; current lien is competitor-held.',
    icon: 'export',
  },
  {
    code: 'permit_activity',
    name: 'Permit Activity',
    color: 'var(--seg-permit-activity)',
    description: 'Filed permits only; gated until a true Cotality permit source lands.',
    icon: 'permit',
  },
];

export function segmentByCode(code: string): SegmentDefinition | undefined {
  return SEGMENT_DEFINITIONS.find((s) => s.code === code);
}

export function segmentColor(code: string): string {
  return segmentByCode(code)?.color ?? 'var(--accent)';
}

export function segmentName(code: string): string {
  return segmentByCode(code)?.name ?? code;
}

// Canonical codes use underscores (matching SegmentCode Literals and the
// gold registry), so normalization folds spaces/hyphens INTO underscores.
// S1.3: `permit_activity` is now a real registered segment, so the legacy
// alias that routed "permit activity" to the HELOC-Intent `permit` code is
// gone — only genuine HELOC phrasings still map there.
const SEGMENT_ALIASES: Record<string, SegmentCode> = {
  heloc: 'permit',
  heloc_intent: 'permit',
};

export function normalizeSegmentCode(value: unknown): SegmentCode | null {
  if (typeof value !== 'string') return null;
  const code = value.trim().toLowerCase().replace(/[\s-]+/g, '_');
  if (!code) return null;
  const normalized = SEGMENT_ALIASES[code] ?? code;
  return segmentByCode(normalized) ? normalized as SegmentCode : null;
}

export function safeSegmentName(value: unknown): string | null {
  const code = normalizeSegmentCode(value);
  return code ? segmentName(code) : null;
}

export function segmentIcon(code: string): IconName {
  return segmentByCode(code)?.icon ?? 'layers';
}
