/** @vitest-environment happy-dom */

import { renderToStaticMarkup } from 'react-dom/server';
import { MemoryRouter } from 'react-router';
import { describe, expect, it, vi } from 'vitest';
import type { UseWarmingUpRetryResult } from '../lib/useWarmingUpRetry';
import { designCss } from '../test/designCss';
import { histogramModel } from './analytics.chart-model';
import type { FunnelStage } from '../types';
import { BAR_FLOOR_PCT, Bars, FunnelBars, HistogramTable, LoadState } from './analytics.charts';

/** Rendered `--bar-pct` of each bar fill, and the not-to-scale note, in the DOM Bars produces. */
function renderBars(values: number[]): { pcts: string[]; note: string | null; host: HTMLElement } {
  const host = document.createElement('div');
  host.innerHTML = renderToStaticMarkup(
    <MemoryRouter>
      <Bars rows={values} value={(v) => v} label={(v) => `row ${v}`} href={(v) => `/lead-queue?v=${v}`} />
    </MemoryRouter>,
  );
  return {
    pcts: [...host.querySelectorAll<HTMLElement>('.analytics-bars__fill')].map((fill) => fill.style.getPropertyValue('--bar-pct')),
    note: host.querySelector('[data-testid="bars-scale-note"]')?.textContent ?? null,
    host,
  };
}

/** Per row: whether its fill is floored, its data-to-scale, and its screen-reader suffix. */
function flooredRows(host: HTMLElement) {
  return [...host.querySelectorAll('.analytics-bars__row')].map((row) => ({
    floored: row.querySelector('.analytics-bars__fill')?.classList.contains('analytics-bars__fill--floored') ?? false,
    toScale: row.querySelector('.analytics-bars__fill')?.getAttribute('data-to-scale') ?? null,
    suffix: row.querySelector('.sr-only')?.textContent ?? null,
  }));
}

describe('Bars scale (dataviz-03)', () => {
  it('floors a tiny non-zero bar at BAR_FLOOR_PCT and says the bars are not to scale', () => {
    const { pcts, note, host } = renderBars([5_160_000, 4_350, 23, 0]);
    expect(pcts).toEqual(['100%', `${BAR_FLOOR_PCT}%`, `${BAR_FLOOR_PCT}%`, '0%']);
    expect(note).toBe(
      `Not to scale: outlined bars are under ${BAR_FLOOR_PCT}% of the largest and are drawn at a minimum length. The printed values are exact.`,
    );
    // D-dataviz-geo-a: the floored rows say so, the others do not (a zero
    // draws no fill, so it is not floored either).
    const suffix = ', drawn at minimum length, not to scale';
    expect(flooredRows(host)).toEqual([
      { floored: false, toScale: null, suffix: null },
      { floored: true, toScale: 'false', suffix },
      { floored: true, toScale: 'false', suffix },
      { floored: false, toScale: null, suffix: null },
    ]);
  });

  it('keeps Primary offer selected unfloored among the six Pipeline Metrics stages', () => {
    const stage = (stage: string, stage_order: number, borrower_count: number): FunnelStage => ({
      stage,
      stage_order,
      borrower_count,
      source: 'mip.gold.borrower_360',
    });
    const host = document.createElement('div');
    host.innerHTML = renderToStaticMarkup(
      <MemoryRouter>
        <FunnelBars
          stages={[
            stage('Addressable', 1, 5_160_000),
            stage('In the Money', 2, 135_520),
            stage('Opportunity Score 75+', 3, 4_350),
            stage('Primary Offer Selected', 4, 4_467_395),
            stage('Approved', 5, 23),
            stage('Actioned', 6, 3),
          ]}
        />
      </MemoryRouter>,
    );
    // In the Money (2.6%) and Primary offer selected (87%) draw to scale.
    expect(flooredRows(host).map((row) => row.floored)).toEqual([false, false, true, false, true, true]);
  });

  it('draws every bar at its linear share, zero as no fill, and adds no note when none is floored', () => {
    const { pcts, note, host } = renderBars([400, 100, 0]);
    expect(pcts).toEqual(['100%', '25%', '0%']);
    expect(note).toBeNull();
    expect(flooredRows(host).some((row) => row.floored || row.suffix !== null)).toBe(false);
  });

  it('keeps no CSS minimum width that would floor a bar behind the note', () => {
    const fill = /\.analytics-bars__fill\s*\{([^}]*)\}/.exec(designCss())?.[1] ?? '';
    expect(fill).not.toMatch(/min-(?:width|inline-size)/);
  });
});

/** Header and body cells of the table twin HistogramTable renders over 25-wide bins. */
function renderHistogramTable(threshold: number | null, pastLabel: string | null) {
  const model = histogramModel(
    [{ start: 25, count: 10 }, { start: 50, count: 20 }, { start: 75, count: 30 }],
    25,
    threshold,
  );
  const host = document.createElement('div');
  host.innerHTML = renderToStaticMarkup(
    <HistogramTable model={model} rangeLabel="Spread bps" pastLabel={pastLabel} formatRange={(start) => `${start}–${start + 24}`} />,
  );
  return {
    head: [...host.querySelectorAll('thead th')].map((th) => th.textContent),
    rows: [...host.querySelectorAll('tbody tr')].map((tr) => [...tr.querySelectorAll('td')].map((td) => td.textContent)),
  };
}

describe('HistogramTable (dataviz-10)', () => {
  it('says Partly for the bin a non-aligned threshold falls inside, not No', () => {
    expect(renderHistogramTable(60, 'At or past the screen')).toEqual({
      head: ['Spread bps', 'Borrowers', 'At or past the screen'],
      rows: [
        ['25–49', '10', 'No'],
        ['50–74', '20', 'Partly'],
        ['75–99', '30', 'Yes'],
      ],
    });
  });

  it('keeps Yes and No when the threshold sits on a bin edge', () => {
    expect(renderHistogramTable(75, 'At or past the screen').rows.map((row) => row[2])).toEqual(['No', 'No', 'Yes']);
  });

  it('drops the column when there is no threshold, instead of a column of dashes', () => {
    const table = renderHistogramTable(null, null);
    expect(table.head).toEqual(['Spread bps', 'Borrowers']);
    expect(table.rows).toEqual([
      ['25–49', '10'],
      ['50–74', '20'],
      ['75–99', '30'],
    ]);
  });
});

function query(overrides: Partial<UseWarmingUpRetryResult<{ value: number }>> = {}) {
  return {
    data: { value: 42 },
    warmingUp: null,
    error: null,
    isFetching: false,
    isPlaceholderData: false,
    manualRetry: vi.fn(),
    ...overrides,
  } as UseWarmingUpRetryResult<{ value: number }>;
}

describe('LoadState stale-while-refreshing contract', () => {
  it('keeps the current panel mounted and visibly announces a background refresh', () => {
    const html = renderToStaticMarkup(
      <LoadState query={query({ isFetching: true })} title="Economics analytics">
        {(data) => <div data-testid="stable-data">Value {data.value}</div>}
      </LoadState>,
    );

    expect(html).toContain('Value 42');
    expect(html).toContain('aria-busy="true"');
    expect(html).toContain('role="status"');
    expect(html).toContain('Updating economics analytics');
    expect(html).toContain('stable-refresh-status');
  });

  it('does not render the overlay after refresh completion', () => {
    const html = renderToStaticMarkup(
      <LoadState query={query()} title="Economics analytics">
        {(data) => <div>Value {data.value}</div>}
      </LoadState>,
    );

    expect(html).not.toContain('stable-refresh-status');
    expect(html).not.toContain('aria-busy');
  });
});
