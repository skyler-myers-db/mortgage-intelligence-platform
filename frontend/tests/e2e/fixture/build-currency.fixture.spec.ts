/**
 * Rendered-layer proof for lane w2-build-currency (2026-09-21 UI/UX audit,
 * wave 2): what the production build actually serves to a browser at
 * 1440x900. Only the natural loads of Home and the Lead Queue are used: no
 * proof drawer, no draft, nothing that is an audit-writing read.
 *
 *  - stack-01 (build slice): hidden source maps. No page asks for a `.map`
 *    and the served entry script carries no `sourceMappingURL` comment
 *    (tools/postbuild_artifacts.mjs moves the maps out of dist).
 *  - bundle-03: vendor chunks. A cold load fetches exactly one vendor-react
 *    and one vendor-data chunk, both announced as modulepreload in the served
 *    HTML, and moving on to the Lead Queue fetches no vendor chunk again.
 *  - bundle-05 / css-v2: variable Geist. In both themes the served HTML
 *    preloads exactly the two woff2 files the page fetches, each once (the
 *    preload is reused, so it carries crossorigin); no .woff is requested;
 *    both faces load with the 100-900 range and render 600 < 650 < 700 < 800
 *    as four distinct widths; the metric-matched fallbacks (a regular and a
 *    real local bold face each) load and set a sample within 3% (sans) /
 *    1.5% (mono) of the webfont's width at 400, 600 and 700, and a
 *    glyph outside the webfont's latin subset (→ ≥ Δ ▲ ▼) keeps the system
 *    face rather than the scaled fallback face.
 *    Home screenshots in both themes are ATTACHED (not baselined) for a
 *    manual 400/500/600/700 read against design_files/module_0_prototype_1.png;
 *    the VRT baselines belong to the safety-net lane.
 *
 * The CSP hygiene gate stays on for every test here.
 */
import type { Page, Response } from '@playwright/test';
import { FIXTURE_THEMES } from './routes';
import { expect, test } from './test';

