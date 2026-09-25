/**
 * @vitest-environment happy-dom
 *
 * Every row and column of a Genie answer (audit 2026-09-21 `genie-06`, slice
 * 2), in place of the capped compact table: "Show all" swaps the table for a
 * windowed, scrollable region that holds every column, mounts only the rows
 * in view, keeps the table's true size in aria-rowcount, and reaches the
 * last row by scrolling. "Show fewer" brings the compact table back.
 */
import { act } from 'react';
import { createRoot, type Root } from 'react-dom/client';
import { MemoryRouter } from 'react-router';
import { afterEach, beforeEach, describe, expect, it } from 'vitest';
import { pickPlan } from './GenieAnswer.logic';
import { GenieRowsVisual } from './GenieAnswer.sections';
import type { GenieAnswer } from '../../types';

(globalThis as unknown as { IS_REACT_ACT_ENVIRONMENT: boolean }).IS_REACT_ACT_ENVIRONMENT = true;

const WIDE_ROWS = Array.from({ length: 120 }, (_, i) => ({
  state: ['IL', 'TX', 'OH', 'FL'][i % 4],
  county_name: `County ${String(i + 1).padStart(3, '0')}`,
  borrowers: 5000 - i,
  avg_score: 70 + (i % 9),
  avg_rate_spread_bps: 80 + (i % 30),
  avg_equity_pct: 30 + (i % 20),
  contactable: 100 + i,
}));

async function waitFor(condition: () => boolean, timeoutMs = 5_000) {
  const startedAt = Date.now();
  while (!condition()) {
    if (Date.now() - startedAt > timeoutMs) throw new Error('waitFor timeout');
    await act(async () => new Promise((resolve) => setTimeout(resolve, 10)));
  }
}

describe('Show all rows and columns (genie-06 slice 2)', () => {
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

  function render(rows: Array<Record<string, unknown>>, reportedRowCount: number | null = null, dense = false) {
    const plan = pickPlan({ answer: '' } as GenieAnswer, rows, Object.keys(rows[0]));
    act(() =>
      root.render(
        <MemoryRouter>
          <GenieRowsVisual rows={rows} plan={plan} reportedRowCount={reportedRowCount} dense={dense} />
        </MemoryRouter>,
      ),
    );
  }

  const showAll = () => container.querySelector<HTMLButtonElement>('button.genie-answer__show-all');
  const region = () => container.querySelector<HTMLElement>('.genie-answer__all-rows');

  it('names what is hidden and replaces the compact table in place with every row and column', async () => {
    render(WIDE_ROWS);
    expect(showAll()?.textContent).toBe('Show all 120 rows and 7 columns');
    expect(showAll()?.getAttribute('aria-expanded')).toBe('false');
    expect(container.querySelectorAll('.genie-answer__table-scroll tbody tr')).toHaveLength(10);

    await act(async () => showAll()!.click());
    await waitFor(() => region() !== null);

    expect(container.querySelector('.genie-answer__table-scroll')).toBeNull();
    expect(container.querySelector('.genie-answer__hidden-columns')).toBeNull();
    expect(region()!.getAttribute('role')).toBe('region');
    expect(region()!.getAttribute('tabindex')).toBe('0');
    expect(region()!.getAttribute('aria-label')).toBe('All 120 rows of this answer');
    const table = region()!.querySelector('table')!;
    expect(table.getAttribute('aria-rowcount')).toBe('121');
    expect(Array.from(table.querySelectorAll('thead th')).map((th) => th.textContent)).toEqual([
      'State',
      'County Name',
      'Borrowers',
      'Avg Score',
      'Avg Rate Spread Bps',
      'Avg Equity Pct',
      'Contactable',
    ]);
    // Windowed: far fewer than 120 rows are mounted.
    const mounted = table.querySelectorAll('tbody tr[aria-rowindex]');
    expect(mounted.length).toBeGreaterThan(0);
    expect(mounted.length).toBeLessThan(120);
    expect(mounted[0].getAttribute('aria-rowindex')).toBe('2');
    expect(showAll()?.textContent).toBe('Show fewer');
    expect(showAll()?.getAttribute('aria-expanded')).toBe('true');
  });

  it('reaches the last row by scrolling the region', async () => {
    render(WIDE_ROWS);
    await act(async () => showAll()!.click());
    await waitFor(() => region() !== null);
    const el = region()!;
    act(() => {
      el.scrollTop = 120 * 28;
      el.dispatchEvent(new Event('scroll'));
    });
    const rows = Array.from(el.querySelectorAll('tbody tr[aria-rowindex]'));
    expect(rows[rows.length - 1].getAttribute('aria-rowindex')).toBe('121');
    expect(rows[rows.length - 1].textContent).toContain('County 120');
    expect(el.querySelectorAll('tbody tr[aria-rowindex]').length).toBeLessThan(120);
  });

  it('Show fewer brings the compact table back', async () => {
    render(WIDE_ROWS);
    await act(async () => showAll()!.click());
    await waitFor(() => region() !== null);
    act(() => showAll()!.click());
    expect(region()).toBeNull();
    expect(container.querySelectorAll('.genie-answer__table-scroll tbody tr')).toHaveLength(10);
  });

  it('labels a trimmed History replay and says so in the expanded view', async () => {
    const held = WIDE_ROWS.slice(0, 50).map(({ state, borrowers }) => ({ state, borrowers }));
    render(held, 120);
    expect(showAll()?.textContent).toBe('Show all 50 rows held (of 120)');
    await act(async () => showAll()!.click());
    await waitFor(() => region() !== null);
    expect(container.querySelector('.genie-answer__rows-note')?.textContent).toBe(
      'Restored from History: 50 of 120 rows were kept. Ask again for the complete result.',
    );
  });

  it('offers only the hidden dimension, and nothing when nothing is hidden', () => {
    render(WIDE_ROWS.slice(0, 5));
    expect(showAll()?.textContent).toBe('Show all 7 columns');
    act(() => root.unmount());
    root = createRoot(container);
    render(WIDE_ROWS.map(({ state, borrowers }) => ({ state, borrowers })));
    expect(showAll()?.textContent).toBe('Show all 120 rows');
    act(() => root.unmount());
    root = createRoot(container);
    render(WIDE_ROWS.slice(0, 5).map(({ state, borrowers }) => ({ state, borrowers })));
    expect(showAll()).toBeNull();
    expect(container.querySelector('.genie-answer__more')).toBeNull();
  });

  it('marks the dense (panel) region so it is shorter', async () => {
    render(WIDE_ROWS, null, true);
    await act(async () => showAll()!.click());
    await waitFor(() => region() !== null);
    expect(region()!.classList.contains('genie-answer__all-rows--dense')).toBe(true);
  });
});
