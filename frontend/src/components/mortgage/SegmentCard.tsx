import { useEffect, useRef, useState, type CSSProperties } from 'react';
import type { SegmentSummary } from '../../types';
import { Icon } from '../Icon';
import { segmentIcon } from '../../lib/segmentMetadata';

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
 * Pending-source state (2026-05-04, prototype-parity-audit P0-2): the
 * `listed` and `permit` segments are defined in the canonical 6-segment
 * registry but their predicates are blocked-FALSE in gold.borrower_360
 * pending the Cotality MLS + Building-Permits Delta Share arrival. The
 * gold rollup CTAS now always emits a row for every segment_code (count=0
 * when no borrower matches), so the FE always sees all 6 segments. We
 * render those zero-count segments with an honest "Awaiting source" badge
 * and disable selection so a user can't filter to an empty result set --
 * the segment is visible (the demo narrative depends on seeing all 6) but
 * the data dependency is called out in plain English next to the card.
 */

interface SegmentCardProps {
  segment: SegmentSummary;
  selected?: boolean;
  onClick?: () => void;
}

/**
 * Segments whose predicates are intentionally blocked in gold pending an
 * upstream Cotality Delta Share. Keep this set narrow — adding a code
 * here changes the user-visible UX (the card disables and shows a
 * "pending source" state instead of "0 borrowers"), so it should only
 * cover segments that genuinely have a known data dependency.
 */
const PENDING_SOURCE_SEGMENTS: Record<string, string> = {
  listed: 'Awaiting Cotality MLS share',
  permit: 'Awaiting Cotality Permits share',
};

export function SegmentCard({ segment, selected, onClick }: SegmentCardProps) {
  const icon = segmentIcon(segment.code);
  const prev = useRef<boolean | undefined>(selected);
  const [emanateKey, setEmanateKey] = useState<number>(selected ? 1 : 0);

  // A segment is in the "pending source" state when it's on the explicit
  // pending list AND the rollup returned zero borrowers. Once the upstream
  // data lands and the count > 0, the card flips to the normal render
  // path automatically -- no FE change required.
  const pendingMessage =
    segment.count === 0 ? PENDING_SOURCE_SEGMENTS[segment.code] : undefined;
  const isPending = Boolean(pendingMessage);

  useEffect(() => {
    // Fire emanation only on deselected → selected transition.
    if (selected && !prev.current) {
      setEmanateKey((k) => k + 1);
    }
    prev.current = selected;
  }, [selected]);

  if (isPending) {
    // Pending-source render: surface the segment, name + color + description,
    // but replace the count with a clear "Awaiting source" badge and
    // disable click. Honest UX: zero borrowers means zero borrowers; the
    // segment is still visible so the demo's "6 borrower segments" framing
    // holds and the data dependency is called out explicitly.
    return (
      <div
        className="seg-card seg-card--pending"
        style={{ '--seg-color': segment.color } as CSSProperties}
        aria-disabled="true"
        data-pending-source="true"
      >
        <div className="seg-card__hdr">
          <div className="seg-card__badge"><Icon name={icon} size={14} /></div>
          <div className="seg-card__title">{segment.name}</div>
        </div>
        <div
          className="seg-card__count seg-card__count--pending num"
        >
          Pending source
        </div>
        <div className="seg-card__sub">{segment.description}</div>
        <div className="seg-card__meta seg-card__meta--pending">
          <span className="seg-card__meta-item">
            <Icon name="info" size={11} />
            {pendingMessage}
          </span>
        </div>
      </div>
    );
  }

  return (
    <button
      type="button"
      className={`seg-card ${selected ? 'is-selected' : ''}`}
      style={{ '--seg-color': segment.color } as CSSProperties}
      onClick={onClick}
      aria-pressed={selected}
    >
      {selected && emanateKey > 0 && (
        <span key={emanateKey} className="seg-card__emanate" aria-hidden="true" />
      )}
      <div className="seg-card__hdr">
        <div className="seg-card__badge"><Icon name={icon} size={14} /></div>
        <div className="seg-card__title">{segment.name}</div>
      </div>
      <div className="seg-card__count num">{segment.count.toLocaleString()}</div>
      <div className="seg-card__sub">{segment.description}</div>
      <div className="seg-card__meta">
        <span className="up">▲ {segment.delta}</span>
        <span>avg {segment.avg_score}</span>
      </div>
    </button>
  );
}
