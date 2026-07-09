import { useEffect, useRef, useState, type CSSProperties, type KeyboardEvent, type MouseEvent } from 'react';
import type { DimensionFacetCount, SegmentSummary } from '../../types';
import { Icon } from '../Icon';
import { segmentByCode, segmentIcon } from '../../lib/segmentMetadata';
import { useApp } from '../AppContext';
import { DRAWER_SOURCES } from '../../lib/drawerSources';

/** S1.6 — short display labels for the borrower-dimension tokens. Any value
 *  not in the map (including unknown) falls back to a title-cased token so the
 *  chip never renders a raw lowercase code. */
const LOAN_PRODUCT_LABELS: Record<string, string> = {
  conventional: 'Conv',
  jumbo: 'Jumbo',
  fha: 'FHA',
  va: 'VA',
  other: 'Other',
  unknown: 'Unknown',
};
const ORIGINATION_CHANNEL_LABELS: Record<string, string> = {
  loan_officer: 'Loan officer',
  digital: 'Digital',
  branch: 'Branch',
  call_center: 'Call center',
  unknown: 'Unknown',
};

function titleCaseToken(token: string): string {
  return token
    .split('_')
    .map((part) => (part ? part.charAt(0).toUpperCase() + part.slice(1) : part))
    .join(' ');
}

/** Compact borrower counts for facet chips: 1240 -> "1.2K", 980 -> "980". */
function formatFacetCount(count: number): string {
  if (count < 1000) return count.toLocaleString();
  const thousands = count / 1000;
  if (thousands < 10) return `${thousands.toFixed(1).replace(/\.0$/, '')}K`;
  if (count < 1_000_000) return `${Math.round(thousands)}K`;
  const millions = count / 1_000_000;
  return `${millions.toFixed(1).replace(/\.0$/, '')}M`;
}

/**
 * SegmentCard — prototype `.seg-card` BEM: badge + title + count + sub + meta row.
 * Segment color is passed as a CSS variable `--seg-color` so the top accent bar,
 * badge, selection shadow, and hover radial gradient all pick it up.
 *
 * Emanation: when `selected` flips false → true, we mount a single
 * `.seg-card__emanate` span with a fresh key. The CSS animation is one-shot
 * (animation-iteration-count: 1); bumping the key restarts the animation on
 * every re-selection without ever playing on initial mount of a deselected
 * card (e.g. when loading a filtered URL).
 *
 * The gold rollup CTAS emits a row for every configured segment_code
 * (count=0 when no borrower matches), so the FE always sees the complete
 * Module 0 segment catalog. Source-readiness status now lives in the
 * evidence drawer/admin surfaces; a zero-count segment remains selectable
 * so users can verify the exact filter result.
 */

/**
 * Non-button evidence chip used inside a SegmentCard (which is itself a
 * <button>, so a nested <button> would be invalid HTML). Rendered as a span
 * with role="button" + tabIndex so it stays keyboard-reachable, and it stops
 * propagation so a click/Enter/Space opens the drawer without toggling card
 * selection.
 */
function EvidenceFacetChip({
  label,
  ariaLabel,
  onOpen,
}: {
  label: string;
  ariaLabel: string;
  onOpen: () => void;
}) {
  const handleClick = (event: MouseEvent<HTMLSpanElement>) => {
    event.stopPropagation();
    onOpen();
  };
  const handleKeyDown = (event: KeyboardEvent<HTMLSpanElement>) => {
    if (event.key === 'Enter' || event.key === ' ') {
      event.preventDefault();
      event.stopPropagation();
      onOpen();
    }
  };
  return (
    <span
      className="chip chip--neutral seg-card__facet-chip"
      role="button"
      tabIndex={0}
      aria-label={ariaLabel}
      onClick={handleClick}
      onKeyDown={handleKeyDown}
    >
      {label}
    </span>
  );
}

interface SegmentCardProps {
  segment: SegmentSummary;
  selected?: boolean;
  updating?: boolean;
  onClick?: () => void;
}

