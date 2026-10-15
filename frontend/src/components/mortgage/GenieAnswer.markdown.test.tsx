/**
 * @vitest-environment happy-dom
 *
 * Deep-research answers title each planned sub-analysis and the closing
 * synthesis with one bold run on its own line. Those lines must render as
 * section headings (`genie-md-p--heading`), while bold inside prose stays
 * ordinary emphasis and the first block never carries the hairline.
 */
import { act } from 'react';
import { createRoot, type Root } from 'react-dom/client';
import { afterEach, beforeEach, describe, expect, it } from 'vitest';

import { MarkdownAnswer, isSectionHeading, isSourceFootnote } from './GenieAnswer.markdown';
import { GenieFigureScopes } from './GenieAnswerFigures';
import { tooltipText } from '../../test/tooltipText';
// Load the lazy mark's module graph (EvidenceHoverCard and its imports) at
// file load, as GenieAnswerFigures.test.tsx does, so its first transform is
// not charged to the first figure-mark test's timeout. renderScoped still
// awaits the import to let React.lazy settle.
import './GenieFigureMark';
import type { GenieVerifiedClaim } from '../../types';

(globalThis as unknown as { IS_REACT_ACT_ENVIRONMENT: boolean }).IS_REACT_ACT_ENVIRONMENT = true;

const SWEEP_ANSWER = [
  'I asked the governed space to plan this request itself; it decomposed the question into 8 sub-analyses.',
  '**Which segments have the highest opportunity, and how large is each segment?**',
  'The in-the-money segment leads with **1,204** borrowers at an average score of 71.',
  '**What this adds up to**',
  'Refinance leads the offer mix; HELOC follows on equity.',
].join('\n\n');

describe('isSectionHeading', () => {
  it('accepts one bold run on its own line and nothing else', () => {
    expect(isSectionHeading('**What this adds up to**')).toBe(true);
    expect(isSectionHeading('  **Which segments lead?**  ')).toBe(true);
    expect(isSectionHeading('The segment has **1,204** borrowers.')).toBe(false);
    expect(isSectionHeading('**Two** bold **runs**')).toBe(false);
    expect(isSectionHeading('plain text')).toBe(false);
  });
});

describe('MarkdownAnswer section headings', () => {
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

  it('marks bold-only paragraphs as headings and leaves inline bold alone', () => {
    act(() => root.render(<MarkdownAnswer text={SWEEP_ANSWER} workspaceHost={null} />));
    const paragraphs = Array.from(container.querySelectorAll('.genie-md-p'));
    expect(paragraphs).toHaveLength(5);
    const headings = paragraphs.filter((p) => p.classList.contains('genie-md-p--heading'));
    expect(headings.map((p) => p.textContent)).toEqual([
      'Which segments have the highest opportunity, and how large is each segment?',
      'What this adds up to',
    ]);
    // The intro is the first block and is not a heading.
    expect(paragraphs[0].classList.contains('genie-md-p--first')).toBe(true);
    expect(paragraphs[0].classList.contains('genie-md-p--heading')).toBe(false);
    // Inline bold inside prose stays emphasis, not a heading.
    expect(paragraphs[2].classList.contains('genie-md-p--heading')).toBe(false);
    expect(paragraphs[2].querySelector('strong')?.textContent).toBe('1,204');
    // A bold-only line is a REAL heading element now (genie-08), h3 by default.
    expect(headings.map((h) => h.tagName)).toEqual(['H3', 'H3']);
    expect(container.querySelectorAll('p.genie-md-p--heading')).toHaveLength(0);
  });

  it('a heading inside a deep-research section is one level down (h4)', () => {
    act(() => root.render(<MarkdownAnswer text={'**Inside a section**\n\nBody.'} headingLevel={4} />));
    expect(container.querySelector('.genie-md-p--heading')?.tagName).toBe('H4');
  });

  it('a heading that opens the answer still gets the first-block class', () => {
    act(() => root.render(<MarkdownAnswer text={'**Opening heading**\n\nBody.'} workspaceHost={null} />));
    const first = container.querySelector('.genie-md-p');
    expect(first?.className).toContain('genie-md-p--first');
    expect(first?.className).toContain('genie-md-p--heading');
  });

  /**
   * The trailing "Source: …" disclosure is provenance, not analysis. On its
   * own line it now reads as a footnote; inside a sentence it stays body copy,
   * and the Catalog Explorer link is untouched either way.
   */
  it('renders a standalone source disclosure as a muted footnote', () => {
    act(() =>
      root.render(
        <MarkdownAnswer
          text={'Texas leads with 900 borrowers.\n\nSource: mip.gold.borrower_360.'}
          workspaceHost="https://dbc-test.cloud.databricks.com"
        />,
      ),
    );
    const paragraphs = Array.from(container.querySelectorAll('.genie-md-p'));
    expect(paragraphs).toHaveLength(2);
    expect(paragraphs[0].classList.contains('genie-md-p--source')).toBe(false);
    expect(paragraphs[1].classList.contains('genie-md-p--source')).toBe(true);
    // Still a Catalog Explorer link, still the plain "Source:" label.
    const link = paragraphs[1].querySelector('a.uc-asset-link');
    expect(link?.textContent).toBe('mip.gold.borrower_360');
    expect(paragraphs[1].textContent).toContain('Source: mip.gold.borrower_360');
  });

  it('leaves a source disclosure inside a sentence as body copy', () => {
    act(() =>
      root.render(
        <MarkdownAnswer
          text={'The average loan age is 5.25 years. Source: mip.gold.borrower_360.'}
          workspaceHost={null}
        />,
      ),
    );
    const paragraph = container.querySelector('p.genie-md-p');
    expect(paragraph?.classList.contains('genie-md-p--source')).toBe(false);
  });
});

