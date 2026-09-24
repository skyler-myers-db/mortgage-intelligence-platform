// @vitest-environment happy-dom

import { afterEach, beforeEach, describe, expect, it } from 'vitest';
import { act } from 'react';
import { createRoot, type Root } from 'react-dom/client';
import { onlineManager } from '@tanstack/react-query';
import { ANALYTICS_SKELETONS, AnalyticsSkeleton, type AnalyticsSkeletonShape } from './analytics.skeleton';

/**
 * Analytics dashboards reserve their loaded shape while they load (audit
 * 2026-09-21 `states-10`), using the loaded views' own grid classes. The
 * rendered comparison against the loaded dashboard is in
 * tests/e2e/fixture/session-recovery.fixture.spec.ts.
 */

describe('AnalyticsSkeleton', () => {
  let container: HTMLDivElement;
  let root: Root;

  beforeEach(() => {
    onlineManager.setOnline(true);
    container = document.createElement('div');
    document.body.appendChild(container);
    root = createRoot(container);
  });

  afterEach(() => {
    act(() => {
      root.unmount();
    });
    container.remove();
    onlineManager.setOnline(true);
  });

  async function render(title: string, shape: AnalyticsSkeletonShape = ANALYTICS_SKELETONS.panel) {
    await act(async () => {
      root.render(<AnalyticsSkeleton title={title} shape={shape} />);
    });
    return container.querySelector<HTMLElement>('[data-analytics-skeleton]');
  }

  it('lays out the Executive dashboard: four KPI cards, a full panel, then the two-panel grid', async () => {
    const skeleton = await render('Executive analytics', ANALYTICS_SKELETONS.executive);
    expect(skeleton?.querySelectorAll('.kpi-row > .kpi.is-loading')).toHaveLength(4);
    const children = Array.from(skeleton?.children ?? [], (child) => child.className);
    expect(children).toEqual(['kpi-row', 'surface analytics-section', 'layoutA-grid analytics-grid']);
    expect(skeleton?.querySelectorAll('.analytics-skeleton__chart')).toHaveLength(3);
    // The title names the first panel only, so the heading outline stays one h2.
    expect(Array.from(skeleton?.querySelectorAll('h2') ?? [], (h2) => h2.textContent)).toEqual(['Executive analytics']);
    expect(skeleton?.getAttribute('aria-busy')).toBe('true');
  });

  it('uses the wide-left grid the Economics view uses', async () => {
    const skeleton = await render('Economics analytics', ANALYTICS_SKELETONS.economics);
    expect(skeleton?.querySelector('.layoutA-grid.analytics-grid--wide-left')?.children).toHaveLength(2);
  });

  it('keeps a single panel for the single-panel callers', async () => {
    const skeleton = await render('Rate window');
    expect(skeleton?.querySelectorAll('.surface')).toHaveLength(1);
  });

  it('says the first panel is waiting for the connection while offline', async () => {
    onlineManager.setOnline(false);
    const skeleton = await render('Signal analytics', ANALYTICS_SKELETONS.signals);
    expect(skeleton?.getAttribute('aria-busy')).toBe('false');
    expect(skeleton?.querySelector('.surface')?.textContent).toContain('Waiting for a connection');
  });
});
