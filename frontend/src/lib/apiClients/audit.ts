/**
 * Audit endpoint clients: the audit event feed, its paged form, the current
 * actor's own events, and the rollup summary.
 *
 * Members moved verbatim out of `api.ts`, which spreads them back into the
 * exported `api` object in the original order. Consumers import `api` from
 * `../api` — never from this module.
 */
import type { AuditEventRow, AuditEventPage, ActorAuditEventPage } from '../apiTypes';
import { getJson } from '../apiTransport';

export const auditApi = {
  /**
   * Recent audit events for the Agent Activity Log. Routes through the
   * same retry/backoff loop as every other read so a transient 503 on
   * Lakebase doesn't immediately render "feed unavailable" — callers
   * get the same cadence the backend's Resilient wrapper runs at.
   * Hole-finder finding #4, 2026-04-23.
   */
  auditEvents: (
    limit = 12,
    signal?: AbortSignal,
    filters: {
      actor?: string | null;
      action?: string | null;
      entity_id?: string | null;
      borrower_id?: string | null;
      subject_clip?: string | null;
      event_type?: string | null;
      since?: string | null;
      until?: string | null;
      offset?: number | null;
    } = {},
  ) => {
    const params = new URLSearchParams();
    params.set('limit', String(limit));
    Object.entries(filters).forEach(([key, value]) => {
      if (value !== null && value !== undefined && value !== '') {
        params.set(key, String(value));
      }
    });
    return getJson<AuditEventRow[]>(`/api/audit/events?${params.toString()}`, signal);
  },

  auditEventPage: (
    limit = 25,
    signal?: AbortSignal,
    filters: {
      actor?: string | null;
      action?: string | null;
      entity_id?: string | null;
      borrower_id?: string | null;
      subject_clip?: string | null;
      event_type?: string | null;
      since?: string | null;
      until?: string | null;
      cursor?: string | null;
    } = {},
  ) => {
    const params = new URLSearchParams();
    params.set('limit', String(limit));
    Object.entries(filters).forEach(([key, value]) => {
      if (value !== null && value !== undefined && value !== '') {
        params.set(key, String(value));
      }
    });
    return getJson<AuditEventPage>(`/api/audit/events/page?${params.toString()}`, signal);
  },

  myAuditEvents: (limit = 8, signal?: AbortSignal, cursor?: string | null) => {
    const params = new URLSearchParams();
    params.set('limit', String(limit));
    if (cursor) params.set('cursor', cursor);
    return getJson<ActorAuditEventPage>(`/api/audit/my-events?${params.toString()}`, signal);
  },

  auditRollups: (period: 'day' | 'week' | 'month' = 'week', signal?: AbortSignal) =>
    getJson<Array<{ bucket_start: string; event_type: string; event_count: number }>>(
      `/api/audit/rollups?period=${period}`,
      signal,
    ),
};
