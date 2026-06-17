/**
 * @vitest-environment happy-dom
 *
 * GenieAnswer "Pin to Home" gating + action (Buyer-Wow #9): a genuine,
 * trusted answer (any source NOT on the degraded denylist — genie,
 * trusted_sql, sales_ops, …) WITH a question shows the pin button and pins to
 * the shared store; a degraded/policy-blocked answer does not; and the
 * deterministic follow-up fallback fills in when Genie returns none.
 */
import { act } from 'react';
import { createRoot, type Root } from 'react-dom/client';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import type { GenieAnswer as GenieAnswerShape } from '../../types';

vi.mock('../AppContext', () => ({ useApp: () => ({ setDrawer: vi.fn() }) }));

import { GenieAnswer } from './GenieAnswer';
import { PINNED_INSIGHTS_KEY, clearPinnedInsights } from '../../lib/pinnedInsights';

function payload(overrides: Partial<GenieAnswerShape> = {}): GenieAnswerShape {
  return {
    answer: 'The average loan age is 5.25 years.',
    source: 'genie',
    trusted_assets: ['mip.gold.lockin_cohort'],
    question_hash: 'h1',
    metric_value: '5.25 years',
    table_rows: null,
    follow_up_questions: [],
    ...overrides,
  } as unknown as GenieAnswerShape;
}

const storedPins = (): Array<{ question: string }> => {
  const raw = window.localStorage.getItem(PINNED_INSIGHTS_KEY);
  return raw ? JSON.parse(raw) : [];
};

function installLocalStorage() {
  // Reuse a working localStorage if the environment already provides one
  // (happy-dom may define it non-configurably in the full-suite worker, where
  // redefining would throw); otherwise install a Map-backed stub.
  try {
    if (window.localStorage && typeof window.localStorage.clear === 'function') {
      window.localStorage.clear();
      return;
    }
  } catch {
    /* fall through to define a stub */
  }
  const m = new Map<string, string>();
  try {
    Object.defineProperty(window, 'localStorage', {
      configurable: true,
      writable: true,
      value: {
        getItem: (k: string) => m.get(k) ?? null,
        setItem: (k: string, v: string) => { m.set(k, String(v)); },
        removeItem: (k: string) => { m.delete(k); },
        clear: () => m.clear(),
        key: (i: number) => [...m.keys()][i] ?? null,
        get length() { return m.size; },
      },
    });
  } catch {
    /* environment owns localStorage; nothing to install */
  }
}

