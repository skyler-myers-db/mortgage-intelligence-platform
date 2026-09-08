/**
 * @vitest-environment happy-dom
 *
 * Deep-research presentation: a sweep answers one question with several
 * planned sub-analyses, and each of them must carry its own prose and its own
 * visual. The prior layout rendered the stitched narrative plus ONE chart —
 * "seven questions, one picture".
 */
import { act } from 'react';
import { createRoot, type Root } from 'react-dom/client';
import { MemoryRouter } from 'react-router';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import type { GenieAnswer as GenieAnswerShape, GenieAnswerSection } from '../../types';

vi.mock('../AppContext', () => ({ useApp: () => ({ setDrawer: vi.fn() }) }));
vi.mock('../../lib/api', async () => {
  const actual = await vi.importActual<typeof import('../../lib/api')>('../../lib/api');
  return { ...actual, api: { genieFeedback: vi.fn().mockResolvedValue({ accepted: true }) } };
});
vi.mock('../HealthProvider', () => ({ useWorkspaceHost: () => null }));

import { GenieAnswer } from './GenieAnswer';

(globalThis as unknown as { IS_REACT_ACT_ENVIRONMENT: boolean }).IS_REACT_ACT_ENVIRONMENT = true;

const TOP_LEVEL_BODY = 'Stitched narrative that repeats every section verbatim.';

function section(overrides: Partial<GenieAnswerSection> = {}): GenieAnswerSection {
  return {
    title: 'Where the opportunity sits',
    question: 'Which states hold the most in-the-money borrowers?',
    answer: 'Texas leads with 900 in-the-money borrowers.',
    trusted_assets: ['mip.gold.borrower_360'],
    sql_query: 'SELECT 1',
    row_count: 3,
    table_rows: [
      { state: 'TX', borrowers: 900 },
      { state: 'CA', borrowers: 700 },
      { state: 'FL', borrowers: 480 },
    ],
    visualization: null,
    ...overrides,
  };
}

function payload(overrides: Partial<GenieAnswerShape> = {}): GenieAnswerShape {
  return {
    answer: TOP_LEVEL_BODY,
    source: 'genie',
    trusted_assets: ['mip.gold.borrower_360'],
    conversation_id: 'conv-1',
    message_id: 'msg-1',
    genie_status: 'COMPLETED',
    table_rows: [{ state: 'NV', top_level_only: 1234 }],
    ...overrides,
  } as unknown as GenieAnswerShape;
}

const SWEEP = payload({
  summary: 'Refinance demand concentrates in three states.',
  sections: [
    section(),
    section({
      title: 'Who to call first',
      question: 'Which cohorts are largest?',
      answer: 'Prime refi candidates dominate the marketable population.',
      table_rows: [
        { segment_code: 'itm', marketable_borrowers: 620 },
        { segment_code: 'equity', marketable_borrowers: 410 },
      ],
    }),
  ],
});

describe('GenieAnswer deep-research sections', () => {
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

  function render(value: GenieAnswerShape) {
    act(() =>
      root.render(
        <MemoryRouter>
          <GenieAnswer payload={value} question="Where should we focus?" withChart />
        </MemoryRouter>,
      ),
    );
  }

  function headings() {
    return Array.from(container.querySelectorAll('.genie-md-p--heading')).map(
      (el) => el.textContent ?? '',
    );
  }

  it('renders the verified summary first, then every section title', () => {
    render(SWEEP);

    expect(headings()).toEqual([
      'Summary',
      'Where the opportunity sits',
      'Who to call first',
    ]);
    expect(container.textContent).toContain('Refinance demand concentrates in three states.');
    expect(container.textContent).toContain('Texas leads with 900 in-the-money borrowers.');
    expect(container.textContent).toContain('Prime refi candidates dominate the marketable population.');
  });

  it('does not repeat the stitched top-level answer body', () => {
    render(SWEEP);

    expect(container.textContent).not.toContain(TOP_LEVEL_BODY);
  });

  it('gives every section its own visual and drops the top-level one', () => {
    render(SWEEP);

    const charts = container.querySelectorAll('.genie-chart');
    expect(charts).toHaveLength(2);
    expect(charts[0].textContent).toContain('Borrowers by State');
    expect(charts[1].textContent).toContain('Marketable Borrowers by Cohort');
    // The top-level rows are the last sub-query's leftovers; they must not
    // render a third table under the sections.
    expect(container.textContent).not.toContain('Top Level Only');
    expect(container.querySelectorAll('.genie-answer__table')).toHaveLength(2);
  });

  it('keeps proof, pin and feedback controls once at the bottom', () => {
    render(payload({ ...SWEEP, proof: { trusted: true, sql_query: 'SELECT 1' } } as Partial<GenieAnswerShape>));

    expect(container.querySelectorAll('.genie-proof-toggle')).toHaveLength(1);
    expect(container.querySelectorAll('[data-testid="pin-to-home"]')).toHaveLength(1);
  });

  it('renders a withheld-narrative section without an inline notice', () => {
    render(
      payload({
        summary: 'Summary text.',
        sections: [section({ narrative_withheld: true, answer: 'TX: 900 borrowers.' })],
      }),
    );

    expect(container.textContent).toContain('TX: 900 borrowers.');
    expect(container.textContent).not.toContain('withheld');
  });

  it('leaves a single-turn answer exactly as it was', () => {
    render(payload());

    expect(container.textContent).toContain(TOP_LEVEL_BODY);
    expect(container.querySelectorAll('.genie-answer__sections')).toHaveLength(0);
    // Top-level rows still render their own table under the prose.
    expect(container.querySelectorAll('.genie-answer__table')).toHaveLength(1);
    expect(container.textContent).toContain('Top Level Only');
  });
});