/**
 * The rest of the answer grammar (audit 2026-09-21 `stack-02` / `genie-07`):
 * one rendered-DOM case per construct. Every string still reaches the DOM
 * through React text nodes; no LLM-authored URL becomes an anchor.
 */
describe('MarkdownAnswer grammar', () => {
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

  const renderText = (text: string, workspaceHost: string | null = null) =>
    act(() => root.render(<MarkdownAnswer text={text} workspaceHost={workspaceHost} />));

  it('(a) renders numbered lines as an ordered list instead of one run-on paragraph', () => {
    renderText('Top states:\n1. Illinois leads.\n2. Texas follows.\n3) Ohio is third.');
    const list = container.querySelector('ol.genie-md-list.genie-md-list--ordered');
    expect(list).not.toBeNull();
    expect(Array.from(list!.querySelectorAll('li')).map((li) => li.textContent)).toEqual([
      'Illinois leads.',
      'Texas follows.',
      'Ohio is third.',
    ]);
    expect(list!.hasAttribute('start')).toBe(false);
    expect(container.querySelector('p')?.textContent).toBe('Top states:');
  });

  it('(a) keeps the numbering of a list an interleaved bullet split, via start', () => {
    renderText('1. First.\n2. Second.\n- a side note\n3. Third.\n4. Fourth.');
    const lists = container.querySelectorAll('ol');
    expect(lists).toHaveLength(2);
    expect(lists[1].getAttribute('start')).toBe('3');
    expect(container.querySelectorAll('ul li')).toHaveLength(1);
  });

  it('(a) leaves a year and a decimal that open a line as prose', () => {
    renderText('2026. The rate environment shifted.\n3.5% of borrowers moved.');
    expect(container.querySelector('ol')).toBeNull();
    expect(container.querySelector('p')?.textContent).toBe(
      '2026. The rate environment shifted. 3.5% of borrowers moved.',
    );
  });

  it('(b) renders ATX headings as heading elements without the # marks', () => {
    renderText('### Where the demand sits ###\nIllinois leads.\n## Why it matters');
    const headings = Array.from(container.querySelectorAll('.genie-md-p--heading'));
    expect(headings.map((h) => [h.tagName, h.textContent])).toEqual([
      ['H3', 'Where the demand sits'],
      ['H3', 'Why it matters'],
    ]);
    expect(headings[0].classList.contains('genie-md-p--first')).toBe(true);
    expect(container.textContent).not.toContain('#');
  });

  it('(c) renders guarded *italics* and never treats _ as emphasis', () => {
    renderText('The *largest* cohort sits in mip.gold.lead_scores and snake_case_column (_x_); 2*3*4 stays math.');
    expect(Array.from(container.querySelectorAll('em')).map((em) => em.textContent)).toEqual(['largest']);
    expect(container.textContent).toBe(
      'The largest cohort sits in mip.gold.lead_scores and snake_case_column (_x_); 2*3*4 stays math.',
    );
  });

  it('(c) italics apply after bold: **bold** is never read as two italics', () => {
    renderText('**1,204** borrowers and * not italic * here');
    expect(container.querySelector('strong')?.textContent).toBe('1,204');
    expect(container.querySelector('em')).toBeNull();
    expect(container.textContent).toBe('1,204 borrowers and * not italic * here');
  });

  it('(d) renders LLM links and images as plain text: no <a>, no <img>, no URL', () => {
    renderText(
      'See [the rate table](https://example.com/rates "Rates") and ![a chart](https://example.com/c.png).',
      'https://dbc-test.cloud.databricks.com',
    );
    expect(container.querySelector('a')).toBeNull();
    expect(container.querySelector('img')).toBeNull();
    expect(container.textContent).toBe('See the rate table and a chart.');
    expect(container.innerHTML).not.toContain('example.com');
  });

  it('(d) keeps the reviewed Source: link as the only anchor', () => {
    renderText('[Click](https://example.com)\n\nSource: mip.gold.borrower_360.', 'https://dbc-test.cloud.databricks.com');
    const anchors = Array.from(container.querySelectorAll('a'));
    expect(anchors.map((a) => a.className)).toEqual(['uc-asset-link']);
  });

  it('(e) renders a prose pipe table verbatim in a labelled, focusable pre, never a <table>', () => {
    const table = '| State | Borrowers |\n|---|---|\n| IL | 1,204 |';
    renderText(`Breakdown:\n${table}\nThat is all.`);
    expect(container.querySelector('table')).toBeNull();
    const figure = container.querySelector('figure.genie-md-pre');
    expect(figure?.querySelector('figcaption.genie-md-pre__label')?.textContent).toBe("As written in Genie's narrative");
    const pre = figure?.querySelector('pre');
    expect(pre?.textContent).toBe(table);
    expect(pre?.getAttribute('tabindex')).toBe('0');
    expect(Array.from(container.querySelectorAll('p')).map((p) => p.textContent)).toEqual(['Breakdown:', 'That is all.']);
  });

  it('(e) a single pipe line stays prose', () => {
    renderText('| only one row |');
    expect(container.querySelector('pre')).toBeNull();
    expect(container.querySelector('p')?.textContent).toBe('| only one row |');
  });

  it('(f) renders a fenced block verbatim in the same pre, with no label', () => {
    renderText('Query:\n```sql\nSELECT **x** FROM t\n```\nDone.');
    const figure = container.querySelector('figure.genie-md-pre');
    expect(figure?.querySelector('figcaption')).toBeNull();
    expect(figure?.querySelector('pre')?.textContent).toBe('SELECT **x** FROM t');
    expect(container.querySelector('strong')).toBeNull();
  });

  it('(g) a thematic break ends the paragraph and renders nothing', () => {
    renderText('First part.\n---\nSecond part.\n* * *\n___');
    expect(Array.from(container.querySelectorAll('p')).map((p) => p.textContent)).toEqual(['First part.', 'Second part.']);
    expect(container.querySelector('hr')).toBeNull();
    expect(container.querySelector('ul')).toBeNull();
  });
});

