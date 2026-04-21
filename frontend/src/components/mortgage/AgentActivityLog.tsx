import { useEffect, useState } from 'react';
import { Icon, type IconName } from '../Icon';
import { demoAgentActivity } from '../../mocks/demoData';

/**
 * AgentActivityLog — prototype `.audit` BEM. Pulls events from
 * GET /api/audit/events (already exists); falls back to demoAgentActivity
 * so the Home page always renders content in booth-mode. Icon + color keyed
 * off action verb so Approvals stand out green, rejects red, Genie asks
 * amber.
 */

interface AuditEvent {
  event_id: string;
  actor: string;
  action: string;
  entity_type: string;
  entity_id: string;
  payload_json: Record<string, unknown>;
  evidence_ids: string[];
  created_at: string;
}

type IconColor = '' | 'green' | 'amber' | 'red';

function classify(action: string, entityType: string): { icon: IconName; color: IconColor } {
  const a = action.toLowerCase();
  if (a.includes('approv')) return { icon: 'check', color: 'green' };
  if (a.includes('reject')) return { icon: 'cross', color: 'red' };
  if (a.includes('genie') || a.includes('ask')) return { icon: 'sparkle', color: 'amber' };
  if (a.includes('load')  || a.includes('pipeline')) return { icon: 'db', color: '' };
  if (a.includes('score') || a.includes('rank')) return { icon: 'bolt', color: 'amber' };
  if (a.includes('export')) return { icon: 'export', color: '' };
  if (a.includes('filter')) return { icon: 'filter', color: '' };
  if (entityType === 'session') return { icon: 'info', color: '' };
  return { icon: 'info', color: '' };
}

function formatTime(iso: string): string {
  try {
    const d = new Date(iso);
    if (!Number.isFinite(d.getTime())) return iso;
    return d.toLocaleTimeString('en-US', { hour12: false });
  } catch {
    return iso;
  }
}

export function AgentActivityLog({ limit = 12 }: { limit?: number }) {
  const [rows, setRows] = useState<AuditEvent[]>([]);

  useEffect(() => {
    let cancelled = false;
    (async () => {
      try {
        const res = await fetch(`/api/audit/events?limit=${limit}`);
        if (!res.ok) throw new Error(String(res.status));
        const data = (await res.json()) as AuditEvent[];
        if (!cancelled) {
          setRows(data.length > 0 ? data : demoAgentActivity);
        }
      } catch {
        if (!cancelled) setRows(demoAgentActivity);
      }
    })();
    return () => {
      cancelled = true;
    };
  }, [limit]);

  return (
    <div className="surface">
      <div className="surface__hdr">
        <Icon name="audit" size={14} style={{ color: 'var(--accent)' }} />
        <div className="h-4">Agent action audit log</div>
      </div>
      <div className="audit-panel">
        {rows.map((r) => {
          const cls = classify(r.action, r.entity_type);
          return (
            <div className="audit" key={r.event_id}>
              <div className="audit__time mono">{formatTime(r.created_at)}</div>
              <div className={`audit__ico ${cls.color}`}><Icon name={cls.icon} size={11} /></div>
              <div className="audit__body">
                <div className="audit__what">{r.action}</div>
                <div className="audit__who">{r.actor}</div>
              </div>
            </div>
          );
        })}
      </div>
      <div className="surface__ft">Written to Lakebase · immutable · exportable to Unity Catalog</div>
    </div>
  );
}
