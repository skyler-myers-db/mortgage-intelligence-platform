/**
 * The in-flight Genie turn's sessionStorage record (audit 2026-09-21
 * `runtime-01`): what a reload needs to resume a turn or to note that it
 * could not. Best effort and never logged.
 *
 * Moved out of lib/genieInFlightTurn.ts (w3-genie-jobs) so the store stays
 * about the lifecycle.
 *
 * v:2 (audit 2026-09-21 `genie-01`) adds `asyncComplete` (the server runs this
 * turn's completion as a job) and `jobId` (written the moment the 202 names
 * the job, before its first status poll). A v:1 record, written by the code
 * before jobs existed, is never resumed: `readRecord` reports it as
 * LEGACY_RECORD and the store removes it with a generic note.
 *
 * `questionHash` (audit 2026-09-21 `genie-03`, optional in v:2) is the
 * submit's 16-hex question label, validated on read, so a reload-resumed job
 * turn can still send its server cancel. Never the question itself.
 */
import { GENIE_QUESTION_LABEL_RE, type GenieTurnIds } from './genieAsk';
import { GENIE_IN_FLIGHT_TURN_KEY } from './genieConversation';
import type { GenieTurnPhase, GenieTurnSurface } from './genieInFlightTurn';

export interface PersistedTurnRecord {
  v: 2;
  question: string;
  conversationId: string | null;
  surface: GenieTurnSurface;
  startedAt: number;
  deep: boolean;
  phase: GenieTurnPhase;
  ids?: GenieTurnIds;
  /** Submit said `completion_jobs`: complete asks for a job (202 + polls). */
  asyncComplete: boolean;
  /** The job the 202 named (UUID-checked on read). */
  jobId?: string;
  /** The submit's 16-hex question label (checked on read). */
  questionHash?: string;
}

/** A record from before completion jobs (v:1): removed, never resumed. */
export const LEGACY_RECORD = 'legacy-record';

const JOB_ID_RE = /^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/;

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

function parseRecord(raw: string): PersistedTurnRecord | typeof LEGACY_RECORD | null {
  const value: unknown = JSON.parse(raw);
  if (!value || typeof value !== 'object') return null;
  if ((value as { v?: unknown }).v === 1) return LEGACY_RECORD;
  const record = value as Partial<PersistedTurnRecord>;
  if (record.v !== 2 || typeof record.question !== 'string' || !record.question.trim()) return null;
  if (!isSurface(record.surface) || !isPhase(record.phase)) return null;
  if (typeof record.startedAt !== 'number' || !Number.isFinite(record.startedAt)) return null;
  const conversationId = typeof record.conversationId === 'string' ? record.conversationId : null;
  return {
    v: 2,
    question: record.question,
    conversationId,
    surface: record.surface,
    startedAt: record.startedAt,
    deep: record.deep === true,
    phase: record.phase,
    ids: parseIds(record.ids),
    asyncComplete: record.asyncComplete === true,
    jobId: typeof record.jobId === 'string' && JOB_ID_RE.test(record.jobId) ? record.jobId : undefined,
    questionHash:
      typeof record.questionHash === 'string' && GENIE_QUESTION_LABEL_RE.test(record.questionHash)
        ? record.questionHash
        : undefined,
  };
}

/** The persisted record, LEGACY_RECORD for a v:1 one, or null. A malformed
 *  record is removed; a legacy one is left for the caller to remove. */
export function readRecord(): PersistedTurnRecord | typeof LEGACY_RECORD | null {
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
