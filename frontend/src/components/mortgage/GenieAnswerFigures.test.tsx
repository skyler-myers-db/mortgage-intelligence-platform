/**
 * @vitest-environment happy-dom
 *
 * Figures in Genie's prose that match one returned cell (audit 2026-09-21
 * `genie-10` phase 2). deviation:genie-figure-highlight (pinned here).
 *
 * Rendered-DOM pins: the mark wraps the trimmed token only; hovering it shows
 * the passive, aria-hidden evidence card (title, column, where the figure is
 * listed) after the hover-intent delay; the mark is not focusable and has no
 * title. Through GenieAnswer: the provider keys each MarkdownAnswer by its
 * exact text (single answer, sweep summary, each section), drops a duplicate
 * text, and is absent for withheld prose, a governed-action receipt, an answer
 * without claims and the Partial research preview.
 */
import { act } from 'react';
import { createRoot, type Root } from 'react-dom/client';
import { MemoryRouter } from 'react-router';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import type { GenieAnswer as GenieAnswerShape, GenieAnswerSection, GenieVerifiedClaim } from '../../types';
import { SHOW_DELAY_MS } from '../ui/anchorPlacement';

vi.mock('../AppContext', () => ({ useApp: () => ({ setDrawer: vi.fn() }) }));
vi.mock('../../lib/api', async () => {
  const actual = await vi.importActual<typeof import('../../lib/api')>('../../lib/api');
  return { ...actual, api: { genieFeedback: vi.fn().mockResolvedValue({ accepted: true }) } };
});
vi.mock('../HealthProvider', () => ({ useWorkspaceHost: () => null }));
// A hover preloads the drawer body chunk only; keep the chunk out of the test.
vi.mock('./evidenceDrawerBodyLoader', () => ({
  preloadEvidenceDrawerBody: vi.fn(),
  getLoadedDrawerProse: () => null,
}));

import { GenieAnswer } from './GenieAnswer';
import { MarkdownAnswer } from './GenieAnswer.markdown';
import { GenieVerifiedReveal } from './GenieVerifiedReveal';
import { GenieFigureScopes } from './GenieAnswerFigures';
import { FIGURE_HOVER_CTA, FIGURE_HOVER_TITLE } from './GenieFigureMark';

function cell(token: string, occurrences: number, column: string | null, section: string | null = null): GenieVerifiedClaim {
  return { token, kind: 'number', derivation: 'returned_value', support: 'cell', column, occurrences, section };
}

function section(overrides: Partial<GenieAnswerSection> = {}): GenieAnswerSection {
  return {
    title: 'Where the opportunity sits',
    question: 'Which states hold the most in-the-money borrowers?',
    answer: 'Texas leads with 900 in-the-money borrowers.',
    trusted_assets: ['mip.gold.borrower_360'],
    sql_query: 'SELECT 1',
    row_count: 2,
    table_rows: [
      { state: 'TX', borrowers: 900 },
      { state: 'CA', borrowers: 700 },
    ],
    visualization: null,
    ...overrides,
  };
}

function payload(overrides: Partial<GenieAnswerShape> = {}): GenieAnswerShape {
  return {
    answer: 'Texas has 250 borrowers and an average of 185.',
    source: 'genie',
    trusted_assets: ['mip.gold.borrower_360'],
    conversation_id: 'conv-1',
    message_id: 'msg-1',
    genie_status: 'COMPLETED',
    table_rows: [
      { state: 'IL', borrowers: 120 },
      { state: 'TX', borrowers: 250 },
    ],
    proof: {
      trusted: true,
      sql_query: 'SELECT 1',
      claims: {
        verified: 2,
        total: 2,
        items: [
          cell('250', 1, 'borrowers'),
          { token: '185', kind: 'number', derivation: 'derived_from_rows', support: 'derived', occurrences: null },
        ],
      },
    },
    ...overrides,
  } as unknown as GenieAnswerShape;
}

/** The mark loads lazily: let the import settle and React re-render. */
async function settleLazyMarks(): Promise<void> {
  await act(async () => {
    await import('./GenieFigureMark');
  });
}

const marks = (root: ParentNode) => Array.from(root.querySelectorAll('mark.genie-md-figure')).map((m) => m.textContent);

