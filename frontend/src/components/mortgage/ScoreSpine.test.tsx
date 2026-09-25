/**
 * @vitest-environment happy-dom
 *
 * ScoreSpine + ProofMargins at the rendered layer: five labelled segments in
 * weight order with widths from weighted points, the seal only for a trusted
 * gap-free proof, every gap listed otherwise, no "Unity Catalog recomputed",
 * and the margins with their dated par provenance and the not-a-credit-
 * decision note.
 *
 * Mutation check (lane report): flipping the seal's trusted check
 * in useBorrowerProof.ts proofSealHolds (`proof.trusted === true` ->
 * `proof.trusted !== true`) fails "shows the seal only for a trusted proof
 * with no gaps" and "seal matrix".
 */
import { act } from 'react';
import { createRoot, type Root } from 'react-dom/client';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { sampleProof } from '../../mocks/scoreAnatomyProof';
import { formatTimestamp } from '../../lib/time';
import type { BorrowerProof } from '../../types';
import { ProofMargins, parRefreshLabel } from './ProofMargins';
import { ScoreSpine } from './ScoreSpine';
import { SCORE_ANATOMY_COPY } from './scoreAnatomy.copy';
import { SCORE_SPINE_COPY } from './scoreSpine.copy';
import { proofSealHolds } from './useBorrowerProof';

vi.mock('../AppContext', () => ({
  useApp: () => ({ setDrawer: vi.fn(), showEvidence: true, showConfidence: true }),
}));

(globalThis as unknown as { IS_REACT_ACT_ENVIRONMENT: boolean }).IS_REACT_ACT_ENVIRONMENT = true;

describe('ScoreSpine', () => {
  let container: HTMLDivElement;
  let root: Root;
  const onOpen = vi.fn();

  beforeEach(() => {
    container = document.createElement('div');
    document.body.appendChild(container);
    root = createRoot(container);
    onOpen.mockClear();
  });

  afterEach(() => {
    act(() => root.unmount());
    container.remove();
  });

  function renderSpine(proof: BorrowerProof) {
    act(() => {
      root.render(<ScoreSpine proof={proof} onOpenComponent={onOpen} />);
    });
  }

  it('renders five labelled segments in weight order, sized by weighted points', () => {
    // Shuffled input: the spine orders by weight, not by payload order.
    const proof = sampleProof();
    renderSpine({ ...proof, score_components: [...proof.score_components].reverse() });

    const segs = [...container.querySelectorAll<HTMLButtonElement>('button.score-spine__seg')];
    expect(segs.map((seg) => seg.dataset.part)).toEqual([
      'economic_incentive', 'intent_trigger', 'fit', 'relationship', 'evidence',
    ]);
    expect(segs.map((seg) => seg.textContent)).toEqual([
      `Economic incentive 34.3 ${SCORE_SPINE_COPY.segmentHint}`,
      `Intent trigger 28.5 ${SCORE_SPINE_COPY.segmentHint}`,
      `Product fit 10.5 ${SCORE_SPINE_COPY.segmentHint}`,
      `Relationship 8.0 ${SCORE_SPINE_COPY.segmentHint}`,
      `Evidence coverage 9.0 ${SCORE_SPINE_COPY.segmentHint}`,
    ]);
    expect(segs[0].className).toContain('score-spine__seg--economic');

    const bar = container.querySelector('.score-spine__bar');
    expect(bar?.getAttribute('aria-hidden')).toBe('true');
    const slices = [...container.querySelectorAll<HTMLElement>('.score-spine__slice')];
    expect(slices.map((slice) => slice.style.inlineSize)).toEqual(['34.3%', '28.5%', '10.5%', '8%', '9%']);
  });

  it('a segment button and a slice both open that component', () => {
    renderSpine(sampleProof());
    act(() => {
      container.querySelector<HTMLButtonElement>('button[data-part="fit"]')?.click();
    });
    act(() => {
      container.querySelector<HTMLElement>('.score-spine__slice[data-part="relationship"]')?.click();
    });
    expect(onOpen.mock.calls).toEqual([['fit'], ['relationship']]);
  });

  it('shows the seal only for a trusted proof with no gaps', () => {
    renderSpine(sampleProof());
    const seal = container.querySelector('[data-testid="score-spine-seal"]');
    expect(seal?.textContent).toBe(SCORE_ANATOMY_COPY.seal);
    expect(container.querySelector('[data-testid="score-spine-gaps"]')).toBeNull();

    const gaps = ['Borrower dossier and lead_scores were refreshed at different times.', 'Recomputed primary offer does not match.'];
    renderSpine(sampleProof({ trusted: false, known_data_gaps: gaps }));
    expect(container.querySelector('[data-testid="score-spine-seal"]')).toBeNull();
    const listed = [...container.querySelectorAll('[data-testid="score-spine-gaps"] li')].map((li) => li.textContent);
    expect(listed).toEqual(gaps);

    // Trusted but with a gap (defence in depth): still no seal.
    renderSpine(sampleProof({ trusted: true, known_data_gaps: ['A gap.'] }));
    expect(container.querySelector('[data-testid="score-spine-seal"]')).toBeNull();
    // Untrusted without a listed gap: no seal either.
    renderSpine(sampleProof({ trusted: false, known_data_gaps: [] }));
    expect(container.querySelector('[data-testid="score-spine-seal"]')).toBeNull();
  });

  it('seal matrix', () => {
    expect(proofSealHolds(sampleProof())).toBe(true);
    expect(proofSealHolds(sampleProof({ trusted: false }))).toBe(false);
    expect(proofSealHolds(sampleProof({ known_data_gaps: ['gap'] }))).toBe(false);
    expect(proofSealHolds(sampleProof({ score_components: [] }))).toBe(false);
  });

  it('with no score components renders no segments, just the gaps', () => {
    const gap = 'Governed lead_scores component row was unavailable, so the score cannot be recomputed.';
    renderSpine(sampleProof({ trusted: false, score_components: [], known_data_gaps: [gap] }));
    expect(container.querySelectorAll('.score-spine__seg')).toHaveLength(0);
    expect(container.querySelector('.score-spine__bar')).toBeNull();
    expect(container.querySelector('[data-testid="score-spine-gaps"]')?.textContent).toContain(gap);
  });

  it('never claims "Unity Catalog recomputed"', () => {
    renderSpine(sampleProof());
    expect(container.textContent).not.toMatch(/unity catalog recomputed/i);
    expect([...Object.values(SCORE_ANATOMY_COPY), ...Object.values(SCORE_SPINE_COPY)].join(' ')).not.toMatch(/unity catalog recomputed/i);
  });
});

