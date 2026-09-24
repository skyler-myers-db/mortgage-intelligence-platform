/**
 * Lane proof for the rendered-layer safety net (audit a11y-05, quality-05,
 * visual-10, quality-02, responsive-v2), at 1440x900 in every fixture run:
 * the helpers the axe matrix and the VRT stand on do what they claim.
 *
 *  (a) app.setAccent / app.setDensity apply data-accent / data-density
 *      before the first paint in both themes, mirror theme-boot.js, and a
 *      change made through the Console survives a reload;
 *  (b) axe.ts's pure partition, fed REAL axe results: an unlabeled button is
 *      unrecorded, a synthetic entry that does not reproduce is stale, a
 *      best-practice-only rule is advisory, and node pinning holds;
 *  (c) expectNoSurfaceOverflow flags an injected 2000px child, passes the
 *      clean page, and fails a stale ratchet entry;
 *  (d) the audited-read guard, as a pure check over ApiCall lists;
 *  (e) the VRT host guard refuses everything but the pinned container.
 *
 * The spec itself opens no audited read: it loads only /glossary and
 * page.setContent() documents, and (a) and (c) assert that.
 */
import fs from 'node:fs';
import path from 'node:path';
import AxeBuilder from '@axe-core/playwright';
import type { Page } from '@playwright/test';
import { FIXTURE_ACCENTS, FIXTURE_DENSITIES } from './app';
import {
  ADVISORY_TAG,
  WCAG_TAGS,
  evaluateAxeRatchet,
  expectAxeClean,
  nodesOutside,
  partitionByTags,
  type AxeScanContext,
} from './axe';
import type { ApiCall } from './mockApi';
import { FIXTURE_THEMES } from './routes';
import { expect, test } from './test';
import {
  auditedReadsAfter,
  expectNoSurfaceOverflow,
  pinnedVrtImage,
  surfaceOverflowProblems,
  vrtHostProblems,
} from './visual';

const GLOSSARY = '/glossary';

interface FirstPaintRecord {
  accent: string[];
  density: string[];
  paintsBeforeFirstAccent: number | null;
}

/**
 * Record, in the page, every value data-accent / data-density takes, and how
 * many paint entries existed when data-accent was first set. Installed before
 * the first document, so it sees theme-boot.js set the attributes.
 */
async function recordFirstPaint(page: Page): Promise<void> {
  await page.addInitScript(() => {
    const record: { accent: string[]; density: string[]; paintsBeforeFirstAccent: number | null } = {
      accent: [],
      density: [],
      paintsBeforeFirstAccent: null,
    };
    (window as unknown as { __mipFirstPaint: typeof record }).__mipFirstPaint = record;
    // Observe the document, not documentElement: an init script can run
    // before the <html> element exists.
    new MutationObserver((mutations) => {
      for (const mutation of mutations) {
        if (mutation.target !== document.documentElement) continue;
        const value = document.documentElement.getAttribute(mutation.attributeName ?? '');
        if (mutation.attributeName === 'data-accent' && value) {
          if (record.paintsBeforeFirstAccent === null) {
            record.paintsBeforeFirstAccent = performance.getEntriesByType('paint').length;
          }
          record.accent.push(value);
        }
        if (mutation.attributeName === 'data-density' && value) record.density.push(value);
      }
    }).observe(document, { attributes: true, subtree: true, attributeFilter: ['data-accent', 'data-density'] });
  });
}

async function firstPaint(page: Page): Promise<FirstPaintRecord> {
  return page.evaluate(() => (window as unknown as { __mipFirstPaint: FirstPaintRecord }).__mipFirstPaint);
}

