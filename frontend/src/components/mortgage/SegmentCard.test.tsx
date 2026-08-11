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

  it('puts selection on a dedicated button instead of the card element', () => {
    render(
      {
        code: 'itm',
        count: 12,
        loan_product_mix: [{ value: 'fha', count: 1240 }],
      },
      vi.fn(),
    );
    const card = container.querySelector('.seg-card');
    expect(card?.tagName).toBe('DIV');
    // The card element itself is no longer a widget — it holds interactive
    // children, and a role="button" host containing buttons is an ARIA
    // violation as well as the click-routing trap this composition replaced.
    expect(card?.getAttribute('role')).toBeNull();
    expect(card?.hasAttribute('tabindex')).toBe(false);

    const select = container.querySelector<HTMLButtonElement>('.seg-card__select');
    expect(select?.tagName).toBe('BUTTON');
    // type="button" + a native <button> is the whole keyboard contract:
    // Enter/Space activation comes from the platform, not a keydown handler.
    expect(select?.getAttribute('type')).toBe('button');
    expect(select?.getAttribute('aria-pressed')).toBe('false');
    expect(select?.getAttribute('aria-label')).toBe(
      'Select Prime Refi Candidates segment — 12 borrowers',
    );

    const chip = container.querySelector('.seg-card__facet-chip');
    expect(chip?.tagName).toBe('SPAN');
    expect(chip?.querySelector('.evidence-chip')).not.toBeNull();
  });

  it('keeps every evidence control outside the selection button', () => {
    render(
      {
        code: 'itm',
        count: 12,
        loan_product_mix: [{ value: 'fha', count: 1240 }],
        origination_channel_mix: [{ value: 'digital', count: 620 }],
      },
      vi.fn(),
    );
    const select = container.querySelector('.seg-card__select');
    expect(select).not.toBeNull();
    const chips = Array.from(container.querySelectorAll('.evidence-chip'));
    // Segment evidence chip + product chip + channel chip.
    expect(chips).toHaveLength(3);
    for (const chip of chips) {
      // Nothing needs stopPropagation() because nothing nests: a chip click
      // cannot reach the selection button if the button is not its ancestor.
      expect(select!.contains(chip)).toBe(false);
    }
  });

  it('selects the segment when the card surface is clicked', () => {
    const onClick = vi.fn();
    render({ code: 'itm', count: 12 }, onClick);
    const select = container.querySelector<HTMLButtonElement>('.seg-card__select');
    act(() => select!.click());
    expect(onClick).toHaveBeenCalledTimes(1);
    expect(setDrawer).not.toHaveBeenCalled();
  });

  it('marks the selection button pressed for the active segment', () => {
    render({ code: 'itm', count: 12 }, vi.fn());
    expect(
      container.querySelector('.seg-card__select')?.getAttribute('aria-pressed'),
    ).toBe('false');
    act(() =>
      root.render(
        <SegmentCard
          segment={{
            code: 'itm',
            name: 'In the Money',
            count: 12,
            delta: '+1%',
            avg_score: 70,
            description: 'x',
            color: '#000',
          }}
          selected
          onClick={vi.fn()}
        />,
      ),
    );
    expect(
      container.querySelector('.seg-card__select')?.getAttribute('aria-pressed'),
    ).toBe('true');
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
    render(
      {
        code: 'permit_activity',
        count: 0,
        source_status: 'not_connected',
        source_name: 'Building Permits',
      },
      vi.fn(),
    );
    expect(container.textContent).toContain('not connected');
    expect(container.textContent).toContain('Building Permits');
    expect(container.textContent).toContain('—');
    expect(container.textContent).not.toContain('no borrowers in current view');
    const card = container.querySelector('.seg-card');
    expect(card?.classList.contains('seg-card--gated')).toBe(true);
    // A gated segment cannot be selected, so it exposes no selection control
    // at all — stronger than aria-disabled on a non-widget, and it keeps the
    // card from advertising a click that would do nothing.
    expect(container.querySelector('.seg-card__select')).toBeNull();
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

  // Addressable-vs-contactable disclosure. The headline count is the whole
  // addressable population; the Lead Queue this card links to applies the
  // contact-eligibility predicate and shows a strict subset — live
  // 2026-08-11 that was 3,217 of 74,335 for Prime Refi (23x). The card has
  // to state the relationship or the click reads as a broken link.
  it('states the contactable subset alongside the headline count', () => {
    render(
      { code: 'itm', count: 74335, contactable: 3217, delta: '+5%', avg_score: 63 },
      vi.fn(),
    );
    const note = container.querySelector('.seg-card__reconcile');
    expect(note).not.toBeNull();
    expect(note?.textContent).toContain('3,217');
    expect(note?.textContent).toContain('74,335');
    expect(note?.textContent).toContain('contactable');
    // The relationship is what makes the two numbers legible together —
    // the smaller one must never be presented as if it stood alone.
    expect(note?.textContent?.replace(/\s+/g, ' ')).toBe(
      '3,217 contactable of 74,335 addressable',
    );
    // Same disclosure for a screen reader, on the control that navigates.
    const select = container.querySelector<HTMLButtonElement>('.seg-card__select');
    expect(select?.getAttribute('aria-label')).toContain('3,217 contactable');
  });

  it('never reports more contactable than addressable', () => {
    // The backend clamps a precomputed-vs-live snapshot skew, so the card
    // should never be handed an inverted pair; assert the rendered numbers
    // stay in the right order for the payloads it does get.
    for (const [count, contactable] of [[74335, 3217], [25, 0], [1, 1]]) {
      render({ code: 'itm', count, contactable });
      const note = container.querySelector('.seg-card__reconcile');
      if (!note) {
        // No note is correct when there is no gap to state: either nobody is
        // in the segment, or every addressable borrower is contactable.
        expect(contactable >= count || count === 0).toBe(true);
        continue;
      }
      const [shown, total] = (note.textContent ?? '')
        .match(/[\d,]+/g)!
        .map((n) => Number(n.replace(/,/g, '')));
      expect(shown).toBeLessThanOrEqual(total);
    }
  });

  it('says nothing when contactable equals addressable', () => {
    // Browser pass 2026-08-11: the default Contactability="Eligible only"
    // filter makes the addressable count ALREADY the contactable one, so the
    // note rendered "3,217 contactable of 3,217 addressable" on every card.
    // The tests above assert the note RENDERS and so could not see the
    // tautology. Nothing to reconcile, nothing to say.
    render({ code: 'itm', count: 3217, contactable: 3217 }, vi.fn());
    expect(container.querySelector('.seg-card__reconcile')).toBeNull();
    const select = container.querySelector<HTMLButtonElement>('.seg-card__select');
    expect(select?.getAttribute('aria-label')).not.toContain('contactable');
  });

  it('says nothing when the payload does not report contactable', () => {
    // An older payload must read "not reported", never a fabricated zero —
    // "0 contactable of 74,335" is a confident, wrong claim.
    render({ code: 'itm', count: 74335, delta: '+5%', avg_score: 63 }, vi.fn());
    expect(container.querySelector('.seg-card__reconcile')).toBeNull();
    const select = container.querySelector<HTMLButtonElement>('.seg-card__select');
    expect(select?.getAttribute('aria-label')).not.toContain('contactable');
  });

  it('suppresses the disclosure on a gated card, like the count itself', () => {
    render({
      code: 'listed',
      count: 5,
      contactable: 1,
      source_status: 'not_connected',
      source_name: 'MLS Listings',
    });
    expect(container.querySelector('.seg-card__reconcile')).toBeNull();
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
    // The borrower_360 row-grain citation renders from the governed
    // segment_population manifest family (pinned in drawerSources.test.ts).
    expect(source.lineageFamily).toBe('segment_population');
  });
});
