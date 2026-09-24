/**
 * @vitest-environment happy-dom
 *
 * Borrower 360 queue pager (audit 2026-09-21 shell-04): "3 of 23" with
 * Previous / Next and J / K, scoped like the queue's row shortcuts: only while
 * focus is inside the dossier's <main>, never in an editable field, never
 * with an overlay open, never on auto-repeat. Rendered through the real
 * router inside a shell-shaped page (a topbar control outside <main>); the
 * fixture spec proves it on the built dossier.
 */

import { act } from 'react';
import { createRoot, type Root } from 'react-dom/client';
import { MemoryRouter, Route, Routes, useLocation, useParams } from 'react-router';
import { afterEach, beforeEach, describe, expect, it } from 'vitest';
import type { QueueContext } from '../lib/queueContext';
import { BorrowerQueuePager } from './borrower-360.pager';

(globalThis as unknown as { IS_REACT_ACT_ENVIRONMENT: boolean }).IS_REACT_ACT_ENVIRONMENT = true;

const IDS = ['B-0000000000001', 'B-0000000000002', 'B-0000000000003'];
const QUEUE: QueueContext = { epoch: 'e1', search: '?state=IL', label: 'IL', ids: IDS };

function Dossier({ queue }: { queue: QueueContext | null }) {
  const { id } = useParams();
  const location = useLocation();
  return (
    <>
      <header>
        <button type="button">Toggle console</button>
      </header>
      <main tabIndex={-1}>
        <h1 tabIndex={-1}>Borrower 360</h1>
        <output id="where">{`${location.pathname}|${JSON.stringify(location.state)}`}</output>
        <input aria-label="notes" />
        {id && <BorrowerQueuePager borrowerId={id} queue={queue} />}
      </main>
    </>
  );
}

describe('BorrowerQueuePager', () => {
  let container: HTMLDivElement;
  let root: Root;

  beforeEach(() => {
    container = document.createElement('div');
    document.body.appendChild(container);
    root = createRoot(container);
  });

  afterEach(() => {
    act(() => root.unmount());
    container.remove();
    document.querySelectorAll('[data-test-overlay]').forEach((el) => el.remove());
  });

  async function renderAt(id: string, queue: QueueContext | null = QUEUE) {
    await act(async () => {
      root.render(
        <MemoryRouter initialEntries={[`/borrower-360/${id}`]}>
          <Routes>
            <Route path="/borrower-360/:id" element={<Dossier queue={queue} />} />
          </Routes>
        </MemoryRouter>,
      );
    });
  }

  const where = () => container.querySelector('#where')?.textContent ?? '';
  const pager = () => container.querySelector('nav[aria-label="Lead queue position"]');
  const button = (name: string) =>
    Array.from(container.querySelectorAll<HTMLButtonElement>('button')).find((b) => b.textContent?.trim() === name)!;
  /** Focus the page heading, where a route change leaves focus (useRouteAnnouncer). */
  const focusDossier = () => {
    const heading = container.querySelector<HTMLElement>('main h1')!;
    act(() => heading.focus());
    return heading;
  };
  /** Keydown on the focused element, as a real keystroke would target it. */
  const press = (key: string, init: KeyboardEventInit = {}, target: Element = document.activeElement ?? document.body) =>
    act(() => {
      target.dispatchEvent(new KeyboardEvent('keydown', { key, bubbles: true, cancelable: true, ...init }));
    });

  it('shows the position in the queue and disables Previous at the top', async () => {
    await renderAt(IDS[0]);
    expect(pager()?.textContent).toContain('1 of 3 ranked in IL');
    expect(button('Previous').disabled).toBe(true);
    expect(button('Next').disabled).toBe(false);
    expect(button('Next').getAttribute('aria-keyshortcuts')).toBe('J');
  });

  it('J opens the next borrower and K the previous one, carrying the queue', async () => {
    await renderAt(IDS[1]);
    focusDossier();
    press('j');
    expect(where().split('|')[0]).toBe(`/borrower-360/${IDS[2]}`);
    expect(JSON.parse(where().split('|')[1]).queue.ids).toEqual(IDS);
    expect(pager()?.textContent).toContain('3 of 3');
    press('j'); // end of the queue: nothing to open
    expect(where().split('|')[0]).toBe(`/borrower-360/${IDS[2]}`);
    press('K', { shiftKey: false });
    expect(where().split('|')[0]).toBe(`/borrower-360/${IDS[1]}`);
  });

  it('the Next button steps too', async () => {
    await renderAt(IDS[0]);
    act(() => button('Next').click());
    expect(where().split('|')[0]).toBe(`/borrower-360/${IDS[1]}`);
  });

  it('keys are live only while focus is inside the dossier: not from the topbar, not on an unfocused page', async () => {
    await renderAt(IDS[1]);
    // A non-editable control outside <main> (the topbar's Console toggle).
    const topbarButton = button('Toggle console');
    act(() => topbarButton.focus());
    press('j', {}, topbarButton);
    expect(where().split('|')[0]).toBe(`/borrower-360/${IDS[1]}`);
    // Nothing focused: the keystroke targets <body>.
    act(() => topbarButton.blur());
    expect(document.activeElement).toBe(document.body);
    press('j', {}, document.body);
    expect(where().split('|')[0]).toBe(`/borrower-360/${IDS[1]}`);
    // A keystroke whose target is in the dossier while focus sits outside it.
    const heading = container.querySelector('main h1')!;
    act(() => topbarButton.focus());
    press('j', {}, heading);
    expect(where().split('|')[0]).toBe(`/borrower-360/${IDS[1]}`);
    // Non-vacuity: the same key from inside the dossier pages.
    press('j', {}, focusDossier());
    expect(where().split('|')[0]).toBe(`/borrower-360/${IDS[2]}`);
  });

  it('ignores J / K typed into a field, with a modifier, on auto-repeat or with an overlay open', async () => {
    await renderAt(IDS[1]);
    const input = container.querySelector('input')!;
    act(() => input.focus());
    press('j', {}, input);
    focusDossier();
    press('j', { ctrlKey: true });
    press('j', { shiftKey: true });
    press('j', { repeat: true });
    const dialog = document.createElement('div');
    dialog.setAttribute('role', 'dialog');
    dialog.setAttribute('data-test-overlay', '');
    document.body.appendChild(dialog);
    press('j');
    expect(where().split('|')[0]).toBe(`/borrower-360/${IDS[1]}`);
    // A closed overlay (aria-hidden) no longer holds the keys.
    dialog.setAttribute('aria-hidden', 'true');
    press('j');
    expect(where().split('|')[0]).toBe(`/borrower-360/${IDS[2]}`);
  });

  it('renders nothing and binds no keys without a queue that lists the borrower', async () => {
    await renderAt('B-9999999999999');
    expect(pager()).toBeNull();
    focusDossier();
    press('j');
    expect(where().split('|')[0]).toBe('/borrower-360/B-9999999999999');
    await renderAt(IDS[0], null);
    expect(pager()).toBeNull();
  });
});
