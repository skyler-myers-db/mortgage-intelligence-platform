import { describe, expect, it } from 'vitest';
import { designCss } from '../test/designCss';
import { featureStylesheets } from '../test/featureCss';

/**
 * The shell-navigation lane's CSS pins (W5c w5-shell-nav-followups; never
 * components.test.ts, which is at its size budget): the route nav's two
 * clusters (flow-07, shell-09) and the Ask tab's nav clearance on the
 * measured dock (report 12.4 #5). Rendered proofs: shell-wayfinding and
 * shell-nav-followups fixture specs.
 */

const stripComments = (text: string): string => text.replace(/\/\*[\s\S]*?\*\//g, '');
const shell = (): string => stripComments(designCss());

/** The body of the first top-level rule whose selector is exactly `selector`. */
function rule(css: string, selector: string): string {
  const escaped = selector.replace(/[.*+?^${}()|[\]\\]/g, '\\$&');
  const match = new RegExp(`(?:^|[}\\n])\\s*${escaped}\\s*\\{([^}]*)\\}`).exec(css);
  expect(match, `${selector} is declared`).not.toBeNull();
  return match![1];
}

describe('route nav clusters (flow-07, shell-09)', () => {
  it('lays each cluster out as a wrapping flex row with the links\' gap and no list chrome', () => {
    const group = rule(shell(), '.route-nav__group');
    expect(group).toMatch(/display:\s*flex;/);
    expect(group).toMatch(/flex-wrap:\s*wrap;/);
    expect(group).toMatch(/align-items:\s*center;/);
    expect(group).toMatch(/gap:\s*var\(--sp-2\);/);
    expect(group).toMatch(/margin:\s*0;/);
    expect(group).toMatch(/padding:\s*0;/);
  });

  it('makes each item a flex box (no line-box strut) and puts the tools cluster flush right', () => {
    expect(rule(shell(), '.route-nav__item')).toMatch(/^\s*display:\s*flex;\s*$/);
    expect(rule(shell(), '.route-nav__group--end')).toMatch(/^\s*margin-inline-start:\s*auto;\s*$/);
  });

  it('keeps the link rule as it was: the clusters wrap the links, they do not restyle them', () => {
    expect(shell()).not.toMatch(/\.route-nav__(?:group|item)[^{]*\.route-nav__link/);
  });
});

describe('the presenter-mode roadmap rail slots (critic-05, D-shell-deviations-e2)', () => {
  it('gives .rail__item no padding, so a <button> slot renders as the link items do', () => {
    expect(rule(shell(), '.rail__item')).toMatch(/padding:\s*0;/);
    // Disabled slots keep their muted look and no pressed scale.
    expect(rule(shell(), '.rail__item--disabled')).toMatch(/cursor:\s*default;/);
    expect(shell()).toMatch(/\.rail__item:active:not\(\.rail__item--disabled\)\s*\{[^}]*scale:/);
  });
});

describe('the Ask tab clears the route nav only while it is docked (report 12.4 #5)', () => {
  const askGenie = (): string => {
    const sheet = featureStylesheets().find((entry) => entry.file === 'src/routes/ask-genie.css');
    expect(sheet, 'ask-genie.css is a feature stylesheet').toBeDefined();
    return stripComments(sheet!.css);
  };
  const ASK_TAB = String.raw`section\[role="tabpanel"\]:not\(\[hidden\]\) \.genie-composer`;
  const RING = String.raw`var\(--focus-ring-width\)\s*\+\s*var\(--focus-ring-offset\)`;

  it('pads the start by the ring alone, and by the measured nav block plus the ring while docked', () => {
    const always = new RegExp(String.raw`\.main:has\(${ASK_TAB}\)\s*\{([^}]*)\}`).exec(askGenie());
    expect(always).not.toBeNull();
    expect(always![1]).toMatch(new RegExp(String.raw`scroll-padding-block-start:\s*calc\(\s*${RING}\s*\);`));
    expect(always![1]).not.toMatch(/route-nav/);
    const docked = new RegExp(String.raw`\.main:has\(> \.route-nav\[data-docked\]\):has\(${ASK_TAB}\)\s*\{([^}]*)\}`).exec(askGenie());
    expect(docked, 'the docked rule').not.toBeNull();
    expect(docked![1]).toMatch(new RegExp(String.raw`^\s*scroll-padding-block-start:\s*calc\(\s*var\(--route-nav-block\)\s*\+\s*${RING}\s*\);\s*$`));
    // The route's own nav measurement is gone (the shell measures the nav).
    expect(askGenie()).not.toMatch(/--genie-route-nav-block-size/);
  });

  it('clears the Growth Agent run slot and the glossary deep links by --nav-clear, not a fixed size', () => {
    expect(askGenie()).toMatch(/\.growth-agent-run\s*\{\s*scroll-margin-block:\s*var\(--nav-clear\)\s+var\(--sp-4\);\s*\}/);
    const glossary = featureStylesheets().find((entry) => entry.file === 'src/routes/glossary.css');
    expect(stripComments(glossary?.css ?? '')).toMatch(
      /\.glossary-section,\s*\.glossary-entry\s*\{\s*scroll-margin-top:\s*var\(--nav-clear\);\s*\}/,
    );
  });
});

describe('the Administration section nav\'s overflow cue (deviation:admin-section-nav-overflow-cue)', () => {
  const sheet = (): string => {
    const found = featureStylesheets().find((entry) => entry.file === 'src/components/admin/AdminSectionNav.css');
    expect(found, 'AdminSectionNav.css is a feature stylesheet').toBeDefined();
    return stripComments(found!.css);
  };

  it('keeps the scrollbar hidden and paints two scrolling covers over two fixed edge shadows on --bg-1', () => {
    const nav = rule(sheet(), '.admin-section-nav');
    expect(nav).toMatch(/scrollbar-width:\s*none;/);
    const background = /background:([^;]*);/.exec(nav)?.[1] ?? '';
    // Top-level commas only (a comma inside gradient() or color-mix() stays in its layer).
    const layers: string[] = [];
    let depth = 0;
    let start = 0;
    [...background].forEach((char, index) => {
      if (char === '(') depth += 1;
      else if (char === ')') depth -= 1;
      else if (char === ',' && depth === 0) {
        layers.push(background.slice(start, index).trim());
        start = index + 1;
      }
    });
    layers.push(background.slice(start).trim());
    expect(layers).toHaveLength(5);
    expect(layers.slice(0, 2).every((layer) => /var\(--bg-1\)/.test(layer) && /\blocal$/.test(layer))).toBe(true);
    expect(layers.slice(2, 4).every((layer) => /color-mix\(in oklab, var\(--text-1\)/.test(layer) && /\bscroll$/.test(layer))).toBe(true);
    expect(layers[4]).toBe('var(--bg-1)');
  });

  it('drops the images in forced colours', () => {
    expect(sheet()).toMatch(/@media \(forced-colors: active\)\s*\{\s*\.admin-section-nav\s*\{\s*background-image:\s*none;\s*\}\s*\}/);
  });
});
