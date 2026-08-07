import { useEffect, useRef, useState } from 'react';
import { api, isAbortError } from '../../lib/api';
import { formatTimestamp } from '../../lib/time';
import type { GenieSessionSummary } from '../../types';
import type { GenieTurn } from '../../lib/genieConversationStore';
import { Icon } from '../Icon';

/**
 * Past-conversation picker for the floating Genie panel header.
 *
 * Reads `GET /api/genie/sessions` on open (not on mount) so the panel's first
 * paint never waits on an optional affordance, and re-reads on each open so a
 * session finished in another tab shows up.
 *
 * Failure posture: history is a convenience, never a dependency. A 404 (older
 * backend without the endpoint), a 5xx, or a network failure all collapse to
 * one inline "History unavailable" row — the chat underneath keeps working
 * and no error is surfaced as a governed answer.
 */
export function GenieHistoryMenu({
  open,
  onToggle,
  onLoad,
  disabled = false,
}: {
  open: boolean;
  onToggle: (next: boolean) => void;
  onLoad: (conversationId: string, turns: GenieTurn[]) => void;
  /** True while a turn is in flight — loading another session mid-answer
   *  would race the in-flight response into the restored transcript. */
  disabled?: boolean;
}) {
  const [sessions, setSessions] = useState<GenieSessionSummary[] | null>(null);
  const [status, setStatus] = useState<'idle' | 'loading' | 'ready' | 'error'>('idle');
  const [loadingId, setLoadingId] = useState<string | null>(null);
  // Synchronous latch: two fast clicks on the same row both read
  // `loadingId === null` in the same frame without it.
  const loadLatchRef = useRef(false);

  useEffect(() => {
    if (!open) return undefined;
    const controller = new AbortController();
    setStatus('loading');
    api
      .genieSessions(controller.signal)
      .then((rows) => {
        if (controller.signal.aborted) return;
        setSessions(Array.isArray(rows) ? rows : []);
        setStatus('ready');
      })
      .catch((err) => {
        if (isAbortError(err) || controller.signal.aborted) return;
        setSessions(null);
        setStatus('error');
      });
    return () => controller.abort();
  }, [open]);

  const load = async (conversationId: string) => {
    if (loadLatchRef.current || disabled) return;
    loadLatchRef.current = true;
    setLoadingId(conversationId);
    try {
      const detail = await api.genieSession(conversationId);
      const turns = Array.isArray(detail?.turns) ? detail.turns : [];
      onLoad(detail?.conversation_id ?? conversationId, turns as GenieTurn[]);
    } catch {
      setStatus('error');
    } finally {
      loadLatchRef.current = false;
      setLoadingId(null);
    }
  };

  return (
    <div className="genie-history">
      <button
        type="button"
        className="drawer__close"
        onClick={(e) => {
          e.stopPropagation();
          onToggle(!open);
        }}
        disabled={disabled}
        aria-expanded={open}
        aria-label="Genie conversation history"
        title="History"
      >
        <Icon name="audit" size={14} />
      </button>
      {open && (
        <div
          className="filter-menu genie-history__menu"
          role="menu"
          aria-label="Past Genie conversations"
          onPointerDown={(e) => e.stopPropagation()}
        >
          {status === 'loading' && <div className="genie-history__state">Loading history…</div>}
          {status === 'error' && (
            <div className="genie-history__state genie-history__state--error">
              History unavailable
            </div>
          )}
          {status === 'ready' && (sessions?.length ?? 0) === 0 && (
            <div className="genie-history__state">No past conversations yet</div>
          )}
          {status === 'ready' &&
            (sessions ?? []).map((session) => (
              <button
                key={session.conversation_id}
                type="button"
                role="menuitem"
                className="filter-menu__item genie-history__item"
                disabled={loadingId !== null || disabled}
                onClick={() => void load(session.conversation_id)}
              >
                <span className="genie-history__title">{session.title || 'Untitled conversation'}</span>
                <span className="genie-history__meta">
                  {session.turn_count} turn{session.turn_count === 1 ? '' : 's'}
                  {session.last_activity_at ? ` · ${formatTimestamp(session.last_activity_at)}` : ''}
                </span>
              </button>
            ))}
        </div>
      )}
    </div>
  );
}
