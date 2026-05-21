import { renderToStaticMarkup } from 'react-dom/server';
import { MemoryRouter } from 'react-router-dom';
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
});
