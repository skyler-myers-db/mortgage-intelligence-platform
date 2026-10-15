import { act } from 'react';

/**
 * Test-only stand-in for a CSS exit transition (motion-01 item 2e). The test
 * DOM has no stylesheet and no Web Animations, so hooks/useExitRetained drops
 * a closing overlay at once there. `runExitOn(element)` makes the element look
 * like it is running its own transition: an inline transition (so
 * exitTransitionMs is non-zero) and a stubbed `getAnimations()` holding one
 * running CSSTransition. `finish()` ends it the way the browser does: the
 * animation stops running, then `transitionend` fires on the element.
 */
class FakeCssTransition {
  constructor(public playState: AnimationPlayState) {}
}

type AnimatedElement = { getAnimations: () => FakeCssTransition[] };

/** Installs the global CSSTransition the hook checks; returns the uninstall. */
export function installFakeCssTransition(): () => void {
  const scope = globalThis as unknown as { CSSTransition?: unknown };
  const prior = scope.CSSTransition;
  scope.CSSTransition = FakeCssTransition;
  return () => {
    if (prior === undefined) delete scope.CSSTransition;
    else scope.CSSTransition = prior;
  };
}

export interface RunningExit {
  /** End the exit: the transition stops running and `transitionend` fires. */
  finish: () => void;
}

/** Call BEFORE the close that starts the exit. */
export function runExitOn(element: HTMLElement): RunningExit {
  let animations = [new FakeCssTransition('running')];
  element.style.transitionProperty = 'opacity';
  element.style.transitionDuration = '216ms';
  (element as unknown as AnimatedElement).getAnimations = () => animations;
  return {
    finish: () => {
      animations = [new FakeCssTransition('finished')];
      act(() => {
        element.dispatchEvent(new Event('transitionend', { bubbles: true }));
      });
    },
  };
}
