/**
 * @vitest-environment happy-dom
 */

import { act } from 'react';
import { createRoot, type Root } from 'react-dom/client';
import { afterEach, beforeEach, describe, expect, it } from 'vitest';
import type { GenieAnswer as GenieAnswerShape, GenieClaimsSummary, GenieVerifiedClaim } from '../../types';
import { GenieProofPanel } from './GenieAnswerProof';

function payload(elapsedMs: number | null): GenieAnswerShape {
  return {
    answer: 'Trusted SQL answer.',
    question: 'Which states lead?',
    source: 'trusted_sql',
    trusted_assets: [],
    proof: {
      trusted: true,
      row_count: 1,
      source_assets: [],
      elapsed_ms: elapsedMs,
    },
  } as GenieAnswerShape;
}

describe('GenieProofPanel', () => {
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
  });

  it('renders zero-millisecond proof latency as an observed value', () => {
    act(() => {
      root.render(<GenieProofPanel payload={payload(0)} onOpenSource={() => {}} />);
    });

    expect(container.textContent).toContain('Latency');
    expect(container.textContent).toContain('0 ms');
  });

  it('uses a dash only when proof latency is absent', () => {
    act(() => {
      root.render(<GenieProofPanel payload={payload(null)} onOpenSource={() => {}} />);
    });

    expect(container.textContent).toContain('Latency');
    expect(container.textContent).toContain('—');
  });

  it('does not render a source-assets section when proof has no source assets', () => {
    act(() => {
      root.render(<GenieProofPanel payload={payload(12)} onOpenSource={() => {}} />);
    });

    expect(container.textContent).not.toContain('Source UC assets');
  });

  it('leaves top-level API reasoning summaries to the answer surface', () => {
    const withReasoning = payload(12);
    withReasoning.reasoning_trace = [
      { kind: 'FILTERING_CONTEXT', content: 'Scoping to trusted borrower_360.' },
    ];

    act(() => {
      root.render(<GenieProofPanel payload={withReasoning} onOpenSource={() => {}} />);
    });

    expect(container.querySelector('.genie-proof__reasoning')).toBeNull();
    expect(container.textContent).not.toContain('Scoping to trusted borrower_360.');
  });

  it('does not render query trace content on untrusted proofs', () => {
    const blocked = payload(12);
    blocked.source = 'policy_blocked';
    blocked.proof = {
      ...(blocked.proof ?? {}),
      trusted: false,
      reasoning_trace: [
        {
          kind: 'THOUGHT_TYPE_TEXT',
          content: 'Unsafe trace mentioned jane@example.com and 123 Main St.',
        },
      ],
    };

    act(() => {
      root.render(<GenieProofPanel payload={blocked} onOpenSource={() => {}} />);
    });

    expect(container.textContent).toContain('Review required');
    expect(container.textContent).not.toContain('Unsafe trace');
    expect(container.textContent).not.toContain('jane@example.com');
    expect(container.textContent).not.toContain('123 Main St.');
  });
});

