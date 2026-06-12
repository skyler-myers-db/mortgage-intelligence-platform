/**
 * @vitest-environment happy-dom
 *
 * Funnel Sankey contract (re-audit Buyer-Wow #5). The geometry is a pure,
 * deterministic model over the existing FunnelStage[]; the component renders
 * keyboard-focusable stage links that route to the lead queue. No new data.
 */
import { act } from 'react';
import { createRoot, type Root } from 'react-dom/client';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import type { FunnelStage } from '../types';
import {
  SANKEY_VIEW,
  buildFunnelSankeyModel,
  formatConversionPct,
} from './analytics.lib';

const STAGES: FunnelStage[] = [
  { stage: 'Addressable', stage_order: 1, borrower_count: 5_156_184 },
  { stage: 'In the Money', stage_order: 2, borrower_count: 117_189 },
  { stage: 'High Opportunity', stage_order: 3, borrower_count: 3_990 },
  { stage: 'Offer Recommended', stage_order: 4, borrower_count: 4_467_395 },
  { stage: 'Approved', stage_order: 5, borrower_count: 35 },
  { stage: 'Actioned', stage_order: 6, borrower_count: 3 },
];

describe('buildFunnelSankeyModel (pure geometry)', () => {
  it('produces one node per stage in stage_order, with N-1 ribbons', () => {
    const shuffled = [STAGES[3], STAGES[0], STAGES[5], STAGES[1], STAGES[4], STAGES[2]];
    const model = buildFunnelSankeyModel(shuffled);
    expect(model.nodes.map((n) => n.stageOrder)).toEqual([1, 2, 3, 4, 5, 6]);
    expect(model.ribbons).toHaveLength(5);
    expect(model.ribbons.map((r) => [r.fromOrder, r.toOrder])).toEqual([
      [1, 2], [2, 3], [3, 4], [4, 5], [5, 6],
    ]);
  });

  it('computes conversion as count/previous (null for the first stage)', () => {
    const model = buildFunnelSankeyModel(STAGES);
    expect(model.nodes[0].conversion).toBeNull();
    expect(model.nodes[1].conversion).toBeCloseTo(117_189 / 5_156_184, 6);
    expect(model.nodes[5].conversion).toBeCloseTo(3 / 35, 6);
  });

  it('sizes node height by count and keeps a zero stage visible as a sliver', () => {
    const model = buildFunnelSankeyModel([
      { stage: 'Big', stage_order: 1, borrower_count: 1000 },
      { stage: 'Zero', stage_order: 2, borrower_count: 0 },
    ]);
    const [big, zero] = model.nodes;
    expect(big.height).toBeGreaterThan(zero.height);
    expect(zero.height).toBeGreaterThan(0); // never invisible
    expect(zero.conversion).toBe(0);
  });

  it('lays nodes left-to-right within the padded viewBox', () => {
    const model = buildFunnelSankeyModel(STAGES);
    const xs = model.nodes.map((n) => n.xCenter);
    for (let i = 1; i < xs.length; i += 1) expect(xs[i]).toBeGreaterThan(xs[i - 1]);
    expect(xs[0]).toBeCloseTo(SANKEY_VIEW.padX, 3);
    expect(xs[xs.length - 1]).toBeCloseTo(SANKEY_VIEW.width - SANKEY_VIEW.padX, 3);
  });

  it('returns an empty model for no stages', () => {
    const model = buildFunnelSankeyModel([]);
    expect(model.nodes).toEqual([]);
    expect(model.ribbons).toEqual([]);
  });

  it('formats conversion percent compactly', () => {
    expect(formatConversionPct(null)).toBeNull();
    expect(formatConversionPct(0.0427)).toBe('4.3%');
    expect(formatConversionPct(0.5)).toBe('50%');
    expect(formatConversionPct(0)).toBe('0.0%');
  });
});

const navigate = vi.fn();
vi.mock('react-router-dom', async (orig) => {
  const actual = await orig<typeof import('react-router-dom')>();
  return { ...actual, useNavigate: () => navigate };
});

import { FunnelSankey } from './analytics.charts';

describe('FunnelSankey (render + a11y)', () => {
  let container: HTMLDivElement;
  let root: Root;
  beforeEach(() => {
    vi.clearAllMocks();
    container = document.createElement('div');
    document.body.appendChild(container);
    root = createRoot(container);
  });
  afterEach(() => {
    act(() => root.unmount());
    container.remove();
  });
  function mount(stages: FunnelStage[]) {
    act(() => root.render(<FunnelSankey stages={stages} />));
  }

  it('renders a focusable, labeled link per stage', () => {
    mount(STAGES);
    const links = container.querySelectorAll('[role="link"]');
    expect(links).toHaveLength(6);
    links.forEach((l) => expect(l.getAttribute('tabindex')).toBe('0'));
    const first = links[0];
    expect(first.getAttribute('aria-label')).toContain('Addressable');
    expect(first.getAttribute('aria-label')).toContain('Open in lead queue');
    // The In-the-Money node announces its conversion from the prior stage.
    expect(links[1].getAttribute('aria-label')).toMatch(/from previous stage/);
  });

  it('navigates to the stage slice of the lead queue on click', () => {
    mount(STAGES);
    const itm = container.querySelectorAll('[role="link"]')[1];
    act(() => (itm as SVGGElement).dispatchEvent(new MouseEvent('click', { bubbles: true })));
    expect(navigate).toHaveBeenCalledWith('/lead-queue?funnel_stage=in_the_money');
  });

  it('navigates on Enter and Space (keyboard)', () => {
    mount(STAGES);
    const approved = container.querySelectorAll('[role="link"]')[4];
    act(() => approved.dispatchEvent(new KeyboardEvent('keydown', { key: 'Enter', bubbles: true })));
    expect(navigate).toHaveBeenCalledWith('/lead-queue?funnel_stage=approved');
    navigate.mockClear();
    act(() => approved.dispatchEvent(new KeyboardEvent('keydown', { key: ' ', bubbles: true })));
    expect(navigate).toHaveBeenCalledWith('/lead-queue?funnel_stage=approved');
  });

  it('renders a graceful empty state (no SVG links) for day-zero/empty data', () => {
    mount([]);
    expect(container.querySelector('svg')).toBeNull();
    expect(container.textContent).toContain('Pipeline funnel appears once');
    // All-zero counts are also treated as empty (no misleading funnel).
    mount([
      { stage: 'A', stage_order: 1, borrower_count: 0 },
      { stage: 'B', stage_order: 2, borrower_count: 0 },
    ]);
    expect(container.querySelector('[role="link"]')).toBeNull();
  });
});
