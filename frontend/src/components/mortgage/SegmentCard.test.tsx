/**
 * @vitest-environment happy-dom
 */
import { act } from 'react';
import { createRoot, type Root } from 'react-dom/client';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { normalizeSegmentCode, SEGMENT_DEFINITIONS } from '../../lib/segmentMetadata';
import type { SegmentSummary } from '../../types';

import { DRAWER_SOURCES } from '../../lib/drawerSources';
import { SegmentCard } from './SegmentCard';

// EvidenceChip (the S1.3 per-segment evidence affordance) reads setDrawer +
// showEvidence from the app context; mock it so the card renders standalone.
const setDrawer = vi.fn();
vi.mock('../AppContext', () => ({
  useApp: () => ({ setDrawer, showEvidence: true }),
}));

describe('Segment definitions', () => {
  it('contains in-the-money segment', () => {
    expect(SEGMENT_DEFINITIONS.some((s) => s.code === 'itm')).toBe(true);
  });

  it('uses a distinct presentation label for the strict refi-ready segment', () => {
    const refiReady = SEGMENT_DEFINITIONS.find((s) => s.code === 'itm');
    expect(refiReady?.name).toBe('Prime Refi Candidates');
    expect(refiReady?.description).toContain('75 bps');
    expect(refiReady?.description).toContain('15%');
  });

  it('uses equity-credit presentation for the HELOC Intent legacy segment code', () => {
    const helocIntent = SEGMENT_DEFINITIONS.find((s) => s.code === 'permit');
    expect(helocIntent?.name).toBe('HELOC Intent');
    expect(helocIntent?.description).toContain('HELOC propensity');
    expect(helocIntent?.icon).toBe('equity');
  });

  it('registers all seven S1.3 overlay segments', () => {
    const codes = new Set<string>(SEGMENT_DEFINITIONS.map((s) => s.code));
    for (const code of [
      'second_lien_itm',
      'heloc_draw_to_payback',
      'home_equity_history',
      'refi_propensity',
      'itm_on_related_property',
      'payoff_loss_leads',
      'permit_activity',
    ]) {
      expect(codes.has(code), `${code} missing from SEGMENT_DEFINITIONS`).toBe(true);
    }
  });

  it('normalizes underscore/hyphen/space forms to canonical codes', () => {
    expect(normalizeSegmentCode('second-lien-itm')).toBe('second_lien_itm');
    expect(normalizeSegmentCode('Payoff Loss Leads')).toBe('payoff_loss_leads');
    // permit_activity is now its own registered segment, no longer an alias
    // for the HELOC-Intent `permit` code.
    expect(normalizeSegmentCode('permit_activity')).toBe('permit_activity');
    expect(normalizeSegmentCode('heloc')).toBe('permit');
  });
});

