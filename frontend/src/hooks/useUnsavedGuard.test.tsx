/**
 * @vitest-environment happy-dom
 */
import { act, type ReactNode } from 'react';
import { createRoot, type Root } from 'react-dom/client';
import { afterEach, beforeEach, describe, expect, it } from 'vitest';
import { DEFAULT_UNSAVED_MESSAGE, unsavedWorkMessage, useUnsavedGuard } from './useUnsavedGuard';

function Guarded({ dirty, message }: { dirty: boolean; message?: string }) {
  useUnsavedGuard(dirty, message);
  return null;
}

/** Dispatch a cancelable beforeunload the way the browser does; true = the page asked to stay. */
function unloadIsBlocked(): boolean {
  const event = new Event('beforeunload', { cancelable: true });
  window.dispatchEvent(event);
  return event.defaultPrevented;
}

describe('useUnsavedGuard (audit states-05)', () => {
  let root: Root;

  beforeEach(() => {
    document.body.innerHTML = '<div id="root"></div>';
    root = createRoot(document.getElementById('root') as HTMLElement);
  });

  afterEach(() => {
    act(() => root.unmount());
    document.body.innerHTML = '';
  });

  function render(node: ReactNode): void {
    act(() => root.render(node));
  }

  it('asks before a tab close only while the page is dirty', () => {
    render(<Guarded dirty={false} />);
    expect(unloadIsBlocked()).toBe(false);
    expect(unsavedWorkMessage()).toBeNull();

    render(<Guarded dirty />);
    expect(unloadIsBlocked()).toBe(true);
    expect(unsavedWorkMessage()).toBe(DEFAULT_UNSAVED_MESSAGE);

    render(<Guarded dirty={false} />);
    expect(unloadIsBlocked()).toBe(false);
    expect(unsavedWorkMessage()).toBeNull();
  });

  it('drops the guard when the dirty page unmounts', () => {
    render(<Guarded dirty message="Your note is not saved." />);
    expect(unsavedWorkMessage()).toBe('Your note is not saved.');
    render(null);
    expect(unloadIsBlocked()).toBe(false);
    expect(unsavedWorkMessage()).toBeNull();
  });

  it('keeps asking while any page is still dirty after another one is saved', () => {
    const both = (a: boolean, b: boolean) => (
      <>
        <Guarded dirty={a} message="A is not saved." />
        <Guarded dirty={b} message="B is not saved." />
      </>
    );
    render(both(true, true));
    expect(unsavedWorkMessage()).toBe('B is not saved.');

    render(both(false, true));
    expect(unloadIsBlocked()).toBe(true);
    expect(unsavedWorkMessage()).toBe('B is not saved.');

    render(both(true, false));
    expect(unloadIsBlocked()).toBe(true);
    expect(unsavedWorkMessage()).toBe('A is not saved.');

    render(both(false, false));
    expect(unloadIsBlocked()).toBe(false);
  });

  it('re-registers with the new message when what is unsaved changes', () => {
    render(<Guarded dirty message="Filters not run." />);
    render(<Guarded dirty message="Filters not run and setup not saved." />);
    expect(unsavedWorkMessage()).toBe('Filters not run and setup not saved.');
    expect(unloadIsBlocked()).toBe(true);
  });
});
