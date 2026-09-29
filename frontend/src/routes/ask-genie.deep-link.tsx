import { lazy, Suspense, useSyncExternalStore } from 'react';
import { useQuery } from '@tanstack/react-query';
import { ApiError, api } from '../lib/api';
import type { GenieTurn } from '../lib/genieConversationStore';
import { getGenieTurnSnapshot, subscribeGenieTurn } from '../lib/genieInFlightTurn';
import { queryKeys } from '../lib/queryKeys';
import type { GenieConversationLinkStateProps } from '../components/mortgage/GenieConversationLinkState';

/**
 * `/ask-genie/:conversationId` — a shareable, actor-scoped link to one Genie
 * conversation (audit 2026-09-21 `shell-03` remainder, constraint 14).
 *
 * The id grammar is the conversation half of the backend's Genie id grammar
 * (backend/api/genie_refusal_report.py `_GENIE_ID_RE`): 32 hex, or a UUID. A
 * URL segment of any other shape is never sent anywhere: it resolves to the
 * same neutral "not available" state as a 404 or a 403, with zero requests.
 *
 * A shape-valid id is read with `GET /api/genie/sessions/{id}`, the caller's
 * own recorded turns (strictly actor-scoped: another actor's conversation is
 * a 404; audit-exempt; a Lakebase read, never a Genie call). It is read ONCE:
 * no retry, never stale, no refetch on focus, reconnect or mount. The key
 * sits under the shared `mip` root, so AppShell's `queryClient.clear()` drops
 * it at an actor switch. History load primes the same key before it writes
 * the URL, so the link then hydrates from the cache with no second request.
 *
 * Outcomes: `ok`; `not-found` for a 404, a 403 (it still goes through the
 * transport's session handling) or a malformed id, one state so a link never
 * tells "yours but gone" from "someone else's"; `unavailable` for a 5xx, a
 * 429 or a network failure, with Retry. The failure copy is fixed and never
 * carries error text (the Genie routes never route copy through the shared
 * error mapper).
 *
 * The loading / not-found / unavailable UI is a lazy chunk, requested only
 * while a conversation param is present
 * (components/mortgage/GenieConversationLinkState.tsx).
 */

export const GENIE_CONVERSATION_ID_RE = /^(?:[0-9a-f]{32}|[0-9a-f]{8}(?:-[0-9a-f]{4}){3}-[0-9a-f]{12})$/i;

export function isGenieConversationId(value: string | null | undefined): value is string {
  return typeof value === 'string' && GENIE_CONVERSATION_ID_RE.test(value);
}

/** The link's cache key: local, under the shared `mip` root. */
export function genieConversationQueryKey(conversationId: string) {
  return [...queryKeys.all, 'genie', 'session', conversationId] as const;
}

/** A verified conversation: the id the link asked for and its replayable turns. */
export interface GenieConversationLinkOk {
  conversationId: string;
  turns: GenieTurn[];
}

export type GenieConversationLink =
  | { kind: 'none' }
  | { kind: 'loading' }
  | { kind: 'ok'; conversation: GenieConversationLinkOk }
  | { kind: 'not-found' }
  | { kind: 'unavailable'; retrying: boolean; retry: () => void };

const NO_LINK: GenieConversationLink = { kind: 'none' };
const LOADING: GenieConversationLink = { kind: 'loading' };
const NOT_FOUND: GenieConversationLink = { kind: 'not-found' };

function readGenieConversation(conversationId: string, signal: AbortSignal): Promise<GenieConversationLinkOk> {
  return api.genieSession(conversationId, signal).then((detail) => ({
    conversationId,
    turns: (Array.isArray(detail?.turns) ? detail.turns : []) as GenieTurn[],
  }));
}

/** 404 and 403 are one outcome: a link never reveals whose conversation it names. */
function isNotFound(error: unknown): boolean {
  return error instanceof ApiError && (error.status === 404 || error.status === 403);
}

export function useGenieConversationLink(param: string | undefined): GenieConversationLink {
  const valid = isGenieConversationId(param);
  const query = useQuery({
    queryKey: genieConversationQueryKey(valid ? param : ''),
    queryFn: ({ signal }) => readGenieConversation(valid ? param : '', signal),
    enabled: valid,
    retry: false,
    staleTime: Infinity,
    refetchOnWindowFocus: false,
    refetchOnReconnect: false,
    refetchOnMount: false,
  });
  if (param === undefined) return NO_LINK;
  if (!valid) return NOT_FOUND;
  if (query.data) return { kind: 'ok', conversation: query.data };
  if (!query.isError) return LOADING;
  if (isNotFound(query.error)) return NOT_FOUND;
  return {
    kind: 'unavailable',
    retrying: query.isFetching,
    retry: () => {
      void query.refetch();
    },
  };
}

/** No turn in flight (a conversation id is a string, '' for a new thread). */
export const NO_GENIE_TURN = null;

function inFlightConversation(): string | null {
  const { inFlight } = getGenieTurnSnapshot();
  return inFlight ? (inFlight.conversationId ?? '') : NO_GENIE_TURN;
}

/**
 * The conversation of the turn in flight ('' for a new thread), or
 * NO_GENIE_TURN. A primitive snapshot: the route re-renders when a turn
 * starts or ends, never on its progress polls.
 */
export function useInFlightGenieConversation(): string | null {
  return useSyncExternalStore(subscribeGenieTurn, inFlightConversation, () => NO_GENIE_TURN);
}

const GenieConversationLinkState = lazy(() => import('../components/mortgage/GenieConversationLinkState'));

/** The link's non-ok states, from the lazy chunk; a busy empty surface meanwhile. */
export function GenieConversationLinkPlaceholder(props: GenieConversationLinkStateProps) {
  return (
    <Suspense fallback={<div className="surface" aria-busy="true" data-genie-link="pending" />}>
      <GenieConversationLinkState {...props} />
    </Suspense>
  );
}
