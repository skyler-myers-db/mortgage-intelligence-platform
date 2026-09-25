import { describe, expect, it } from 'vitest';
// @ts-expect-error Frontend app types intentionally exclude Node globals; this
// unit test reads the route stylesheet text under Vitest only.
import { readFileSync } from 'node:fs';
// @ts-expect-error see node:fs note above.
import { join } from 'node:path';

declare const process: { cwd(): string };

/** Declarations only: comments cite prototype line numbers. */
const css = (): string =>
  readFileSync(join(process.cwd(), 'src', 'routes', 'lead-queue.css'), 'utf8').replace(/\/\*[\s\S]*?\*\//g, '');

function block(selectorGroup: RegExp): string {
  const match = css().match(new RegExp(`${selectorGroup.source}\\s*\\{([^}]*)\\}`));
  return match ? match[1] : '';
}

describe('lead-queue.css queue presets (audit tables-09)', () => {
  // The preset pills are <Link className="filter"> anchors; tokens.css only
  // sets a { color: inherit }, and the one `.filter` underline reset is
  // scoped to `.route-nav` (and leaves with motion-nav). Without this rule
  // the pills render underlined: the visual-05 defect, reintroduced.
  it('resets the preset anchors\' underline at rest, on hover and on keyboard focus', () => {
    const reset = block(
      /\.lead-queue-views \.filter,\s*\.lead-queue-views \.filter:hover,\s*\.lead-queue-views \.filter:focus-visible/,
    );
    expect(reset).toMatch(/text-decoration:\s*none;/);
  });

  it('stacks the presets over the hero chips, right-aligned, with tokens only', () => {
    const stack = block(/\.lead-queue-hero/);
    expect(stack).toMatch(/flex-direction:\s*column;/);
    expect(stack).toMatch(/align-items:\s*flex-end;/);
    const row = block(/\.lead-queue-views/);
    expect(row).toMatch(/justify-content:\s*flex-end;/);
    expect(row).toMatch(/gap:\s*var\(--sp-2\);/);
    for (const rule of [stack, row, block(/\.lead-queue-views__copy/)]) {
      expect(rule).not.toMatch(/\d+px|#[0-9a-f]{3,8}\b/i);
    }
  });
});