export function SegmentCard({ segment, selected, updating, onClick }: SegmentCardProps) {
  const { setDrawer } = useApp();
  const icon = segmentIcon(segment.code);
  const presentation = segmentByCode(segment.code);
  const displayName = presentation?.name ?? segment.name;
  const displayDescription = presentation?.description ?? segment.description;
  const displayColor = presentation?.color ?? segment.color;
  const prev = useRef<boolean | undefined>(selected);
  const [emanateKey, setEmanateKey] = useState<number>(selected ? 1 : 0);

  const loanProductMix: DimensionFacetCount[] = (segment.loan_product_mix ?? []).slice(0, 3);
  const originationChannelMix: DimensionFacetCount[] = (segment.origination_channel_mix ?? []).slice(0, 2);
  const hasFacets = loanProductMix.length > 0 || originationChannelMix.length > 0;

  const deltaIsFirstSnapshot =
    segment.delta.trim() === '+0%' ||
    segment.delta.trim() === '0%' ||
    segment.delta.trim() === '+0.0%' ||
    segment.delta.trim() === '0.0%';
  const hasNoBorrowers = segment.count === 0;

  useEffect(() => {
    // Fire emanation only on deselected → selected transition.
    if (selected && !prev.current) {
      setEmanateKey((k) => k + 1);
    }
    prev.current = selected;
  }, [selected]);

  return (
    <button
      type="button"
      className={`seg-card ${selected ? 'is-selected' : ''} ${updating ? 'is-updating' : ''}`}
      style={{ '--seg-color': displayColor } as CSSProperties}
      onClick={onClick}
      aria-pressed={selected}
      aria-busy={updating || undefined}
    >
      {selected && emanateKey > 0 && (
        <span key={emanateKey} className="seg-card__emanate" aria-hidden="true" />
      )}
      <div className="seg-card__hdr">
        <div className="seg-card__badge"><Icon name={icon} size={14} /></div>
        <div className="seg-card__title">{displayName}</div>
      </div>
      <div className="seg-card__count num">{segment.count.toLocaleString()}</div>
      <div className="seg-card__sub">{displayDescription}</div>
      <div className="seg-card__meta">
        {hasNoBorrowers ? (
          <span>no borrowers in current view</span>
        ) : deltaIsFirstSnapshot ? (
          <span>first snapshot · deltas pending</span>
        ) : (
          <span className={segment.delta.startsWith('-') ? 'down' : 'up'}>
            {segment.delta.startsWith('-') ? '▼' : '▲'} {segment.delta}
          </span>
        )}
        {!hasNoBorrowers && <span>avg {segment.avg_score}</span>}
      </div>
      {hasFacets && (
        <div className="seg-card__facets">
          {loanProductMix.length > 0 && (
            <div className="seg-card__facet-row">
              <span className="seg-card__facet-label">Product</span>
              {loanProductMix.map((facet) => {
                const label = LOAN_PRODUCT_LABELS[facet.value] ?? titleCaseToken(facet.value);
                return (
                  <EvidenceFacetChip
                    key={`product-${facet.value}`}
                    label={`${label} ${formatFacetCount(facet.count)}`}
                    ariaLabel={`${label} loan product, ${facet.count.toLocaleString()} borrowers — open evidence`}
                    onOpen={() => setDrawer(DRAWER_SOURCES.loanProductType)}
                  />
                );
              })}
            </div>
          )}
          {originationChannelMix.length > 0 && (
            <div className="seg-card__facet-row">
              <span className="seg-card__facet-label">Channel</span>
              {originationChannelMix.map((facet) => {
                const label = ORIGINATION_CHANNEL_LABELS[facet.value] ?? titleCaseToken(facet.value);
                return (
                  <EvidenceFacetChip
                    key={`channel-${facet.value}`}
                    label={`${label} ${formatFacetCount(facet.count)}`}
                    ariaLabel={`${label} origination channel, ${facet.count.toLocaleString()} borrowers — open evidence`}
                    onOpen={() => setDrawer(DRAWER_SOURCES.originationChannel)}
                  />
                );
              })}
            </div>
          )}
        </div>
      )}
    </button>
  );
}

export function SegmentCardSkeleton() {
  return (
    <div className="seg-card seg-card--skeleton" aria-hidden="true">
      <div className="seg-card__hdr">
        <div className="seg-card__badge seg-card__badge--skeleton skeleton" />
        <div className="seg-card__title-skeleton skeleton" />
      </div>
      <div className="seg-card__count-skeleton skeleton" />
      <div className="seg-card__sub-skeleton skeleton" />
      <div className="seg-card__sub-skeleton seg-card__sub-skeleton--short skeleton" />
      <div className="seg-card__meta">
        <span className="seg-card__meta-skeleton skeleton" />
        <span className="seg-card__meta-skeleton seg-card__meta-skeleton--short skeleton" />
      </div>
    </div>
  );
}