test.describe('(a) accent and density seeding', () => {
  test('the driver accepts exactly the accents and densities theme-boot.js accepts', async ({}, testInfo) => {
    const configFile = testInfo.config.configFile;
    if (!configFile) throw new Error('needs frontend/playwright.config.ts');
    const boot = fs.readFileSync(path.join(path.dirname(configFile), 'public', 'theme-boot.js'), 'utf8');
    const list = (name: string): string[] => {
      const match = new RegExp(`var ${name} = \\[([^\\]]*)\\]`).exec(boot);
      if (!match) throw new Error(`theme-boot.js has no ${name} array`);
      return match[1].split(',').map((item) => item.trim().replace(/^'|'$/g, ''));
    };
    expect([...FIXTURE_ACCENTS]).toEqual(list('ACCENTS'));
    expect([...FIXTURE_DENSITIES]).toEqual(list('DENSITIES'));
  });

  for (const theme of FIXTURE_THEMES) {
    test(`setAccent and setDensity apply before the first paint (${theme})`, async ({ app, mockApi, page }) => {
      await recordFirstPaint(page);
      await app.setTheme(theme);
      await app.setAccent('teal');
      await app.setDensity('compact');
      await app.gotoRoute(GLOSSARY);

      const record = await firstPaint(page);
      // theme-boot.js set the seeded value first, while no paint had happened;
      // React never had to correct a wrong first value.
      expect(record.accent[0]).toBe('teal');
      expect(record.density[0]).toBe('compact');
      expect(record.paintsBeforeFirstAccent).toBe(0);
      expect(record.accent.every((value) => value === 'teal')).toBe(true);
      await expect(page.locator('html')).toHaveAttribute('data-theme', theme);
      await expect(page.locator('html')).toHaveAttribute('data-accent', 'teal');
      await expect(page.locator('html')).toHaveAttribute('data-density', 'compact');
      expect(auditedReadsAfter(mockApi.calls, 0), 'this spec opens no audited read').toEqual([]);
    });
  }

  test('an accent and density chosen in the Console survive a reload (the seed is once per tab)', async ({ app, mockApi, page }) => {
    await app.setTheme('dark');
    await app.setAccent('teal');
    await app.setDensity('compact');
    await app.gotoRoute(GLOSSARY);
    const panel = await app.openConsole();
    await panel.getByRole('button', { name: 'Accent navy' }).click();
    await panel.getByRole('button', { name: 'Comfortable', exact: true }).click();
    await expect(page.locator('html')).toHaveAttribute('data-accent', 'navy');
    await expect(page.locator('html')).toHaveAttribute('data-density', 'comfortable');

    await page.reload({ waitUntil: 'domcontentloaded' });
    await app.settle();
    await expect(page.locator('html')).toHaveAttribute('data-accent', 'navy');
    await expect(page.locator('html')).toHaveAttribute('data-density', 'comfortable');
    expect(auditedReadsAfter(mockApi.calls, 0), 'this spec opens no audited read').toEqual([]);
  });
});

const PROBE = `<!doctype html><html lang="en"><head><title>axe probe</title></head><body>
<main><h1>Probe</h1>
<button type="button" class="probe-unlabeled"></button>
<h3>Skips a heading level (best-practice only)</h3>
<p>Plain text.</p></main></body></html>`;

