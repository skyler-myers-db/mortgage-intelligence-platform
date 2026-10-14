/**
 * A page-side record of one exit retained by hooks/useExitRetained: close an
 * overlay by clicking its trigger, then report what held while it faded, its
 * own transitionend (and what still held then), any transitioncancel, and
 * when the node left the DOM. Bounded: the page answers within `boundMs`
 * even if the exit never ends, so a missing transitionend fails the test with
 * the timings below instead of hanging it until the test timeout (CI run
 * 38565698599: Linux WebKit, 60 s on an unbounded `await transitionend`).
 *
 * Times are wall clock (performance.now; the harness freezes Date only) in
 * ms after the close. `slowTo` sets the playbackRate of the element's own
 * CSS transitions once the exit starts, so the document timeline runs the
 * exit later than its computed duration: the condition a starved renderer
 * creates, and the one the hook's deadline must not cut short.
 */
import type { Locator } from '@playwright/test';

/** hooks/useExitRetained TRANSITION_END_GRACE_MS: the first deadline is the computed exit plus this. */
export const EXIT_GRACE_MS = 120;
const EXIT_BOUND_MS = 10_000;
/** How long past the first deadline the `pastDeadline` sample is taken. */
const PAST_DEADLINE_MARGIN_MS = 40;

export interface ExitObservation {
  computedMs: number;
  during: { exiting: boolean; inert: boolean; hidden: string | null; connected: boolean };
  pastDeadline: { connected: boolean; exiting: boolean; running: boolean } | null;
  end: { afterMs: number; property: string; exiting: boolean; inert: boolean; connected: boolean } | null;
  removedAfterMs: number | null;
  cancels: string[];
  boundMs: number;
}

export interface ExitOptions {
  /** Selector of the control whose click closes the overlay. */
  trigger: string;
  /** The class the component adds while the exit runs. */
  exitingClass: string;
  slowTo?: number;
}

interface PageOptions extends Required<ExitOptions> {
  boundMs: number;
  graceMs: number;
  marginMs: number;
}

async function observeInPage(element: Element, options: PageOptions): Promise<ExitObservation> {
  const node = element as HTMLElement;
  const exiting = () => node.classList.contains(options.exitingClass);
  const running = () =>
    node.getAnimations().some((animation) => animation instanceof CSSTransition && animation.playState === 'running');
  let closedAt = performance.now();
  const since = () => Math.round(performance.now() - closedAt);
  const result: ExitObservation = {
    computedMs: 0,
    during: { exiting: false, inert: false, hidden: null, connected: false },
    pastDeadline: null,
    end: null,
    removedAfterMs: null,
    cancels: [],
    boundMs: options.boundMs,
  };
  let settle: () => void = () => undefined;
  const settled = new Promise<void>((resolve) => {
    settle = resolve;
  });
  node.addEventListener('transitionend', (event) => {
    if (event.target !== node || result.end) return;
    result.end = {
      afterMs: since(),
      property: (event as TransitionEvent).propertyName,
      exiting: exiting(),
      inert: node.hasAttribute('inert'),
      connected: node.isConnected,
    };
  });
  node.addEventListener('transitioncancel', (event) => {
    if (event.target === node) result.cancels.push(`${(event as TransitionEvent).propertyName} at +${since()} ms`);
  });
  const removal = new MutationObserver(() => {
    if (node.isConnected || result.removedAfterMs !== null) return;
    result.removedAfterMs = since();
    removal.disconnect();
    settle();
  });
  removal.observe(document.body, { childList: true, subtree: true });
  const bound = window.setTimeout(settle, options.boundMs);

  closedAt = performance.now();
  document.querySelector<HTMLElement>(options.trigger)?.click();
  // The close commits after the click returns; wait (bounded) for the exit to start.
  await new Promise<void>((resolve) => {
    if (exiting()) return resolve();
    const started = new MutationObserver(() => {
      if (!exiting() && node.isConnected) return;
      started.disconnect();
      resolve();
    });
    started.observe(node, { attributes: true, attributeFilter: ['class'] });
    void settled.then(() => resolve());
  });
  if (options.slowTo !== 1) {
    for (const animation of node.getAnimations()) {
      if (animation instanceof CSSTransition) animation.playbackRate = options.slowTo;
    }
  }
  result.computedMs = Math.max(
    0,
    ...getComputedStyle(node).transitionDuration.split(',').map((part) => {
      const value = Number.parseFloat(part);
      return part.trim().endsWith('ms') ? value : value * 1000;
    }),
  );
  result.during = {
    exiting: exiting(),
    inert: node.hasAttribute('inert'),
    hidden: node.getAttribute('aria-hidden'),
    connected: node.isConnected,
  };
  window.setTimeout(() => {
    result.pastDeadline = { connected: node.isConnected, exiting: exiting(), running: running() };
  }, result.computedMs + options.graceMs + options.marginMs);

  await settled;
  window.clearTimeout(bound);
  // WebKit reports a removed node's cancelled transitions after the removal.
  await new Promise((resolve) => window.setTimeout(resolve, 50));
  return result;
}

/** Close the overlay `target` through `trigger` and record its exit. */
export function observeExit(target: Locator, options: ExitOptions): Promise<ExitObservation> {
  return target.evaluate(observeInPage, {
    slowTo: 1,
    ...options,
    boundMs: EXIT_BOUND_MS,
    graceMs: EXIT_GRACE_MS,
    marginMs: PAST_DEADLINE_MARGIN_MS,
  });
}

/** The measured story of an exit, for assertion messages. */
export function describeExit(exit: ExitObservation): string {
  const end = exit.end ? `${exit.end.property} transitionend at +${exit.end.afterMs} ms` : 'no transitionend of its own';
  const removed =
    exit.removedAfterMs === null ? `still mounted after ${exit.boundMs} ms` : `left the DOM at +${exit.removedAfterMs} ms`;
  const cancels = exit.cancels.length > 0 ? exit.cancels.join(', ') : 'none';
  const past = exit.pastDeadline ? JSON.stringify(exit.pastDeadline) : 'not sampled';
  return (
    `computed exit ${exit.computedMs} ms (first deadline +${exit.computedMs + EXIT_GRACE_MS} ms); ${end}; ${removed}; ` +
    `transitioncancel: ${cancels}; just past the first deadline: ${past}`
  );
}
