/**
 * @vitest-environment happy-dom
 */

import { act } from 'react';
import { createRoot, type Root } from 'react-dom/client';
import { MemoryRouter } from 'react-router-dom';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import type { PropertyLoanLookupResponse } from '../../types';

const propertyLookup = vi.fn();

vi.mock('../../lib/api', () => {
  class MockApiError extends Error {
    status: number | null;
    dependency: string | null;
    constructor(message: string, status: number | null, dependency: string | null = null) {
      super(message);
      this.name = 'ApiError';
      this.status = status;
      this.dependency = dependency;
    }
  }
  return {
    api: {
      propertyLookup: (...args: unknown[]) => propertyLookup(...args),
    },
    ApiError: MockApiError,
  };
});

import { ApiError } from '../../lib/api';
import { PropertyLookupPanel } from './PropertyLookupPanel';

// The mocked ApiError constructor accepts (message, status, dependency).
const makeApiError = (message: string, status: number, dependency?: string) =>
  new (ApiError as unknown as new (m: string, s: number, d?: string) => Error)(
    message,
    status,
    dependency,
  );

(globalThis as unknown as { IS_REACT_ACT_ENVIRONMENT: boolean }).IS_REACT_ACT_ENVIRONMENT = true;

const MATCH: PropertyLoanLookupResponse = {
  matched: true,
  match_basis: 'exact_normalized_address_zip',
  clip_ref: 'CLIP••4821',
  owner_link_ref: 'OL••7733',
  borrower_id: 'B-102FL7THC6Q3L',
  lead_score: 88,
  segment: ['itm', 'equity'],
  loan: {
    lender_brand: 'Summit Mortgage',
    current_rate: 6.75,
    current_lien_balance: 412000,
    has_open_lien: true,
    ltv: 68,
  },
  dossier_path: '/borrower-360/B-102FL7THC6Q3L',
  audit_event_id: 'evt_match_001',
};

const MISS: PropertyLoanLookupResponse = {
  matched: false,
  match_basis: 'exact_normalized_address_zip',
  audit_event_id: 'evt_miss_002',
};

let container: HTMLDivElement;
let root: Root;

beforeEach(() => {
  propertyLookup.mockReset();
  container = document.createElement('div');
  document.body.appendChild(container);
  root = createRoot(container);
});

afterEach(() => {
  act(() => root.unmount());
  container.remove();
});

function render() {
  act(() => {
    root.render(
      <MemoryRouter>
        <PropertyLookupPanel />
      </MemoryRouter>,
    );
  });
}

function setInput(label: string, value: string) {
  const el = container.querySelector<HTMLInputElement>(`input[aria-label="${label}"]`);
  if (!el) throw new Error(`no input for ${label}`);
  const setter = Object.getOwnPropertyDescriptor(HTMLInputElement.prototype, 'value')!.set!;
  act(() => {
    setter.call(el, value);
    el.dispatchEvent(new Event('input', { bubbles: true }));
  });
}

async function submit() {
  const button = container.querySelector<HTMLButtonElement>('button[type="submit"]');
  if (!button) throw new Error('no submit button');
  await act(async () => {
    button.click();
    await Promise.resolve();
    await Promise.resolve();
  });
}

function fillValidForm(address = '123 Secret Elm St') {
  setInput('Property lookup — street address', address);
  setInput('Property lookup — ZIP', '60614');
}

const resultEl = () => container.querySelector('[data-testid="property-lookup-result"]');

describe('PropertyLookupPanel', () => {
  it('renders masked refs, loan facts, score, and a dossier link on a match', async () => {
    propertyLookup.mockResolvedValue(MATCH);
    render();
    fillValidForm();
    await submit();

    const result = resultEl();
    expect(result).not.toBeNull();
    const text = result!.textContent ?? '';
    expect(text).toContain('CLIP••4821');
    expect(text).toContain('OL••7733');
    expect(text).toContain('Summit Mortgage');
    expect(text).toContain('6.75%');
    expect(text).toContain('88');
    expect(text).toContain('Lookup audited · evt_match_001');

    const link = result!.querySelector('a[href="/borrower-360/B-102FL7THC6Q3L"]');
    expect(link).not.toBeNull();
    expect(link!.textContent).toContain('Open dossier');
  });

  it('renders the honest no-match copy on a miss', async () => {
    propertyLookup.mockResolvedValue(MISS);
    render();
    fillValidForm();
    await submit();

    const text = resultEl()!.textContent ?? '';
    expect(text).toContain('No exact match in the refreshed coverage');
    expect(text).toContain('fuzzy mastering is Cotality CLIP resolution');
    expect(text).toContain('Lookup audited · evt_miss_002');
  });

  it('never renders the submitted address inside the result card', async () => {
    propertyLookup.mockResolvedValue(MATCH);
    render();
    fillValidForm('999 Confidential Willow Ave');
    await submit();

    // The address is passed to the API but must never surface in the result
    // region — the response contract does not echo it and we do not persist it.
    const text = resultEl()!.textContent ?? '';
    expect(text).not.toContain('Confidential');
    expect(text).not.toContain('999 Confidential Willow Ave');

    // Sanity: the API did receive the typed address.
    expect(propertyLookup).toHaveBeenCalledWith(
      expect.objectContaining({ address_line: '999 Confidential Willow Ave', zip5: '60614' }),
    );
  });

  it('surfaces the sanitized 422 detail as a validation callout', async () => {
    propertyLookup.mockRejectedValue(makeApiError('zip5 must be 5 digits', 422));
    render();
    fillValidForm();
    await submit();

    expect(resultEl()).toBeNull();
    const alert = container.querySelector('[role="alert"]');
    expect(alert).not.toBeNull();
    expect(alert!.textContent).toContain('zip5 must be 5 digits');
  });

  it('shows a degraded, retryable state on a 503 dependency-down', async () => {
    propertyLookup.mockRejectedValue(makeApiError('dependency unavailable', 503, 'lakebase'));
    render();
    fillValidForm();
    await submit();

    expect(resultEl()).toBeNull();
    const alert = container.querySelector('[role="alert"]');
    expect(alert).not.toBeNull();
    expect(alert!.textContent).toContain('lakebase');
    expect(alert!.textContent).toContain('warming up or unavailable');
    expect(container.querySelector('button[aria-label="Retry property lookup"]')).not.toBeNull();
  });
});
