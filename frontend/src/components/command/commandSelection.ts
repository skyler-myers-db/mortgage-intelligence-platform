/**
 * The selection context a page publishes for Cmd-K verbs (audit
 * wow-power-4): "Approve 12 selected…", "Assign selected…".
 *
 * A module store rather than a React context: the publisher (LeadTable,
 * inside a route) and the consumer (the palette, in the shell) live in
 * different subtrees. The publisher hands over `run`, which calls the SAME
 * guarded handlers its own toolbar uses (the bulk rationale gate, the
 * assignment control), so a verb can never become a parallel approval path.
 * Only one selection is published at a time; the latest publisher wins and
 * its cleanup never clears a newer one.
 */
import { useSyncExternalStore } from 'react';

export type CommandVerb = 'approve-selected' | 'assign-selected';

export interface CommandSelectionContext {
  /** Rows selected on the page. */
  selectedCount: number;
  /** Selected rows eligible for approval (what the approve gate would run). */
  approveCount: number;
  /**
   * The actor may approve right now (approverGate says so, no campaign
   * binding blocks it, no bulk run in flight). False hides the approve verb.
   */
  canApprove: boolean;
  /** An assignment target exists and nothing is in flight. */
  canAssign: boolean;
  run: (verb: CommandVerb) => void;
}

let current: CommandSelectionContext | null = null;
const subscribers = new Set<() => void>();

function notify(): void {
  subscribers.forEach((callback) => callback());
}

/** Publish (or replace) the selection; returns a cleanup that clears only this one. */
export function publishCommandSelection(context: CommandSelectionContext): () => void {
  current = context;
  notify();
  return () => {
    if (current !== context) return;
    current = null;
    notify();
  };
}

export function currentCommandSelection(): CommandSelectionContext | null {
  return current;
}

function subscribe(callback: () => void): () => void {
  subscribers.add(callback);
  return () => {
    subscribers.delete(callback);
  };
}

export function useCommandSelection(): CommandSelectionContext | null {
  return useSyncExternalStore(subscribe, currentCommandSelection, () => null);
}
