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
import { installLocalStorage } from '../../test/installLocalStorage';
import { formatCount } from '../../lib/formatters';
import { KEYMAP_SCOPE_LABELS, listKeyBindings } from '../../lib/keymap';
import { clearSingleKeyShortcutsPreference, setSingleKeyShortcutsEnabled } from '../../lib/keymapPreference';
import { MAX_QUEUE_CONTEXT_IDS, type QueueContext } from '../../lib/queueContext';
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
    installLocalStorage();
    clearSingleKeyShortcutsPreference();
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

  async function renderQueueOf(length: number) {
    const ids = Array.from({ length }, (_, index) => `B-${String(index + 1).padStart(13, '0')}`);
    const big: QueueContext = { ...QUEUE, ids };
    await act(async () => {
      root.render(
        <MemoryRouter>
          <QueuePager borrowerId={ids[length - 1]} queue={big} pathFor={offerPath} />
        </MemoryRouter>,
      );
    });
  }

  it('counts through lib/formatters: a four-digit queue reads grouped', async () => {
    await renderQueueOf(MAX_QUEUE_CONTEXT_IDS);
    const cap = formatCount(MAX_QUEUE_CONTEXT_IDS);
    expect(pager()?.textContent).toContain(`${cap} of the first ${cap} ranked in IL`);
  });

  it('a queue that reached the published cap says it walks only the first ranked ids', async () => {
    await renderQueueOf(MAX_QUEUE_CONTEXT_IDS);
    expect(pager()?.textContent).toContain(`of the first ${formatCount(MAX_QUEUE_CONTEXT_IDS)} ranked`);
    await renderQueueOf(MAX_QUEUE_CONTEXT_IDS - 1);
    expect(pager()?.textContent).not.toContain('the first');
    expect(pager()?.textContent).toContain(
      `${formatCount(MAX_QUEUE_CONTEXT_IDS - 1)} of ${formatCount(MAX_QUEUE_CONTEXT_IDS - 1)} ranked in IL`,
    );
  });

  it('names J / K in a Tooltip and aria-keyshortcuts, never a DOM title (wow-power-4)', async () => {
    await renderAt(IDS[1]);
    for (const [name, key, text] of [['Previous', 'K', 'Previous borrower'], ['Next', 'J', 'Next borrower']] as const) {
      const control = button(name);
      expect(control.hasAttribute('title')).toBe(false);
      expect(control.getAttribute('aria-keyshortcuts')).toBe(key);
      expect(control.getAttribute('data-tooltip-shortcut')).toBe(key);
      const description = document.getElementById(control.getAttribute('data-tooltip') ?? '');
      expect(description?.textContent).toBe(`${text} (${key})`);
    }
  });

  it('lists J / K in the queue-pager scope only while each one can page', async () => {
    await renderAt(IDS[0]);
    const pagerBindings = () => listKeyBindings().filter((binding) => binding.scope === 'queue-pager')
      .map((binding) => [binding.id, binding.keys.join(' '), binding.description]);
    expect(KEYMAP_SCOPE_LABELS['queue-pager']).toBe('Queue pager');
    // The top of the queue: no previous borrower, so no K.
    expect(pagerBindings()).toEqual([['queue-pager-next', 'j', 'Next borrower in the queue']]);
    act(() => button('Next').click());
    expect(pagerBindings()).toEqual(expect.arrayContaining([
      ['queue-pager-next', 'j', 'Next borrower in the queue'],
      ['queue-pager-previous', 'k', 'Previous borrower in the queue'],
    ]));
  });

  it('the Console single-key switch turns J / K and their names off', async () => {
    await renderAt(IDS[0]);
    act(() => setSingleKeyShortcutsEnabled(false));
    pressJ();
    expect(where()[0]).toBe(`/offer-orchestrator/${IDS[0]}`);
    expect(button('Next').hasAttribute('aria-keyshortcuts')).toBe(false);
    expect(button('Next').hasAttribute('data-tooltip-shortcut')).toBe(false);
    act(() => setSingleKeyShortcutsEnabled(true));
    pressJ();
    expect(where()[0]).toBe(`/offer-orchestrator/${IDS[1]}`);
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
    expect(button('Next').hasAttribute('aria-keyshortcuts')).toBe(false);
    expect(listKeyBindings().some((binding) => binding.scope === 'queue-pager')).toBe(false);
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