test.describe('(b) the axe ratchet over real axe results', () => {
  test('an unlabeled button is unrecorded, a stale entry is stale, best-practice is advisory, nodes are pinned', async ({ page }) => {
    await page.setContent(PROBE);
    const results = await new AxeBuilder({ page }).withTags([...WCAG_TAGS, ADVISORY_TAG]).analyze();
    const { gating, advisory } = partitionByTags(results.violations);
    expect(gating.map((violation) => violation.id)).toContain('button-name');
    expect(advisory.map((violation) => violation.id)).toContain('heading-order');
    expect(gating.map((violation) => violation.id)).not.toContain('heading-order');
    for (const violation of advisory) expect(violation.tags.some((tag) => (WCAG_TAGS as readonly string[]).includes(tag))).toBe(false);

    const context: AxeScanContext = {
      key: { route: 'probe', state: 'default' },
      theme: 'dark',
      accent: 'bright',
      known: {
        'probe|default|color-contrast': { finding: 'synthetic', recorded: '2026-09-24', themes: ['dark'], nodes: 'body' },
      },
    };
    const verdict = evaluateAxeRatchet(gating, context, new Map());
    expect(verdict.unrecorded.some((line) => line.startsWith('button-name'))).toBe(true);
    expect(verdict.stale).toEqual([
      'probe|default|color-contrast (synthetic, recorded 2026-09-24) no longer reproduces in dark/bright: retire it',
    ]);

    // Node pinning: an entry covers button-name only through its selector.
    const buttonName = gating.find((violation) => violation.id === 'button-name');
    if (!buttonName) throw new Error('button-name did not fire');
    const pinned = { finding: 'synthetic', recorded: '2026-09-24', themes: ['dark'] as const };
    const covered = evaluateAxeRatchet([buttonName], { ...context, known: { 'probe|default|button-name': { ...pinned, nodes: '.probe-unlabeled' } } },
      new Map([['button-name', await nodesOutside(page, buttonName, '.probe-unlabeled')]]));
    expect(covered).toEqual({ unrecorded: [], stale: [] });
    const elsewhere = evaluateAxeRatchet([buttonName], { ...context, known: { 'probe|default|button-name': { ...pinned, nodes: '.somewhere-else' } } },
      new Map([['button-name', await nodesOutside(page, buttonName, '.somewhere-else')]]));
    expect(elsewhere.unrecorded).toHaveLength(1);
    expect(elsewhere.unrecorded[0]).toContain('outside .somewhere-else');
    // A light-only entry does not cover the dark scan.
    const wrongTheme = evaluateAxeRatchet([buttonName], { ...context, known: { 'probe|default|button-name': { ...pinned, themes: ['light'], nodes: '.probe-unlabeled' } } }, new Map());
    expect(wrongTheme.unrecorded).toHaveLength(1);

    await expect(expectAxeClean(page, { key: context.key, theme: 'dark', known: {} })).rejects.toThrow(/unrecorded axe violations/);
  });

  test('a page with only a best-practice violation passes and reports it as advisory', async ({ page }, testInfo) => {
    await page.setContent(PROBE.replace('<button type="button" class="probe-unlabeled"></button>', ''));
    await expectAxeClean(page, { key: { route: 'probe', state: 'default' }, theme: 'dark', known: {} });
    const note = testInfo.annotations.find((annotation) => annotation.type === 'axe-best-practice');
    expect(note?.description).toContain('heading-order');
    expect(testInfo.attachments.map((attachment) => attachment.name)).toContain('axe-best-practice.json');
  });
});

test.describe('(c) surface overflow', () => {
  test('the clean page passes, an injected 2000px child fails, a stale entry fails', async ({ app, mockApi, page }) => {
    await app.gotoRoute(GLOSSARY);
    const key = { route: 'glossary', state: 'default', theme: 'dark' };
    expect(await page.locator('.surface').count()).toBeGreaterThan(0);
    await expectNoSurfaceOverflow(page, key);
    expect(await surfaceOverflowProblems(page, key, {
      'glossary|default': { finding: 'synthetic', recorded: '2026-09-24', themes: ['dark'], nodes: '.surface' },
    })).toEqual(['glossary|default (synthetic, recorded 2026-09-24) no longer overflows in dark: retire it']);

    await page.locator('.surface').first().evaluate((surface) => {
      const wide = document.createElement('div');
      wide.className = 'safety-net-probe';
      wide.style.inlineSize = '2000px';
      wide.style.blockSize = '1px';
      surface.appendChild(wide);
    });
    const problems = await surfaceOverflowProblems(page, key);
    expect(problems).toHaveLength(1);
    expect(problems[0]).toMatch(/^aside\.surface\.glossary-index .*scrolls sideways \(scrollWidth \d+ > clientWidth \d+\)$/);
    await expect(expectNoSurfaceOverflow(page, key)).rejects.toThrow(/surface overflow on glossary/);
    // The same overflow, recorded against its surface, is covered.
    expect(await surfaceOverflowProblems(page, key, {
      'glossary|default': { finding: 'synthetic', recorded: '2026-09-24', themes: ['dark'], nodes: '.glossary-index' },
    })).toEqual([]);
    expect(auditedReadsAfter(mockApi.calls, 0), 'this spec opens no audited read').toEqual([]);
  });
});

