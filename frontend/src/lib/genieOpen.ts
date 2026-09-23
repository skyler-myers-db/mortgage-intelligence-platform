/**
 * `openGenie({ prompt })`: open the floating Genie panel with a question
 * already in its composer (audit 2026-09-21 `genie-04`, phase 1).
 *
 * It PREFILLS; it never submits. The user reads the question, edits it if
 * they like, and presses Ask, so the prompt of record is exactly what the
 * server-side guards scan. Nothing in this module (or anything it calls) may
 * ever send a question.
 *
 * Kept tiny and React-free on purpose: the shell (GenieDock, the command
 * palette) imports it into the initial chunk, and a KPI card rendered without
 * the app provider (unit tests, Storybook) can still carry an entry point.
 * The reviewed templates live in `genieContext.ts`, which only lazy route
 * chunks import.
 *
 * Opening travels as a window event that `GenieDock` turns into
 * `setGenieOpen(true)`; the prefill waits in this module until the mounted
 * panel consumes it -- when it opens, or at once when it is already open.
 * A conversation reset (`GENIE_CONVERSATION_RESET_EVENT`: an actor boundary,
 * or New thread) drops a prefill nobody consumed, e.g. one queued while the
 * panel was closed or its chunk failed to load, so one actor's question can
 * never surface in the next actor's composer. An open panel consumes a
 * prefill at once, so New thread never races a visible one.
 */

import { GENIE_CONVERSATION_RESET_EVENT } from './genieConversation';

export interface OpenGenieOptions {
  /** The composer text. Prefilled verbatim; never submitted. */
  prompt: string;
}

/** Fired on `window` by `openGenie`; the dock opens the panel on it. */
export const GENIE_OPEN_REQUEST_EVENT = 'mip:genie-open-request';

let pendingPrefill: string | null = null;
const prefillListeners = new Set<() => void>();
let resetListenerInstalled = false;

/** Installed on the first queued prefill, not at import (no load-time side effect). */
function dropPrefillOnActorBoundaryReset(): void {
  if (resetListenerInstalled || typeof window === 'undefined') return;
  resetListenerInstalled = true;
  window.addEventListener(GENIE_CONVERSATION_RESET_EVENT, () => {
    pendingPrefill = null;
  });
}

/** Queue a composer prefill for the panel. Replaces any earlier one. */
export function requestGeniePrefill(prompt: string): void {
  dropPrefillOnActorBoundaryReset();
  pendingPrefill = prompt;
  for (const listener of prefillListeners) listener();
}

/** Take the queued prefill (once). Null when nothing is waiting. */
export function consumeGeniePrefill(): string | null {
  const prompt = pendingPrefill;
  pendingPrefill = null;
  return prompt;
}

export function subscribeGeniePrefill(listener: () => void): () => void {
  prefillListeners.add(listener);
  return () => {
    prefillListeners.delete(listener);
  };
}

/**
 * Open the floating Genie panel with `prompt` in the composer. The user still
 * has to press Ask: nothing here submits.
 */
export function openGenie({ prompt }: OpenGenieOptions): void {
  const text = prompt.trim();
  if (!text) return;
  requestGeniePrefill(text);
  if (typeof window === 'undefined') return;
  window.dispatchEvent(new Event(GENIE_OPEN_REQUEST_EVENT));
}

/** Listen for `openGenie` requests (the dock mounts exactly one listener). */
export function subscribeGenieOpenRequests(onOpen: () => void): () => void {
  if (typeof window === 'undefined') return () => undefined;
  const listener = () => onOpen();
  window.addEventListener(GENIE_OPEN_REQUEST_EVENT, listener);
  return () => window.removeEventListener(GENIE_OPEN_REQUEST_EVENT, listener);
}
