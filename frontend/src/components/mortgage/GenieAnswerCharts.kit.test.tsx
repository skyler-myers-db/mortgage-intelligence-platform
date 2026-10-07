/**
 * @vitest-environment happy-dom
 *
 * Genie line and bar charts on the chart kit (audit 2026-09-21 dataviz-05 /
 * stack-06, Genie half). deviation:genie-chart-axes-drill (pinned here)
 *
 * Rendered-DOM checks at the layer the defects lived in: nice y ticks with
 * their gridlines, more than two x labels, a readout that prints formatCell
 * (money keeps "$"), the keyboard live text, HTML (never scaling viewBox)
 * text, bar hrefs that ARE genieCellHref's output with the answer cohort,
 * no href for labels genieCellHref does not link (dates), the Lead Queue
 * disclosure only when a link targets it, and no drill at all when the
 * source rows cannot be aligned or the chart is a preview.
 */
import { act } from 'react';
import { createRoot, type Root } from 'react-dom/client';
import { MemoryRouter } from 'react-router';
import { afterEach, beforeEach, describe, expect, it } from 'vitest';
import { genieCellHref, type GenieAnswerCohort } from '../../lib/genieCellLinks';
import { formatCell, type ChartRow } from './GenieAnswer.logic';
import { GenieBarChart } from './GenieBarChart';
import { chartPointHrefs, GENIE_LEAD_QUEUE_DRILL_NOTE } from './GenieChartDrill';
import { GenieLineChart } from './GenieLineChart';

(globalThis as unknown as { IS_REACT_ACT_ENVIRONMENT: boolean }).IS_REACT_ACT_ENVIRONMENT = true;

const COHORT: GenieAnswerCohort = { segmentFilter: ['ITM'], segmentFilterMode: 'any' };
const STATE_ROWS = [
  { state: 'IL', total_balance: 1_250_000 },
  { state: 'TX', total_balance: 640_500 },
  { state: 'FL', total_balance: 98_000 },
];
const toChart = (rows: Array<Record<string, unknown>>, label: string, value: string): ChartRow[] =>
  rows.map((row) => ({ label: formatCell(label, row[label]), value: Number(row[value]) }));