describe('GenieProofPanel: figures verified against the rows (genie-10 phase 1)', () => {
  // deviation:genie-figures-verified (pinned here)
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
  });

  const withClaims = (claims: GenieClaimsSummary | null): GenieAnswerShape => {
    const base = payload(12);
    return { ...base, proof: { ...base.proof, claims } } as GenieAnswerShape;
  };
  const render = (value: GenieAnswerShape) =>
    act(() => root.render(<GenieProofPanel payload={value} onOpenSource={() => undefined} />));
  const metrics = () =>
    Array.from(container.querySelectorAll('.genie-proof__metric')).map((m) => m.querySelector('.eyebrow')?.textContent);

  it('shows the count and every figure grouped under the summary and each section', () => {
    render(
      withClaims({
        verified: 3,
        total: 3,
        items: [
          { token: '48,396', kind: 'number', derivation: 'returned_value', section: null },
          { token: '$1.2M', kind: 'currency', derivation: 'derived_from_rows', section: 'Market size' },
          { token: '80%', kind: 'percent', derivation: 'bound', section: 'Market size' },
        ],
      }),
    );

    expect(metrics()).toContain('Figures');
    expect(container.textContent).toContain('3 of 3 answer figures verified');
    const groups = Array.from(container.querySelectorAll('.genie-claims__group'));
    expect(groups.map((g) => g.querySelector('.genie-claims__group-title')?.textContent)).toEqual(['Summary', 'Market size']);
    const items = Array.from(container.querySelectorAll('.genie-claims__item')).map((item) => item.textContent);
    expect(items).toEqual([
      '48,396Count or valueMatches a returned value',
      '$1.2MAmountDerived from the returned rows (a total, average, share or change)',
      '80%PercentA threshold the returned values satisfy',
    ]);
    expect(container.querySelector('.genie-claims__more')).toBeNull();
  });

  it('says the list is the first 40 when the metric counts more verified figures', () => {
    const items = Array.from({ length: 40 }, (_, i) => ({
      token: String(100 + i),
      kind: 'number' as const,
      derivation: 'returned_value' as const,
    }));
    render(withClaims({ verified: 45, total: 45, items }));

    expect(container.textContent).toContain('45 of 45 answer figures verified');
    expect(container.querySelectorAll('.genie-claims__item')).toHaveLength(40);
    expect(container.querySelector('.genie-claims__more')?.textContent).toBe(
      'The first 40 of 45 verified answer figures are listed.',
    );
  });

  it('shows no group heading on a single-turn answer', () => {
    render(withClaims({ verified: 1, total: 1, items: [{ token: '123', kind: 'number', derivation: 'returned_value' }] }));

    expect(container.querySelector('.genie-claims__group-title')).toBeNull();
    expect(container.querySelectorAll('.genie-claims__item')).toHaveLength(1);
  });

  it('counts the answer figures in the singular for one figure (owner ruling O2)', () => {
    render(withClaims({ verified: 1, total: 1, items: [{ token: '123', kind: 'number', derivation: 'returned_value' }] }));

    expect(Array.from(container.querySelectorAll('.genie-proof__value')).map((v) => v.textContent)).toContain(
      '1 of 1 answer figure verified',
    );
  });

  it('counts only the answer figures: 8 of 9 when one is not verified (owner ruling O2)', () => {
    render(withClaims({ verified: 8, total: 9, items: [{ token: '123', kind: 'number', derivation: 'returned_value' }] }));

    expect(container.textContent).toContain('8 of 9 answer figures verified');
  });

  it.each<[Partial<GenieVerifiedClaim>, string]>([
    [{ support: 'cell', column: 'in_the_money_borrowers' }, 'Matches a returned value in In The Money Borrowers'],
    [{ support: 'cell', column: null }, 'Matches a returned value'],
    [{ support: 'row_count' }, 'The number of returned rows'],
    [{ support: 'sum', column: 'borrowers' }, 'The total of the returned Borrowers values'],
    [{ support: 'sum', column: null }, 'A total of a returned column'],
    [{ support: 'share', column: 'borrowers' }, 'A share of the returned Borrowers total'],
    [{ support: 'share' }, 'A share of a returned column total'],
    [{ support: 'derived', derivation: 'derived_from_rows' }, 'Derived from the returned rows (an average, range, change or running total)'],
    [{ support: 'bound', derivation: 'bound' }, 'A threshold the returned values satisfy'],
  ])('labels the provenance %j as "%s"', (fields, label) => {
    const item: GenieVerifiedClaim = { token: '250', kind: 'number', derivation: 'returned_value', ...fields };
    render(withClaims({ verified: 1, total: 1, items: [item] }));

    expect(container.querySelector('.genie-claims__derivation')?.textContent).toBe(label);
  });

  it.each<GenieVerifiedClaim['derivation']>(['returned_value', 'derived_from_rows', 'bound'])(
    'falls back to the %s derivation label when the item carries no provenance (older answers)',
    (derivation) => {
      const labels = {
        returned_value: 'Matches a returned value',
        derived_from_rows: 'Derived from the returned rows (a total, average, share or change)',
        bound: 'A threshold the returned values satisfy',
      };
      render(withClaims({ verified: 1, total: 1, items: [{ token: '250', kind: 'number', derivation, support: null }] }));

      expect(container.querySelector('.genie-claims__derivation')?.textContent).toBe(labels[derivation]);
    },
  );

  it.each([null, { verified: 0, total: 0, items: [] }])('renders nothing for claims %j (older answers included)', (claims) => {
    render(withClaims(claims));

    expect(metrics()).not.toContain('Figures');
    expect(container.querySelector('.genie-claims')).toBeNull();
  });
});
