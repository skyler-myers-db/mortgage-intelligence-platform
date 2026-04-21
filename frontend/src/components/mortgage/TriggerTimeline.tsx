import type { EvidenceEvent } from '../../types';

export function TriggerTimeline({ events }: { events: EvidenceEvent[] }) {
  return (
    <div className="grid">
      {events.map((e) => (
        <div className="card card-body" key={e.evidence_id}>
          <div className="eyebrow">{e.signal_type}</div>
          <strong>{e.display_text}</strong>
          <div className="muted mono">{e.source_product} · {e.timestamp}</div>
        </div>
      ))}
    </div>
  );
}