describe('Genie charts on the chart kit', () => {
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

  const texts = (selector: string) => Array.from(container.querySelectorAll(selector)).map((el) => el.textContent ?? '');

  it('the line chart has nice money y ticks with gridlines, edge-anchored x labels and a formatCell readout', () => {
    const data = Array.from({ length: 24 }, (_, i) => ({ label: `2026-W${String(i + 1).padStart(2, '0')}`, value: 100_000 + i * 12_500 }));
    act(() =>
      root.render(<GenieLineChart data={data} labelCol="week" valueCol="total_balance" tableRowCount={24} />),
    );

    const yTicks = texts('.analytics-chart__tick--y');
    expect(yTicks.length).toBeGreaterThanOrEqual(3);
    expect(yTicks.every((tick) => tick.startsWith('$'))).toBe(true);
    expect(container.querySelectorAll('svg .analytics-chart__grid')).toHaveLength(yTicks.length);
    const xTicks = Array.from(container.querySelectorAll('.analytics-chart__tick--x'));
    expect(xTicks.length).toBeGreaterThanOrEqual(3);
    expect(xTicks[0].className).toContain('edge-start');
    expect(xTicks[xTicks.length - 1].className).toContain('edge-end');
    expect(container.querySelector('svg text')).toBeNull();
    expect(container.querySelectorAll('.genie-line__point')).toHaveLength(24);

    const plot = container.querySelector<HTMLElement>('.chart-frame__plot')!;
    act(() => plot.focus());
    act(() => plot.dispatchEvent(new KeyboardEvent('keydown', { key: 'ArrowRight', bubbles: true })));
    expect(container.querySelector('.analytics-chart__tip-y')?.textContent).toBe(formatCell('total_balance', 112_500));
    expect(container.querySelector('.analytics-chart__tip-y')?.textContent).toContain('$');
    expect(container.querySelector('[aria-live="polite"]')?.textContent).toBe(`2026-W02: ${formatCell('total_balance', 112_500)}`);
  });

  it('the bar chart prints money values with "$" and links each state through genieCellHref with the cohort', () => {
    act(() =>
      root.render(
        <MemoryRouter>
          <GenieBarChart
            data={toChart(STATE_ROWS, 'state', 'total_balance')}
            labelCol="state"
            valueCol="total_balance"
            tableRowCount={3}
            sourceRows={STATE_ROWS}
            cellCohort={COHORT}
          />
        </MemoryRouter>,
      ),
    );

    expect(texts('.genie-bars__value')).toEqual(STATE_ROWS.map((row) => formatCell('total_balance', row.total_balance)));
    expect(texts('.genie-bars__value').every((value) => value.startsWith('$'))).toBe(true);
    const hrefs = Array.from(container.querySelectorAll<HTMLAnchorElement>('.genie-bars__link')).map((a) => a.getAttribute('href'));
    expect(hrefs).toEqual(STATE_ROWS.map((row) => genieCellHref('state', row.state, COHORT, row)));
    expect(hrefs.every((href) => href?.startsWith('/lead-queue'))).toBe(true);
    expect(container.querySelector('.genie-chart__drill-note')?.textContent).toBe(GENIE_LEAD_QUEUE_DRILL_NOTE);
    const ticks = texts('.genie-bars__tick');
    expect(ticks.length).toBeGreaterThanOrEqual(3);
    expect(ticks[0]).toBe('$0');
    expect(container.querySelectorAll('.genie-bars__gridline')).toHaveLength(ticks.length);
    // The bar width lives on the bar leaf, never on an ancestor.
    const bar = container.querySelector<HTMLElement>('.genie-bars__bar')!;
    expect(bar.style.getPropertyValue('--bar-w')).toMatch(/%$/);
    expect(container.querySelector<HTMLElement>('.genie-bars__row')!.style.length).toBe(0);
  });

  it('never links a label genieCellHref does not link, and then shows no disclosure', () => {
    const weeks = [
      { week: '2026-09-01', borrowers: 120 },
      { week: '2026-09-08', borrowers: 140 },
    ];
    act(() =>
      root.render(
        <MemoryRouter>
          <GenieBarChart data={toChart(weeks, 'week', 'borrowers')} labelCol="week" valueCol="borrowers" tableRowCount={2} sourceRows={weeks} cellCohort={COHORT} />
        </MemoryRouter>,
      ),
    );

    expect(container.querySelectorAll('a')).toHaveLength(0);
    expect(container.querySelector('.genie-chart__drill-note')).toBeNull();
  });

  it('drills nowhere when the source rows cannot be aligned, or in a preview', () => {
    const withNull = [...STATE_ROWS, { state: 'CA', total_balance: null }];
    const chartRows = toChart(STATE_ROWS, 'state', 'total_balance');
    // The chart dropped the null row: still aligned, so it drills.
    expect(chartPointHrefs({ rows: withNull, chartRows, labelCol: 'state', valueCol: 'total_balance', cohort: COHORT }).every(Boolean)).toBe(true);
    // One chart point too many for the rows: misaligned, so nothing drills.
    const misaligned = [...chartRows, { label: 'NV', value: 1 }];
    expect(chartPointHrefs({ rows: STATE_ROWS, chartRows: misaligned, labelCol: 'state', valueCol: 'total_balance', cohort: COHORT })).toEqual([null, null, null, null]);
    expect(chartPointHrefs({ rows: STATE_ROWS, chartRows, labelCol: 'state', valueCol: 'total_balance', cohort: COHORT, preview: true })).toEqual([null, null, null]);

    act(() =>
      root.render(
        <MemoryRouter>
          <GenieBarChart data={misaligned} labelCol="state" valueCol="total_balance" tableRowCount={3} sourceRows={STATE_ROWS} cellCohort={COHORT} />
        </MemoryRouter>,
      ),
    );
    expect(container.querySelectorAll('a')).toHaveLength(0);
    expect(container.querySelector('.genie-chart__drill-note')).toBeNull();
  });
});
