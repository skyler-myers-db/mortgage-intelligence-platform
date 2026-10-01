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
import { GenieAnswerSections } from './GenieAnswer.sections';

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

  it('makes the Summary and every section title a real h3, prose headings inside at h4 (genie-08)', () => {
    render(
      payload({
        summary: 'Refinance demand concentrates in three states.',
        sections: [section({ answer: '**A finding inside the section**\n\nTexas leads.' }), section({ title: 'Second' })],
      }),
    );
    const titled = Array.from(container.querySelectorAll('.genie-md-p--heading')).map((el) => [el.tagName, el.textContent]);
    expect(titled).toEqual([
      ['H3', 'Summary'],
      ['H3', 'Where the opportunity sits'],
      ['H4', 'A finding inside the section'],
      ['H3', 'Second'],
    ]);
    expect(container.querySelectorAll('p.genie-md-p--heading')).toHaveLength(0);
    // Three or fewer sections: plain headings, no toggles.
    expect(container.querySelector('.genie-answer__section-toggle')).toBeNull();
  });

  it('gives a 4-section sweep accordion headings, every section open', () => {
    const titles = ['Market size', 'Rate spread', 'Equity', 'What this adds up to'];
    render(payload({ summary: 'Summary text.', sections: titles.map((title) => section({ title })) }));
    const toggles = Array.from(container.querySelectorAll<HTMLButtonElement>('h3 > button.genie-answer__section-toggle'));
    expect(toggles.map((t) => t.textContent)).toEqual(titles);
    for (const toggle of toggles) {
      expect(toggle.getAttribute('aria-expanded')).toBe('true');
      const body = document.getElementById(toggle.getAttribute('aria-controls') ?? '');
      expect(body?.hidden).toBe(false);
      expect(body?.querySelector('.genie-answer__table')).not.toBeNull();
    }
  });

  it('collapses and re-opens a section from its toggle', () => {
    render(payload({ sections: ['A', 'B', 'C', 'D'].map((title) => section({ title })) }));
    const toggle = container.querySelectorAll<HTMLButtonElement>('.genie-answer__section-toggle')[1];
    const body = () => document.getElementById(toggle.getAttribute('aria-controls') ?? '');
    act(() => toggle.click());
    expect(toggle.getAttribute('aria-expanded')).toBe('false');
    expect(body()?.hidden).toBe(true);
    act(() => toggle.click());
    expect(toggle.getAttribute('aria-expanded')).toBe('true');
    expect(body()?.hidden).toBe(false);
  });

  it('says the table below shows every row once a section\'s Show all is open (genie-06 item 1)', () => {
    const states = Array.from({ length: 30 }, (_, i) => ({ state: `S${String(i + 1).padStart(2, '0')}`, borrowers: 3000 - i * 10 }));
    render(payload({
      summary: 'Borrowers spread across thirty states.',
      sections: [section({ table_rows: states, row_count: 30 })],
    } as unknown as Partial<GenieAnswerShape>));
    const caption = () => container.querySelector('.genie-chart__more')?.textContent ?? '';
    expect(caption()).toContain('the table below shows 10 of 30 rows');
    const showAll = container.querySelector<HTMLButtonElement>('button.genie-answer__show-all');
    expect(showAll?.textContent).toBe('Show all 30 rows');
    act(() => showAll?.click());
    expect(showAll?.getAttribute('aria-expanded')).toBe('true');
    expect(caption()).toMatch(/; the table below shows all 30 rows\.$/);
    expect(caption()).not.toContain('10 of 30');
    act(() => showAll?.click());
    expect(caption()).toContain('the table below shows 10 of 30 rows');
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

describe('GenieAnswerSections preview (genie-01 phase 1b partial research)', () => {
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

  function renderSections(preview: boolean) {
    const sections = [section({ row_count: 40 }), section({ title: 'Second', row_count: 3 })];
    act(() => {
      root.render(
        <MemoryRouter>
          <GenieAnswerSections summary={null} sections={sections} exportBase={null} preview={preview} />
        </MemoryRouter>,
      );
    });
  }

  it('hides row actions, CSV and cell links, and says the rows were trimmed', () => {
    renderSections(true);

    expect(container.querySelectorAll('a')).toHaveLength(0);
    expect(container.querySelector('.genie-answer__rows-actions, .genie-answer__toolbar')).toBeNull();
    expect(container.textContent).not.toMatch(/Show all|CSV/);
    const notes = Array.from(container.querySelectorAll('.genie-answer__preview-note')).map((n) => n.textContent);
    expect(notes).toEqual(['Preview: the first 3 of 40 rows. Every row arrives with the recorded answer.']);
  });

  it('keeps the links and the row actions on the recorded answer (control)', () => {
    renderSections(false);

    expect(container.querySelectorAll('a[href^="/lead-queue"]').length).toBeGreaterThan(0);
    expect(container.querySelector('.genie-answer__preview-note')).toBeNull();
  });
});
