// @ts-expect-error Frontend app types intentionally exclude Node globals; this
// unit test reads the route stylesheet as text under Vitest only.
import { readFileSync } from 'node:fs';
// @ts-expect-error see node:fs note above.
import { join } from 'node:path';
import { renderToStaticMarkup } from 'react-dom/server';
import { MemoryRouter } from 'react-router';
import { describe, expect, it } from 'vitest';
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

    expect(css).toMatch(/\.glossary-section,\s*\.glossary-entry\s*\{\s*scroll-margin-top:\s*var\(--sp-16\);/);
    expect(css).toMatch(
      /\.glossary-entry:target,\s*\.glossary-entry\.is-target\s*\{\s*background:\s*var\(--accent-soft\);/,
    );
    expect(css).toMatch(/\.glossary-section:target,\s*\.glossary-section\.is-target\s*\{/);
  });

  it('ships that styling with the lazy route, not the initial stylesheet', () => {
    expect(designCss()).not.toContain('is-target');
  });
});
