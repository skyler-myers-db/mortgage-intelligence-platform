import type { CSSProperties } from 'react';
import type { SegmentSummary } from '../../types';

export function SegmentCard({ segment, active, onClick }: { segment: SegmentSummary; active?: boolean; onClick?: () => void }) {
  return (
    <button className={`card segment-card ${active ? 'active' : ''}`} style={{ '--seg-color': segment.color } as CSSProperties} onClick={onClick}>
      <div style={{ display: 'flex', justifyContent: 'space-between', gap: 8 }}>
        <strong>{segment.name}</strong>
        <span className="chip">avg {segment.avg_score}</span>
      </div>
      <div className="segment-count num">{segment.count.toLocaleString()}</div>
      <p className="muted" style={{ marginBottom: 0 }}>{segment.description}</p>
    </button>
  );
}
