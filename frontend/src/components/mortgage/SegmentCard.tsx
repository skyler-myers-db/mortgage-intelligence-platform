import type { CSSProperties } from 'react';
import type { SegmentSummary } from '../../types';
import { Icon, type IconName } from '../Icon';
import { SEGMENT_ICONS } from '../../mocks/demoData';

/**
 * SegmentCard — prototype `.seg-card` BEM: badge + title + count + sub + meta row.
 * Segment color is passed as a CSS variable `--seg-color` so the top accent bar,
 * badge, selection shadow, and hover radial gradient all pick it up.
 */

interface SegmentCardProps {
  segment: SegmentSummary;
  selected?: boolean;
  onClick?: () => void;
}

export function SegmentCard({ segment, selected, onClick }: SegmentCardProps) {
  const icon = (SEGMENT_ICONS[segment.code] ?? 'layers') as IconName;
  return (
    <button
      type="button"
      className={`seg-card ${selected ? 'is-selected' : ''}`}
      style={{ '--seg-color': segment.color } as CSSProperties}
      onClick={onClick}
      aria-pressed={selected}
    >
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
