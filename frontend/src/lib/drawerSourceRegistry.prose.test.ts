/**
 * The evidence registry split (audit 2026-09-21 bundle-04 item 3): a slim
 * index in the shell (title, short, asset, lineage family, registryKey) and
 * the prose (description, signals, "used in", definition) behind the drawer's
 * lazy body. Pins: the two key sets are equal both ways, resolveDrawerProse's
 * rules, and that only the drawer body (and its loader) imports the prose, so
 * it never re-enters the initial closure.
 */
// @ts-expect-error Frontend app types intentionally exclude Node globals; this
// source gate reads the tree under Vitest only.
import { readdirSync, readFileSync } from 'node:fs';
// @ts-expect-error see node:fs note above.
import { join } from 'node:path';
import { describe, expect, it } from 'vitest';
import { DRAWER_SOURCES, isDrawerSourceKey } from './drawerSourceRegistry';
import { DRAWER_SOURCE_PROSE, resolveDrawerProse } from './drawerSourceRegistry.prose';

declare const process: { cwd(): string };

const INDEX_FIELDS = new Set(['registryKey', 'title', 'short', 'assetKey', 'assetPath', 'lineageFamily']);

describe('the slim registry index and its prose', () => {
  it('holds the same 41 keys on both sides', () => {
    const index = Object.keys(DRAWER_SOURCES).sort();
    expect(index).toHaveLength(41);
    expect(Object.keys(DRAWER_SOURCE_PROSE).sort()).toEqual(index);
  });

  it('carries only what a chip and the frame need, with its own key', () => {
    for (const [key, source] of Object.entries(DRAWER_SOURCES)) {
      expect(source.registryKey, key).toBe(key);
      expect(Object.keys(source).filter((field) => !INDEX_FIELDS.has(field)), key).toEqual([]);
      expect(isDrawerSourceKey(key)).toBe(true);
    }
    expect(isDrawerSourceKey('notAKey')).toBe(false);
    expect(isDrawerSourceKey(undefined)).toBe(false);
  });

  it('defines what each headline count counts', () => {
    for (const key of ['population', 'populationMarketable', 'itm', 'leadScore', 'nbo', 'portfolioHeadlineView'] as const) {
      const definition = DRAWER_SOURCE_PROSE[key].definition ?? '';
      expect(definition, key).toMatch(/^[A-Z].+\.$/);
      expect(definition.split('. ')).toHaveLength(1);
    }
  });
});

describe('resolveDrawerProse', () => {
  it('fills a registry entry with its description, signals, used-in list and definition', () => {
    const resolved = resolveDrawerProse(DRAWER_SOURCES.callDispositions);
    expect(resolved.description).toBe(DRAWER_SOURCE_PROSE.callDispositions.description);
    expect(resolved.signals).toEqual(DRAWER_SOURCE_PROSE.callDispositions.signals);
    expect(resolved.usedIn).toEqual(['Campaign performance qualification', 'Sales Ops analytics']);
    expect(resolveDrawerProse(DRAWER_SOURCES.itm).definition).toBe(DRAWER_SOURCE_PROSE.itm.definition);
  });

  it('lets a source\'s own description win, an explicit undefined included', () => {
    expect(resolveDrawerProse({ ...DRAWER_SOURCES.itm, description: 'Own.' }).description).toBe('Own.');
    const explicit = resolveDrawerProse({ ...DRAWER_SOURCES.itm, description: undefined });
    expect(Object.prototype.hasOwnProperty.call(explicit, 'description')).toBe(true);
    expect(explicit.description).toBeUndefined();
  });

  it('puts a source\'s own signals first, then the registry\'s', () => {
    const own = { label: 'Live count', source: 'x', value: '42' };
    const resolved = resolveDrawerProse({ ...DRAWER_SOURCES.itm, signals: [own] });
    expect(resolved.signals).toEqual([own, ...(DRAWER_SOURCE_PROSE.itm.signals ?? [])]);
  });

  it('keeps a source\'s own used-in list and definition', () => {
    const resolved = resolveDrawerProse({ ...DRAWER_SOURCES.callDispositions, usedIn: ['Own'], definition: 'Own.' });
    expect(resolved.usedIn).toEqual(['Own']);
    expect(resolved.definition).toBe('Own.');
  });

  it('adds nothing to a source without a known registry key', () => {
    const plain = { title: 'Unmapped', description: 'Own.', signals: [] };
    expect(resolveDrawerProse(plain)).toBe(plain);
    const unknown = { title: 'Unknown', registryKey: 'notAKey' };
    expect(resolveDrawerProse(unknown)).toBe(unknown);
    const dropped = { ...DRAWER_SOURCES.segmentPopulation, registryKey: undefined, description: undefined, signals: [] };
    expect(resolveDrawerProse(dropped)).toBe(dropped);
  });
});

describe('the prose stays out of the initial closure', () => {
  const src = join(process.cwd(), 'src');
  const files = (readdirSync(src, { recursive: true }) as string[])
    .map((entry) => entry.split('\\').join('/'))
    .filter((entry) => /\.(ts|tsx)$/.test(entry));
  const IMPORTS_PROSE = /(?:from\s+|import\s*\(\s*)['"][^'"]*drawerSourceRegistry\.prose['"]/;

  it('is imported only by the drawer body, its loader and tests', () => {
    const importers = files.filter((entry) => IMPORTS_PROSE.test(readFileSync(join(src, entry), 'utf8') as string));
    const offenders = importers.filter(
      (entry) =>
        !/\.test\.tsx?$/.test(entry) &&
        !/^components\/mortgage\/EvidenceDrawerBody[^/]*\.tsx$/.test(entry) &&
        entry !== 'components/mortgage/evidenceDrawerBodyLoader.ts',
    );
    expect(importers).toContain('components/mortgage/EvidenceDrawerBody.tsx');
    expect(offenders).toEqual([]);
  });

  it('is never imported by the slim index', () => {
    expect(readFileSync(join(src, 'lib/drawerSourceRegistry.ts'), 'utf8') as string).not.toMatch(IMPORTS_PROSE);
  });
});
