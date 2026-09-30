import { useSyncExternalStore } from 'react';
import { registerActorScopedMemoryCache } from './actorScopedMemoryCaches';

/**
 * What a Genie answer's controls remember past the component that shows them
 * (audit 2026-09-21 `genie-08` item 3, w3-genie-reading review). Collapsing
 * an earlier turn, a boundary remounting the panel, or leaving and returning
 * to /ask-genie unmounts the answer; its feedback vote and its CSV export
 * latch used to live in component state, so a recorded vote was offered
 * again and an export in flight showed "Download CSV" as if nothing were
 * recording.
 *
 * A module store read with useSyncExternalStore (the pinnedInsights /
 * genieConversationStore pattern). It holds only opaque ids and fixed copy,
 * never question or answer text:
 *   - a vote per answer, keyed `${conversationId}:${messageId}`: pending or
 *     recorded, plus ONE request id per vote direction. A retry, after a
 *     remount too, reuses that id, so the server's idempotent feedback write
 *     never sees a second one; a recorded vote is never offered again.
 *   - a CSV export latch per rows block, keyed
 *     `${conversationId}:${messageId}:${scope}:${sectionIndex ?? '-'}`: the
 *     recording flag and the last outcome's fixed copy.
 *
 * At most MAX_GENIE_ANSWER_MEMORY entries, oldest out. The shell's actor
 * boundary clears it (registered at module evaluation, the lib/toast.ts
 * precedent), and a request that settles after the clear writes nothing.
 */

export type GenieVoteDirection = 'up' | 'down';

export interface GenieVoteMemory {
  /** 'idle' until a vote starts, and again after one fails. */
  readonly status: 'idle' | 'pending' | 'recorded';
  /** The direction in flight while pending, and the one recorded. */
  readonly direction: GenieVoteDirection | null;
  /** The request id minted for each direction, reused by every retry. */
  readonly requestIds: Readonly<Partial<Record<GenieVoteDirection, string>>>;
}

export interface GenieExportLatchMemory {
  readonly recording: boolean;
  /** The last outcome's fixed copy (GENIE_EXPORT_*), or null. */
  readonly status: string | null;
}

/** Which rows block an export latch belongs to. */
export interface GenieExportLatchTarget {
  conversationId: string;
  messageId: string;
  scope: string;
  sectionIndex: number | null;
}

export const MAX_GENIE_ANSWER_MEMORY = 500;

const NO_VOTE: GenieVoteMemory = { status: 'idle', direction: null, requestIds: {} };
const NO_EXPORT: GenieExportLatchMemory = { recording: false, status: null };

/** One insertion-ordered map, so the cap evicts the oldest entry of either kind. */
const entries = new Map<string, GenieVoteMemory | GenieExportLatchMemory>();
const listeners = new Set<() => void>();
/** Bumped by every clear: a request begun before it settles into nothing. */
let epoch = 0;

function emit(): void {
  for (const listener of listeners) listener();
}

function write(key: string, value: GenieVoteMemory | GenieExportLatchMemory): void {
  entries.delete(key);
  entries.set(key, value);
  if (entries.size > MAX_GENIE_ANSWER_MEMORY) {
    const oldest = entries.keys().next().value;
    if (oldest !== undefined) entries.delete(oldest);
  }
  emit();
}

function subscribe(listener: () => void): () => void {
  listeners.add(listener);
  return () => {
    listeners.delete(listener);
  };
}

export function genieVoteKey(conversationId: string, messageId: string): string {
  return `vote|${conversationId}:${messageId}`;
}

export function genieExportLatchKey(target: GenieExportLatchTarget): string {
  return `csv|${target.conversationId}:${target.messageId}:${target.scope}:${target.sectionIndex ?? '-'}`;
}

function readVote(key: string): GenieVoteMemory {
  return (entries.get(key) as GenieVoteMemory | undefined) ?? NO_VOTE;
}

function readExport(key: string): GenieExportLatchMemory {
  return (entries.get(key) as GenieExportLatchMemory | undefined) ?? NO_EXPORT;
}

/** The vote for `key` (null: no answer to vote on). */
export function useGenieVote(key: string | null): GenieVoteMemory {
  return useSyncExternalStore(
    subscribe,
    () => (key === null ? NO_VOTE : readVote(key)),
    () => NO_VOTE,
  );
}

export function useGenieExportLatch(key: string): GenieExportLatchMemory {
  return useSyncExternalStore(subscribe, () => readExport(key), () => NO_EXPORT);
}

export interface GenieVoteStart {
  readonly requestId: string;
  readonly epoch: number;
}

/**
 * The synchronous vote latch: null while a vote is pending or recorded.
 * Otherwise marks it pending and returns the direction's request id, minted
 * only the first time that direction is voted.
 */
export function beginGenieVote(key: string, direction: GenieVoteDirection, mint: () => string): GenieVoteStart | null {
  const current = readVote(key);
  if (current.status !== 'idle') return null;
  const requestId = current.requestIds[direction] ?? mint();
  write(key, { status: 'pending', direction, requestIds: { ...current.requestIds, [direction]: requestId } });
  return { requestId, epoch };
}

/** Recorded, or back to idle with its request ids kept. False (and nothing
 *  written) when the memory was cleared since the vote began. */
export function settleGenieVote(key: string, start: GenieVoteStart, recorded: boolean): boolean {
  if (start.epoch !== epoch) return false;
  const current = readVote(key);
  write(key, { status: recorded ? 'recorded' : 'idle', direction: recorded ? current.direction : null, requestIds: current.requestIds });
  return true;
}

/** The synchronous export latch: the epoch, or null while one is recording. */
export function beginGenieExport(key: string): number | null {
  if (readExport(key).recording) return null;
  write(key, { recording: true, status: null });
  return epoch;
}

/** The outcome's fixed copy; false (nothing written) after a clear. */
export function settleGenieExport(key: string, startedEpoch: number, status: string): boolean {
  if (startedEpoch !== epoch) return false;
  write(key, { recording: false, status });
  return true;
}

export function clearGenieAnswerMemory(): void {
  epoch += 1;
  if (entries.size === 0) return;
  entries.clear();
  emit();
}

/** Entries held (tests and the cap). */
export function genieAnswerMemorySize(): number {
  return entries.size;
}

// AppShell calls clearActorScopedMemoryCaches when the signed-in actor changes.
registerActorScopedMemoryCache(clearGenieAnswerMemory);
