/**
 * @vitest-environment happy-dom
 */
import { beforeEach, describe, expect, it } from 'vitest';
import type { GenieAnswer as GenieAnswerShape } from '../types';
import {
  PINNED_INSIGHTS_KEY,
  buildFallbackFollowUps,
  buildPinFromAnswer,
  clearPinnedInsights,
  isTrustedGenieSource,
  pinInsight,
  unpinInsight,
} from './pinnedInsights';

function answer(overrides: Partial<GenieAnswerShape> = {}): GenieAnswerShape {
  return {
    answer: 'The average loan age is 5.25 years.',
    source: 'genie',
    trusted_assets: ['mip.gold.lockin_cohort'],
    question_hash: 'abc123',
    message_id: 'm1',
    metric_value: '5.25 years',
    table_rows: null,
    follow_up_questions: [],
    ...overrides,
  } as unknown as GenieAnswerShape;
}

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

describe('pinned insights store', () => {
  beforeEach(() => {
    installLocalStorage();
    clearPinnedInsights();
  });

  it('pins, persists to localStorage, and unpins', () => {
    const pin = buildPinFromAnswer(answer(), 'The average loan age is 5.25 years.', 'What is the average loan age?');
    pinInsight(pin);
    const stored = JSON.parse(window.localStorage.getItem(PINNED_INSIGHTS_KEY)!);
    expect(stored).toHaveLength(1);
    expect(stored[0].question).toBe('What is the average loan age?');
    expect(stored[0].summary).toBe('5.25 years'); // metric preferred over prose
    expect(stored[0].source).toBe('mip.gold.lockin_cohort');
    unpinInsight(pin.id);
    expect(JSON.parse(window.localStorage.getItem(PINNED_INSIGHTS_KEY)!)).toHaveLength(0);
  });

  it('dedupes by id (re-pinning the same answer moves it to front, no dup)', () => {
    const a = buildPinFromAnswer(answer({ question_hash: 'x' }), 's', 'Qa');
    const b = buildPinFromAnswer(answer({ question_hash: 'y' }), 's', 'Qb');
    pinInsight(a);
    pinInsight(b);
    pinInsight(a); // re-pin a
    const stored = JSON.parse(window.localStorage.getItem(PINNED_INSIGHTS_KEY)!);
    expect(stored.map((p: { id: string }) => p.id)).toEqual([a.id, b.id]); // a moved to front, single copy
  });

  it('caps the pin list at 6, keeping the newest and dropping the oldest', () => {
    for (let i = 0; i < 10; i += 1) {
      pinInsight(buildPinFromAnswer(answer({ question_hash: `h${i}` }), 's', `Q${i}`));
    }
    const stored = JSON.parse(window.localStorage.getItem(PINNED_INSIGHTS_KEY)!) as Array<{ id: string }>;
    expect(stored).toHaveLength(6);
    // Newest-first: h9 (last pinned) at front, h4 at the tail; h0..h3 dropped.
    expect(stored.map((p) => p.id)).toEqual(['h9', 'h8', 'h7', 'h6', 'h5', 'h4']);
  });

  it('clearPinnedInsights empties the store (actor-change reset)', () => {
    pinInsight(buildPinFromAnswer(answer(), 's', 'Q'));
    clearPinnedInsights();
    expect(JSON.parse(window.localStorage.getItem(PINNED_INSIGHTS_KEY)!)).toHaveLength(0);
  });

  it('falls back to the cleaned answer when there is no metric, truncating', () => {
    const long = 'x'.repeat(500);
    const pin = buildPinFromAnswer(answer({ metric_value: null }), long, 'Q');
    expect(pin.summary.length).toBeLessThanOrEqual(221); // 220 + the ellipsis
  });

  it('flattens markdown in the summary (the Home card renders text, not markdown)', () => {
    const md = 'Illinois (**IL**) leads with **55,037** in-the-money borrowers';
    const pin = buildPinFromAnswer(answer({ metric_value: null }), md, 'Q');
    expect(pin.summary).toBe('Illinois (IL) leads with 55,037 in-the-money borrowers');
    expect(pin.summary).not.toContain('**');
    expect(pin.summary).not.toContain('`');
  });

  it('collapses newlines/bullets and code spans into a single clean line', () => {
    const md = '- `mip.gold.state_rollup`\n- **55,037** borrowers';
    const pin = buildPinFromAnswer(answer({ metric_value: null }), md, 'Q');
    expect(pin.summary).toBe('mip.gold.state_rollup 55,037 borrowers');
    expect(pin.summary).not.toMatch(/\n/);
  });

  it('truncates at a word boundary with an ellipsis, never mid-token or on a dangling bracket', () => {
    // After stripping markdown the cut must not leave a half-word or a stray "(".
    const long = `${'word '.repeat(60)}Illinois (**IL**) with **55,037**`;
    const pin = buildPinFromAnswer(answer({ metric_value: null }), long, 'Q');
    expect(pin.summary.endsWith('…')).toBe(true);
    expect(pin.summary.length).toBeLessThanOrEqual(221);
    // No dangling opener / partial markup right before the ellipsis.
    expect(pin.summary).not.toMatch(/[([{*`\-–—,;:/&]…$/);
    expect(pin.summary).not.toContain('**');
  });
});

describe('isTrustedGenieSource (pin/persist trust boundary)', () => {
  it('treats genuine, source-cited answer sources as trusted', () => {
    // Not just `genie`: the canonical booth answers are `trusted_sql`/`sales_ops`.
    for (const s of ['genie', 'trusted_sql', 'sales_ops']) {
      expect(isTrustedGenieSource(s)).toBe(true);
    }
  });

  it('treats degraded/caveat sources as non-trusted (denylist)', () => {
    for (const s of ['degraded', 'policy_blocked', 'refused', 'data_gap', 'out_of_footprint']) {
      expect(isTrustedGenieSource(s)).toBe(false);
    }
  });

  it('treats empty/missing source as non-trusted', () => {
    expect(isTrustedGenieSource('')).toBe(false);
    expect(isTrustedGenieSource(null)).toBe(false);
    expect(isTrustedGenieSource(undefined)).toBe(false);
  });
});

describe('buildFallbackFollowUps', () => {
  it('suggests a state breakdown + top cohorts for a tabular answer', () => {
    const ups = buildFallbackFollowUps(answer({ table_rows: [{ a: 1 }], metric_value: null }));
    expect(ups).toContain('Break this down by state.');
    expect(ups).toContain('Show the top cohorts.');
    expect(ups).toHaveLength(2);
  });

  it('suggests a segment pivot for a metric-only answer', () => {
    const ups = buildFallbackFollowUps(answer({ table_rows: null, metric_value: '5.25 years' }));
    expect(ups).toContain('Break this down by state.');
    expect(ups).toContain('Which segments drive this?');
  });
});
