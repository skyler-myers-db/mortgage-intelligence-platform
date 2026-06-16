/**
 * @vitest-environment happy-dom
 *
 * BorrowerOfferPreviewMock (auto-offer Module 1 prototype): an unmistakably
 * labelled mock of the borrower-facing "click yes" offer experience. It must
 * carry the PROTOTYPE watermark + the legal disclaimer, show no real financial
 * terms, and on accept confirm that NOTHING was submitted.
 */
import { act } from 'react';
import { createRoot, type Root } from 'react-dom/client';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import type { Borrower360 } from '../../types';

vi.mock('../AppContext', () => ({ useApp: () => ({ lender: 'Summit Mortgage' }) }));

import { BorrowerOfferPreviewMock } from './BorrowerOfferPreviewMock';

function dossier(overrides: Partial<Borrower360> = {}): Borrower360 {
  return {
    borrower_id: 'B-1ABCDEFGHIJK2',
    recommended_offer: 'Refinance + HELOC',
    ...overrides,
  } as unknown as Borrower360;
}

describe('BorrowerOfferPreviewMock', () => {
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
    document.body.innerHTML = '';
  });

  it('renders a clearly-labelled prototype: watermark, banner, and legal disclaimer', () => {
    act(() => root.render(<BorrowerOfferPreviewMock borrower={dossier()} onClose={() => {}} />));
    const mock = document.body.querySelector('[data-testid="borrower-offer-mock"]')!;
    expect(mock).not.toBeNull();
    expect(mock.querySelector('.offer-mock__watermark')!.textContent).toBe('PROTOTYPE');
    expect(mock.textContent).toContain('Prototype of the borrower-facing experience');
    // The honest legal line: not a firm offer / credit decision / application.
    expect(mock.textContent).toContain('not a firm offer of credit');
    // Real (qualitative) product label, no fabricated APR/payment figures.
    expect(mock.textContent).toContain('Refinance + home-equity review');
    expect(mock.textContent).toContain('Summit Mortgage');
    // No TILA "trigger terms" of any shape (APR/rate/$/payment) — Slice 1 shows
    // only a qualitative product label. (Slice 2's indicative preview will
    // revise this deliberately, with stronger "illustrative" framing.)
    expect(mock.textContent).not.toMatch(/\bapr\b|\$\s?\d|\d\s?%|per month|monthly payment|interest rate/i);
  });

  it('shows a qualitative, figure-free reason when a signal is present', () => {
    act(() =>
      root.render(
        <BorrowerOfferPreviewMock borrower={dossier({ is_competitor_lien: true })} onClose={() => {}} />,
      ),
    );
    const reason = document.body.querySelector('.offer-mock__reason')!;
    expect(reason).not.toBeNull();
    expect(reason.textContent).toContain('another lender');
    // Borrower-facing → no TILA trigger terms in the reason.
    expect(reason.textContent).not.toMatch(/\bapr\b|\$\s?\d|\d\s?%/i);
  });

  it('renders no reason line when no confident signal is present (never fabricates)', () => {
    act(() => root.render(<BorrowerOfferPreviewMock borrower={dossier()} onClose={() => {}} />));
    expect(document.body.querySelector('.offer-mock__reason')).toBeNull();
  });

  it('on accept, confirms that nothing was submitted (no real offer made)', () => {
    act(() => root.render(<BorrowerOfferPreviewMock borrower={dossier()} onClose={() => {}} />));
    act(() => document.body.querySelector<HTMLButtonElement>('[data-testid="offer-mock-accept"]')!.click());
    const mock = document.body.querySelector('[data-testid="borrower-offer-mock"]')!;
    expect(mock.textContent).toContain('no information was submitted and no offer was made');
  });

  it('calls onClose from the close control', () => {
    const onClose = vi.fn();
    act(() => root.render(<BorrowerOfferPreviewMock borrower={dossier()} onClose={onClose} />));
    act(() => document.body.querySelector<HTMLButtonElement>('.offer-mock__close')!.click());
    expect(onClose).toHaveBeenCalledTimes(1);
  });
});
