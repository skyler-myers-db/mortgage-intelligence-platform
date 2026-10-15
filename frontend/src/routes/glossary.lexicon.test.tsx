import { renderToStaticMarkup } from 'react-dom/server';
import { MemoryRouter } from 'react-router';
import { describe, expect, it } from 'vitest';
import { LEXICON } from '../lib/lexicon';
import GlossaryRoute from './glossary';

/**
 * critic-11 / D-platform-process-g: /glossary shows the In the Money term in
 * its current forms only. The retired names ("refi economics", "prime refi
 * candidates", ...) are search-only (GlossaryEntry.searchAliases): typing one
 * still finds the term, but no alias chip ever displays one.
 */
const RETIRED = /\bprime\b|\bprime[- ]refi\w*|\brefi(?:nance)?[- ]economics\b|\brefi screen\b/i;

function render(entry: string): string {
  return renderToStaticMarkup(
    <MemoryRouter initialEntries={[entry]}>
      <GlossaryRoute />
    </MemoryRouter>,
  );
}

function article(html: string, id: string): string {
  const match = html.match(new RegExp(`<article[^>]*\\sid="${id}"[^>]*>[\\s\\S]*?</article>`));
  return match ? match[0] : '';
}

/** Each element's text on its own line, so adjacent chips never fuse. */
function lines(html: string): string {
  return html.replace(/<[^>]+>/g, '\n');
}

function aliasChips(entryHtml: string): string[] {
  const block = entryHtml.match(/<div class="glossary-entry__aliases">([\s\S]*?)<\/div>/);
  return block ? [...block[1].matchAll(/<span class="mono">([^<]*)<\/span>/g)].map((chip) => chip[1]) : [];
}

describe('the In the Money glossary entry', () => {
  it('shows only its current forms under the term', () => {
    const entry = article(render('/glossary'), LEXICON.inTheMoney.glossaryId);
    expect(entry).toContain(`>${LEXICON.inTheMoney.label}</h3>`);
    expect(aliasChips(entry)).toEqual(['ITM', 'in-the-money']);
  });

  it('renders no retired name anywhere on the page', () => {
    expect(lines(render('/glossary'))).not.toMatch(RETIRED);
  });

  it.each(LEXICON.inTheMoney.legacyAliases)('is still found by the retired name "%s"', (alias) => {
    const html = render(`/glossary?q=${encodeURIComponent(alias)}`);
    const entry = article(html, LEXICON.inTheMoney.glossaryId);
    expect(entry, alias).not.toBe('');
    expect(aliasChips(entry)).toEqual(['ITM', 'in-the-money']);
  });

  it('the line split is what makes the scan bite (non-vacuity)', () => {
    const fused = '<div class="glossary-entry__aliases"><span class="mono">ITM</span><span class="mono">refi economics</span></div>';
    expect(fused.replace(/<[^>]+>/g, '')).not.toMatch(RETIRED);
    expect(lines(fused)).toMatch(RETIRED);
  });
});
