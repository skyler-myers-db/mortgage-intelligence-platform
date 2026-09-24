import type { EvidenceEvent } from '../../types';
import type { CSSProperties } from 'react';
import { EvidenceChip } from '../Primitives';
import { descriptorForEvidence } from '../../lib/drawerSources';
import { Timestamp } from '../ui/Timestamp';

/**
 * TriggerTimeline — prototype `.trig` BEM. Vertical rail with dot markers
 * (colored via `--seg-color`). Each row shows WHEN / WHAT / WHY plus an
 * EvidenceChip linking to the source drawer.
 *
 * WHEN is the prototype's narrow relative age ("2d ago", uppercased by the
 * `.trig__when` CSS) from lib/time via <Timestamp>, so the row carries the
 * instant and an absolute-UTC tooltip, and a year-old trigger reads as a
 * dated one instead of "1y ago" (2026-09-21 audit, responsive-07).
 */

interface TriggerTimelineProps {
  events: EvidenceEvent[];
  segmentColor?: string;
}

export function TriggerTimeline({ events, segmentColor }: TriggerTimelineProps) {
  const style = { '--seg-color': segmentColor ?? 'var(--accent)' } as CSSProperties;
  return (
    <div className="trig" style={style}>
      {events.map((e) => {
        const source = descriptorForEvidence(e);
        return (
          <div className="trig__item" key={e.evidence_id}>
            <div className="trig__when">
              <Timestamp value={e.timestamp} relativeStyle="narrow" />
            </div>
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
