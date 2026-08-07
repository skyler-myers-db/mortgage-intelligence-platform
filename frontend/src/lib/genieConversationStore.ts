import type { GenieAnswer as GenieAnswerShape } from '../types';

/**
 * Genie conversation transcript store.
 *
 * The floating Genie panel is mounted conditionally (`{genieOpen ? <GenieChat/> : null}`
 * in AppShell), so closing the panel — or any remount — used to destroy the
 * entire visible conversation. This module keeps the turn list in a
 * module-level cache mirrored into `sessionStorage`, so the transcript
 * survives panel close/reopen and route navigation for the life of the tab.
 *
 * Scope decisions:
 *   - `sessionStorage`, not `localStorage`: a transcript is tab-scoped
 *     working state, not a durable artifact. Closing the tab ends it.
 *     The Genie *conversation id* keeps living in localStorage
 *     (`lib/genieConversation.ts`) — that is a Databricks-side handle, this
 *     is the rendered transcript.
 *   - Capped at MAX_STORED_TURNS so a long booth session cannot grow the
 *     quota unbounded; the OLDEST turns are dropped first.
 *   - Writes happen on turn completion only. An in-flight turn is never
 *     persisted, so a reload mid-answer restores the last settled state
 *     rather than a half-rendered bubble.
 *   - The persisted shape is deliberately `{question, response}[]` — the
 *     same shape `GET /api/genie/sessions/{id}` returns — so loading a past
 *     session and restoring local state go through one code path.
 */

export const GENIE_CONVERSATION_TURNS_KEY = 'mip-genie-conversation-v1';

/** Hard cap on persisted turns. Oldest-first eviction. */
export const MAX_STORED_TURNS = 20;

export interface GenieTurn {
  /** The user question that produced `response`. Empty string for entries
   *  with no originating question (governed action results, transport
   *  errors) so the transcript still round-trips faithfully. */
  question: string;
  response: GenieAnswerShape;
}

/** Rendered message shape consumed by GenieChat. */
export type GenieChatMessage =
  | { who: 'user'; text: string }
  | { who: 'ai'; payload: GenieAnswerShape; sources?: string[] };

let cache: GenieTurn[] | null = null;
const listeners = new Set<() => void>();

const EMPTY: GenieTurn[] = [];

function isTurn(value: unknown): value is GenieTurn {
  if (!value || typeof value !== 'object') return false;
  const turn = value as Partial<GenieTurn>;
  return typeof turn.question === 'string' && Boolean(turn.response) && typeof turn.response === 'object';
}

function hydrate(): GenieTurn[] {
  if (typeof window === 'undefined') return EMPTY;
  try {
    const raw = window.sessionStorage.getItem(GENIE_CONVERSATION_TURNS_KEY);
    if (!raw) return EMPTY;
    const parsed: unknown = JSON.parse(raw);
    if (!Array.isArray(parsed)) return EMPTY;
    const turns = parsed.filter(isTurn);
    return turns.length > 0 ? turns.slice(-MAX_STORED_TURNS) : EMPTY;
  } catch {
    // Unparseable or storage-denied: start clean rather than throwing into
    // the render path.
    return EMPTY;
  }
}

function persist(turns: GenieTurn[]): void {
  if (typeof window === 'undefined') return;
  try {
    if (turns.length === 0) {
      window.sessionStorage.removeItem(GENIE_CONVERSATION_TURNS_KEY);
      return;
    }
    window.sessionStorage.setItem(GENIE_CONVERSATION_TURNS_KEY, JSON.stringify(turns));
  } catch {
    // Quota exceeded / privacy mode. In-memory state still works for this
    // mount; persistence is best-effort.
  }
}

function emit(): void {
  for (const listener of listeners) listener();
}

/** Current turns. Stable reference between writes (safe for
 *  `useSyncExternalStore`). */
export function getGenieTurns(): GenieTurn[] {
  if (cache === null) cache = hydrate();
  return cache;
}

/** Server-render snapshot — no sessionStorage available. */
export function getGenieTurnsServerSnapshot(): GenieTurn[] {
  return EMPTY;
}

export function subscribeGenieTurns(listener: () => void): () => void {
  listeners.add(listener);
  return () => {
    listeners.delete(listener);
  };
}

/** Replace the whole transcript (restore-from-history, or a trimmed set). */
export function setGenieTurns(turns: GenieTurn[]): GenieTurn[] {
  const next = turns.slice(-MAX_STORED_TURNS);
  cache = next.length > 0 ? next : EMPTY;
  persist(cache);
  emit();
  return cache;
}

/** Drop the transcript (New thread, actor boundary reset, 403). */
export function clearGenieTurns(): void {
  cache = EMPTY;
  persist(EMPTY);
  emit();
}

/** Convert rendered messages into the persisted turn shape. An `ai` message
 *  is paired with the `user` message immediately before it. */
export function messagesToTurns(messages: readonly GenieChatMessage[]): GenieTurn[] {
  const turns: GenieTurn[] = [];
  for (let i = 0; i < messages.length; i += 1) {
    const message = messages[i];
    if (message.who !== 'ai') continue;
    const previous = messages[i - 1];
    turns.push({
      question: previous && previous.who === 'user' ? previous.text : '',
      response: message.payload,
    });
  }
  return turns;
}

/** Inverse of `messagesToTurns`. Also the adapter for a loaded history
 *  session, whose `turns` arrive in exactly this shape. */
export function turnsToMessages(
  turns: readonly GenieTurn[],
  sourcesFor?: (payload: GenieAnswerShape) => string[],
): GenieChatMessage[] {
  const messages: GenieChatMessage[] = [];
  for (const turn of turns) {
    if (!turn || !turn.response) continue;
    if (turn.question) messages.push({ who: 'user', text: turn.question });
    messages.push({
      who: 'ai',
      payload: turn.response,
      sources: sourcesFor ? sourcesFor(turn.response) : undefined,
    });
  }
  return messages;
}

/**
 * Persist the settled transcript. No-ops while a turn is in flight so a
 * half-complete exchange is never written.
 */
export function persistGenieMessages(
  messages: readonly GenieChatMessage[],
  { inFlight }: { inFlight: boolean },
): void {
  if (inFlight) return;
  setGenieTurns(messagesToTurns(messages));
}
