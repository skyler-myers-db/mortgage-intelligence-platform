import { useRef, useState } from 'react';
import { useQuery } from '@tanstack/react-query';
import { api } from '../../lib/api';
import { queryKeys } from '../../lib/queryKeys';
import { formatTimestamp } from '../../lib/time';
import type { GenieTurn } from '../../lib/genieConversationStore';
import { Icon } from '../Icon';

/**
 * The history list's cache key. Kept local (rate-lever's geoQueryKeys
 * precedent) under the shared `mip` root, so AppShell's queryClient.clear()
 * on an actor switch drops the previous actor's rows with everything else.
 */
const genieSessionsQueryKey = () => [...queryKeys.all, 'genie', 'sessions'] as const;

/**
 * One past session as turns, or null when it could not be read. Never
 * rejects. Outside the component so the load handler needs no try statement
 * (audit 2026-09-21 `runtime-03`: a try/finally stops the React Compiler).
 */
function readGenieSessionTurns(
  conversationId: string,
): Promise<{ conversationId: string; turns: GenieTurn[] } | null> {
  return api.genieSession(conversationId).then(
    (detail) => ({
      conversationId: detail?.conversation_id ?? conversationId,
      turns: (Array.isArray(detail?.turns) ? detail.turns : []) as GenieTurn[],
    }),
    () => null,
  );
}

/**
 * Past-conversation picker for the floating Genie panel header.
 *
 * Reads `GET /api/genie/sessions` on open (not on mount) so the panel's first
 * paint never waits on an optional affordance, and re-reads on each open so a
 * session finished in another tab shows up. The read is audit-exempt, but it
 * still happens only on the explicit open: the rows live in a child that
 * mounts while the menu is open, so a closed menu never reads or prefetches.
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
  const [loadingId, setLoadingId] = useState<string | null>(null);
  // Synchronous latch: two fast clicks on the same row both read
  // `loadingId === null` in the same frame without it.
  const loadLatchRef = useRef(false);

  /** Resolves false only when the picked session could not be read; an
   *  ignored click (latched or disabled) is not a failure. */
  const load = (conversationId: string): Promise<boolean> => {
    if (loadLatchRef.current || disabled) return Promise.resolve(true);
    loadLatchRef.current = true;
    setLoadingId(conversationId);
    return readGenieSessionTurns(conversationId)
      .then((session) => {
        if (!session) return false;
        onLoad(session.conversationId, session.turns);
        return true;
      })
      .catch(() => false)
      .finally(() => {
        loadLatchRef.current = false;
        setLoadingId(null);
      });
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
      {open && <GenieHistoryRows loadingId={loadingId} disabled={disabled} onPick={load} />}
    </div>
  );
}

/**
 * The open menu. Mounting it IS the read: `refetchOnMount: 'always'` with
 * `staleTime: 0` reads on every open, a reopen shows the cached rows while
 * that read runs, and unmounting (close) aborts it because the query function
 * consumes the signal. `retry: false`: an error shows "History unavailable"
 * after exactly one request, never a retry storm against a 5xx.
 * `networkMode: 'always'`: offline, the read is attempted and fails into
 * "History unavailable" like any network failure, instead of pausing on
 * "Loading history…" under the client's default 'online' mode.
 * `refetchOnReconnect: false` (also TanStack's default under 'always'): a
 * reconnect is not an open, so only a reopen reads again.
 */
function GenieHistoryRows({
  loadingId,
  disabled,
  onPick,
}: {
  loadingId: string | null;
  disabled: boolean;
  onPick: (conversationId: string) => Promise<boolean>;
}) {
  // A failed session read hides the rows until the menu is reopened; the
  // state lives here so each open starts clean.
  const [loadFailed, setLoadFailed] = useState(false);
  const sessions = useQuery({
    queryKey: genieSessionsQueryKey(),
    queryFn: ({ signal }) => api.genieSessions(signal).then((rows) => (Array.isArray(rows) ? rows : [])),
    staleTime: 0,
    refetchOnMount: 'always',
    retry: false,
    networkMode: 'always',
    refetchOnReconnect: false,
  });
  // A settled error only: while a reopen's read runs, the rows cached from an
  // earlier open stay on screen instead of the previous open's error.
  const failed = loadFailed || (sessions.isError && sessions.fetchStatus === 'idle');
  const rows = failed ? undefined : sessions.data;

  return (
    <div
      className="filter-menu genie-history__menu"
      role="menu"
      aria-label="Past Genie conversations"
      onPointerDown={(e) => e.stopPropagation()}
    >
      {failed && (
        <div className="genie-history__state genie-history__state--error">History unavailable</div>
      )}
      {!failed && rows === undefined && <div className="genie-history__state">Loading history…</div>}
      {rows !== undefined && rows.length === 0 && (
        <div className="genie-history__state">No past conversations yet</div>
      )}
      {(rows ?? []).map((session) => (
        <button
          key={session.conversation_id}
          type="button"
          role="menuitem"
          className="filter-menu__item genie-history__item"
          disabled={loadingId !== null || disabled}
          onClick={() =>
            void onPick(session.conversation_id).then((ok) => {
              if (!ok) setLoadFailed(true);
            })
          }
        >
          <span className="genie-history__title">{session.title || 'Untitled conversation'}</span>
          <span className="genie-history__meta">
            {session.turn_count} turn{session.turn_count === 1 ? '' : 's'}
            {session.last_activity_at ? ` · ${formatTimestamp(session.last_activity_at)}` : ''}
          </span>
        </button>
      ))}
    </div>
  );
}