describe('isSourceFootnote', () => {
  it.each([
    'Source: mip.gold.borrower_360',
    'Source: mip.gold.borrower_360.',
    'source: mip.silver.property_features',
    'Sources: mip.gold.lead_scores',
    '  Source: mip.gold.borrower_360  ',
  ])('accepts %s', (line) => {
    expect(isSourceFootnote(line)).toBe(true);
  });

  it.each([
    'The average loan age is 5.25 years. Source: mip.gold.borrower_360.',
    'Source: borrower_360',
    'Sources reviewed by the analyst',
    'Source: mip.gold.borrower_360 and mip.gold.lead_scores',
  ])('rejects %s', (line) => {
    expect(isSourceFootnote(line)).toBe(false);
  });
});

/**
 * genie-10 phase 2: the figure marks live inside the inline grammar. With a
 * GenieFigureScopes entry for the text, a located cell figure is marked in
 * paragraphs, headings, list items, bold, italics and link text; never in
 * inline code, a verbatim block or the Source anchor. Without a provider the
 * renderer is unchanged: the expected HTML below was captured from the base
 * renderer (2931d82c) for the same five answers.
 */
describe('MarkdownAnswer figure marks', () => {
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

  const HOST = 'https://dbc-test.cloud.databricks.com';
  const cell = (token: string, occurrences: number): GenieVerifiedClaim => ({
    token,
    kind: 'number',
    derivation: 'returned_value',
    support: 'cell',
    column: 'borrowers',
    occurrences,
  });
  const renderScoped = async (text: string, items: GenieVerifiedClaim[], workspaceHost: string | null = null) => {
    act(() =>
      root.render(
        <GenieFigureScopes value={new Map([[text, items]])}>
          <MarkdownAnswer text={text} workspaceHost={workspaceHost} />
        </GenieFigureScopes>,
      ),
    );
    // The mark loads lazily: let the import settle and React re-render.
    await act(async () => {
      await import('./GenieFigureMark');
    });
  };
  const markParents = () =>
    Array.from(container.querySelectorAll('mark.genie-md-figure')).map((m) => [m.textContent, m.parentElement?.tagName]);

  it('marks inside strong, em, list items, headings and link text', async () => {
    await renderScoped(
      '### 412 in the lead\nThe leader holds **1,204** borrowers and *388* more.\n- 71 in the list\n\nSee [the 905 rows](https://example.test/x).',
      [cell('412', 1), cell('1,204', 1), cell('388', 1), cell('71', 1), cell('905', 1)],
    );
    expect(markParents()).toEqual([
      ['412', 'SPAN'],
      ['1,204', 'STRONG'],
      ['388', 'EM'],
      ['71', 'SPAN'],
      ['905', 'SPAN'],
    ]);
    expect(container.querySelector('h3 mark')?.textContent).toBe('412');
    expect(container.querySelector('li mark')?.textContent).toBe('71');
  });

  it('never marks inside inline code, a verbatim block or the Source anchor', async () => {
    // Four 250s are counted (code, prose, verbatim, the anchor's table name);
    // only the prose one is marked.
    await renderScoped('Use `250` here and 250 borrowers.\n\n```\n250\n```\n\nSource: mip.gold.250.', [cell('250', 4)], HOST);
    expect(markParents()).toEqual([['250', 'SPAN']]);
    expect(container.querySelector('code mark')).toBeNull();
    expect(container.querySelector('pre mark')).toBeNull();
    expect(container.querySelector('a.uc-asset-link')?.textContent).toBe('mip.gold.250');
    expect(container.querySelector('a.uc-asset-link mark')).toBeNull();
  });

  it('marks nothing for a text the provider has no entry for', () => {
    act(() =>
      root.render(
        <GenieFigureScopes value={new Map([['another text', [cell('250', 1)]]])}>
          <MarkdownAnswer text="Texas has 250 borrowers." workspaceHost={null} />
        </GenieFigureScopes>,
      ),
    );
    expect(container.querySelector('mark')).toBeNull();
  });

  const BASE_HTML: Array<[string, string | null, string]> = [
    [
      'Illinois leads with **48,396** in-the-money borrowers, ahead of Texas at 10,914 (a 48.3% share).\n\nSource: mip.gold.borrower_360.',
      HOST,
      '<p class="genie-md-p genie-md-p--first"><span>Illinois leads with </span><strong>48,396</strong><span> in-the-money borrowers, ahead of Texas at 10,914 (a 48.3% share).</span></p><p class="genie-md-p genie-md-p--source"><span>Source: <a class="uc-asset-link" href="https://dbc-test.cloud.databricks.com/explore/data/mip/gold/borrower_360" target="_blank" rel="noopener noreferrer" aria-describedby="tooltip-desc_ID_" data-tooltip="tooltip-desc_ID_">mip.gold.borrower_360</a>.</span></p>',
    ],
    [
      'I asked the governed space to plan this request itself; it decomposed the question into 8 sub-analyses.\n\n**Which segments lead?**\n\nThe in-the-money segment leads with **1,204** borrowers at an average score of 71.',
      null,
      '<p class="genie-md-p genie-md-p--first"><span>I asked the governed space to plan this request itself; it decomposed the question into 8 sub-analyses.</span></p><h3 class="genie-md-p genie-md-p--heading"><strong>Which segments lead?</strong></h3><p class="genie-md-p"><span>The in-the-money segment leads with </span><strong>1,204</strong><span> borrowers at an average score of 71.</span></p>',
    ],
    [
      'Top states:\n1. Illinois holds 412 borrowers.\n2. Texas holds 388.\n- a side note at 5 m\n\n### Where 12k sits\nSee [the 7 rates](https://example.com/7) and `250 rows`.',
      null,
      '<p class="genie-md-p genie-md-p--first"><span>Top states:</span></p><ol class="genie-md-list genie-md-list--ordered"><li><span>Illinois holds 412 borrowers.</span></li><li><span>Texas holds 388.</span></li></ol><ul class="genie-md-list"><li><span>a side note at 5 m</span></li></ul><h3 class="genie-md-p genie-md-p--heading"><span>Where 12k sits</span></h3><p class="genie-md-p"><span>See </span><span>the 7 rates</span><span> and </span><code class="inline-code">250 rows</code><span>.</span></p>',
    ],
    [
      'Breakdown:\n| State | Borrowers |\n|---|---|\n| IL | 1,204 |\nThe *largest* cohort is 1,204 borrowers; mip.gold.lead_scores_2026 and _5_ stay.\n```\nSELECT 5\n```',
      HOST,
      '<p class="genie-md-p genie-md-p--first"><span>Breakdown:</span></p><figure class="genie-md-pre"><figcaption class="genie-md-pre__label">As written in Genie\'s narrative</figcaption><pre tabindex="0">| State | Borrowers |\n|---|---|\n| IL | 1,204 |</pre></figure><p class="genie-md-p"><span>The </span><em>largest</em><span> cohort is 1,204 borrowers; mip.gold.lead_scores_2026 and _5_ stay.</span></p><figure class="genie-md-pre"><pre tabindex="0">SELECT 5</pre></figure>',
    ],
    [
      'The book holds $1.2B across 3,410 loans, +350 bps over par, with 5Source: mip.gold.borrower_360 cited.',
      HOST,
      '<p class="genie-md-p genie-md-p--first"><span>The book holds $1.2B across 3,410 loans, +350 bps over par, with 5Source: <a class="uc-asset-link" href="https://dbc-test.cloud.databricks.com/explore/data/mip/gold/borrower_360" target="_blank" rel="noopener noreferrer" aria-describedby="tooltip-desc_ID_" data-tooltip="tooltip-desc_ID_">mip.gold.borrower_360</a> cited.</span></p>',
    ],
  ];

  it('names a Catalog Explorer source link in a design-system tooltip, never a native title (critic-08)', () => {
    act(() => root.render(<MarkdownAnswer text="Source: mip.gold.borrower_360." workspaceHost={HOST} />));
    const link = container.querySelector('a.uc-asset-link');
    expect(link?.textContent).toBe('mip.gold.borrower_360');
    expect(link?.hasAttribute('title')).toBe(false);
    expect(tooltipText(link)).toBe('Open mip.gold.borrower_360 in Databricks Catalog Explorer');
  });

  // React useId values (the Tooltip's description id) depend on render
  // order, so they compare as _ID_.
  it.each(BASE_HTML)('renders %j byte-identically to the base without a provider', (text, workspaceHost, html) => {
    act(() => root.render(<MarkdownAnswer text={text} workspaceHost={workspaceHost} />));
    expect(container.innerHTML.replace(/_r_[0-9a-z]+_/g, '_ID_')).toBe(html);
  });
});
