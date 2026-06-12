/**
 * @vitest-environment happy-dom
 *
 * GenieAnswer "Pin to Home" gating + action (Buyer-Wow #9): a genuine
 * (source==='genie') answer WITH a question shows the pin button and pins to
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
});
