import type { EvidenceEvent } from '../../types';
import type { CSSProperties } from 'react';
import { EvidenceChip } from '../Primitives';
import { descriptorForEvidence } from '../../lib/drawerSources';

/**
 * TriggerTimeline — prototype `.trig` BEM. Vertical rail with dot markers
 * (colored via `--seg-color`). Each row shows WHEN / WHAT / WHY plus an
 * EvidenceChip linking to the source drawer.
 */

interface TriggerTimelineProps {
  events: EvidenceEvent[];
  segmentColor?: string;
}

function relativeWhen(iso: string): string {
  try {
    const t = Date.parse(iso);
    if (!Number.isFinite(t)) return iso;
    const deltaMs = Date.now() - t;
    const days = Math.round(deltaMs / (1000 * 60 * 60 * 24));
    if (days <= 0) return 'today';
    if (days === 1) return '1d ago';
    if (days < 30) return `${days}d ago`;
    if (days < 365) return `${Math.round(days / 30)}mo ago`;
    return `${Math.round(days / 365)}y ago`;
  } catch {
    return iso;
  }
}

export function TriggerTimeline({ events, segmentColor }: TriggerTimelineProps) {
  const style = { '--seg-color': segmentColor ?? 'var(--accent)' } as CSSProperties;
  return (
    <div className="trig" style={style}>
      {events.map((e) => {
        const source = descriptorForEvidence(e);
        return (
          <div className="trig__item" key={e.evidence_id}>
            <div className="trig__when">{relativeWhen(e.timestamp)}</div>
            <div className="trig__what">{e.display_text}</div>
            <div className="trig__why">
              {e.source_product} · {e.signal_value}{' '}
              <EvidenceChip source={source}>{source.title}</EvidenceChip>
            </div>
          </div>
        );
      })}
    </div>
  );
}
