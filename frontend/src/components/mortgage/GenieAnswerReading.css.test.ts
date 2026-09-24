import { describe, expect, it } from 'vitest';
// @ts-expect-error Frontend app types intentionally exclude Node globals; this
// unit test reads the lazy stylesheet text under Vitest only.
import { readFileSync } from 'node:fs';
// @ts-expect-error see node:fs note above.
import { join } from 'node:path';
import { designCss } from '../../test/designCss';

declare const process: { cwd(): string };

const stylesheet = (): string =>
  readFileSync(join(process.cwd(), 'src', 'components', 'mortgage', 'GenieAnswerReading.css'), 'utf8');

/** Declarations only: comments cite prototype lines. */
const declarations = (): string => stylesheet().replace(/\/\*[\s\S]*?\*\//g, '');

/** The body of the first rule whose selector list is exactly `selector`. */
function rule(selector: string): string {
  const escaped = selector.replace(/[.*+?^${}()|[\]\\]/g, '\\$&');
  const match = new RegExp(`(?:^|\\})\\s*${escaped}\\s*\\{([^}]*)\\}`).exec(declarations());
  if (!match) throw new Error(`no rule for ${selector}`);
  return match[1];
}

describe('GenieAnswerReading.css (audit 2026-09-21 wave 3, lazy)', () => {
  it('stays out of the initial component cascade', () => {
    expect(designCss()).not.toContain('.genie-md-pre');
    expect(designCss()).not.toContain(':is(h3, h4).genie-md-p');
  });

  it('uses tokens only: no hex colours and no raw pixel lengths beyond hairlines', () => {
    const css = declarations();
    expect(css).not.toMatch(/#[0-9a-fA-F]{3,8}\b/);
    const pixels = css.match(/\b\d+(?:\.\d+)?px\b/g) ?? [];
    expect(pixels.every((px) => px === '1px')).toBe(true);
  });

  it('resets the UA bold so a real heading renders like the paragraph it replaced (genie-08)', () => {
    expect(rule(':is(h3, h4).genie-md-p')).toMatch(/font-weight:\s*inherit;/);
  });

  it('scrolls a narrative table sideways inside its own box (stack-02)', () => {
    const pre = rule('.genie-md-pre pre');
    expect(pre).toMatch(/overflow-x:\s*auto;/);
    expect(pre).toMatch(/white-space:\s*pre;/);
    expect(pre).toMatch(/max-inline-size:\s*100%;/);
  });
});
