/**
 * @vitest-environment happy-dom
 *
 * Chart truncation captions (audit 2026-09-21 `genie-06`, slice 1). The bar
 * caption used to promise "full N rows in the table below" while the table
 * shows at most MAX_TABLE_ROWS; the line chart truncated silently.
 */
import { act, type ReactNode } from 'react';
import { createRoot, type Root } from 'react-dom/client';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { MAX_TABLE_ROWS, type ChartRow } from './GenieAnswer.logic';
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

describe('chart truncation captions', () => {
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

  it('names the real caps of both the chart and the table', () => {
    expect(chartTruncationCaption(MAX_BAR_POINTS, 30, 'rows')).toBe(
      `Chart shows the top ${MAX_BAR_POINTS} of 30 rows; the table below shows ${MAX_TABLE_ROWS} of 30 rows.`,
    );
    // Fewer rows than the table cap: the table shows them all.
    expect(chartTruncationCaption(5, 8, 'points')).toBe('Chart shows the top 5 of 8 points; the table below shows 8 of 8 rows.');
  });

  it('the bar chart draws MAX_BAR_POINTS bars and no longer promises the full rows below', () => {
    act(() => root.render(<GenieBarChart data={rows(30)} labelCol="state" valueCol="borrowers" />));
    expect(container.querySelectorAll('svg text').length).toBe(MAX_BAR_POINTS * 2);
    expect(caption()).toBe(chartTruncationCaption(MAX_BAR_POINTS, 30, 'rows'));
    expect(caption()).not.toContain('full 30 rows');
    expect(caption()).toContain(`the table below shows ${MAX_TABLE_ROWS} of 30 rows`);
  });

  it('the bar chart shows no caption when nothing is truncated', () => {
    act(() => root.render(<GenieBarChart data={rows(MAX_BAR_POINTS)} labelCol="state" valueCol="borrowers" />));
    expect(caption()).toBeNull();
  });

  it('the line chart discloses its point cap too', () => {
    act(() => root.render(<GenieLineChart data={rows(40)} labelCol="week" valueCol="score" />));
    expect(container.querySelectorAll('circle').length).toBe(MAX_LINE_POINTS);
    expect(caption()).toBe(chartTruncationCaption(MAX_LINE_POINTS, 40, 'points'));
    act(() => root.render(<GenieLineChart data={rows(MAX_LINE_POINTS)} labelCol="week" valueCol="score" />));
    expect(caption()).toBeNull();
  });
});
