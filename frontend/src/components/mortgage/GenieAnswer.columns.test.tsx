/**
 * @vitest-environment happy-dom
 *
 * Hidden-column disclosure (audit 2026-09-21 `genie-06`, slice 1): the
 * compact answer table caps columns at MAX_TABLE_COLS and used to drop the
 * rest without a word. They are now named under the table.
 */
import { act } from 'react';
import { createRoot, type Root } from 'react-dom/client';
import { MemoryRouter } from 'react-router';
import { afterEach, beforeEach, describe, expect, it } from 'vitest';
import { GenieRowsVisual } from './GenieAnswer.sections';
import { MAX_TABLE_COLS } from './GenieAnswer.logic';

(globalThis as unknown as { IS_REACT_ACT_ENVIRONMENT: boolean }).IS_REACT_ACT_ENVIRONMENT = true;

const NO_PLAN = { kind: 'none', chart: null, viz: null };

function row(i: number): Record<string, unknown> {
  return {
    state: `S${i}`,
    borrowers: 100 + i,
    avg_rate_spread_bps: 90 + i,
    avg_equity_pct: 40 + i,
    contactable_borrowers: 10 + i,
    top_segment: 'itm',
    refreshed_at: '2026-07-14T12:00:00Z',
  };
}

describe('GenieRowsVisual hidden columns', () => {
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

  it('names every column the capped table does not show, in header form', () => {
    const rows = [row(1), row(2), row(3)];
    act(() => {
      root.render(
        <MemoryRouter>
          <GenieRowsVisual rows={rows} plan={NO_PLAN} />
        </MemoryRouter>,
      );
    });
    const headers = Array.from(container.querySelectorAll('th')).map((th) => th.textContent);
    expect(headers.length).toBe(MAX_TABLE_COLS);
    const note = container.querySelector('.genie-answer__hidden-columns');
    expect(note, 'the disclosure renders under the table').not.toBeNull();
    // `top_segment` carries the product's "Cohort" label, like its header would.
    expect(note?.textContent).toBe('3 columns not shown: Contactable Borrowers, Top Cohort, Refreshed At');
    // The disclosure lists exactly the columns that are absent from the table.
    for (const hidden of ['Contactable Borrowers', 'Top Cohort', 'Refreshed At']) {
      expect(headers).not.toContain(hidden);
    }
  });

  it('renders no disclosure when every column fits', () => {
    const rows = [{ state: 'TX', borrowers: 900 }, { state: 'CA', borrowers: 700 }];
    act(() => {
      root.render(
        <MemoryRouter>
          <GenieRowsVisual rows={rows} plan={NO_PLAN} />
        </MemoryRouter>,
      );
    });
    expect(container.querySelector('.genie-answer__hidden-columns')).toBeNull();
  });

  it('uses the singular for one hidden column and discloses on the single-row stat strip too', () => {
    const rows = [{ borrowers: 1200, avg_spread_bps: 112, avg_equity_pct: 46, states: 8, refreshed_at: '2026-07-14' }];
    act(() => {
      root.render(
        <MemoryRouter>
          <GenieRowsVisual rows={rows} plan={NO_PLAN} />
        </MemoryRouter>,
      );
    });
    expect(container.querySelector('.genie-answer__stats'), 'a single metric row renders as a stat strip').not.toBeNull();
    expect(container.querySelector('.genie-answer__hidden-columns')?.textContent).toBe('1 column not shown: Refreshed At');
  });
});
