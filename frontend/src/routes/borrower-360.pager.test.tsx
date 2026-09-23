/**
 * @vitest-environment happy-dom
 *
 * Borrower 360 queue pager (audit 2026-09-21 shell-04): "3 of 23" with
 * Previous / Next and J / K, scoped like the queue's row shortcuts (never in
 * an editable field, never with an overlay open, never on auto-repeat).
 * Rendered through the real router; the fixture spec proves it on the built
 * dossier.
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
      <output id="where">{`${location.pathname}|${JSON.stringify(location.state)}`}</output>
      <input aria-label="notes" />
      {id && <BorrowerQueuePager borrowerId={id} queue={queue} />}
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
  const press = (key: string, init: KeyboardEventInit = {}, target: Element = document.body) =>
    act(() => {
      target.dispatchEvent(new KeyboardEvent('keydown', { key, bubbles: true, cancelable: true, ...init }));
    });

  it('shows the position in the queue and disables Previous at the top', async () => {
    await renderAt(IDS[0]);
    expect(pager()?.textContent).toContain('1 of 3 in IL');
    expect(button('Previous').disabled).toBe(true);
    expect(button('Next').disabled).toBe(false);
    expect(button('Next').getAttribute('aria-keyshortcuts')).toBe('J');
  });

  it('J opens the next borrower and K the previous one, carrying the queue', async () => {
    await renderAt(IDS[1]);
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

  it('ignores J / K typed into a field, with a modifier, on auto-repeat or with an overlay open', async () => {
    await renderAt(IDS[1]);
    const input = container.querySelector('input')!;
    act(() => input.focus());
    press('j', {}, input);
    act(() => input.blur());
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
    press('j');
    expect(where().split('|')[0]).toBe('/borrower-360/B-9999999999999');
    await renderAt(IDS[0], null);
    expect(pager()).toBeNull();
  });
});
