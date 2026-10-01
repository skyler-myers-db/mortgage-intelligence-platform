/**
 * @vitest-environment happy-dom
 */
/**
 * SignalStack (audit wow-stage-5) in the rendered DOM: every state of the
 * read, the headline and the largest overlap, the disclosure, the UpSet
 * columns' accessible names, the table alternative and the evidence chip.
 */
import { act, type ReactNode } from 'react';
import { createRoot, type Root } from 'react-dom/client';
import { MemoryRouter } from 'react-router';
import { QueryClientProvider } from '@tanstack/react-query';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { ApiError } from '../../lib/api';
import { createMipQueryClient } from '../../lib/queryClient';
import type { SegmentCombinationResponse } from '../../types/segmentCombinations';
import { SignalStack } from './SignalStack';

(globalThis as unknown as { IS_REACT_ACT_ENVIRONMENT: boolean }).IS_REACT_ACT_ENVIRONMENT = true;

const mocks = vi.hoisted(() => ({ combinations: vi.fn(), setDrawer: vi.fn() }));
vi.mock('../../lib/apiClients/segmentCombinations', () => ({
  segmentCombinationsApi: { combinations: mocks.combinations },
}));
vi.mock('../AppContext', () => ({
  useApp: () => ({ setDrawer: mocks.setDrawer, showEvidence: true, showConfidence: true }),
}));

const PROVENANCE = {
  source: 'mip.gold.segment_combination_rollup',
  contactable_source: 'mip.gold.borrower_360 (live eligibility predicate, per request)',
  refreshed_at: '2026-07-14 12:00:00',
  note: 'n',
};

const BUILT: SegmentCombinationResponse = {
  built: true,
  core_codes: ['itm', 'listed', 'permit', 'investor', 'equity', 'retention'],
  combinations: [
    { segment_codes: ['itm'], signal_count: 1, addressable: 9305, contactable: 874 },
    { segment_codes: ['itm', 'equity'], signal_count: 2, addressable: 2100, contactable: 240 },
    { segment_codes: ['itm', 'permit', 'equity'], signal_count: 3, addressable: 520, contactable: 61 },
    { segment_codes: ['itm', 'investor', 'retention'], signal_count: 3, addressable: 180, contactable: 19 },
    { segment_codes: ['itm', 'listed', 'equity', 'retention'], signal_count: 4, addressable: 95, contactable: 12 },
  ],
  provenance: PROVENANCE,
};

let client = createMipQueryClient();
function Providers({ children }: { children: ReactNode }) {
  return (
    <QueryClientProvider client={client}>
      <MemoryRouter>{children}</MemoryRouter>
    </QueryClientProvider>
  );
}

async function settle(): Promise<void> {
  await act(async () => {
    await new Promise((resolve) => window.setTimeout(resolve, 5));
  });
}

async function until(check: () => boolean): Promise<void> {
  for (let i = 0; i < 300 && !check(); i += 1) await settle();
  expect(check()).toBe(true);
}

const text = () => (document.querySelector('.signal-stack')?.textContent ?? '').replace(/\s+/g, ' ');
const button = (name: string) => [...document.querySelectorAll<HTMLButtonElement>('.signal-stack button')].find((b) => b.textContent === name);

