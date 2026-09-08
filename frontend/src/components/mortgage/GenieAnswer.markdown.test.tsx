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

import { MarkdownAnswer, isSectionHeading } from './GenieAnswer.markdown';

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
});
