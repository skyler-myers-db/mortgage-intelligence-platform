/**
 * @vitest-environment happy-dom
 *
 * The shared queue pager (audit 2026-09-21 shell-04 / flow-09): the same
 * "n of N ranked in <label>" and J / K contract Borrower 360 pins
 * (routes/borrower-360.pager.test.tsx), generalized over the destination
 * (`pathFor`) so the Offer Orchestrator steps offer to offer. Offer turns the
 * keys off while the reject rationale is open (`hotkeys`) and the whole pager
 * off while a decision is on the wire (`disabled`).
 */

import { act } from 'react';
import { createRoot, type Root } from 'react-dom/client';
import { MemoryRouter, Route, Routes, useLocation, useParams } from 'react-router';
import { afterEach, beforeEach, describe, expect, it } from 'vitest';
import type { QueueContext } from '../../lib/queueContext';
import { offerPath } from '../../lib/routeMeta';
import { QueuePager } from './QueuePager';

(globalThis as unknown as { IS_REACT_ACT_ENVIRONMENT: boolean }).IS_REACT_ACT_ENVIRONMENT = true;

const IDS = ['B-0000000000001', 'B-0000000000002', 'B-0000000000003'];
const QUEUE: QueueContext = { epoch: 'e1', search: '?state=IL', label: 'IL', ids: IDS };

interface PageProps {
  hotkeys?: boolean;
  disabled?: boolean;
}

function OfferPage({ hotkeys, disabled }: PageProps) {
  const { id } = useParams();
  const location = useLocation();
  return (
    <main tabIndex={-1}>
      <h1 tabIndex={-1}>Offer</h1>
      <output id="where">{`${location.pathname}|${JSON.stringify(location.state)}`}</output>
      {id && (
        <QueuePager borrowerId={id} queue={QUEUE} pathFor={offerPath} hotkeys={hotkeys} disabled={disabled} />
      )}
    </main>
  );
}

describe('QueuePager', () => {
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
  });

  async function renderAt(id: string, props: PageProps = {}) {
    await act(async () => {
      root.render(
        <MemoryRouter initialEntries={[`/offer-orchestrator/${id}`]}>
          <Routes>
            <Route path="/offer-orchestrator/:id" element={<OfferPage {...props} />} />
          </Routes>
        </MemoryRouter>,
      );
    });
  }

  const where = () => (container.querySelector('#where')?.textContent ?? '').split('|');
  const pager = () => container.querySelector('nav[aria-label="Lead queue position"]');
  const button = (name: string) =>
    Array.from(container.querySelectorAll<HTMLButtonElement>('button')).find((b) => b.textContent?.trim() === name)!;
  const pressJ = () => {
    const heading = container.querySelector<HTMLElement>('main h1')!;
    act(() => heading.focus());
    act(() => {
      heading.dispatchEvent(new KeyboardEvent('keydown', { key: 'j', bubbles: true, cancelable: true }));
    });
  };

  it('steps offer to offer through pathFor, carrying the queue in the link state', async () => {
    await renderAt(IDS[1]);
    expect(pager()?.textContent).toContain('2 of 3 ranked in IL');
    act(() => button('Next').click());
    expect(where()[0]).toBe(`/offer-orchestrator/${IDS[2]}`);
    expect(JSON.parse(where()[1]).queue.ids).toEqual(IDS);
    expect(pager()?.textContent).toContain('3 of 3 ranked in IL');
    act(() => button('Previous').click());
    expect(where()[0]).toBe(`/offer-orchestrator/${IDS[1]}`);
  });

  it('counts through lib/formatters: a four-digit queue reads grouped', async () => {
    const ids = Array.from({ length: 1000 }, (_, index) => `B-${String(index + 1).padStart(13, '0')}`);
    const big: QueueContext = { ...QUEUE, ids };
    await act(async () => {
      root.render(
        <MemoryRouter>
          <QueuePager borrowerId={ids[999]} queue={big} pathFor={offerPath} />
        </MemoryRouter>,
      );
    });
    expect(pager()?.textContent).toContain('1,000 of 1,000 ranked in IL');
  });

  it('J pages by default', async () => {
    await renderAt(IDS[0]);
    pressJ();
    expect(where()[0]).toBe(`/offer-orchestrator/${IDS[1]}`);
  });

  it('hotkeys=false turns J / K off but keeps the buttons', async () => {
    await renderAt(IDS[0], { hotkeys: false });
    pressJ();
    expect(where()[0]).toBe(`/offer-orchestrator/${IDS[0]}`);
    expect(button('Next').disabled).toBe(false);
    act(() => button('Next').click());
    expect(where()[0]).toBe(`/offer-orchestrator/${IDS[1]}`);
  });

  it('disabled=true turns the buttons and the keys off, and still shows the position', async () => {
    await renderAt(IDS[1], { disabled: true });
    expect(pager()?.textContent).toContain('2 of 3 ranked in IL');
    expect(button('Previous').disabled).toBe(true);
    expect(button('Next').disabled).toBe(true);
    pressJ();
    expect(where()[0]).toBe(`/offer-orchestrator/${IDS[1]}`);
  });
});
