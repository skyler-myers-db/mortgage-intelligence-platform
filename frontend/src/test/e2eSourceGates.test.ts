/**
 * Source gates over the Playwright tree, frontend/tests/e2e/**\/*.ts
 * (2026-09-21 audit a11y-05 item 1, and the wave-4a overlays hand-off):
 *
 *  1. ONE axe entry point. Every scan goes through expectAxeClean
 *     (tests/e2e/fixture/axe.ts): one tag set (WCAG A/AA gating at every
 *     impact, best-practice advisory), one KNOWN_VIOLATIONS ratchet with a
 *     stale check. A private `new AxeBuilder(...)`, an '@axe-core/playwright'
 *     import, or the axe ENGINE reached directly (a runtime import, require,
 *     import() or require.resolve of 'axe-core' or an 'axe-core/...' subpath;
 *     an addScriptTag naming axe; `axe.run(`, `axe.source` or `window.axe`)
 *     anywhere else fails here with file:line, except (a type-only
 *     `import type ... from 'axe-core'` is not an engine use):
 *       - PERMANENT_RAW_AXE: safety-net's raw-engine probe, which must SEE
 *         violations on a planted page to prove the ratchet's pure functions;
 *       - PENDING_AXE_MIGRATION: shrink-only; files a same-batch lane owns and
 *         converts. Each entry must still show exactly its count of scans, or
 *         it fails as stale and leaves the list in the change that fixed it.
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
// 1. One axe entry point
// ---------------------------------------------------------------------------

/** The shared helper: the only file that may build a scan. */
const AXE_ENTRY = 'fixture/axe.ts';

interface RawAxeExemption {
  /** Exact number of `new AxeBuilder(` scans the file holds. */
  count: number;
  reason: string;
}

interface PendingAxeMigration extends RawAxeExemption {
  /** The lane (or wave) that converts the file onto expectAxeClean. */
  owner: string;
}

const PERMANENT_RAW_AXE: Readonly<Record<string, RawAxeExemption>> = {
  'fixture/safety-net.fixture.spec.ts': {
    count: 1,
    reason:
      "a raw-engine probe of the ratchet's pure functions (partitionByTags, evaluateAxeRatchet) on a planted page, which must see violations; routing it through expectAxeClean would fail the probe itself",
  },
};

/**
 * SHRINK-ONLY. Seeded only with files a same-batch (W4b) lane owns; the
 * integrator retires each entry when its owner's merge converts it (the entry
 * then fails as stale). Never add a file: migrate its scan.
 */
const PENDING_AXE_MIGRATION: Readonly<Record<string, PendingAxeMigration>> = {};

