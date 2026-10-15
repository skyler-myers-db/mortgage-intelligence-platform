/**
 * @vitest-environment happy-dom
 *
 * The Sales ops per-officer workload (2026-09-21 audit critic-10 item 6c):
 * every in-scope officer, sortable numeric columns with aria-sort, the Lead
 * Queue link per officer, no email in the DOM, skeletons before the read
 * settles and the shared failure copy. The Sales ops tab owns the read
 * (analytics.sales-ops.test.tsx pins its key and window); the table renders
 * the state it is handed.
 */
import { act } from 'react';
import { createRoot, type Root } from 'react-dom/client';
import { MemoryRouter } from 'react-router';
import { afterEach, beforeAll, beforeEach, describe, expect, it } from 'vitest';
import { ApiError } from '../../lib/apiTransport';
import { signedCount } from '../../lib/formatters';
import type { SalesWorkloadResponse } from '../../lib/apiClients/salesWorkload';
import { preloadDescribedError } from '../ui/DescribedError';
import SalesWorkloadTable, { type SalesWorkloadState } from './SalesWorkloadTable';

const row = (n: number, open: number, stale: number, assigned: [number, number], worked: [number, number]) => ({
  email: `lo0${n}@summit.example`,
  display_label: `Summit LO 0${n}`,
  region: n === 3 ? null : 'IL',
  capacity_per_day: 20 + n,
  open_count: open,
  stale_count: stale,
  assigned_count: assigned[0],
  previous_assigned_count: assigned[1],
  worked_count: worked[0],
  previous_worked_count: worked[1],
});

const RESPONSE: SalesWorkloadResponse = {
  from_date: '2026-07-08',
  to_date: '2026-07-14',
  previous_from_date: '2026-07-01',
  previous_to_date: '2026-07-07',
  stale_days: 7,
  rows: [row(1, 4, 1, [3, 1], [2, 2]), row(2, 9, 0, [5, 7], [4, 1]), row(3, 4, 2, [0, 0], [0, 3])],
};

const CONVERSION = [
  { group_key: 'LO02@summit.example', calls_attempted: 30, contacts_reached: 10, callbacks_scheduled: 1, applications_started: 3, unique_application_starts: 2, application_start_rate: 0.2 },
];

let root: Root;

const LOADED: SalesWorkloadState = { isError: false, error: null, data: RESPONSE };

async function render(conversion: typeof CONVERSION | null = CONVERSION, workload: SalesWorkloadState = LOADED): Promise<void> {
  await act(async () => {
    root.render(
      <MemoryRouter>
        <SalesWorkloadTable workload={workload} days={7} conversion={conversion} />
      </MemoryRouter>,
    );
  });
}

const officers = () => [...document.querySelectorAll('tbody tr')].map((tr) => tr.querySelector('td a')?.textContent ?? null);
const header = (label: string) =>
  [...document.querySelectorAll<HTMLTableCellElement>('thead th')].find((th) => th.textContent?.startsWith(label))!;

describe('SalesWorkloadTable', () => {
  beforeAll(async () => {
    await preloadDescribedError();
  });

  beforeEach(() => {
    document.body.innerHTML = '<div id="root"></div>';
    root = createRoot(document.getElementById('root') as HTMLElement);
  });

  afterEach(() => {
    act(() => root.unmount());
    document.body.innerHTML = '';
  });

  it('lists every officer, Open descending, ties by label', async () => {
    await render();
    expect(officers()).toEqual(['Summit LO 02', 'Summit LO 01', 'Summit LO 03']);
    expect(header('Open').getAttribute('aria-sort')).toBe('descending');
    expect(header('Stale').getAttribute('aria-sort')).toBe('none');
  });

  it('sorts by a numeric column on its header button, toggling direction', async () => {
    await render();
    const stale = header('Stale').querySelector('button')!;
    act(() => stale.click());
    expect(header('Stale').getAttribute('aria-sort')).toBe('descending');
    expect(header('Open').getAttribute('aria-sort')).toBe('none');
    expect(officers()).toEqual(['Summit LO 03', 'Summit LO 01', 'Summit LO 02']);
    act(() => stale.click());
    expect(header('Stale').getAttribute('aria-sort')).toBe('ascending');
    expect(officers()).toEqual(['Summit LO 02', 'Summit LO 01', 'Summit LO 03']);
  });

  it('links each officer to the Lead Queue by its assigned_to filter, and no email reaches the DOM', async () => {
    await render();
    const links = [...document.querySelectorAll<HTMLAnchorElement>('tbody a')];
    expect(links.map((link) => link.getAttribute('href'))).toEqual([
      '/lead-queue?assigned_to=lo02%40summit.example',
      '/lead-queue?assigned_to=lo01%40summit.example',
      '/lead-queue?assigned_to=lo03%40summit.example',
    ]);
    expect(document.body.textContent).not.toContain('@summit.example');
    expect(document.querySelectorAll('[title]')).toHaveLength(0);
    expect([...document.querySelectorAll('[aria-label]')].map((node) => node.getAttribute('aria-label')).join(' ')).not.toContain('@');
  });

  it('shows the change against the previous span, and app starts only where a conversion row exists', async () => {
    await render();
    const cells = (name: string) => {
      const tr = [...document.querySelectorAll('tbody tr')].find((candidate) => candidate.textContent?.includes(name))!;
      return [...tr.querySelectorAll('td')].map((td) => td.textContent);
    };
    expect(cells('Summit LO 02')).toEqual([
      'Summit LO 02', 'IL', '22', '9', '0',
      `5 ${signedCount(-2)} vs previous 7 days`,
      `4 ${signedCount(3)} vs previous 7 days`,
      '2', '20.0%',
    ]);
    expect(cells('Summit LO 03').slice(1, 2)).toEqual(['—']);
    expect(cells('Summit LO 03').slice(7)).toEqual(['—', '—']);
  });

  it('sets every numeric column in the numeric cell face its header carries', async () => {
    await render();
    const numeric = [...document.querySelectorAll('thead th')].map((th) => th.classList.contains('num'));
    expect(numeric).toEqual([false, false, true, true, true, true, true, true, true]);
    for (const tr of document.querySelectorAll('tbody tr')) {
      expect([...tr.querySelectorAll('td')].map((td) => td.classList.contains('num'))).toEqual(numeric);
    }
  });

  it('shows skeletons, never a zero, before the read settles', async () => {
    await render(null, { isError: false, error: null, data: undefined });
    expect(document.querySelectorAll('tbody .skeleton').length).toBe(3);
    expect(document.querySelector('tbody')?.textContent).toBe('');
  });

  it('says why the read failed in the shared vocabulary, never the transport text', async () => {
    const error = new ApiError('SENTINEL detail', { path: '/api/v1/sales/workload', status: 403 });
    await render(CONVERSION, { isError: true, error, data: undefined });
    const alert = document.querySelector('[role="alert"]');
    expect(alert?.textContent).toContain('Workload unavailable:');
    expect(document.body.textContent).not.toContain('SENTINEL');
  });

  it('says the officer list is empty rather than drawing an empty table', async () => {
    await render(CONVERSION, { isError: false, error: null, data: { ...RESPONSE, rows: [] } });
    expect(document.querySelector('tbody')?.textContent).toBe('No active loan officers in your scope.');
  });
});
