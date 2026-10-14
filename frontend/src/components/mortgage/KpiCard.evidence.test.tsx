/**
 * @vitest-environment happy-dom
 *
 * KpiCard's opt-in evidence (audit 2026-09-21 flow-06): with `evidence`, the
 * chip opens a drawer source carrying the displayed number, its as-of and its
 * reproduce key, so the drawer can say "How we got {value}". Without it the
 * card is byte-identical and its chip opens the registry source untouched, so
 * a criteria-filtered count (Portfolio Builder) never borrows an unfiltered
 * definition or proof.
 */
import { act } from 'react';
import { createRoot, type Root } from 'react-dom/client';
import { renderToStaticMarkup } from 'react-dom/server';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { DRAWER_SOURCES } from '../../lib/drawerSources';
import { KpiCard } from './KpiCard';

(globalThis as unknown as { IS_REACT_ACT_ENVIRONMENT: boolean }).IS_REACT_ACT_ENVIRONMENT = true;

const appMocks = vi.hoisted(() => ({ setDrawer: vi.fn() }));
vi.mock('../AppContext', () => ({
  useApp: () => ({ setDrawer: appMocks.setDrawer, showEvidence: true }),
}));

const EVIDENCE = { asOf: '2026-07-14T08:00:00Z', proofKey: 'home.in_the_money' } as const;

describe('KpiCard evidence prop', () => {
  let root: Root;

  beforeEach(() => {
    document.body.innerHTML = '<div id="root"></div>';
    root = createRoot(document.getElementById('root') as HTMLElement);
  });

  afterEach(() => {
    act(() => root.unmount());
    document.body.innerHTML = '';
    vi.clearAllMocks();
  });

  function clickChip() {
    act(() => document.querySelector<HTMLButtonElement>('.evidence-chip')?.click());
    const calls = appMocks.setDrawer.mock.calls;
    return calls[calls.length - 1]?.[0];
  }

  it('opens a source carrying the displayed value, its as-of and its proof key', () => {
    act(() => root.render(<KpiCard label="Test KPI" valueAnimated={12840} source={DRAWER_SOURCES.itm} evidence={EVIDENCE} />));

    expect(clickChip()).toEqual({
      ...DRAWER_SOURCES.itm,
      value: '12,840',
      asOf: '2026-07-14T08:00:00Z',
      proofKey: 'home.in_the_money',
    });
  });

  it('opens the registry source itself without the prop', () => {
    act(() => root.render(<KpiCard label="Test KPI" valueAnimated={12840} source={DRAWER_SOURCES.itm} />));

    expect(clickChip()).toBe(DRAWER_SOURCES.itm);
  });

  it('carries no value while loading or for an unknown number', () => {
    act(() => root.render(<KpiCard label="Test KPI" valueAnimated={12840} source={DRAWER_SOURCES.itm} evidence={EVIDENCE} loading />));
    expect(clickChip()).toBe(DRAWER_SOURCES.itm);

    act(() => root.render(<KpiCard label="Test KPI" valueAnimated={null} source={DRAWER_SOURCES.itm} evidence={EVIDENCE} />));
    expect(clickChip()).toBe(DRAWER_SOURCES.itm);
  });

  it('renders byte-identical markup with or without the prop', () => {
    const plain = renderToStaticMarkup(<KpiCard label="Test KPI" valueAnimated={12840} source={DRAWER_SOURCES.itm} />);
    const withEvidence = renderToStaticMarkup(
      <KpiCard label="Test KPI" valueAnimated={12840} source={DRAWER_SOURCES.itm} evidence={EVIDENCE} />,
    );
    expect(withEvidence).toBe(plain);
  });
});