describe('ProofMargins', () => {
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

  it('lists every margin with its threshold and evidence chip, the dated par and the note', () => {
    act(() => {
      root.render(<ProofMargins proof={sampleProof()} titled />);
    });
    const rows = [...container.querySelectorAll<HTMLElement>('.score-margins__row')];
    expect(rows.map((row) => row.dataset.margin)).toEqual([
      'spread_screen', 'par_break_even', 'equity_floor', 'offer_flip_equity', 'offer_flip_par',
    ]);
    expect(rows[1].querySelector('dt')?.textContent).toBe('Par break-even');
    expect(rows[1].querySelector('.score-margins__threshold')?.textContent).toBe('par 6.26%');
    expect(rows.every((row) => row.querySelector('.evidence-chip') !== null)).toBe(true);
    expect(container.querySelector('.score-margins__title')?.textContent).toBe(SCORE_SPINE_COPY.marginsTitle);
    expect(container.querySelector('[data-testid="score-margins-note"]')?.textContent).toBe(
      'Marketing prioritization, not a credit decision.',
    );
    const provenance = container.querySelector('[data-testid="score-margins-provenance"]')?.textContent ?? '';
    // The par sits on the dossier row: dated by the "dossier <ts>" part of source_refresh_at.
    expect(provenance).toBe(
      `Par 6.26% as of ${formatTimestamp('2026-07-14T06:12:00Z')} — rates move only when the gold refresh runs; the FRED ingest schedule ships paused`,
    );
    expect(container.textContent).not.toMatch(/today/i);
    // Neutral styling: no pass / fail chips on a margin.
    expect(container.querySelectorAll('.chip--success, .chip--warning, .chip--danger')).toHaveLength(0);
  });

  it('dates the par "as of the last gold refresh" when the proof carries no refresh time', () => {
    expect(parRefreshLabel(null)).toBe('the last gold refresh');
    expect(parRefreshLabel('not a time')).toBe('the last gold refresh');
    act(() => {
      root.render(<ProofMargins proof={sampleProof({ source_refresh_at: null })} />);
    });
    expect(container.querySelector('[data-testid="score-margins-provenance"]')?.textContent).toBe(
      'Par 6.26% as of the last gold refresh — rates move only when the gold refresh runs; the FRED ingest schedule ships paused',
    );
  });

  it('drops the par provenance when no par margin was computed', () => {
    const margins = (sampleProof().margins ?? []).map((margin) =>
      margin.key === 'par_break_even' || margin.key === 'offer_flip_par'
        ? { ...margin, direction: 'unavailable' as const, value_text: 'Not computed for this row', threshold: 'no par rate on this row' }
        : margin,
    );
    act(() => {
      root.render(<ProofMargins proof={sampleProof({ margins })} />);
    });
    expect(container.querySelector('[data-testid="score-margins-provenance"]')).toBeNull();
    expect(container.querySelector('[data-testid="score-margins-note"]')).not.toBeNull();
  });

  it('hides the section for a payload without margins (an older server)', () => {
    act(() => {
      root.render(<ProofMargins proof={sampleProof({ margins: undefined })} />);
    });
    expect(container.querySelector('[data-testid="score-margins"]')).toBeNull();
  });
});
