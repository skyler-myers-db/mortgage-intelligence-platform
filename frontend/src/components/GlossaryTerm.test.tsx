import type { ReactElement } from 'react';
import { renderToStaticMarkup } from 'react-dom/server';
import { MemoryRouter } from 'react-router';
import { describe, expect, it } from 'vitest';
import { GlossaryTerm } from './GlossaryTerm';

describe('GlossaryTerm', () => {
  it('renders an inline glossary link with a concise tooltip', () => {
    const html = renderToStaticMarkup(
      <MemoryRouter>
        <GlossaryTerm term="avm">AVM</GlossaryTerm>
      </MemoryRouter>,
    );

    expect(html).toContain('href="/glossary#avm"');
    expect(html).toContain('aria-describedby=');
    expect(html).toContain('role="tooltip"');
    expect(html).toContain("Cotality&#x27;s automated valuation estimate");
    expect(html).toContain('Used to calculate equity and LTV');
  });

  function linkName(node: ReactElement): string {
    // This suite renders to static markup under Node: read the first link's
    // aria-label from the markup and undo React's attribute escaping.
    const html = renderToStaticMarkup(<MemoryRouter>{node}</MemoryRouter>);
    const raw = /<a [^>]*aria-label="([^"]*)"/.exec(html)?.[1] ?? '';
    return raw.replace(/&#x27;/g, "'").replace(/&quot;/g, '"').replace(/&amp;/g, '&');
  }

  // W5d print deferral 6 (label in name, WCAG 2.5.3): the accessible name
  // starts with the visible text whenever it is not the entry's term.
  it('leads its accessible name with visible text that differs from the term', () => {
    const name = linkName(<GlossaryTerm term="inTheMoney">Not in the money</GlossaryTerm>);
    expect(name.startsWith('Not in the money. In the Money: ')).toBe(true);
    expect(name.endsWith('Open glossary entry.')).toBe(true);
  });

  it('keeps the term-first name when the visible text is the term', () => {
    expect(linkName(<GlossaryTerm term="inTheMoney">In the Money</GlossaryTerm>).startsWith('In the Money: ')).toBe(true);
    expect(linkName(<GlossaryTerm term="inTheMoney" />).startsWith('In the Money: ')).toBe(true);
  });

  it('names the Segments mode links by what they show', () => {
    const name = linkName(<GlossaryTerm term="segmentMatchMode">Any selected</GlossaryTerm>);
    expect(name.startsWith('Any selected. Segment match mode: ')).toBe(true);
  });
});