const NATURAL_LOADS = ['/', '/lead-queue'] as const;
const SOURCE_MAP_COMMENT = /^\/\/[#@]\s*sourceMappingURL=/m;
const VENDOR_CHUNKS = ['vendor-react', 'vendor-data'] as const;
const vendorChunk = (name: string) => new RegExp(`^/assets/${name}-[\\w-]+\\.js$`);
const ANY_VENDOR_CHUNK = /^\/assets\/vendor-[\w-]+\.js$/;

interface Traffic {
  requests: string[];
  scripts: Response[];
}

function recordTraffic(page: Page): Traffic {
  const traffic: Traffic = { requests: [], scripts: [] };
  page.on('request', (request) => {
    traffic.requests.push(new URL(request.url()).pathname);
  });
  page.on('response', (response) => {
    if (response.request().resourceType() === 'script') traffic.scripts.push(response);
  });
  return traffic;
}

test.describe('hidden source maps (stack-01 build slice)', () => {
  for (const path of NATURAL_LOADS) {
    test(`${path} requests no source map and its entry script carries no sourceMappingURL`, async ({ app, page }) => {
      const traffic = recordTraffic(page);
      await app.gotoRoute(path);

      expect(traffic.requests.filter((pathname) => pathname.endsWith('.map'))).toEqual([]);
      const entrySrc = await page.locator('script[type="module"][src^="/assets/index-"]').getAttribute('src');
      expect(entrySrc, 'the served index.html names a hashed entry module').toMatch(/^\/assets\/index-[\w-]+\.js$/);
      const entry = traffic.scripts.find((response) => new URL(response.url()).pathname === entrySrc);
      expect(entry, 'the entry module was fetched').toBeDefined();
      const body = (await entry?.text()) ?? '';
      expect(body.length).toBeGreaterThan(0);
      expect(body).not.toMatch(SOURCE_MAP_COMMENT);
      for (const script of traffic.scripts) {
        expect(await script.text(), `${new URL(script.url()).pathname} carries a sourceMappingURL`).not.toMatch(
          SOURCE_MAP_COMMENT,
        );
      }
    });
  }
});

test.describe('vendor chunks (bundle-03)', () => {
  test('a cold load preloads and fetches each vendor chunk once; the next route fetches none', async ({ app, page }) => {
    const traffic = recordTraffic(page);
    await app.gotoRoute('/');

    const preloads = await page
      .locator('link[rel="modulepreload"]')
      .evaluateAll((links) => links.map((link) => new URL((link as HTMLLinkElement).href).pathname));
    for (const name of VENDOR_CHUNKS) {
      const pattern = vendorChunk(name);
      expect(preloads.filter((pathname) => pattern.test(pathname)), `${name} is announced as modulepreload`).toHaveLength(1);
      expect(traffic.requests.filter((pathname) => pattern.test(pathname)), `${name} is fetched once`).toHaveLength(1);
    }
    expect(traffic.requests.filter((pathname) => ANY_VENDOR_CHUNK.test(pathname))).toHaveLength(VENDOR_CHUNKS.length);

    const beforeNavigation = traffic.requests.length;
    await page.getByRole('navigation', { name: 'Main navigation' }).getByRole('link', { name: 'Leads' }).click();
    await expect(page).toHaveURL(/\/lead-queue$/);
    await app.settle();
    const afterNavigation = traffic.requests.slice(beforeNavigation);
    expect(traffic.requests.some((pathname) => /^\/assets\/lead-queue-[\w-]+\.js$/.test(pathname)), 'the route chunk loaded').toBe(
      true,
    );
    expect(afterNavigation.filter((pathname) => ANY_VENDOR_CHUNK.test(pathname))).toEqual([]);
  });
});

const FONT_FILE = /\.(?:woff2?|ttf|otf)$/;
/**
 * Glyphs the UI prints that Geist's latin subset does not carry (U+2192,
 * U+2265, U+0394, U+25B2, U+25BC). No space: the subset does carry that.
 */
const OUT_OF_SUBSET = '→≥Δ▲▼';
const WEIGHT_LADDER = [600, 650, 700, 800] as const;
const METRIC_SAMPLE = 'Who should we contact, why now, and with what offer? Summit Mortgage 2026';
/** Regular text, then the two bold weights the partials set most (600, 700). */
const FALLBACK_WEIGHTS = [400, 600, 700] as const;

interface FaceState {
  family: string;
  weight: string;
  status: string;
}

/**
 * document.fonts after asking for each face at each weight (the mono face may
 * not be on screen yet, and a family's bold face loads only for a bold ask).
 */
async function loadedFaces(page: Page, families: readonly string[], weights: readonly number[] = [400]): Promise<FaceState[]> {
  return page.evaluate(
    async ({ names, ladder }) => {
      await Promise.all(names.flatMap((name) => ladder.map((weight) => document.fonts.load(`${weight} 16px '${name}'`))));
      return [...document.fonts].map((face) => ({
        family: face.family.replace(/["']/g, ''),
        weight: face.weight,
        status: face.status,
      }));
    },
    { names: [...families], ladder: [...weights] },
  );
}

/** Rendered width of `text` in `family` at each weight (40px, off-screen probe spans). */
async function probeWidths(page: Page, family: string, text: string, weights: readonly number[]): Promise<number[]> {
  return page.evaluate(
    ({ family: name, text: sample, weights: ladder }) => {
      const widths = ladder.map((weight) => {
        const probe = document.createElement('span');
        probe.textContent = sample;
        probe.style.cssText = `position:absolute;left:-10000px;top:0;white-space:nowrap;font-size:40px;font-family:'${name}';font-weight:${weight}`;
        document.body.append(probe);
        const width = probe.getBoundingClientRect().width;
        probe.remove();
        return width;
      });
      return widths;
    },
    { family, text, weights: [...weights] },
  );
}

test.describe('variable Geist webfonts (bundle-05 / css-v2)', () => {
  for (const theme of FIXTURE_THEMES) {
    test(`${theme}: two preloaded woff2 files, each fetched once, render the whole weight axis`, async ({ app, page }, testInfo) => {
      const traffic = recordTraffic(page);
      await app.setTheme(theme);
      await app.gotoRoute('/');

      const preloads = await page.locator('link[rel="preload"][as="font"]').evaluateAll((links) =>
        links.map((link) => ({
          path: new URL((link as HTMLLinkElement).href).pathname,
          type: link.getAttribute('type'),
          crossorigin: link.getAttribute('crossorigin'),
        })),
      );
      // Every check here is soft, so a regression reports all of its
      // consequences: a dropped crossorigin also shows the second fetch of
      // each font (a no-CORS preload is not reused), and static faces also
      // show the weights they snap.
      expect.soft(preloads, 'exactly two font preloads').toHaveLength(2);
      for (const preload of preloads) {
        expect.soft(preload.path).toMatch(/^\/assets\/geist(?:-mono)?-latin-wght-normal-[\w-]+\.woff2$/);
        expect.soft(preload.type).toBe('font/woff2');
        expect.soft(preload.crossorigin, `${preload.path} preload is CORS-mode, like the font fetch`).not.toBeNull();
      }

      const faces = await loadedFaces(page, ['Geist', 'Geist Mono']);
      for (const family of ['Geist', 'Geist Mono']) {
        const face = faces.find((candidate) => candidate.family === family);
        expect.soft(face, `${family} is a document font`).toBeDefined();
        expect.soft(face?.status, `${family} loaded`).toBe('loaded');
        expect.soft(face?.weight, `${family} is variable`).toBe('100 900');
      }

      const fontRequests = traffic.requests.filter((pathname) => FONT_FILE.test(pathname));
      expect.soft(fontRequests.filter((pathname) => pathname.endsWith('.woff')), 'no legacy .woff').toEqual([]);
      expect.soft([...fontRequests].sort(), 'each preloaded font is fetched once and nothing else').toEqual(
        preloads.map((preload) => preload.path).sort(),
      );

      const widths = await probeWidths(page, 'Geist', 'Mortgage Intelligence Platform 0123456789', WEIGHT_LADDER);
      for (let index = 1; index < widths.length; index += 1) {
        expect.soft(
          widths[index],
          `Geist ${WEIGHT_LADDER[index]} renders wider than ${WEIGHT_LADDER[index - 1]} (${widths.join(' / ')})`,
        ).toBeGreaterThan(widths[index - 1]);
      }

      const bodyFont = await page.evaluate(() => getComputedStyle(document.body).fontFamily);
      expect.soft(bodyFont).toMatch(/^"?Geist"?, "Geist Fallback", /);

      await testInfo.attach(`home-${theme}.png`, { body: await page.screenshot(), contentType: 'image/png' });
    });
  }

  test('a glyph outside the webfont subset keeps the system face, not the scaled fallback face', async ({ app, page }) => {
    await app.gotoRoute('/');
    const widths = await page.evaluate(async (sample) => {
      const stack = getComputedStyle(document.documentElement).getPropertyValue('--font-sans').trim();
      const system = stack.split(',').slice(2).join(',').trim();
      const measure = (family: string) => {
        const probe = document.createElement('span');
        probe.textContent = sample;
        probe.style.cssText = 'position:absolute;left:-10000px;top:0;white-space:nowrap;font-size:40px';
        probe.style.fontFamily = family;
        document.body.append(probe);
        const width = probe.getBoundingClientRect().width;
        probe.remove();
        return width;
      };
      // Control: the served 'Geist Fallback' face (same local source, same
      // size-adjust) WITHOUT its unicode-range. It must capture the glyphs,
      // or this environment could not tell a ranged face from an unranged one.
      const rule = [...document.styleSheets]
        .flatMap((sheet) => [...sheet.cssRules])
        .find(
          (candidate): candidate is CSSFontFaceRule =>
            candidate instanceof CSSFontFaceRule &&
            candidate.style.getPropertyValue('font-family').replace(/["']/g, '') === 'Geist Fallback',
        );
      if (!rule) throw new Error("the served stylesheet has no 'Geist Fallback' face");
      // size-adjust is a FontFace descriptor Chromium supports and the DOM
      // lib does not type yet.
      const descriptors: FontFaceDescriptors & { sizeAdjust: string } = {
        sizeAdjust: rule.style.getPropertyValue('size-adjust'),
      };
      const unranged = new FontFace('Unranged Geist Fallback', rule.style.getPropertyValue('src'), descriptors);
      document.fonts.add(unranged);
      await unranged.load();
      const result = {
        stack: measure(stack),
        system: measure(system),
        unranged: measure(`'Unranged Geist Fallback', ${system}`),
      };
      document.fonts.delete(unranged);
      return result;
    }, OUT_OF_SUBSET);

    expect(
      Math.abs(widths.unranged / widths.system - 1),
      `control: an unranged fallback face sets ${OUT_OF_SUBSET} at ${widths.unranged}px vs the system stack ${widths.system}px`,
    ).toBeGreaterThan(0.02);
    expect(
      Math.abs(widths.stack / widths.system - 1),
      `--font-sans sets ${OUT_OF_SUBSET} at ${widths.stack}px vs the system stack ${widths.system}px`,
    ).toBeLessThan(0.005);
  });

  test('the metric-matched fallbacks load and track the webfont widths, regular and bold', async ({ app, page }) => {
    await app.gotoRoute('/');
    const faces = await loadedFaces(page, ['Geist', 'Geist Mono', 'Geist Fallback', 'Geist Mono Fallback'], [400, 700]);
    for (const family of ['Geist Fallback', 'Geist Mono Fallback']) {
      // A regular face (no weight descriptor) and a real bold one for 600-900,
      // so bold text during the swap is the local bold face, not a synthesized
      // bold of the regular one.
      const own = faces.filter((face) => face.family === family);
      expect.soft(own.map((face) => face.weight).sort(), `${family} faces`).toEqual(['600 900', 'normal']);
      for (const face of own) {
        expect.soft(face.status, `${family} ${face.weight} found its local face`).toBe('loaded');
      }
    }
    const cases = [
      { webfont: 'Geist', fallback: 'Geist Fallback', tolerance: 0.03 },
      { webfont: 'Geist Mono', fallback: 'Geist Mono Fallback', tolerance: 0.015 },
    ];
    for (const { webfont, fallback, tolerance } of cases) {
      const webfontWidths = await probeWidths(page, webfont, METRIC_SAMPLE, FALLBACK_WEIGHTS);
      const fallbackWidths = await probeWidths(page, fallback, METRIC_SAMPLE, FALLBACK_WEIGHTS);
      FALLBACK_WEIGHTS.forEach((weight, index) => {
        const drift = Math.abs(fallbackWidths[index] / webfontWidths[index] - 1);
        expect
          .soft(drift, `${fallback} ${weight} sets the sample at ${fallbackWidths[index]}px vs ${webfont} ${webfontWidths[index]}px`)
          .toBeLessThan(tolerance);
      });
    }
  });
});
