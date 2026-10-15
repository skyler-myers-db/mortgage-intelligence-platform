// @ts-expect-error Frontend app types intentionally exclude Node globals; this
// unit test reads the route stylesheet as text under Vitest only.
import { readFileSync } from 'node:fs';
// @ts-expect-error see node:fs note above.
import { join } from 'node:path';
import { renderToStaticMarkup } from 'react-dom/server';
import { MemoryRouter } from 'react-router';
import { describe, expect, it } from 'vitest';
import { glossaryEntries } from '../lib/mortgageGlossary';
import { designCss } from '../test/designCss';
import GlossaryRoute from './glossary';

declare const process: { cwd(): string };

/**
 * Glossary deep links (audit 2026-09-21 `shell-03`): every <GlossaryTerm> in
 * the app links to `/glossary#<id>`, and on client navigation that landed at
 * the top of the page with nothing marked. Scrolling is the shell's job
 * (useMainScroll.test.tsx); this pins what the route must provide for it.
 */

function render(entry: string): string {
  return renderToStaticMarkup(
    <MemoryRouter initialEntries={[entry]}>
      <GlossaryRoute />
    </MemoryRouter>,
  );
}

/** Each rendered category section's header count line, by section id. */
function sectionCounts(html: string): Record<string, string> {
  const pattern = /<section[^>]*\sid="([^"]+)"[^>]*>.*?<\/h2><div class="muted fs-12">([^<]*)<\/div>/g;
  return Object.fromEntries([...html.matchAll(pattern)].map(([, id, count]) => [id, count]));
}

function openingTag(html: string, id: string): string {
  const match = html.match(new RegExp(`<(?:article|section)[^>]*\\sid="${id}"[^>]*>`));
  return match ? match[0] : '';
}

describe('GlossaryRoute hash targets', () => {
  it('marks the entry the hash addresses, and only that entry', () => {
    const html = render('/glossary#clip');

    expect(openingTag(html, 'clip')).toContain('class="glossary-entry is-target"');
    expect(openingTag(html, 'avm')).toContain('class="glossary-entry"');
    expect(html.match(/is-target/g)).toHaveLength(1);
  });

  it('marks a category section when a category chip is the target', () => {
    const html = render('/glossary#scoring');

    expect(openingTag(html, 'scoring')).toContain('glossary-section is-target');
    expect(html.match(/is-target/g)).toHaveLength(1);
  });

  it('marks nothing without a hash, or for an unknown or malformed one', () => {
    expect(render('/glossary')).not.toContain('is-target');
    expect(render('/glossary#no-such-term')).not.toContain('is-target');
    expect(render('/glossary#%E0%A4%A')).not.toContain('is-target');
  });

  it('makes entries and sections programmatically focusable for the route announcer', () => {
    const html = render('/glossary');

    expect(openingTag(html, 'clip')).toContain('tabindex="-1"');
    expect(openingTag(html, 'property')).toContain('tabindex="-1"');
  });

  it('category chips are router links, so each jump gets its own history entry', () => {
    const html = render('/glossary');

    expect(html).toContain('href="/glossary#property"');
    expect(html).toContain('href="/glossary#principles"');
  });

  it('styles the target from tokens and clears the sticky route nav', () => {
    const css: string = readFileSync(join(process.cwd(), 'src', 'routes', 'glossary.css'), 'utf8');

    // The docked route nav's measured clearance (0 while it is in flow; report 12.4 #5).
    expect(css).toMatch(/\.glossary-section,\s*\.glossary-entry\s*\{\s*scroll-margin-top:\s*var\(--nav-clear\);/);
    expect(css).toMatch(
      /\.glossary-entry:target,\s*\.glossary-entry\.is-target\s*\{\s*background:\s*var\(--accent-soft\);/,
    );
    expect(css).toMatch(/\.glossary-section:target,\s*\.glossary-section\.is-target\s*\{/);
  });

  it('ships that styling with the lazy route, not the initial stylesheet', () => {
    expect(designCss()).not.toContain('is-target');
  });
});

/**
 * Glossary search (W5d, critic-07): ?q= filters the entries by term, alias or
 * short text; categories with no match leave the index and the sections; a
 * polite status line counts the matches; the chips keep ?q=. Typing and Clear
 * search are driven in print-glossary-sales-manager.fixture.spec.ts.
 */
describe('GlossaryRoute search', () => {
  it('labels a search field with the query from ?q=', () => {
    const html = render('/glossary?q=automated%20valuation');
    expect(html).toMatch(/<label class="glossary-search"><span class="field__label">Search the glossary<\/span><input/);
    expect(html).toMatch(/<input[^>]*type="search"[^>]*value="automated valuation"/);
  });

  it('keeps only matching entries and categories, and counts them politely', () => {
    const html = render('/glossary?q=automated%20valuation');

    expect(openingTag(html, 'avm')).not.toBe('');
    expect(openingTag(html, 'clip')).toBe('');
    expect(openingTag(html, 'property')).not.toBe('');
    expect(openingTag(html, 'governance')).toBe('');
    expect(html).not.toContain('href="/glossary?q=automated%20valuation#governance"');
    expect(html).toContain('href="/glossary?q=automated%20valuation#property"');
    expect(html).toMatch(/role="status"[^>]*>1 term matches</);
  });

  it('says nothing matched, offers Clear search and shows no section', () => {
    const html = render('/glossary?q=zzqx');

    expect(html).toContain('No terms match &quot;zzqx&quot;');
    expect(html).toContain('>Clear search</button>');
    expect(html).not.toContain('glossary-section');
  });

  it('counts each section in the singular when the search leaves it one term', () => {
    // 'automated valuation' leaves Property and ownership holding AVM alone;
    // before search no category was that small, so '1 terms' never showed.
    const filtered = sectionCounts(render('/glossary?q=automated%20valuation'));
    expect(filtered).toEqual({ property: '1 term' });

    const full = sectionCounts(render('/glossary'));
    const sizes = glossaryEntries.reduce<Record<string, number>>((acc, entry) => {
      acc[entry.category] = (acc[entry.category] ?? 0) + 1;
      return acc;
    }, {});
    expect(full).toEqual(Object.fromEntries(Object.entries(sizes).map(([category, n]) => [category, `${n} terms`])));
  });

  it('lists every category without a query, with an empty status line', () => {
    const html = render('/glossary');
    expect(html).toContain('href="/glossary#principles"');
    expect(html).toMatch(/class="glossary-search__status muted fs-12 sr-only" role="status" aria-live="polite"><\/p>/);
    expect(html).not.toContain('Clear search');
  });
});
