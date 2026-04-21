import { useEffect, useRef, useState, type CSSProperties } from 'react';
import type { SegmentSummary } from '../../types';
import { Icon, type IconName } from '../Icon';
import { SEGMENT_ICONS } from '../../mocks/fixtureData';

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
 */

interface SegmentCardProps {
  segment: SegmentSummary;
  selected?: boolean;
  onClick?: () => void;
}

export function SegmentCard({ segment, selected, onClick }: SegmentCardProps) {
  const icon = (SEGMENT_ICONS[segment.code] ?? 'layers') as IconName;
  const prev = useRef<boolean | undefined>(selected);
  const [emanateKey, setEmanateKey] = useState<number>(selected ? 1 : 0);

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
