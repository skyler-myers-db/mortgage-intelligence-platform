import { useCallback, useEffect, useRef, useState, type AnimationEvent } from 'react';
import {
  subscribeGenieTurnSettled,
  type GenieInFlightTurn,
  type GenieTurnNote,
} from '../../lib/genieInFlightTurn';
import { prefersReducedMotion } from './useGenieTranscriptScroll';

/** The keyframes name in GenieAnswerReading.css. */
export const GENIE_MESSAGE_ENTRANCE_ANIMATION = 'genie-msg-in';
/** Slack past the entrance duration before the class is dropped anyway. */
const ENTRANCE_GRACE_MS = 100;
/** --dur-base, the entrance duration, when the token cannot be read. */
const FALLBACK_ENTRANCE_MS = 200;

function entranceMs(): number {
  if (typeof document === 'undefined') return FALLBACK_ENTRANCE_MS;
  const raw = window.getComputedStyle(document.documentElement).getPropertyValue('--dur-base').trim();
  const amount = Number.parseFloat(raw);
  if (!Number.isFinite(amount)) return FALLBACK_ENTRANCE_MS;
  return raw.endsWith('ms') ? amount : amount * 1000;
}

export interface GenieMessageEntrance {
  /** A landed answer (by response object) or a Stopped note is entering. */
  entering: (key: object) => boolean;
  /** The pending question bubble of in-flight turn `generation` is entering. */
  userEntering: (generation: number) => boolean;
  /** Enter a bubble this surface appended itself (a governed-action result). */
  mark: (key: object) => void;
  /** animationend handler for an entering bubble: drop its class. */
  onEntered: (key: object | number) => (event: AnimationEvent<HTMLElement>) => void;
}

/**
 * The one-shot entrance of a new Genie message (audit 2026-09-21
 * `motion-v2`): opacity and a --sp-1 rise over --dur-base, keyframes
 * `genie-msg-in` in the lazy GenieAnswerReading.css. It marks, by object
 * identity and never with a store field (critic fix 10):
 *   - an answer the turn store settled (subscribeGenieTurnSettled), only
 *     when this surface is visible at that moment (`isVisible`: the panel is
 *     open, or /ask-genie shows its Ask tab);
 *   - the pending question bubble of a NEW in-flight turn, keyed by its
 *     generation, never a resumed one;
 *   - a Stopped note (never an Interrupted one);
 *   - a bubble the surface appends itself (`mark`: the panel's governed
 *     action result).
 * A History restore or a hydrated transcript fires none of these, so it
 * never animates. Nothing is marked under prefers-reduced-motion (the CSS
 * also sets animation: none). The class is dropped on animationend, or after
 * the duration plus a grace, so reopening the panel never replays it.
 */
export function useGenieMessageEntrance({
  isVisible,
  inFlight,
  notes,
}: {
  isVisible: () => boolean;
  inFlight: GenieInFlightTurn | null;
  notes: readonly GenieTurnNote[];
}): GenieMessageEntrance {
  const [entering, setEntering] = useState<readonly object[]>([]);
  const [userGeneration, setUserGeneration] = useState<number | null>(null);
  // A question in flight or a note already present at mount never enters.
  const generation = inFlight?.revealed ? inFlight.generation : null;
  const [seenGeneration, setSeenGeneration] = useState<number | null>(generation);
  const [seenNotes, setSeenNotes] = useState<readonly GenieTurnNote[]>(notes);
  if (generation !== null && generation !== seenGeneration) {
    setSeenGeneration(generation);
    if (inFlight && !inFlight.resumed && isVisible() && !prefersReducedMotion()) setUserGeneration(generation);
  }
  if (notes !== seenNotes) {
    setSeenNotes(notes);
    const stopped = notes.filter((note) => note.kind === 'stopped' && !seenNotes.includes(note));
    if (stopped.length > 0 && isVisible() && !prefersReducedMotion()) setEntering([...entering, ...stopped]);
  }

  const visibleRef = useRef(isVisible);
  useEffect(() => {
    visibleRef.current = isVisible;
  });

  const mark = useCallback((key: object) => {
    if (prefersReducedMotion()) return;
    setEntering((current) => (current.includes(key) ? current : [...current, key]));
  }, []);

  useEffect(
    () =>
      subscribeGenieTurnSettled((event) => {
        if (visibleRef.current()) mark(event.response);
      }),
    [mark],
  );

  // The fallback: whatever is still entering after the duration plus a grace
  // is dropped (an animationend never arrives from a display:none panel).
  useEffect(() => {
    if (entering.length === 0 && userGeneration === null) return undefined;
    const timer = window.setTimeout(() => {
      setEntering([]);
      setUserGeneration(null);
    }, entranceMs() + ENTRANCE_GRACE_MS);
    return () => window.clearTimeout(timer);
  }, [entering, userGeneration]);

  const onEntered = (key: object | number) => (event: AnimationEvent<HTMLElement>) => {
    // Charts and chips inside the bubble animate too; only its own entrance counts.
    if (event.target !== event.currentTarget || event.animationName !== GENIE_MESSAGE_ENTRANCE_ANIMATION) return;
    if (typeof key === 'number') setUserGeneration((current) => (current === key ? null : current));
    else setEntering((current) => current.filter((item) => item !== key));
  };

  return {
    entering: (key) => entering.includes(key),
    userEntering: (gen) => userGeneration === gen,
    mark,
    onEntered,
  };
}