describe('GenieFigureMark', () => {
  let container: HTMLDivElement;
  let root: Root;

  beforeEach(() => {
    vi.useFakeTimers();
    vi.setSystemTime(new Date(Date.now() + 60_000));
    vi.stubGlobal('CSS', { supports: (condition: string) => !/anchor-name/.test(condition), escape: (v: string) => v });
    container = document.createElement('div');
    document.body.appendChild(container);
    root = createRoot(container);
  });
  afterEach(() => {
    act(() => root.unmount());
    container.remove();
    vi.useRealTimers();
    vi.unstubAllGlobals();
  });

  async function renderScoped(text: string, items: GenieVerifiedClaim[]) {
    act(() =>
      root.render(
        <GenieFigureScopes value={new Map([[text, items]])}>
          <MarkdownAnswer text={text} workspaceHost={null} />
        </GenieFigureScopes>,
      ),
    );
    await settleLazyMarks();
  }

  it('wraps the trimmed token only and leaves the trailing whitespace outside', async () => {
    await renderScoped('The total is 250 .', [cell('250', 1, 'borrowers')]);
    const mark = container.querySelector('mark.genie-md-figure');
    expect(mark?.textContent).toBe('250');
    expect(mark?.nextSibling?.textContent).toBe(' .');
    expect(container.querySelector('p')?.textContent).toBe('The total is 250 .');
  });

  it('is not focusable and carries no title', async () => {
    await renderScoped('Texas has 250 borrowers.', [cell('250', 1, 'borrowers')]);
    const mark = container.querySelector('mark.genie-md-figure')!;
    expect(mark.hasAttribute('tabindex')).toBe(false);
    expect(mark.hasAttribute('title')).toBe(false);
    expect(mark.getAttributeNames().sort()).toEqual(['class']);
  });

  it('shows the aria-hidden evidence card with the title, column and CTA after the hover delay', async () => {
    await renderScoped('Texas has 250 borrowers.', [cell('250', 1, 'in_the_money_borrowers')]);
    const mark = container.querySelector('mark.genie-md-figure')!;
    act(() => {
      mark.dispatchEvent(new MouseEvent('mouseenter'));
    });
    act(() => vi.advanceTimersByTime(SHOW_DELAY_MS - 50));
    expect(document.body.querySelector('.evidence-hovercard')).toBeNull();
    act(() => vi.advanceTimersByTime(50));
    const card = document.body.querySelector<HTMLElement>('.evidence-hovercard');
    expect(card).not.toBeNull();
    expect(container.contains(card)).toBe(false);
    expect(card!.getAttribute('aria-hidden')).toBe('true');
    expect(card!.querySelector('.evidence-hovercard__title')?.textContent).toBe(FIGURE_HOVER_TITLE);
    expect(card!.querySelector('.evidence-hovercard__signal-label')?.textContent).toBe('Column');
    expect(card!.querySelector('.evidence-hovercard__signal-value')?.textContent).toBe('In The Money Borrowers');
    expect(card!.querySelector('.evidence-hovercard__cta')?.textContent).toBe(FIGURE_HOVER_CTA);
    act(() => {
      mark.dispatchEvent(new MouseEvent('mouseleave'));
    });
    expect(document.body.querySelector('.evidence-hovercard:not(.is-closing)')).toBeNull();
  });

  it('shows no column row when the figure names no column', async () => {
    await renderScoped('Texas has 250 borrowers.', [cell('250', 1, null)]);
    act(() => {
      container.querySelector('mark')!.dispatchEvent(new MouseEvent('mouseenter'));
    });
    act(() => vi.advanceTimersByTime(SHOW_DELAY_MS));
    expect(document.body.querySelector('.evidence-hovercard__title')?.textContent).toBe(FIGURE_HOVER_TITLE);
    expect(document.body.querySelector('.evidence-hovercard__signal')).toBeNull();
  });
});

