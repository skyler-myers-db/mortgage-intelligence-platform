/**
 * A page-side probe for same-document View Transitions (2026-09-21 audit
 * stack-04 / motion-03 / runtime-10 / css-10 / shell-10, phase 1; used by
 * motion-nav.fixture.spec.ts).
 *
 * An init script wraps `document.startViewTransition` before the app boots,
 * counts every call (React's route transitions and lib/themeTransition's
 * theme cross-fade alike) and records, per transition:
 *  - at `updateCallbackDone`: `.main`'s scrollTop and the painted route
 *    marker (the new snapshot is taken from exactly this state);
 *  - at `ready`: every `::view-transition-*` animation the browser runs, with
 *    its pseudo-element, its end time (delay + duration) and, for group
 *    animations, the largest translation any of its keyframes applies;
 *  - whether it finished.
 * It also records every CSS animation that STARTS on a `.route-transition`
 * wrapper (route-in), from an animationstart listener, since a 200 ms
 * animation is gone before most reads.
 * Every promise it touches is caught, so a skipped transition never becomes
 * an unhandled rejection the hygiene gate would report.
 */
import type { Page } from '@playwright/test';

export interface PseudoAnimation {
  pseudo: string;
  endMs: number;
  /** Largest distance in px a group travels between its keyframes (group animations only). */
  maxShiftPx: number;
}

export interface TransitionRecord {
  updateDone: { scrollTop: number | null; paintedPath: string | null } | null;
  ready: PseudoAnimation[] | null;
  finished: boolean;
}

export interface ViewTransitionLog {
  supported: boolean;
  calls: number;
  transitions: TransitionRecord[];
  /** Every CSS animation that STARTED on a `.route-transition` wrapper (class: name). */
  routeAnimations: string[];
}

type ProbeWindow = Window & { __mipViewTransitions?: ViewTransitionLog };

/** Install the probe; call before the first `goto`. */
export async function installViewTransitionProbe(page: Page): Promise<void> {
  await page.addInitScript(() => {
    type Starter = (arg?: unknown) => ViewTransition;
    const doc = document as Document & { startViewTransition?: Starter };
    const original = typeof doc.startViewTransition === 'function' ? doc.startViewTransition.bind(doc) : null;
    const log = { supported: original !== null, calls: 0, transitions: [] as unknown[], routeAnimations: [] as string[] };
    (window as ProbeWindow).__mipViewTransitions = log as ViewTransitionLog;
    // route-in lasts 200 ms, so a later getAnimations() read can miss it;
    // the start event cannot.
    document.addEventListener('animationstart', (event) => {
      const target = event.target;
      if (target instanceof Element && target.classList.contains('route-transition')) {
        log.routeAnimations.push(`${target.className}: ${event.animationName}`);
      }
    }, true);
    if (!original) return;

    // How far a group moves: its keyframes place it at its snapshot's
    // viewport position, so the movement is the distance between frames.
    const shiftOf = (keyframes: ComputedKeyframe[]): number => {
      const points = keyframes.map((frame) => {
        const transform = typeof frame.transform === 'string' ? frame.transform : 'none';
        const matrix = new DOMMatrixReadOnly(transform === 'none' ? undefined : transform);
        return [matrix.m41, matrix.m42] as const;
      });
      let max = 0;
      for (const [x, y] of points) {
        max = Math.max(max, Math.abs(x - points[0][0]), Math.abs(y - points[0][1]));
      }
      return max;
    };

    doc.startViewTransition = (arg?: unknown) => {
      log.calls += 1;
      const transition = original(arg);
      const record: TransitionRecord = { updateDone: null, ready: null, finished: false };
      log.transitions.push(record);
      transition.updateCallbackDone.then(
        () => {
          const main = document.querySelector('#main-content');
          record.updateDone = {
            scrollTop: main instanceof HTMLElement ? main.scrollTop : null,
            paintedPath: main?.querySelector('.route-transition[data-route-path]')?.getAttribute('data-route-path') ?? null,
          };
        },
        () => undefined,
      );
      transition.ready.then(
        () => {
          record.ready = document.documentElement
            .getAnimations({ subtree: true })
            .flatMap((animation) => {
              const effect = animation.effect;
              if (!(effect instanceof KeyframeEffect)) return [];
              const pseudo = effect.pseudoElement ?? '';
              if (!pseudo.startsWith('::view-transition')) return [];
              const timing = effect.getComputedTiming();
              const duration = typeof timing.duration === 'number' ? timing.duration : 0;
              return [{
                pseudo,
                endMs: (timing.delay ?? 0) + duration,
                maxShiftPx: pseudo.startsWith('::view-transition-group') ? shiftOf(effect.getKeyframes()) : 0,
              }];
            });
        },
        () => {
          record.ready = [];
        },
      );
      transition.finished.then(
        () => {
          record.finished = true;
        },
        () => {
          record.finished = true;
        },
      );
      return transition;
    };
  });
}

export async function readViewTransitions(page: Page): Promise<ViewTransitionLog> {
  return page.evaluate(() => {
    const log = (window as ProbeWindow).__mipViewTransitions;
    return log
      ? JSON.parse(JSON.stringify(log)) as ViewTransitionLog
      : { supported: false, calls: 0, transitions: [], routeAnimations: [] };
  });
}

/** Wait until every recorded transition has finished (a skipped one counts). */
export async function waitForViewTransitionsToFinish(page: Page): Promise<ViewTransitionLog> {
  await page.waitForFunction(() => {
    const log = (window as ProbeWindow).__mipViewTransitions;
    return !log || log.transitions.every((record) => record.finished && record.ready !== null);
  });
  return readViewTransitions(page);
}

/** CSS animations that started on a route wrapper (painted or fallback) after `before` was read. */
export function newRouteAnimations(after: ViewTransitionLog, before?: ViewTransitionLog): string[] {
  return after.routeAnimations.slice(before?.routeAnimations.length ?? 0);
}
