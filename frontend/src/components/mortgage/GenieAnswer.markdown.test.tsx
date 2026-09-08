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
    const paragraphs = Array.from(container.querySelectorAll('p.genie-md-p'));
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
  });

  it('a heading that opens the answer still gets the first-block class', () => {
    act(() => root.render(<MarkdownAnswer text={'**Opening heading**\n\nBody.'} workspaceHost={null} />));
    const first = container.querySelector('p.genie-md-p');
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
    const paragraphs = Array.from(container.querySelectorAll('p.genie-md-p'));
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