describe('GenieAnswer figure scopes', () => {
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

  async function render(value: GenieAnswerShape) {
    const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
    act(() =>
      root.render(
        <QueryClientProvider client={client}>
          <MemoryRouter>
            <GenieAnswer payload={value} question="Where should we focus?" />
          </MemoryRouter>
        </QueryClientProvider>,
      ),
    );
    await settleLazyMarks();
  }

  it('marks a single answer\'s cell figure and never its derived figure', async () => {
    await render(payload());
    expect(marks(container)).toEqual(['250']);
  });

  it.each<[string, Partial<GenieAnswerShape>]>([
    ['a withheld answer', { source: 'policy_blocked' }],
    ['a governed-action receipt', { source: 'governed_action', action_audit_event_id: null }],
    ['an answer without claims', { proof: { trusted: true, claims: null } } as Partial<GenieAnswerShape>],
    [
      'an answer recorded before provenance',
      {
        proof: {
          trusted: true,
          claims: { verified: 1, total: 1, items: [{ token: '250', kind: 'number', derivation: 'returned_value' }] },
        },
      } as Partial<GenieAnswerShape>,
    ],
  ])('marks nothing on %s', async (_label, overrides) => {
    await render(payload(overrides));
    expect(container.textContent).toContain('250');
    expect(marks(container)).toEqual([]);
  });

  const SUMMARY = 'Texas leads the book with 900 borrowers.';
  const sweep = (sections: GenieAnswerSection[], items: GenieVerifiedClaim[]) =>
    payload({
      answer: 'Stitched narrative with 900 and 620.',
      summary: SUMMARY,
      sections,
      proof: { trusted: true, sql_query: 'SELECT 1', claims: { verified: items.length, total: items.length, items } },
    } as Partial<GenieAnswerShape>);

  it('keys the sweep summary and each section by the exact text it renders', async () => {
    await render(
      sweep(
        [
          section(),
          section({
            title: 'Who to call first',
            question: 'Which cohorts are largest?',
            answer: 'Prime refi candidates number 620 borrowers.',
            table_rows: [{ segment_code: 'itm', marketable_borrowers: 620 }],
          }),
          section({
            title: 'Withheld section',
            question: 'Which rates moved?',
            answer: '2 results, shown in the chart and table below: 900 and 620.',
            narrative_withheld: true,
          }),
        ],
        [
          cell('900', 1, 'borrowers', null),
          cell('900', 1, 'borrowers', 'Where the opportunity sits'),
          cell('620', 1, 'marketable_borrowers', 'Who to call first'),
        ],
      ),
    );
    const sectionNodes = Array.from(container.querySelectorAll('.genie-answer__section'));
    const summaryNode = container.querySelector('.genie-answer__sections > .genie-md-p--heading + p')!;
    expect(summaryNode.textContent).toBe(SUMMARY);
    expect(marks(summaryNode)).toEqual(['900']);
    expect(sectionNodes.map((node) => marks(node))).toEqual([['900'], ['620'], []]);
  });

  it('drops a duplicate text: two sections with the same answer mark nothing', async () => {
    const twin = 'Texas leads with 900 in-the-money borrowers.';
    await render(
      sweep(
        [section({ answer: twin }), section({ title: 'Second view', question: 'Again?', answer: twin })],
        [cell('900', 1, 'borrowers', 'Where the opportunity sits'), cell('900', 1, 'borrowers', 'Second view')],
      ),
    );
    expect(marks(container)).toEqual([]);
  });

  it('drops two sections that share a title', async () => {
    await render(
      sweep(
        [
          section({ answer: 'Texas leads with 900 borrowers.' }),
          section({ question: 'Another?', answer: 'California holds 700 borrowers.' }),
        ],
        [cell('900', 1, 'borrowers', 'Where the opportunity sits'), cell('700', 1, 'borrowers', 'Where the opportunity sits')],
      ),
    );
    expect(marks(container)).toEqual([]);
  });

  it('marks nothing in the Partial research preview, even beside a marked answer with the same section text', async () => {
    // The preview renders outside GenieAnswer, whose provider is the only
    // source of marks. Here a live provider holds the preview's exact section
    // text and the mark chunk is loaded (the answer's own section IS marked),
    // so a provider that leaked out of GenieAnswer's subtree (a module-level
    // store, a hoisted context) would mark the preview too. The fixture spec
    // (genie-provenance-tiles case (c)) pins the real in-flight thread.
    const shared = section();
    const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
    act(() =>
      root.render(
        <QueryClientProvider client={client}>
          <MemoryRouter>
            <GenieAnswer payload={sweep([shared], [cell('900', 1, 'borrowers', shared.title)])} question="Where should we focus?" />
            <GenieVerifiedReveal reveal={{ jobId: 'job-1', verified: 3, partsPlanned: 3, rev: 1, sections: [shared] }} />
          </MemoryRouter>
        </QueryClientProvider>,
      ),
    );
    await settleLazyMarks();
    const preview = container.querySelector('[role="group"][aria-label="Partial research"]')!;
    const answerSection = container.querySelector('.genie-answer .genie-answer__section')!;
    expect(marks(answerSection), 'the answer marks its section (provider live, chunk loaded)').toEqual(['900']);
    expect(preview.textContent).toContain(shared.answer);
    expect(marks(preview)).toEqual([]);
  });
});
