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
