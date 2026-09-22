import { useCallback, useEffect, useRef, type RefObject } from 'react';

function prefersReducedMotion(): boolean {
  if (typeof window === 'undefined' || typeof window.matchMedia !== 'function') return false;
  return window.matchMedia('(prefers-reduced-motion: reduce)').matches;
}

interface UseGenieTranscriptScrollOptions {
  open: boolean;
  bodyRef: RefObject<HTMLElement | null>;
  /** The most recent settled answer bubble. */
  lastAnswerRef: RefObject<HTMLElement | null>;
  /** The three things whose change re-lays the transcript out: the settled
   *  messages, the pending question bubble, and the progress bubble. They are
   *  effect dependencies only; the effect reads the DOM, not these values. */
  messages: unknown;
  pendingQuestion: string | null;
  busy: boolean;
}

/**
 * Transcript scrolling for the floating Genie panel (audit 2026-09-21
 * `motion-v2`).
 *
 * The panel used to set `scrollTop = scrollHeight` on every change. Answers
 * are Summary-first and routinely taller than the 640px panel, so a new answer
 * landed with its Summary scrolled off the top and the follow-up chips in
 * view. Now:
 *   - a question being sent, progress appearing, or the panel opening still
 *     sticks to the bottom (that is where the new content is);
 *   - a landed answer scrolls its START into view, once. If it landed while
 *     the panel was closed, that happens when the panel next opens.
 * Smooth scrolling is used only for an answer landing in an open panel, and
 * never under `prefers-reduced-motion`.
 */
export function useGenieTranscriptScroll({
  open,
  bodyRef,
  lastAnswerRef,
  messages,
  pendingQuestion,
  busy,
}: UseGenieTranscriptScrollOptions): { anchorNextAnswer: () => void; cancelAnchor: () => void } {
  const anchorPendingRef = useRef(false);
  const wasOpenRef = useRef(false);

  useEffect(() => {
    const opening = open && !wasOpenRef.current;
    wasOpenRef.current = open;
    const body = bodyRef.current;
    // Closed panel: leave the pending anchor armed for the next open.
    if (!body || !open) return;
    const answer = lastAnswerRef.current;
    if (anchorPendingRef.current && answer && typeof answer.scrollIntoView === 'function') {
      anchorPendingRef.current = false;
      answer.scrollIntoView({
        block: 'start',
        behavior: opening || prefersReducedMotion() ? 'auto' : 'smooth',
      });
      return;
    }
    body.scrollTop = body.scrollHeight;
  }, [bodyRef, busy, lastAnswerRef, messages, open, pendingQuestion]);

  const anchorNextAnswer = useCallback(() => {
    anchorPendingRef.current = true;
  }, []);
  const cancelAnchor = useCallback(() => {
    anchorPendingRef.current = false;
  }, []);
  return { anchorNextAnswer, cancelAnchor };
}
