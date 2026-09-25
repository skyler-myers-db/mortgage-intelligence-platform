/**
 * Source gates over the Playwright tree, frontend/tests/e2e/**\/*.ts
 * (the wave-4a overlays hand-off):
 *
 *  2. Retired overlay selectors. The wave-4a overlays lane replaced the
 *     evidence drawer's scrim element and the offer preview's scrim with a
 *     native <dialog> and its ::backdrop. For each RETIRED_OVERLAY_CLASSES
 *     class that production source no longer renders, no e2e file may still
 *     select it: a live spec clicking a class that cannot exist fails only
 *     on the nightly operator run, long after the change.
 *
 * Node environment: reads files only, never renders.
 */
// @ts-expect-error Frontend app types intentionally exclude Node globals; this
// unit test reads the Playwright sources under Vitest only.
import { readFileSync, readdirSync } from 'node:fs';
import { describe, expect, it } from 'vitest';

const FRONTEND = decodeURIComponent(new URL('../../', import.meta.url).pathname);
const E2E_ROOT = `${FRONTEND}tests/e2e/`;
const SRC_ROOT = `${FRONTEND}src/`;

interface SourceFile {
  /** Path relative to its root, with forward slashes. */
  path: string;
  text: string;
}

function readTree(root: string, keep: (path: string) => boolean): SourceFile[] {
  return (readdirSync(root, { recursive: true }) as string[])
    .map((entry) => entry.split('\\').join('/'))
    .filter((entry) => !entry.split('/').some((segment) => segment.endsWith('-snapshots')))
    .filter(keep)
    .sort()
    .map((path) => ({ path, text: readFileSync(`${root}${path}`, 'utf8') as string }));
}

function e2eSources(): SourceFile[] {
  return readTree(E2E_ROOT, (path) => path.endsWith('.ts'));
}

function lineOf(text: string, index: number): number {
  return text.slice(0, index).split('\n').length;
}

// ---------------------------------------------------------------------------
// 2. Retired overlay selectors
// ---------------------------------------------------------------------------

/** Classes the wave-4a native-dialog move deleted from the markup. */
const RETIRED_OVERLAY_CLASSES = ['drawer-scrim', 'offer-mock-scrim'] as const;

function classToken(name: string): RegExp {
  const escaped = name.replace(/[.*+?^${}()|[\]\\]/g, '\\$&');
  // Not part of a longer class or custom property (`--z-drawer-scrim`).
  return new RegExp(`(?<![\\w-])${escaped}(?![\\w-])`);
}

function stripCssComments(css: string): string {
  return css.replace(/\/\*[\s\S]*?\*\//g, '');
}

/** Production source: components, routes, lib and styles, not tests, mocks or test helpers. */
function productionSources(): SourceFile[] {
  return readTree(SRC_ROOT, (path) =>
    /\.(tsx?|css)$/.test(path) &&
    !/\.test\.tsx?$/.test(path) &&
    !/\.test-support\.tsx?$/.test(path) &&
    !path.startsWith('test/') &&
    !path.startsWith('mocks/'),
  );
}

/** Whether production source still renders or styles the class. */
function classExistsIn(sources: readonly SourceFile[], name: string): boolean {
  const token = classToken(name);
  return sources.some(({ path, text }) => {
    if (!path.endsWith('.css')) return token.test(text);
    return new RegExp(`\\.${token.source}`).test(stripCssComments(text));
  });
}

/** e2e references to classes production source no longer has, as file:line. */
function retiredSelectorReferences(
  classes: readonly string[],
  sources: readonly SourceFile[],
  e2e: readonly SourceFile[],
): string[] {
  const offenders: string[] = [];
  for (const name of classes) {
    if (classExistsIn(sources, name)) continue;
    const token = new RegExp(classToken(name).source, 'g');
    for (const { path, text } of e2e) {
      for (const match of text.matchAll(token)) offenders.push(`${path}:${lineOf(text, match.index ?? 0)} (.${name} is retired)`);
    }
  }
  return offenders;
}

describe('retired overlay selectors (wave-4a native dialogs)', () => {
  const sources = productionSources();
  const e2e = e2eSources();

  it('reads production source and the whole e2e tree (non-vacuity)', () => {
    expect(sources.length).toBeGreaterThan(100);
    expect(e2e.some((file) => file.path.startsWith('fixture/'))).toBe(true);
    expect(e2e.some((file) => file.path === 'live_hardening_regressions.spec.ts')).toBe(true);
  });

  it('the retired classes are really gone from production source, so the check is live', () => {
    for (const name of RETIRED_OVERLAY_CLASSES) expect(classExistsIn(sources, name), name).toBe(false);
  });

  it('a class production still renders is never flagged (control: .drawer)', () => {
    expect(classExistsIn(sources, 'drawer')).toBe(true);
    expect(e2e.some((file) => classToken('drawer').test(file.text)), 'e2e selects .drawer').toBe(true);
    expect(retiredSelectorReferences(['drawer'], sources, e2e)).toEqual([]);
  });

  it('flags a planted reference to a retired class, and not a longer name or a token', () => {
    const planted: SourceFile[] = [
      { path: 'x.spec.ts', text: "await page.locator('.drawer-scrim.is-open').click();" },
      { path: 'y.spec.ts', text: "const z = 'var(--z-drawer-scrim)'; page.locator('.drawer-scrim-legacy');" },
    ];
    expect(retiredSelectorReferences(['drawer-scrim'], sources, planted)).toEqual(['x.spec.ts:1 (.drawer-scrim is retired)']);
  });

  it('no e2e file selects a retired overlay class', () => {
    expect(retiredSelectorReferences(RETIRED_OVERLAY_CLASSES, sources, e2e)).toEqual([]);
  });
});
