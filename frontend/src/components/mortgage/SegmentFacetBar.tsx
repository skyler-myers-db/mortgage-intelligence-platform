import type { CSSProperties } from 'react';
import type { DimensionFacetCount } from '../../types';
import { EvidenceChip } from '../Primitives';
import { DRAWER_SOURCES } from '../../lib/drawerSources';

/**
 * One segment-card facet (S1.6 loan product type / origination channel) as a
 * single evidence trigger: a 4px stacked share bar of the whole mix plus a
 * short legend naming the largest share (audit 2026-09-21 visual-04).
 *
 * Prototype extension: design_files/index.html:488-533 defines `.seg-card`
 * with no facet element at all. The app's former rendering, one evidence
 * chip per facet value, wrapped one chip per line and made the card about
 * 2.2x the prototype's height. The whole facet is now ONE `EvidenceChip`
 * (so evidence access survives: one keyboard-reachable trigger per facet
 * per card, opening the same drawer source the per-value chips opened).
 *
 * Accessible name (WCAG 2.5.3 Label in Name, Level A): the name STARTS with
 * the visible legend (`Conv 58%`), so a speech-input user who says what they
 * see reaches the trigger; a visually hidden continuation then names the
 * facet and its top values, so the name reads `Conv 58% (Loan product mix:
 * Conventional 58%, FHA 24%, Jumbo 12%)`. The continuation opens with a
 * parenthesis, not a comma: the legend and the hidden span are separate
 * boxes, so browsers put a space between them (`Conv 58% , Loan ...`), and
 * a space before `(` is ordinary typography. Only the bar is presentational.
 */

export type SegmentFacetKind = 'product' | 'channel';

const FACET_NAMES: Record<SegmentFacetKind, string> = {
  product: 'Loan product mix',
  channel: 'Origination channel mix',
};

const FACET_SOURCES = {
  product: DRAWER_SOURCES.loanProductType,
  channel: DRAWER_SOURCES.originationChannel,
} as const;

/** Full names, spoken in the accessible summary. */
const VALUE_LABELS: Record<string, string> = {
  conventional: 'Conventional',
  jumbo: 'Jumbo',
  fha: 'FHA',
  va: 'VA',
  other: 'Other',
  unknown: 'Unknown',
  loan_officer: 'Loan officer',
  digital: 'Digital',
  branch: 'Branch',
  call_center: 'Call center',
};

/** Short forms for the one-line legend in a narrow card. */
const LEGEND_LABELS: Record<string, string> = {
  conventional: 'Conv',
  unknown: 'Unk.',
  loan_officer: 'LO',
  call_center: 'Call ctr',
};

/** Top values named in the accessible summary. */
const SUMMARY_VALUES = 3;
/** Distinct bar tones; smaller shares share the last one. */
const BAR_TONES = 4;

export interface FacetShare {
  value: string;
  label: string;
  legendLabel: string;
  count: number;
  /** Share of the facet total, 0-100. */
  pct: number;
  pctLabel: string;
}

const SNAKE_CASE_CODE_RE = /^[a-z][a-z0-9]*(?:_[a-z0-9]+)*$/;

/**
 * A code the maps above do not know yet (a new LOS channel, say) reads as
 * sentence case, like the mapped labels: `home_equity` -> `Home equity`.
 * Anything that is not a snake_case code is shown as the source spelled it.
 */
function fallbackLabel(value: string): string {
  if (!SNAKE_CASE_CODE_RE.test(value)) return value;
  const words = value.replace(/_/g, ' ');
  return words.charAt(0).toUpperCase() + words.slice(1);
}

function pctLabel(pct: number): string {
  if (pct > 0 && pct < 1) return '<1%';
  return `${Math.round(pct)}%`;
}

/** Shares of the whole mix, largest first; empty when there is nothing to show. */
export function facetShares(mix: readonly DimensionFacetCount[] | undefined): FacetShare[] {
  const rows = (mix ?? []).filter((row) => row.count > 0);
  const total = rows.reduce((sum, row) => sum + row.count, 0);
  if (total <= 0) return [];
  return [...rows]
    .sort((a, b) => b.count - a.count)
    .map((row) => {
      const pct = (row.count / total) * 100;
      const label = VALUE_LABELS[row.value] ?? fallbackLabel(row.value);
      return {
        value: row.value,
        label,
        legendLabel: LEGEND_LABELS[row.value] ?? label,
        count: row.count,
        pct,
        pctLabel: pctLabel(pct),
      };
    });
}

export function facetSummary(kind: SegmentFacetKind, shares: readonly FacetShare[]): string {
  const top = shares.slice(0, SUMMARY_VALUES).map((share) => `${share.label} ${share.pctLabel}`);
  return `${FACET_NAMES[kind]}: ${top.join(', ')}`;
}

interface SegmentFacetBarProps {
  kind: SegmentFacetKind;
  mix: readonly DimensionFacetCount[] | undefined;
}

export function SegmentFacetBar({ kind, mix }: SegmentFacetBarProps) {
  const shares = facetShares(mix);
  if (shares.length === 0) return null;
  const top = shares[0];
  return (
    <span className={`seg-card__facet-chip seg-card__facet-chip--${kind}`}>
      <EvidenceChip source={FACET_SOURCES[kind]}>
        <span className="seg-card__facet-legend">
          {top.legendLabel} {top.pctLabel}
        </span>
        <span className="seg-card__facet-bar" aria-hidden="true">
          {shares.map((share, index) => (
            <span
              key={share.value}
              className={`seg-card__facet-share seg-card__facet-share--${Math.min(index + 1, BAR_TONES)}`}
              style={{ '--facet-share': `${share.pct}%` } as CSSProperties}
            />
          ))}
        </span>
        <span className="sr-only">{` (${facetSummary(kind, shares)})`}</span>
      </EvidenceChip>
    </span>
  );
}
