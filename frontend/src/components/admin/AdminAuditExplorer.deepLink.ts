/**
 * Bring a deep-linked audit row into view (audit flow-04 phase 1).
 *
 * admin-config scrolls `#audit` to the top when the page opens on that hash,
 * but it does so on mount, before the ledger page has loaded: the explorer is
 * still short then, the page cannot scroll far enough, and the expanded row
 * lands below the fold under the rollups. Once the linked row is on screen in
 * the DOM, centre it (centre, not start: the route nav is sticky over the top
 * edge of `.main`). One scroll per linked id, deferred a frame so it runs
 * after the route's own hash scroll when the page is served from cache.
 */
import { useEffect, useRef, type RefObject } from 'react';

interface LinkedRow {
  event_id: string;
}

export function useScrollDeepLinkedRow(
  containerRef: RefObject<HTMLElement | null>,
  eventId: string,
  events: readonly LinkedRow[] | null,
): void {
  const scrolledFor = useRef<string | null>(null);
  const present = Boolean(eventId) && Boolean(events?.some((event) => event.event_id === eventId));

  useEffect(() => {
    // Leaving the deep link re-arms it, so Back to the same link scrolls again.
    if (!eventId) scrolledFor.current = null;
    if (!present || scrolledFor.current === eventId) return;
    const frame = window.requestAnimationFrame(() => {
      const row = [...(containerRef.current?.querySelectorAll<HTMLElement>('tr[data-audit-event-id]') ?? [])]
        .find((candidate) => candidate.dataset.auditEventId === eventId);
      if (!row) return;
      scrolledFor.current = eventId;
      row.scrollIntoView({ block: 'center' });
    });
    return () => window.cancelAnimationFrame(frame);
  }, [containerRef, eventId, present]);
}