function call(method: string, pathWithQuery: string): ApiCall {
  const [path, search = ''] = pathWithQuery.split('?');
  return { method, path, search, status: 200, outcome: 'fixture' };
}

test.describe('(d) the audited-read guard', () => {
  test('flags a proof read after the natural load and passes the Offer detail natural recommend and draft', () => {
    const offerLoad = [
      call('GET', '/api/v1/config/options'),
      call('GET', '/api/v1/borrowers/B-P5YP9ESW32R7Z'),
      call('POST', '/api/v1/offers/recommend'),
      call('POST', '/api/v1/outreach/draft'),
    ];
    const naturalLoad = offerLoad.length;
    // The natural load itself is where those reads belong: the guard sees them, and allows them.
    expect(auditedReadsAfter(offerLoad, 0)).toEqual([
      'GET /api/v1/borrowers/B-P5YP9ESW32R7Z (VIEW_BORROWER)',
      'POST /api/v1/offers/recommend (RECOMMEND_OFFER)',
      'POST /api/v1/outreach/draft (DRAFT_OUTREACH)',
    ]);
    expect(auditedReadsAfter(offerLoad, naturalLoad)).toEqual([]);

    const afterDrawer = [...offerLoad, call('GET', '/api/v1/data-estate/assets/x'), call('GET', '/api/v1/borrowers/B-P5YP9ESW32R7Z/proof')];
    expect(auditedReadsAfter(afterDrawer, naturalLoad)).toEqual(['GET /api/v1/borrowers/B-P5YP9ESW32R7Z/proof (VIEW_BORROWER_PROOF)']);

    const benign = [
      call('GET', '/api/borrowers/search?q=chicago'),
      call('GET', '/api/borrowers/B-P5YP9ESW32R7Z/lifecycle'),
      call('POST', '/api/portfolio/preview'),
      call('GET', '/api/leads/export'),
    ];
    expect(auditedReadsAfter(benign, 0)).toEqual([]);
    expect(auditedReadsAfter([call('GET', '/api/leads?limit=500'), call('POST', '/api/lookup/property-loan')], 0)).toEqual([
      'GET /api/leads?limit=500 (VIEW_LEADS)',
      'POST /api/lookup/property-loan (PROPERTY_LOOKUP)',
    ]);
  });
});

test.describe('(e) the VRT host guard', () => {
  test('only the pinned amd64 Linux container may capture or compare baselines', () => {
    const image = pinnedVrtImage('1.59.1');
    expect(image).toBe('mcr.microsoft.com/playwright:v1.59.1-noble');
    expect(vrtHostProblems({ image, platform: 'linux', arch: 'x64', playwrightVersion: '1.59.1' })).toEqual([]);
    expect(vrtHostProblems({ image, platform: 'darwin', arch: 'arm64', playwrightVersion: '1.59.1' })).toHaveLength(2);
    expect(vrtHostProblems({ image, platform: 'linux', arch: 'arm64', playwrightVersion: '1.59.1' })).toEqual(['arch is arm64, expected x64 (amd64)']);
    expect(vrtHostProblems({ image: 'mcr.microsoft.com/playwright:v1.58.0-noble', platform: 'linux', arch: 'x64', playwrightVersion: '1.59.1' }))
      .toHaveLength(1);
    expect(vrtHostProblems({ image: undefined, platform: 'linux', arch: 'x64', playwrightVersion: '1.59.1' })[0]).toContain('unset');
  });
});
