/**
 * @vitest-environment happy-dom
 *
 * Chart captions (audit 2026-09-21 `genie-06`, slice 1). The bar caption
 * used to promise "full N rows in the table below" while the table shows at
 * most MAX_TABLE_ROWS; the line chart truncated silently. The chart and the
 * table also hold DIFFERENT row sets: the chart drops a row whose measure is
 * null, the table keeps it. And the chart takes rows in SQL order, so it
 * shows the first rows, not the "top" ones.
 */
import { act, type ReactNode } from 'react';
import { createRoot, type Root } from 'react-dom/client';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import type { GenieAnswer } from '../../types';
import { GenieRowsVisual } from './GenieAnswer.sections';
import { MAX_TABLE_ROWS, pickPlan, type ChartRow } from './GenieAnswer.logic';
import {
  GenieBarChart,
  GenieLineChart,
  MAX_BAR_POINTS,
  MAX_LINE_POINTS,
  chartTruncationCaption,
} from './GenieAnswerCharts';

(globalThis as unknown as { IS_REACT_ACT_ENVIRONMENT: boolean }).IS_REACT_ACT_ENVIRONMENT = true;

vi.mock('react-router', () => ({
  Link: ({ children, to }: { children: ReactNode; to: string }) => <a href={to}>{children}</a>,
}));

function rows(n: number): ChartRow[] {
  return Array.from({ length: n }, (_, i) => ({ label: `L${i + 1}`, value: n - i }));
}

describe('chart captions', () => {
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

  const caption = () => container.querySelector('.genie-chart__more')?.textContent ?? null;

  it('names the real caps of both the chart and the table, counting each row set on its own', () => {
    expect(chartTruncationCaption(MAX_BAR_POINTS, 30, 30, 'rows')).toBe(
      `Chart shows the first ${MAX_BAR_POINTS} of 30 charted rows; the table below shows ${MAX_TABLE_ROWS} of 30 rows.`,
    );
    // Rows the chart could not plot are counted against the TABLE's total.
    expect(chartTruncationCaption(MAX_BAR_POINTS, 28, 30, 'rows')).toBe(
      `Chart shows the first ${MAX_BAR_POINTS} of 28 charted rows (2 rows have no value to chart); the table below shows ${MAX_TABLE_ROWS} of 30 rows.`,
    );
    // Fewer rows than the table cap: the table shows them all.
    expect(chartTruncationCaption(5, 8, 8, 'points')).toBe(
      'Chart shows the first 5 of 8 charted points; the table below shows 8 of 8 rows.',
    );
    // Nothing truncated, but one row had no measure: still disclosed.
    expect(chartTruncationCaption(6, 6, 7, 'rows')).toBe(
      'Chart shows all 6 charted rows (1 row has no value to chart); the table below shows 7 of 7 rows.',
    );
    // The chart shows every table row: no caption.
    expect(chartTruncationCaption(6, 6, 6, 'rows')).toBeNull();
  });

  it('never claims a "top" the SQL order does not promise', () => {
    expect(chartTruncationCaption(MAX_BAR_POINTS, 30, 30, 'rows')).not.toContain('top');
  });

  it('the bar chart draws MAX_BAR_POINTS bars and no longer promises the full rows below', () => {
    act(() => root.render(<GenieBarChart data={rows(30)} labelCol="state" valueCol="borrowers" tableRowCount={30} />));
    expect(container.querySelectorAll('svg text').length).toBe(MAX_BAR_POINTS * 2);
    expect(caption()).toBe(chartTruncationCaption(MAX_BAR_POINTS, 30, 30, 'rows'));
    expect(caption()).not.toContain('full 30 rows');
    expect(caption()).toContain(`the table below shows ${MAX_TABLE_ROWS} of 30 rows`);
  });

  it('the bar chart shows no caption when it shows every table row', () => {
    act(() =>
      root.render(
        <GenieBarChart data={rows(MAX_BAR_POINTS)} labelCol="state" valueCol="borrowers" tableRowCount={MAX_BAR_POINTS} />,
      ),
    );
    expect(caption()).toBeNull();
  });

  it('the line chart discloses its point cap too', () => {
    act(() => root.render(<GenieLineChart data={rows(40)} labelCol="week" valueCol="score" tableRowCount={40} />));
    expect(container.querySelectorAll('circle').length).toBe(MAX_LINE_POINTS);
    expect(caption()).toBe(chartTruncationCaption(MAX_LINE_POINTS, 40, 40, 'points'));
    act(() =>
      root.render(
        <GenieLineChart data={rows(MAX_LINE_POINTS)} labelCol="week" valueCol="score" tableRowCount={MAX_LINE_POINTS} />,
      ),
    );
    expect(caption()).toBeNull();
  });

  it('in a rendered answer, the caption and the table agree on the table row count when a measure is null', () => {
    // 30 SQL rows; 2 have a null measure, so the chart has 28 rows to draw.
    const answerRows = Array.from({ length: 30 }, (_, i) => ({
      region: `Region ${String(i + 1).padStart(2, '0')}`,
      borrowers: i === 4 || i === 17 ? null : 3000 - i * 10,
    }));
    const plan = pickPlan({ answer: '' } as GenieAnswer, answerRows, Object.keys(answerRows[0]));
    expect(plan.kind).toBe('bar');
    expect(plan.chart?.rows.length).toBe(28);

    act(() => root.render(<GenieRowsVisual rows={answerRows} plan={plan} />));

    const shownTableRows = container.querySelectorAll('.genie-answer__table tbody tr').length;
    const more = Array.from(container.querySelectorAll('.genie-answer__more'))
      .map((el) => el.textContent ?? '')
      .find((text) => text.startsWith('+'));
    expect(more).toBe('+20 more rows');
    const hiddenTableRows = Number(/^\+(\d+)/.exec(more ?? '')?.[1]);
    // The caption's table count is the table's own: shown + "+N more".
    expect(caption()).toContain(`the table below shows ${shownTableRows} of ${shownTableRows + hiddenTableRows} rows`);
    expect(caption()).toBe(
      `Chart shows the first ${MAX_BAR_POINTS} of 28 charted rows (2 rows have no value to chart); the table below shows ${MAX_TABLE_ROWS} of 30 rows.`,
    );
    expect(container.querySelectorAll('.genie-chart svg text').length).toBe(MAX_BAR_POINTS * 2);
  });
});
