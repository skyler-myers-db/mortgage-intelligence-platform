import { useEffect, useRef, useState, type CSSProperties } from 'react';
import type { SegmentSummary } from '../../types';
import { Icon } from '../Icon';
import { segmentByCode, segmentIcon } from '../../lib/segmentMetadata';

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

interface SegmentCardProps {
  segment: SegmentSummary;
  selected?: boolean;
  updating?: boolean;
  onClick?: () => void;
}

export function SegmentCard({ segment, selected, updating, onClick }: SegmentCardProps) {
  const icon = segmentIcon(segment.code);
  const presentation = segmentByCode(segment.code);
  const displayName = presentation?.name ?? segment.name;
  const displayDescription = presentation?.description ?? segment.description;
  const displayColor = presentation?.color ?? segment.color;
  const prev = useRef<boolean | undefined>(selected);
  const [emanateKey, setEmanateKey] = useState<number>(selected ? 1 : 0);

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
