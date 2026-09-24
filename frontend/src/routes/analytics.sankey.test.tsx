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
  NESTED_FUNNEL_STAGE_PAIRS,
  SANKEY_VIEW,
  activationFunnelStages,
  buildFunnelSankeyModel,
  formatConversionPct,
} from './analytics.lib';

// Sources mirror the server's per-stage provenance; the geometry ignores them.
const POP = 'mip.gold.borrower_360';
const WORKFLOW = 'mip.gold.borrower_360 + mip.gold.borrower_lifecycle_state';

const STAGES: FunnelStage[] = [
  { stage: 'Addressable', stage_order: 1, borrower_count: 5_156_184, source: POP },
  { stage: 'Refi Economics', stage_order: 2, borrower_count: 117_189, source: POP },
  { stage: 'Opportunity Score 75+', stage_order: 3, borrower_count: 3_990, source: POP },
  { stage: 'Primary Offer Selected', stage_order: 4, borrower_count: 4_467_395, source: POP },
  { stage: 'Approved', stage_order: 5, borrower_count: 35, source: WORKFLOW },
  { stage: 'Actioned', stage_order: 6, borrower_count: 3, source: WORKFLOW },
];

describe('buildFunnelSankeyModel (pure geometry)', () => {
  it('keeps offer coverage out of the strict activation funnel', () => {
    expect(activationFunnelStages(STAGES).map((stage) => stage.stage)).toEqual([
      'Addressable',
      'Refi Economics',
      'Opportunity Score 75+',
      'Approved',
      'Actioned',
    ]);
    const model = buildFunnelSankeyModel(activationFunnelStages(STAGES));
    expect(model.nodes.map((n) => n.count)).toEqual([5_156_184, 117_189, 3_990, 35, 3]);
    expect(model.nodes.every((node, idx, nodes) => idx === 0 || node.count <= nodes[idx - 1].count)).toBe(true);
  });

  it('produces one node per stage in stage_order, with N-1 ribbons', () => {
    const shuffled = [STAGES[3], STAGES[0], STAGES[5], STAGES[1], STAGES[4], STAGES[2]];
    const model = buildFunnelSankeyModel(shuffled);
    expect(model.nodes.map((n) => n.stageOrder)).toEqual([1, 2, 3, 4, 5, 6]);
    expect(model.ribbons).toHaveLength(5);
    expect(model.ribbons.map((r) => [r.fromOrder, r.toOrder])).toEqual([
      [1, 2], [2, 3], [3, 4], [4, 5], [5, 6],
    ]);
  });

  // 2026-09-21 audit (dataviz-v1). The stages are independent SUM(CASE ...)
  // cuts of the addressable rows, so "count / previous stage" is a ratio of
  // unrelated counts. These pin what replaced it.
  it('labels every stage with its share of ADDRESSABLE, the one guaranteed superset', () => {
    const model = buildFunnelSankeyModel(STAGES);
    expect(model.nodes[0].shareOfAddressable).toBeNull(); // addressable itself
    expect(model.nodes[1].shareOfAddressable).toBeCloseTo(117_189 / 5_156_184, 9);
    expect(model.nodes[2].shareOfAddressable).toBeCloseTo(3_990 / 5_156_184, 9);
    expect(model.nodes[3].shareOfAddressable).toBeCloseTo(4_467_395 / 5_156_184, 9);
    expect(model.nodes[4].shareOfAddressable).toBeCloseTo(35 / 5_156_184, 12);
    expect(model.nodes[5].shareOfAddressable).toBeCloseTo(3 / 5_156_184, 12);
    // A subset can never exceed its superset.
    expect(model.nodes.every((n) => n.shareOfAddressable === null || n.shareOfAddressable <= 1)).toBe(true);
  });

  it('publishes a conversion ONLY for the SQL-nested pair, Approved -> Actioned', () => {
    expect(NESTED_FUNNEL_STAGE_PAIRS).toEqual([[5, 6]]);
    const model = buildFunnelSankeyModel(STAGES);
    // Refi economics / score 75+ / offers / approved are independent cuts:
    // high-opportunity borrowers need not be in the money, approved borrowers
    // need not be high-opportunity. No stage-to-stage ratio is published.
    expect(model.nodes.slice(0, 5).map((n) => n.conversion)).toEqual([null, null, null, null, null]);
    expect(model.nodes.slice(0, 5).map((n) => n.nestedIn)).toEqual([null, null, null, null, null]);
    const actioned = model.nodes[5];
    expect(actioned.conversion).toBeCloseTo(3 / 35, 9);
    expect(actioned.nestedIn).toEqual({ stage: 'Approved', stage_order: 5 });
    // The same holds on the five-stage activation funnel the Executive view draws.
    const activation = buildFunnelSankeyModel(activationFunnelStages(STAGES));
    expect(activation.nodes.map((n) => n.conversion === null)).toEqual([true, true, true, true, false]);
  });

  it('never publishes the defect value: high-opportunity over refi-economics', () => {
    const model = buildFunnelSankeyModel(STAGES);
    const defect = 3_990 / 117_189; // what "from previous stage" used to print (3.4%)
    for (const node of model.nodes) {
      expect(node.conversion ?? -1).not.toBeCloseTo(defect, 6);
      expect(node.shareOfAddressable ?? -1).not.toBeCloseTo(defect, 6);
    }
  });

  it('treats a ratio against an absent or empty parent as undefined (null), not a fake 0%', () => {
    // No Approved stage in the array: Actioned has nothing to be nested in.
    const orphan = buildFunnelSankeyModel([
      { stage: 'Addressable', stage_order: 1, borrower_count: 1000, source: POP },
      { stage: 'Actioned', stage_order: 6, borrower_count: 3, source: WORKFLOW },
    ]);
    expect(orphan.nodes[1].conversion).toBeNull();
    expect(orphan.nodes[1].nestedIn).toBeNull();
    // Approved is present but empty: you cannot divide by zero.
    const emptyParent = buildFunnelSankeyModel([
      { stage: 'Addressable', stage_order: 1, borrower_count: 1000, source: POP },
      { stage: 'Approved', stage_order: 5, borrower_count: 0, source: WORKFLOW },
      { stage: 'Actioned', stage_order: 6, borrower_count: 0, source: WORKFLOW },
    ]);
    expect(emptyParent.nodes[2].conversion).toBeNull();
    expect(emptyParent.nodes[1].shareOfAddressable).toBe(0); // a real 0% of a non-empty book
    // Addressable empty or missing: share is undefined too.
    const emptyBook = buildFunnelSankeyModel([
      { stage: 'Addressable', stage_order: 1, borrower_count: 0, source: POP },
      { stage: 'Refi Economics', stage_order: 2, borrower_count: 500, source: POP },
    ]);
    expect(emptyBook.nodes[1].shareOfAddressable).toBeNull();
    const noBook = buildFunnelSankeyModel([
      { stage: 'Refi Economics', stage_order: 2, borrower_count: 500, source: POP },
    ]);
    expect(noBook.nodes[0].shareOfAddressable).toBeNull();
  });

  // 2026-09-21 audit (dataviz-03): live magnitudes drew stages two to five as
  // 3-4px hairlines. Linear scale kept; a non-empty stage has a visible floor
  // and the model says when it used it.
  it('draws every non-empty stage at a visible minimum thickness and flags it as not to scale', () => {
    const model = buildFunnelSankeyModel(activationFunnelStages(STAGES));
    const maxBarH = SANKEY_VIEW.height - SANKEY_VIEW.padY * 2;
    expect(model.nodes[0].height).toBeCloseTo(maxBarH, 6); // the largest stage is to scale
    expect(model.nodes[0].clamped).toBe(false);
    for (const node of model.nodes.slice(1)) {
      expect(node.height).toBe(SANKEY_VIEW.minNodeHeight);
      expect(node.clamped).toBe(true);
    }
    expect(SANKEY_VIEW.minNodeHeight).toBeGreaterThanOrEqual(10); // not a hairline
    expect(model.notToScale).toBe(true);
  });

  it('stays linear and silent when every stage is large enough to draw in proportion', () => {
    const model = buildFunnelSankeyModel([
      { stage: 'Addressable', stage_order: 1, borrower_count: 1000, source: POP },
      { stage: 'Refi Economics', stage_order: 2, borrower_count: 500, source: POP },
    ]);
    expect(model.nodes[1].height).toBeCloseTo(model.nodes[0].height / 2, 6); // true proportion
    expect(model.nodes.some((n) => n.clamped)).toBe(false);
    expect(model.notToScale).toBe(false);
  });

  it('keeps an EMPTY stage a thin sliver: no visible ribbon into zero borrowers', () => {
    const model = buildFunnelSankeyModel([
      { stage: 'Big', stage_order: 1, borrower_count: 1000, source: POP },
      { stage: 'Zero', stage_order: 2, borrower_count: 0, source: POP },
    ]);
    const [big, zero] = model.nodes;
    expect(big.height).toBeGreaterThan(zero.height);
    expect(zero.height).toBe(SANKEY_VIEW.emptyNodeHeight); // never invisible
    expect(zero.height).toBeLessThan(SANKEY_VIEW.minNodeHeight);
    expect(zero.clamped).toBe(false);
    expect(model.notToScale).toBe(false);
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

  it('formats conversion percent compactly and suppresses meaningless values', () => {
    expect(formatConversionPct(null)).toBeNull();
    expect(formatConversionPct(0.0427)).toBe('4.3%');
    expect(formatConversionPct(0.5)).toBe('50%');
    expect(formatConversionPct(0)).toBe('0.0%'); // a true zero stage is honest
    expect(formatConversionPct(1)).toBe('100%'); // a stage that exactly held
    // A grown stage (>100%) shows NO label rather than "115000%".
    expect(formatConversionPct(4_467_395 / 3_878)).toBeNull();
    expect(formatConversionPct(1.5)).toBeNull();
    // Re-audit #6: a tiny-but-REAL narrowing must not round to "0.0%" (which
    // reads as a flatline). The live artifact: Approved 8 / 61,500.
    expect(formatConversionPct(8 / 61_500)).toBe('<0.1%'); // 0.013%
    expect(formatConversionPct(0.0004)).toBe('<0.1%'); // 0.04%
    expect(formatConversionPct(0.0005)).toBe('0.1%'); // 0.05% still rounds to a real 0.1%
  });
});

const navigate = vi.fn();
vi.mock('react-router', async (orig) => {
  const actual = await orig<typeof import('react-router')>();
  return { ...actual, useNavigate: () => navigate };
});

import { FunnelSankey } from './analytics.charts';
import { __resetFirstAppearanceForTests } from '../lib/useFirstAppearance';

describe('FunnelSankey (render + a11y)', () => {
  let container: HTMLDivElement;
  let root: Root;
  beforeEach(() => {
    vi.clearAllMocks();
    __resetFirstAppearanceForTests(); // so the one-time draw class is deterministic
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
  });

  // 2026-09-21 audit (dataviz-v1): the rendered chart, not just the model.
  it('announces and prints share of addressable, never a stage-to-stage conversion', () => {
    mount(STAGES);
    const links = [...container.querySelectorAll('[role="link"]')];
    const labels = links.map((l) => l.getAttribute('aria-label') ?? '');
    for (const label of labels) expect(label).not.toMatch(/from previous stage/);
    // The accessible name reads the exact count; only the drawn node label
    // is compact (2026-09-21 audit responsive-04: lib/formatters).
    expect(labels[0]).toBe('Addressable: 5,156,184 borrowers. Open in lead queue.');
    expect(labels[1]).toBe('Refi economics: 117,189 borrowers, 2.3% of addressable. Open in lead queue.');
    // 3,990 / 117,189 = 3.4% was the published "conversion"; the honest
    // figure is 3,990 / 5,156,184 = 0.1% of addressable.
    expect(labels[2]).toContain('0.1% of addressable');
    expect(labels[2]).not.toContain('3.4%');
    expect(labels[3]).toContain('87% of addressable'); // offers: a share, not ">100% conversion"
    expect(labels[4]).toBe('Approved: 35 borrowers, <0.1% of addressable. Open in lead queue.');
    // Visible text mirrors the accessible name.
    const printed = (link: Element) => [...link.querySelectorAll('.funnel-sankey__conv')].map((t) => t.textContent);
    expect(printed(links[0])).toEqual([]);
    expect(printed(links[1])).toEqual(['2.3% of addressable']);
    expect(printed(links[4])).toEqual(['<0.1% of addressable']);
  });

  it('keeps a conversion label only on Actioned, measured against Approved', () => {
    mount(STAGES);
    const links = [...container.querySelectorAll('[role="link"]')];
    const conversions = links.map((l) => l.querySelector('[data-ratio="nested-conversion"]')?.textContent ?? null);
    expect(conversions).toEqual([null, null, null, null, null, '8.6% of approved']);
    expect(links[5].getAttribute('aria-label')).toBe(
      'Actioned: 3 borrowers, <0.1% of addressable, 8.6% of approved. Open in lead queue.',
    );
    // The two lines and the count stack without sharing a baseline.
    const ys = [...links[5].querySelectorAll('text')].slice(0, 3).map((t) => Number(t.getAttribute('y')));
    expect(new Set(ys).size).toBe(3);
  });

  it('says the chart is not to scale whenever a stage is drawn at the minimum thickness', () => {
    mount(activationFunnelStages(STAGES));
    const note = container.querySelector('[data-testid="funnel-sankey-scale-note"]');
    expect(note?.textContent).toContain('Not to scale');
    expect(note?.textContent).toContain('exact');
    const svgLabel = container.querySelector('svg')!.getAttribute('aria-label') ?? '';
    expect(svgLabel).toContain('share of the addressable population');
    expect(svgLabel).toContain('not a conversion');
    expect(svgLabel).toContain('not to scale');
    // Drawn, not hairline: every bar is at least the visible minimum.
    const heights = [...container.querySelectorAll('.funnel-sankey__bar')].map((b) => Number(b.getAttribute('height')));
    expect(Math.min(...heights)).toBeGreaterThanOrEqual(SANKEY_VIEW.minNodeHeight);
  });

  it('omits the not-to-scale note when every stage is drawn in proportion', () => {
    mount([
      { stage: 'Addressable', stage_order: 1, borrower_count: 1000, source: POP },
      { stage: 'Refi Economics', stage_order: 2, borrower_count: 500, source: POP },
    ]);
    expect(container.querySelector('[data-testid="funnel-sankey-scale-note"]')).toBeNull();
    expect(container.querySelector('svg')!.getAttribute('aria-label')).not.toContain('not to scale');
  });

  it('animates the ribbon draw ONCE on first appearance, never on re-mount (no demo-ticker replay)', () => {
    mount(STAGES);
    expect(container.querySelector('svg')!.getAttribute('class')).toContain('funnel-sankey--enter');
    // Re-mount with the same stage signature: no replay.
    act(() => root.unmount());
    container.remove();
    container = document.createElement('div');
    document.body.appendChild(container);
    root = createRoot(container);
    mount(STAGES);
    expect(container.querySelector('svg')!.getAttribute('class')).not.toContain('funnel-sankey--enter');
  });

  it('does NOT replay the draw when a live data REFRESH changes the counts (re-audit #5)', () => {
    // The entrance is keyed on the funnel STRUCTURE, not the volatile counts —
    // a refreshed snapshot (same stages, new numbers) that remounts the chart
    // must not re-animate mid-walkthrough. Keying on counts (the prior bug)
    // would re-key and replay here.
    mount(STAGES);
    expect(container.querySelector('svg')!.getAttribute('class')).toContain('funnel-sankey--enter');
    act(() => root.unmount());
    container.remove();
    container = document.createElement('div');
    document.body.appendChild(container);
    root = createRoot(container);
    const refreshed = STAGES.map((s) => ({ ...s, borrower_count: s.borrower_count + 101 }));
    mount(refreshed);
    expect(container.querySelector('svg')!.getAttribute('class')).not.toContain('funnel-sankey--enter');
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
      { stage: 'A', stage_order: 1, borrower_count: 0, source: POP },
      { stage: 'B', stage_order: 2, borrower_count: 0, source: POP },
    ]);
    expect(container.querySelector('[role="link"]')).toBeNull();
  });
});
