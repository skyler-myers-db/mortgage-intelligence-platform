/**
 * The in-flight Genie turn's sessionStorage record (audit 2026-09-21
 * `runtime-01`): what a reload needs to resume a turn or to note that it
 * could not. Best effort and never logged.
 *
 * Moved verbatim out of lib/genieInFlightTurn.ts (w3-genie-jobs) so the store
 * stays about the lifecycle; only `export` keywords were added.
 */
import type { GenieTurnIds } from './genieAsk';
import { GENIE_IN_FLIGHT_TURN_KEY } from './genieConversation';
import type { GenieTurnPhase, GenieTurnSurface } from './genieInFlightTurn';

export interface PersistedTurnRecord {
  v: 1;
  question: string;
  conversationId: string | null;
  surface: GenieTurnSurface;
  startedAt: number;
  deep: boolean;
  phase: GenieTurnPhase;
  ids?: GenieTurnIds;
}

function isSurface(value: unknown): value is GenieTurnSurface {
  return value === 'panel' || value === 'route';
}

function isPhase(value: unknown): value is GenieTurnPhase {
  return value === 'submitting' || value === 'polling' || value === 'completing';
}

function parseIds(value: unknown): GenieTurnIds | undefined {
  if (!value || typeof value !== 'object') return undefined;
  const ids = value as Partial<GenieTurnIds>;
  if (typeof ids.conversationId !== 'string' || !ids.conversationId) return undefined;
  if (typeof ids.messageId !== 'string' || !ids.messageId) return undefined;
  if (typeof ids.progressToken !== 'string' || !ids.progressToken) return undefined;
  return { conversationId: ids.conversationId, messageId: ids.messageId, progressToken: ids.progressToken };
}

function parseRecord(raw: string): PersistedTurnRecord | null {
  const value: unknown = JSON.parse(raw);
  if (!value || typeof value !== 'object') return null;
  const record = value as Partial<PersistedTurnRecord>;
  if (record.v !== 1 || typeof record.question !== 'string' || !record.question.trim()) return null;
  if (!isSurface(record.surface) || !isPhase(record.phase)) return null;
  if (typeof record.startedAt !== 'number' || !Number.isFinite(record.startedAt)) return null;
  const conversationId = typeof record.conversationId === 'string' ? record.conversationId : null;
  return {
    v: 1,
    question: record.question,
    conversationId,
    surface: record.surface,
    startedAt: record.startedAt,
    deep: record.deep === true,
    phase: record.phase,
    ids: parseIds(record.ids),
  };
}

/** The persisted record, or null. A malformed record is removed. */
export function readRecord(): PersistedTurnRecord | null {
  if (typeof window === 'undefined') return null;
  try {
    const raw = window.sessionStorage.getItem(GENIE_IN_FLIGHT_TURN_KEY);
    if (!raw) return null;
    const record = parseRecord(raw);
    if (!record) window.sessionStorage.removeItem(GENIE_IN_FLIGHT_TURN_KEY);
    return record;
  } catch {
    return null;
  }
}

/** Best effort: storage full or blocked means the turn runs in memory only. */
export function writeRecord(record: PersistedTurnRecord): void {
  if (typeof window === 'undefined') return;
  try {
    window.sessionStorage.setItem(GENIE_IN_FLIGHT_TURN_KEY, JSON.stringify(record));
  } catch {
    // Quota exceeded / privacy mode: only the reload resume is lost.
  }
}

export function removeRecord(): void {
  if (typeof window === 'undefined') return;
  try {
    window.sessionStorage.removeItem(GENIE_IN_FLIGHT_TURN_KEY);
  } catch {
    // Storage unavailable: there is nothing to remove.
  }
}