const AXE_SCAN = /\bnew\s+AxeBuilder\s*\(/g;
const AXE_IMPORT = /['"]@axe-core\/playwright['"]/g;
/** 'axe-core' or an 'axe-core/...' subpath as a module specifier (the quote rules out '@axe-core/...'). */
const AXE_ENGINE_MODULE = /['"]axe-core(?:\/[^'"]*)?['"]/g;
/** A type-only import or re-export of the engine: erased at runtime, so not an engine use. */
const AXE_ENGINE_TYPE_ONLY = /\b(?:import|export)\s+type\b[^;]*?\bfrom\s*['"]axe-core(?:\/[^'"]*)?['"]/g;
const AXE_ENGINE_SCRIPT_TAG = /\baddScriptTag\s*\([^)]*?\baxe/gi;
const AXE_ENGINE_GLOBAL = /\baxe\.(?:run|source)\b|\bwindow\.axe\b/g;

interface AxeSite {
  file: string;
  line: number;
  kind: 'scan' | 'import' | 'engine';
}

/** Lines that reach the axe engine directly, one site per line. */
function axeEngineLines(text: string): number[] {
  const typeOnly = new Set<number>();
  for (const match of text.matchAll(AXE_ENGINE_TYPE_ONLY)) typeOnly.add((match.index ?? 0) + match[0].length);
  const lines = new Set<number>();
  for (const match of text.matchAll(AXE_ENGINE_MODULE)) {
    if (!typeOnly.has((match.index ?? 0) + match[0].length)) lines.add(lineOf(text, match.index ?? 0));
  }
  for (const pattern of [AXE_ENGINE_SCRIPT_TAG, AXE_ENGINE_GLOBAL]) {
    for (const match of text.matchAll(pattern)) lines.add(lineOf(text, match.index ?? 0));
  }
  return [...lines];
}

function axeSites(files: readonly SourceFile[]): AxeSite[] {
  const sites: AxeSite[] = [];
  for (const { path, text } of files) {
    const found: AxeSite[] = [];
    for (const match of text.matchAll(AXE_SCAN)) found.push({ file: path, line: lineOf(text, match.index ?? 0), kind: 'scan' });
    for (const match of text.matchAll(AXE_IMPORT)) found.push({ file: path, line: lineOf(text, match.index ?? 0), kind: 'import' });
    for (const line of axeEngineLines(text)) found.push({ file: path, line, kind: 'engine' });
    sites.push(...found.sort((a, b) => a.line - b.line));
  }
  return sites;
}

const AXE_SITE_LABEL: Readonly<Record<AxeSite['kind'], string>> = {
  scan: 'new AxeBuilder',
  import: "'@axe-core/playwright' import",
  engine: 'axe-core engine',
};

/** Private scans outside the allowed files, as file:line (kind). */
function privateAxeOffenders(sites: readonly AxeSite[]): string[] {
  return sites
    .filter((site) => site.file !== AXE_ENTRY && !(site.file in PERMANENT_RAW_AXE) && !(site.file in PENDING_AXE_MIGRATION))
    .map((site) => `${site.file}:${site.line} (${AXE_SITE_LABEL[site.kind]}: use expectAxeClean from fixture/axe.ts)`);
}

/** Listed files whose scan count no longer matches their entry. */
function staleAxeEntries(
  sites: readonly AxeSite[],
  listed: Readonly<Record<string, RawAxeExemption>>,
): string[] {
  return Object.entries(listed)
    .map(([file, entry]) => ({ file, entry, scans: sites.filter((site) => site.file === file && site.kind === 'scan').length }))
    .filter(({ entry, scans }) => scans !== entry.count)
    .map(({ file, entry, scans }) => `${file}: listed with ${entry.count} scan(s), found ${scans}`);
}

describe('one axe entry point (a11y-05 item 1)', () => {
  const sites = axeSites(e2eSources());

  it('the scanner sees the shared helper and the permanent probe (non-vacuity)', () => {
    expect(sites.some((site) => site.file === AXE_ENTRY && site.kind === 'scan')).toBe(true);
    expect(sites.some((site) => site.file === AXE_ENTRY && site.kind === 'import')).toBe(true);
    expect(sites.some((site) => site.file === 'fixture/safety-net.fixture.spec.ts' && site.kind === 'scan')).toBe(true);
  });

  it('recognises every spelling of a private scan, and nothing else', () => {
    const planted: SourceFile[] = [
      { path: 'fixture/a.fixture.spec.ts', text: "import AxeBuilder from '@axe-core/playwright';\nconst r = await new AxeBuilder({ page }).analyze();" },
      { path: 'fixture/b.fixture.spec.ts', text: 'const { default: A } = await import("@axe-core/playwright");' },
      { path: 'fixture/c.fixture.spec.ts', text: 'const r = await new  AxeBuilder ({ page });' },
      { path: 'fixture/d.fixture.spec.ts', text: "import { expectAxeClean } from './axe';\n// Imports only @axe-core/playwright via axe.ts\nnew AxeBuilderish();" },
    ];
    expect(privateAxeOffenders(axeSites(planted))).toEqual([
      "fixture/a.fixture.spec.ts:1 ('@axe-core/playwright' import: use expectAxeClean from fixture/axe.ts)",
      'fixture/a.fixture.spec.ts:2 (new AxeBuilder: use expectAxeClean from fixture/axe.ts)',
      "fixture/b.fixture.spec.ts:1 ('@axe-core/playwright' import: use expectAxeClean from fixture/axe.ts)",
      'fixture/c.fixture.spec.ts:1 (new AxeBuilder: use expectAxeClean from fixture/axe.ts)',
    ]);
  });

  it('recognises direct axe-core engine injection, and not a type-only import', () => {
    const planted: SourceFile[] = [
      { path: 'fixture/e.fixture.spec.ts', text: "await page.addScriptTag({ path: require.resolve('axe-core') });" },
      { path: 'fixture/f.fixture.spec.ts', text: 'const r = await page.evaluate(() => window.axe.run());' },
      { path: 'fixture/g.fixture.spec.ts', text: "import src from 'axe-core/axe.min.js?raw';\nawait page.addScriptTag({ content: src });" },
      { path: 'fixture/h.fixture.spec.ts', text: "const engine = await import('axe-core');\nawait page.evaluate(engine.source);" },
      { path: 'fixture/i.fixture.spec.ts', text: "import type { AxeResults } from 'axe-core';\nexport type { Result } from 'axe-core';" },
      { path: 'fixture/j.fixture.spec.ts', text: "import AxeBuilder from '@axe-core/playwright';" },
      { path: 'fixture/k.fixture.spec.ts', text: "const { run } = require('axe-core');\n// axe-core is the engine" },
    ];
    expect(privateAxeOffenders(axeSites(planted))).toEqual([
      'fixture/e.fixture.spec.ts:1 (axe-core engine: use expectAxeClean from fixture/axe.ts)',
      'fixture/f.fixture.spec.ts:1 (axe-core engine: use expectAxeClean from fixture/axe.ts)',
      'fixture/g.fixture.spec.ts:1 (axe-core engine: use expectAxeClean from fixture/axe.ts)',
      'fixture/h.fixture.spec.ts:1 (axe-core engine: use expectAxeClean from fixture/axe.ts)',
      "fixture/j.fixture.spec.ts:1 ('@axe-core/playwright' import: use expectAxeClean from fixture/axe.ts)",
      'fixture/k.fixture.spec.ts:1 (axe-core engine: use expectAxeClean from fixture/axe.ts)',
    ]);
    // The engine kind never counts toward an exemption's scan count.
    expect(staleAxeEntries(axeSites(planted.slice(0, 1)), { 'fixture/e.fixture.spec.ts': { count: 0, reason: 'r' } })).toEqual([]);
  });

  it('no e2e file builds its own axe scan: use expectAxeClean', () => {
    expect(privateAxeOffenders(sites)).toEqual([]);
  });

  it('the permanent exemption holds exactly its probe', () => {
    expect(staleAxeEntries(sites, PERMANENT_RAW_AXE)).toEqual([]);
  });

  it('PENDING_AXE_MIGRATION is shrink-only: every entry still holds exactly its count', () => {
    for (const [file, entry] of Object.entries(PENDING_AXE_MIGRATION)) {
      expect(entry.owner, file).not.toBe('');
      expect(entry.reason, file).not.toBe('');
    }
    expect(staleAxeEntries(sites, PENDING_AXE_MIGRATION)).toEqual([]);
  });
});

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
