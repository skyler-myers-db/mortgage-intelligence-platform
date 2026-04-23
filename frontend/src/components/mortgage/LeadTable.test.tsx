import { describe, expect, it } from 'vitest';
import { isEditableTarget } from './LeadTable';

/**
 * LeadTable hotkey-bailout guard (R5-12, 2026-04-23).
 *
 * The window-level keydown handler must NEVER swallow letters typed
 * into text inputs, textareas, selects, or contenteditable regions —
 * otherwise typing "a" into the Genie chat fires the approve hotkey.
 *
 * The runtime DOM listener lives inside a `useEffect` closure so we
 * can't unit-test it in a node environment. Instead the helper is
 * factored out as `isEditableTarget` and covered exhaustively here;
 * the handler itself also checks `document.activeElement` through
 * the same helper as a belt-and-suspenders defense.
 *
 * This matters because vitest runs under `environment: 'node'` — we
 * can't mount the component and drive real keyboard events. Covering
 * the pure predicate keeps the guard from regressing silently.
 */

describe('isEditableTarget (LeadTable hotkey bailout)', () => {
  const fakeElement = (tag: string, contentEditable = false): Element => {
    return {
      tagName: tag,
      isContentEditable: contentEditable,
    } as unknown as Element;
  };

  it('returns false for null / undefined (no focused element)', () => {
    expect(isEditableTarget(null)).toBe(false);
    expect(isEditableTarget(undefined)).toBe(false);
  });

  it('returns true for INPUT, TEXTAREA, SELECT', () => {
    expect(isEditableTarget(fakeElement('INPUT'))).toBe(true);
    expect(isEditableTarget(fakeElement('TEXTAREA'))).toBe(true);
    expect(isEditableTarget(fakeElement('SELECT'))).toBe(true);
  });

  it('returns true for contenteditable DIV (Genie rich-text fallback)', () => {
    expect(isEditableTarget(fakeElement('DIV', true))).toBe(true);
  });

  it('returns false for BODY, DIV (not contenteditable), BUTTON, TR', () => {
    expect(isEditableTarget(fakeElement('BODY'))).toBe(false);
    expect(isEditableTarget(fakeElement('DIV', false))).toBe(false);
    expect(isEditableTarget(fakeElement('BUTTON'))).toBe(false);
    expect(isEditableTarget(fakeElement('TR'))).toBe(false);
  });
});

/**
 * useRef in-flight latch (R5-04, 2026-04-23).
 *
 * The component uses `useRef<boolean>` for the approve/bulk-approve
 * in-flight latch because `setState` is async — two clicks in the
 * same frame both see `bulkApproving=false` and spawn parallel
 * loops, each writing an audit row per borrower.
 *
 * The ref is a primitive boolean — the test below pins the
 * invariant: a synchronous flip-and-check gate excludes a second
 * caller, and reset on completion allows a later retry.
 */

function makeLatch(): {
  tryEnter: () => boolean;
  release: () => void;
  snapshot: () => boolean;
} {
  const ref = { current: false };
  return {
    tryEnter: () => {
      if (ref.current) return false;
      ref.current = true;
      return true;
    },
    release: () => {
      ref.current = false;
    },
    snapshot: () => ref.current,
  };
}

describe('synchronous in-flight latch shape', () => {
  it('lets the first caller enter and blocks a concurrent caller', () => {
    const latch = makeLatch();
    expect(latch.tryEnter()).toBe(true);
    expect(latch.tryEnter()).toBe(false);
    expect(latch.snapshot()).toBe(true);
  });

  it('releases cleanly so a retry after completion is allowed', () => {
    const latch = makeLatch();
    expect(latch.tryEnter()).toBe(true);
    latch.release();
    expect(latch.snapshot()).toBe(false);
    expect(latch.tryEnter()).toBe(true);
  });
});
