/**
 * @vitest-environment happy-dom
 */

import { act } from 'react';
import { createRoot, type Root } from 'react-dom/client';
import { MemoryRouter } from 'react-router';
import { afterEach, beforeAll, beforeEach, describe, expect, it, vi } from 'vitest';
import type { PropertyLoanLookupResponse } from '../../types';

const propertyLookup = vi.fn();

vi.mock('../../lib/api', async (importOriginal) => ({
  ...(await importOriginal<typeof import('../../lib/api')>()),
  api: {
    propertyLookup: (...args: unknown[]) => propertyLookup(...args),
  },
}));

import { ApiError, type ApiValidationIssue } from '../../lib/api';
import { PropertyLookupPanel } from './PropertyLookupPanel';
import { preloadDescribedError } from '../ui/DescribedError';

/** Server detail the panel must never print (audit states-04). */
const SENTINEL = 'SENTINEL server detail 500 Internal Server Error';

const makeApiError = (status: number, opts: { dependency?: string; validationIssues?: ApiValidationIssue[] } = {}) =>
  new ApiError(SENTINEL, { path: '/api/v1/lookup/property-loan', status, ...opts });

// The error vocabulary is its own chunk (loaded with the first failure).
beforeAll(async () => {
  await preloadDescribedError();
});

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

  it('surfaces the 422 validation issue text as a validation callout, never the message', async () => {
    propertyLookup.mockRejectedValue(makeApiError(422, {
      validationIssues: [{ field: 'zip5', message: 'String should match pattern', location: ['body', 'zip5'] }],
    }));
    render();
    fillValidForm();
    await submit();

    expect(resultEl()).toBeNull();
    const alert = container.querySelector('[role="alert"]');
    expect(alert).not.toBeNull();
    expect(alert!.textContent).toBe('zip5: String should match pattern.');
    expect(container.textContent).not.toContain('SENTINEL');
  });

  it('shows a degraded, retryable state on a 503 dependency-down, naming the dependency as the banner does', async () => {
    propertyLookup.mockRejectedValue(makeApiError(503, { dependency: 'lakebase' }));
    render();
    fillValidForm();
    await submit();

    expect(resultEl()).toBeNull();
    const alert = container.querySelector('[role="alert"]');
    expect(alert).not.toBeNull();
    expect(alert!.textContent).toContain('The operational database is warming up or unavailable.');
    expect(alert!.textContent).not.toContain('lakebase');
    expect(container.querySelector('button[aria-label="Retry property lookup"]')).not.toBeNull();
  });

  it('says why any other failure happened, without the transport message', async () => {
    propertyLookup.mockRejectedValue(makeApiError(500));
    render();
    fillValidForm();
    await submit();

    const alert = container.querySelector('[role="alert"]');
    expect(alert!.textContent).toContain("Couldn't complete the lookup: The server hit an unexpected error.");
    expect(container.textContent).not.toContain('SENTINEL');
    expect(container.textContent).not.toContain('Internal Server Error');
  });
});
