import { useCallback, useEffect, useRef, useState, type RefObject } from 'react';
import { prefersReducedMotion } from './genieMotion';

/** How close to the end still counts as "following" (about one --fs-13 line). */
export const GENIE_FOLLOW_SLACK_PX = 24;

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

export interface GenieTranscriptScroll {
  /** The next settled answer should be brought into view (a turn settled). */
  anchorNextAnswer: () => void;
  /** Forget a pending anchor and the "New answer" notice. */
  cancelAnchor: () => void;
  /** This panel just sent a question: the reader follows the transcript again. */
  markSent: () => void;
  /** An answer landed while the reader was reading further up. */
  newAnswer: boolean;
  /** Scroll the new answer's start into view and focus its bubble. */
  jumpToNewAnswer: () => void;
}

/** The answer's top edge sits inside the scroller's visible box. */
function answerTopInView(body: HTMLElement, answer: HTMLElement): boolean {
  const view = body.getBoundingClientRect();
  const top = answer.getBoundingClientRect().top;
  return top >= view.top - 1 && top < view.bottom;
}

/**
 * Transcript scrolling for the floating Genie panel (audit 2026-09-21
 * `motion-v2`, then `genie-08`).
 *
 * The panel used to set `scrollTop = scrollHeight` on every change, and later
 * scrolled every landed answer's START into view. Both moved a reader who
 * had scrolled up to an earlier turn. Now the panel tracks whether the reader
 * is FOLLOWING the transcript (within GENIE_FOLLOW_SLACK_PX of its end, read
 * from a passive scroll listener; set again whenever this panel sends and
 * whenever it opens):
 *   - following: a question being sent, progress appearing, or the panel
 *     opening sticks to the bottom, and a landed answer scrolls its START
 *     into view, once (on the next open if it landed while closed);
 *   - reading further up: nothing moves. A landed answer raises `newAnswer`
 *     instead, which the panel shows as a "New answer" button; it clears when
 *     the button is used or when the reader scrolls the answer's top into
 *     view.
 * Smooth scrolling is used only for an answer landing in an open panel, and
 * never under `prefers-reduced-motion`. The /ask-genie route does not use
 * this hook: its reveal key holds when an answer lands (visual-07).
 */
export function useGenieTranscriptScroll({
  open,
  bodyRef,
  lastAnswerRef,
  messages,
  pendingQuestion,
  busy,
}: UseGenieTranscriptScrollOptions): GenieTranscriptScroll {
  const anchorPendingRef = useRef(false);
  const wasOpenRef = useRef(false);
  const followingRef = useRef(true);
  const newAnswerRef = useRef(false);
  const [newAnswer, setNewAnswerState] = useState(false);
  const setNewAnswer = useCallback((next: boolean) => {
    newAnswerRef.current = next;
    setNewAnswerState(next);
  }, []);

  useEffect(() => {
    const body = bodyRef.current;
    if (!body) return undefined;
    const onScroll = () => {
      followingRef.current = body.scrollHeight - body.scrollTop - body.clientHeight <= GENIE_FOLLOW_SLACK_PX;
      const answer = lastAnswerRef.current;
      if (newAnswerRef.current && answer && answerTopInView(body, answer)) setNewAnswer(false);
    };
    body.addEventListener('scroll', onScroll, { passive: true });
    return () => body.removeEventListener('scroll', onScroll);
  }, [bodyRef, lastAnswerRef, setNewAnswer]);

  useEffect(() => {
    const opening = open && !wasOpenRef.current;
    wasOpenRef.current = open;
    const body = bodyRef.current;
    // Closed panel: leave the pending anchor armed for the next open.
    if (!body || !open) return;
    if (opening) followingRef.current = true;
    const answer = lastAnswerRef.current;
    if (anchorPendingRef.current && answer && typeof answer.scrollIntoView === 'function') {
      anchorPendingRef.current = false;
      if (!followingRef.current) {
        setNewAnswer(true);
        return;
      }
      setNewAnswer(false);
      answer.scrollIntoView({
        block: 'start',
        behavior: opening || prefersReducedMotion() ? 'auto' : 'smooth',
      });
      return;
    }
    if (followingRef.current) body.scrollTop = body.scrollHeight;
  }, [bodyRef, busy, lastAnswerRef, messages, open, pendingQuestion, setNewAnswer]);

  const anchorNextAnswer = useCallback(() => {
    anchorPendingRef.current = true;
  }, []);
  const cancelAnchor = useCallback(() => {
    anchorPendingRef.current = false;
    setNewAnswer(false);
  }, [setNewAnswer]);
  const markSent = useCallback(() => {
    followingRef.current = true;
    setNewAnswer(false);
  }, [setNewAnswer]);
  const jumpToNewAnswer = useCallback(() => {
    setNewAnswer(false);
    const answer = lastAnswerRef.current;
    if (!answer) return;
    answer.scrollIntoView({ block: 'start', behavior: prefersReducedMotion() ? 'auto' : 'smooth' });
    answer.focus({ preventScroll: true });
  }, [lastAnswerRef, setNewAnswer]);
  return { anchorNextAnswer, cancelAnchor, markSent, newAnswer, jumpToNewAnswer };
}