describe('SegmentCard', () => {
  let container: HTMLDivElement;
  let root: Root;

  beforeEach(() => {
    setDrawer.mockClear();
    container = document.createElement('div');
    document.body.appendChild(container);
    root = createRoot(container);
  });

  afterEach(() => {
    act(() => root.unmount());
    container.remove();
  });

  function render(segment: Partial<SegmentSummary>, onClick?: () => void) {
    const base: SegmentSummary = {
      code: 'retention',
      name: 'Retention Risk',
      count: 0,
      delta: '+42%',
      avg_score: 0,
      description: 'Current customer retention segment.',
      color: 'var(--seg-retention)',
    };
    act(() => root.render(<SegmentCard segment={{ ...base, ...segment }} onClick={onClick} />));
  }

  it('does not show stale delta or average when the filtered count is zero', () => {
    render({ count: 0, delta: '+42%', avg_score: 0 });
    expect(container.textContent).toContain('no borrowers in current view');
    expect(container.textContent).not.toContain('+42%');
    expect(container.textContent).not.toContain('avg 0');
  });

  it('prefers canonical presentation copy over stale backend labels', () => {
    render({
      code: 'itm',
      name: 'In the Money',
      description: 'Legacy backend label.',
      count: 12,
      color: '#000000',
    });
    expect(container.textContent).toContain('Prime Refi Candidates');
    expect(container.textContent).toContain('Lien rate ≥ 75 bps above par and equity ≥ 15%.');
    expect(container.textContent).not.toContain('Legacy backend label.');
  });

  it('renders product and channel facet chips when mixes are present', () => {
    render({
      code: 'itm',
      count: 12,
      loan_product_mix: [
        { value: 'fha', count: 1240 },
        { value: 'conventional', count: 980 },
        { value: 'jumbo', count: 210 },
        { value: 'va', count: 40 },
      ],
      origination_channel_mix: [
        { value: 'loan_officer', count: 1500 },
        { value: 'digital', count: 620 },
        { value: 'branch', count: 90 },
      ],
    });
    // Product row: top-3 only, exact counts, short labels.
    expect(container.textContent).toContain('FHA 1,240');
    expect(container.textContent).toContain('Conv 980');
    expect(container.textContent).toContain('Jumbo 210');
    expect(container.textContent).not.toContain('VA 40');
    // Channel row: top-2 only, display labels.
    expect(container.textContent).toContain('Loan officer 1,500');
    expect(container.textContent).toContain('Digital 620');
    expect(container.textContent).not.toContain('Branch 90');
    expect(container.querySelector('.seg-card__facets')).not.toBeNull();
  });

  it('hides the facet block when both mixes are empty or absent', () => {
    render({ code: 'itm', count: 12 });
    expect(container.querySelector('.seg-card__facets')).toBeNull();
  });

  it('keeps the card on the div role-button composition and wraps facet controls in spans', () => {
    render({
      code: 'itm',
      count: 12,
      loan_product_mix: [{ value: 'fha', count: 1240 }],
    });
    const card = container.querySelector('.seg-card');
    expect(card?.tagName).toBe('DIV');
    expect(card?.getAttribute('role')).toBe('button');
    const chip = container.querySelector('.seg-card__facet-chip');
    expect(chip?.tagName).toBe('SPAN');
    expect(chip?.querySelector('.evidence-chip')).not.toBeNull();
  });

  it('opens the product-type drawer on chip click without toggling card selection', () => {
    const onClick = vi.fn();
    render(
      {
        code: 'itm',
        count: 12,
        loan_product_mix: [{ value: 'fha', count: 1240 }],
      },
      onClick,
    );
    const chip = container.querySelector('.seg-card__facet-chip .evidence-chip') as HTMLElement;
    act(() => {
      chip.dispatchEvent(new MouseEvent('click', { bubbles: true, cancelable: true }));
    });
    expect(setDrawer).toHaveBeenCalledWith(DRAWER_SOURCES.loanProductType);
    expect(onClick).not.toHaveBeenCalled();
  });

  it('opens the origination-channel drawer from a channel chip', () => {
    render({
      code: 'itm',
      count: 12,
      origination_channel_mix: [{ value: 'loan_officer', count: 1500 }],
    });
    const chip = container.querySelector('.seg-card__facet-chip .evidence-chip') as HTMLElement;
    act(() => {
      chip.dispatchEvent(new MouseEvent('click', { bubbles: true, cancelable: true }));
    });
    expect(setDrawer).toHaveBeenCalledWith(DRAWER_SOURCES.originationChannel);
  });

  it('renders an explicit gated panel for a not-connected source and suppresses the count', () => {
    render({
      code: 'permit_activity',
      count: 0,
      source_status: 'not_connected',
      source_name: 'Building Permits',
    });
    expect(container.textContent).toContain('not connected');
    expect(container.textContent).toContain('Building Permits');
    expect(container.textContent).toContain('—');
    expect(container.textContent).not.toContain('no borrowers in current view');
    const card = container.querySelector('.seg-card');
    expect(card?.classList.contains('seg-card--gated')).toBe(true);
    expect(card?.getAttribute('aria-disabled')).toBe('true');
  });

  it('labels a permission-denied source as not licensed', () => {
    render({
      code: 'second_lien_itm',
      count: 0,
      source_status: 'not_licensed',
      source_name: 'Voluntary Lien',
    });
    expect(container.textContent).toContain('not licensed');
    expect(container.textContent).toContain('Voluntary Lien');
  });

  it('opens the evidence drawer from the segment evidence chip', () => {
    render({ code: 'refi_propensity', count: 42, delta: '+5%', avg_score: 71 });
    const chip = container.querySelector<HTMLButtonElement>('.evidence-chip');
    expect(chip).not.toBeNull();
    act(() => chip!.click());
    expect(setDrawer).toHaveBeenCalledTimes(1);
    const source = setDrawer.mock.calls[0][0];
    expect(source.assetKey).toBe('segment_population');
    expect(source.title).toContain('Refi Propensity');
    expect(JSON.stringify(source.lineage)).toContain('mip.gold.borrower_360');
  });
});