describe('SignalStack', () => {
  let root: Root;

  beforeEach(() => {
    document.body.innerHTML = '<div id="root"></div>';
    root = createRoot(document.getElementById('root') as HTMLElement);
    client = createMipQueryClient();
  });

  afterEach(() => {
    act(() => root.unmount());
    document.body.innerHTML = '';
    vi.clearAllMocks();
  });

  const render = () => act(async () => root.render(<Providers><SignalStack /></Providers>));

  it('reserves its place while loading', async () => {
    mocks.combinations.mockReturnValue(new Promise(() => undefined));
    await render();
    expect(document.querySelector('.signal-stack')?.getAttribute('aria-busy')).toBe('true');
    expect(text()).toContain('Loading the signal stack');
    expect(document.querySelector('.signal-stack h2')?.textContent).toBe('Signal stack');
  });

  it('says it is not built yet', async () => {
    mocks.combinations.mockResolvedValue({ ...BUILT, built: false, combinations: [] });
    await render();
    await until(() => text().includes('not built yet'));
    expect(text()).toContain(
      'The signal stack is not built yet: the gold refresh job builds it (deploy or Admin > Data operations).',
    );
  });

  it('says so when nobody fires three or more signals', async () => {
    mocks.combinations.mockResolvedValue({ ...BUILT, combinations: BUILT.combinations.slice(0, 2) });
    await render();
    await until(() => text().includes('No borrower fires three or more signals in this refresh.'));
    expect(button('Show combinations')).toBeUndefined();
  });

  it('shows a failure with Retry, never a number', async () => {
    mocks.combinations.mockRejectedValue(new ApiError('boom', { path: '/api/segments/combinations', status: 500 }));
    await render();
    await until(() => [...document.querySelectorAll('.signal-stack button')].some((b) => b.textContent === 'Retry'));
    expect(text()).not.toMatch(/\d+ borrowers fire/);
    mocks.combinations.mockResolvedValue(BUILT);
    await act(async () => button('Retry')?.click());
    await until(() => text().includes('borrowers fire three or more signals at once'));
  });

  it('leads with the three-plus headline and the largest overlap, linked into the Lead Queue', async () => {
    mocks.combinations.mockResolvedValue(BUILT);
    await render();
    await until(() => text().includes('borrowers fire'));
    expect(text()).toContain('795 borrowers fire three or more signals at once. 92 of them are contactable.');
    expect(text()).toContain(
      'Largest overlap: Prime Refi Candidates, HELOC Intent and Home Equity Candidate: 520 borrowers carry all three (61 contactable).',
    );
    const link = [...document.querySelectorAll<HTMLAnchorElement>('.signal-stack a')].find((a) => a.textContent === 'Open in Lead Queue');
    expect(link?.getAttribute('href')).toBe('/lead-queue?segment_codes=itm%2Cpermit%2Cequity&segment_mode=all');
    expect(text()).toContain(
      'Whole book, six core segments; not narrowed by the filters below. The Lead Queue lists only contact-eligible borrowers scoring 50 or more.',
    );
  });

  it('says the contactable count is unavailable rather than inventing one', async () => {
    mocks.combinations.mockResolvedValue({
      ...BUILT,
      combinations: BUILT.combinations.map((row) => (row.signal_count === 3 ? { ...row, contactable: null } : row)),
    });
    await render();
    await until(() => text().includes('borrowers fire'));
    expect(text()).toContain('795 borrowers fire three or more signals at once. Contactable count unavailable.');
    expect(text()).toContain('(contactable count unavailable)');
  });

  it('reveals the UpSet strip behind a disclosure, then its table', async () => {
    mocks.combinations.mockResolvedValue(BUILT);
    await render();
    await until(() => button('Show combinations') !== undefined);
    const disclosure = button('Show combinations') as HTMLButtonElement;
    const panel = document.getElementById(disclosure.getAttribute('aria-controls') ?? '');
    expect(disclosure.getAttribute('aria-expanded')).toBe('false');
    expect(panel?.hidden).toBe(true);
    await act(async () => disclosure.click());
    expect(disclosure.getAttribute('aria-expanded')).toBe('true');
    expect(panel?.hidden).toBe(false);

    const columns = [...document.querySelectorAll<HTMLAnchorElement>('.signal-stack__column-link')];
    expect(columns).toHaveLength(4);
    expect(columns[0].getAttribute('aria-label')).toBe(
      'Prime Refi Candidates and Home Equity Candidate: 2,100 borrowers carry exactly these signals, 2,715 carry at least these, 313 contactable. Open in Lead Queue',
    );
    expect(columns[0].getAttribute('href')).toBe('/lead-queue?segment_codes=itm%2Cequity&segment_mode=all');
    expect(document.querySelector('.signal-stack__dots')?.getAttribute('aria-hidden')).toBe('true');
    expect(columns[0].querySelectorAll('.signal-stack__dot.is-on')).toHaveLength(2);

    await act(async () => button('View as table')?.click());
    const table = document.querySelector('.signal-stack table.tbl');
    expect(table?.querySelector('caption')?.textContent).toBe('Every exact combination of the six core signals, whole book');
    expect([...(table?.querySelectorAll('thead th') ?? [])].map((th) => th.textContent)).toEqual([
      'Signals', 'Exactly these', 'At least these', 'Contactable (at least)', 'Lead Queue',
    ]);
    const first = table?.querySelector('tbody tr');
    expect([...(first?.querySelectorAll('th, td') ?? [])].map((cell) => cell.textContent)).toEqual([
      'Prime Refi Candidates', '9,305', '12,200', '1,206', 'Open',
    ]);
    expect(table?.querySelector('[data-testid="signal-stack-total"]')?.textContent).toBe('12,200');
    expect(button('View as chart')).toBeDefined();
  });

  it('opens the evidence drawer on the gold table', async () => {
    mocks.combinations.mockResolvedValue(BUILT);
    await render();
    await until(() => text().includes('borrowers fire'));
    const chip = document.querySelector<HTMLButtonElement>('.signal-stack .surface__hdr button');
    await act(async () => chip?.click());
    expect(mocks.setDrawer).toHaveBeenCalledWith(expect.objectContaining({
      assetPath: 'mip.gold.segment_combination_rollup',
      lineageFamily: 'segment_population',
    }));
  });
});
