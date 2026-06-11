import { describe, expect, it } from 'vitest';
// @ts-expect-error Frontend app types intentionally exclude Node globals; this
// unit test reads the design-system CSS text under Vitest only.
import { readFileSync } from 'node:fs';
// @ts-expect-error CSS lint helper is an ESM Node script used by lint/tests only.
import { findCssLiteralViolations } from '../../../tools/lint_css_literals.mjs';

const designCss = () => readFileSync(
  new URL('./components.css', import.meta.url),
  'utf8',
);
const tokensCss = () => readFileSync(
  new URL('./tokens.css', import.meta.url),
  'utf8',
);

describe('layout containment contracts', () => {
  it('keeps component CSS free of hard-coded color literals', () => {
    expect(findCssLiteralViolations()).toEqual([]);
  });

  it('keeps topbar borrower search visibly actionable', () => {
    const css = designCss();

    expect(css).toMatch(/\.topbar\s*\{[^}]*display:\s*grid;/s);
    expect(css).toMatch(
      /\.topbar\s*\{[^}]*grid-template-columns:\s*minmax\(0,\s*1fr\) minmax\(30rem,\s*44rem\) minmax\(0,\s*1fr\);/s,
    );
    expect(css).toMatch(/\.topbar__search\s*\{[^}]*grid-column:\s*2;/s);
    expect(css).toMatch(/\.topbar__search\s*\{[^}]*inline-size:\s*min\(44rem,\s*100%\);/s);
    expect(css).toContain('.topbar__actions');
    expect(css).toContain('.topbar__search-results');
    expect(css).toContain('.topbar__search-status');
    expect(css).toMatch(/\.topbar__search-results\s*\{[^}]*z-index:\s*60;/s);
  });

  it('prevents lead-table chips from compressing into neighboring cells', () => {
    const css = designCss();

    expect(css).toMatch(/\.tbl-wrap\s*\{[^}]*overflow:\s*auto;/s);
    expect(css).toMatch(/\.lead-table__table\s*\{[^}]*inline-size:\s*max-content;/s);
    expect(css).toMatch(/\.chip__label\s*\{[^}]*text-overflow:\s*ellipsis;/s);
    expect(css).toMatch(/\.lead-table__segments\s*\{[^}]*grid-template-columns:\s*minmax\(0,\s*1fr\);/s);
  });

  it('keeps small interactive chips at the WCAG 2.2 AA touch-target floor', () => {
    const css = designCss();

    expect(css).toMatch(/\.evidence-chip\s*\{[^}]*min-block-size:\s*calc\(var\(--sp-6\) \+ var\(--sp-1\)\);/s);
    expect(css).toMatch(/\.chip--compact\s*\{[^}]*min-block-size:\s*var\(--sp-6\);/s);
    expect(css).toMatch(/\.tbl__sort\s*\{[^}]*min-block-size:\s*var\(--sp-6\);/s);
  });

  it('lets evidence drawer signal rows wrap long source and value text', () => {
    const css = designCss();

    // fit-content(50%) caps the value column so a long value can never
    // squeeze the label/source column into mid-word wraps or visual
    // collision (operator report 2026-06-11, "Configured tenant lens" row).
    expect(css).toMatch(/\.lineage-node--signal\s*\{[^}]*grid-template-columns:\s*minmax\(0,\s*1fr\) fit-content\(50%\);/s);
    expect(css).toMatch(/\.lineage-node__name\s*\{[^}]*overflow-wrap:\s*anywhere;/s);
    expect(css).toMatch(/\.lineage-node__value\s*\{[^}]*overflow-wrap:\s*anywhere;/s);
  });

  it('keeps data-estate lanes from starving asset labels when the console rail opens', () => {
    const css = designCss();

    // 2026-06-11 audit P2-1 (live repro: 65px label column, 5-6 line
    // mid-word wraps with the Console rail open). Lanes wrap to fewer
    // columns instead of compressing below a readable floor, and the
    // asset-name column keeps its own minimum.
    expect(css).toMatch(/\.data-estate__grid\s*\{[^}]*grid-template-columns:\s*repeat\(auto-fit,\s*minmax\(13\.5rem,\s*1fr\)\);/s);
    expect(css).toMatch(/\.data-estate__asset\s*\{[^}]*grid-template-columns:\s*minmax\(7\.5rem,\s*1fr\) auto;/s);
  });

  it('makes proof affordance rows visibly interactive without layout shifts', () => {
    const css = designCss();

    expect(css).toContain('.data-estate__lane-proof');
    expect(css).toMatch(/\.data-estate__asset\s*\{[^}]*display:\s*grid;/s);
    expect(css).toMatch(/\.data-estate__asset\s*\{[^}]*cursor:\s*pointer;/s);
    expect(css).toContain('.trusted-asset--button');
    expect(css).toMatch(/\.trusted-asset--button\s*\{[^}]*width:\s*100%;/s);
    expect(css).toContain('.trusted-asset--button.is-active');
  });

  it('shows Ask Genie sample chips inside the composer grid', () => {
    const css = designCss();

    expect(css).toContain('.genie-composer__samples');
    expect(css).toMatch(/\.genie-composer__samples\s*\{[^}]*display:\s*grid;/s);
    expect(css).toMatch(/\.genie-composer__samples\s*\{[^}]*grid-template-columns:\s*repeat\(2,\s*minmax\(0,\s*1fr\)\);/s);
  });

  it('renders skeleton placeholders for slow lead and data-estate loads', () => {
    const css = designCss();

    expect(css).toContain('.lead-queue-skeleton__row');
    expect(css).toMatch(/\.lead-queue-skeleton__row\s*\{[^}]*grid-template-columns:/s);
    expect(css).toContain('.data-estate__lane-skeleton-main');
    expect(css).toContain('.data-estate__asset--skeleton');
  });

  it('keeps long error messages wrapped inside callouts', () => {
    const css = designCss();

    expect(css).toMatch(/\.status-callout--danger\s*\{[^}]*flex-wrap:\s*wrap;/s);
    expect(css).toMatch(/\.status-callout--danger > span\s*\{[^}]*flex:\s*1 1 24rem;/s);
  });

  it('keeps the floating Genie entrypoint out of desktop table/map content', () => {
    const css = designCss();

    expect(css).toMatch(/\.genie__fab\s*\{[^}]*display:\s*none;/s);
    expect(css).toMatch(/@media \(max-width:\s*720px\)\s*\{[\s\S]*?\.genie__fab\s*\{[\s\S]*?display:\s*grid;/s);
  });

  it('keeps theme switches visually coherent across shell surfaces', () => {
    const css = designCss();

    expect(css).toMatch(/\.topbar,[\s\S]*?\.topbar__icon-btn\s*\{[^}]*transition:/s);
    expect(css).toMatch(/@media \(prefers-reduced-motion:\s*reduce\)\s*\{[\s\S]*?\.topbar,[\s\S]*?transition:\s*none;/s);
  });

  it('uses readable accent ink for light-theme active and hover text', () => {
    const css = designCss();
    const tokens = tokensCss();

    expect(tokens).toContain('[data-theme="light"][data-accent="bright"] { --accent-ink: #014E80; }');
    expect(tokens).toContain('[data-theme="light"][data-accent="teal"]   { --accent-ink: #045D62; }');
    expect(tokens).toContain('[data-theme="light"][data-accent="red"]    { --accent-ink: #B42318; }');
    expect(css).toMatch(/\.rail__item\.is-active\s*\{[^}]*color:\s*var\(--accent-ink\);/s);
    expect(css).toMatch(/\.topbar__icon-btn\.is-active\s*\{[^}]*color:\s*var\(--accent-ink\);/s);
    expect(css).toMatch(/\.filter\.is-active\s*\{[^}]*color:\s*var\(--accent-ink\);/s);
    expect(css).toMatch(/\.proof-tab\.is-active,[^{]+\{[^}]*color:\s*var\(--accent-ink\);/s);
    expect(css).toMatch(/\.filter-menu__item\.is-selected\s*\{[^}]*color:\s*var\(--accent-ink\);/s);
    expect(css).toMatch(/\.text-accent\s*\{[^}]*color:\s*var\(--accent-ink\);/s);
    expect(css).toMatch(/\.icon-accent\s*\{[^}]*color:\s*var\(--accent-ink\);/s);
  });

  it('lets segment cards wrap content instead of clipping labels or pending copy', () => {
    const css = designCss();

    expect(css).toMatch(/\.seg-card\s*\{[^}]*min-block-size:\s*184px;/s);
    expect(css).toMatch(/\.seg-card__hdr\s*\{[^}]*min-inline-size:\s*0;/s);
    expect(css).toMatch(/\.seg-card__title\s*\{[^}]*overflow-wrap:\s*anywhere;/s);
    expect(css).toMatch(/\.seg-card__count\s*\{[^}]*overflow-wrap:\s*anywhere;/s);
    expect(css).toMatch(/\.seg-card__sub\s*\{[^}]*overflow-wrap:\s*anywhere;/s);
    expect(css).toMatch(/\.seg-card__meta\s*\{[^}]*flex-wrap:\s*wrap;/s);
    expect(css).toMatch(/\.seg-card__meta-item\s*\{[^}]*overflow-wrap:\s*anywhere;/s);
  });
});