describe('GenieAnswer pin + fallback', () => {
  let container: HTMLDivElement;
  let root: Root;
  beforeEach(() => {
    installLocalStorage();
    clearPinnedInsights();
    container = document.createElement('div');
    document.body.appendChild(container);
    root = createRoot(container);
  });
  afterEach(() => {
    act(() => root.unmount());
    container.remove();
  });

  it('shows the pin button for a genuine answer with a question, and pins to the store', () => {
    act(() => root.render(
      <GenieAnswer payload={payload()} question="What is the average loan age?" onFollowUp={() => {}} />,
    ));
    const pinBtn = container.querySelector<HTMLButtonElement>('[data-testid="pin-to-home"]');
    expect(pinBtn).not.toBeNull();
    expect(pinBtn!.textContent).toContain('Pin to Home');
    act(() => pinBtn!.click());
    const stored = storedPins();
    expect(stored).toHaveLength(1);
    expect(stored[0].question).toBe('What is the average loan age?');
    // Button flips to the pinned state.
    expect(container.querySelector('[data-testid="pin-to-home"]')!.textContent).toContain('Pinned to Home');
  });

  it('shows the pin button for a genuine trusted_sql answer (canonical booth demo set)', () => {
    // The canonical demo questions (top borrowers by state, top ITM ZIPs, …)
    // return source='trusted_sql', NOT 'genie'. They are fully trusted,
    // source-cited, persisted answers and MUST be pinnable. Regression guard
    // for the denylist boundary (was wrongly an `=== 'genie'` allowlist).
    act(() => root.render(
      <GenieAnswer
        payload={payload({ source: 'trusted_sql' })}
        question="Top borrowers by state?"
        onFollowUp={() => {}}
      />,
    ));
    const pinBtn = container.querySelector<HTMLButtonElement>('[data-testid="pin-to-home"]');
    expect(pinBtn).not.toBeNull();
    act(() => pinBtn!.click());
    expect(storedPins()).toHaveLength(1);
  });

  it.each(['policy_blocked', 'degraded', 'refused', 'data_gap', 'out_of_footprint'])(
    'does NOT show the pin button for a degraded source (%s)',
    (source) => {
      act(() => root.render(
        <GenieAnswer
          payload={payload({ source, metric_value: null, answer: 'Result was not displayed.' })}
          question="What is the average loan age?"
          onFollowUp={() => {}}
        />,
      ));
      expect(container.querySelector('[data-testid="pin-to-home"]')).toBeNull();
    },
  );

  it('does NOT show the pin button without a question (no provenance)', () => {
    act(() => root.render(<GenieAnswer payload={payload()} onFollowUp={() => {}} />));
    expect(container.querySelector('[data-testid="pin-to-home"]')).toBeNull();
  });

  it('renders the deterministic follow-up fallback when Genie returns none', () => {
    act(() => root.render(<GenieAnswer payload={payload({ follow_up_questions: [] })} question="Q" onFollowUp={() => {}} />));
    const chips = Array.from(container.querySelectorAll('.filter--question')).map((c) => c.textContent);
    expect(chips.some((t) => t?.includes('Break this down by state'))).toBe(true);
  });

  it('closes the Genie proof drawer on Escape and restores focus to the proof toggle', async () => {
    await act(async () => {
      root.render(
        <GenieAnswer
          payload={payload({
            proof: {
              trusted: true,
              source_assets: ['mip.gold.borrower_360'],
              row_count: 1,
              sql_query: 'SELECT COUNT(*) FROM mip.gold.borrower_360',
            },
          })}
          question="How many borrowers?"
          onFollowUp={() => {}}
        />,
      );
    });
    const proofToggle = Array.from(container.querySelectorAll<HTMLButtonElement>('button')).find((button) =>
      button.textContent?.includes('Show proof'),
    );
    expect(proofToggle).toBeTruthy();
    proofToggle!.focus();

    await act(async () => {
      proofToggle!.click();
      await Promise.resolve();
    });

    const drawer = document.body.querySelector<HTMLElement>('[aria-label="Genie answer proof"]');
    expect(drawer).toBeTruthy();
    expect(document.activeElement?.getAttribute('aria-label')).toBe('Close Genie proof');

    await act(async () => {
      window.dispatchEvent(new KeyboardEvent('keydown', { key: 'Escape' }));
      await Promise.resolve();
    });

    expect(document.body.querySelector('[aria-label="Genie answer proof"]')).toBeNull();
    expect(document.activeElement).toBe(proofToggle);
  });

  it('does NOT synthesize fallback follow-ups on a governed refusal', () => {
    // A fabricated "Which segments drive this?" under a fair-lending refusal
    // reads as the product ignoring its own guardrail — the fallback is gated
    // on the same trust boundary as the pin.
    act(() => root.render(
      <GenieAnswer
        payload={payload({ source: 'policy_blocked', metric_value: null, table_rows: null, answer: 'Result was not displayed.', follow_up_questions: [] })}
        question="What is the average borrower age in Illinois?"
        onFollowUp={() => {}}
      />,
    ));
    expect(container.querySelectorAll('.filter--question').length).toBe(0);
  });

  it('still honors backend-provided follow-ups even on a non-trusted source', () => {
    // We only suppress SYNTHESIZED pivots; if the backend explicitly returns
    // follow-ups they are intentional and rendered.
    act(() => root.render(
      <GenieAnswer
        payload={payload({ source: 'degraded', metric_value: null, follow_up_questions: ['Retry the question'] })}
        question="Q"
        onFollowUp={() => {}}
      />,
    ));
    const chips = Array.from(container.querySelectorAll('.filter--question')).map((c) => c.textContent);
    expect(chips.some((t) => t?.includes('Retry the question'))).toBe(true);
  });
});
