import type { IconName } from '../components/Icon';
import type { SegmentCode } from '../types';

/**
 * Per-segment UI metadata — code, display name, accent color, description,
 * and icon. This is the canonical *presentation* definition of each
 * Module 0 segment; counts/deltas/avg_score come from the backend at
 * runtime (never hard-coded here).
 *
 * Safe to import from production code — no fake borrower attributes live
 * in this module. If the backend hands back a segment code that is not
 * in this table, callers should fall back to the code string itself.
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
    description: 'Current customer with rate spread above the retention threshold; listing and competitor overlays join only when live evidence exists.',
    icon: 'shield',
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

export function segmentIcon(code: string): IconName {
  return segmentByCode(code)?.icon ?? 'layers';
}
