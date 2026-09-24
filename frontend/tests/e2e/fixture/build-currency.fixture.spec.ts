/**
 * Rendered-layer proof for lane w2-build-currency (2026-09-21 UI/UX audit,
 * wave 2): what the production build actually serves to a browser at
 * 1440x900. Only the natural loads of Home and the Lead Queue are used: no
 * proof drawer, no draft, nothing that is an audit-writing read.
 *
 *  - stack-01 (build slice): hidden source maps. No page asks for a `.map`
 *    and the served entry script carries no `sourceMappingURL` comment
 *    (tools/postbuild_artifacts.mjs moves the maps out of dist).
 *
 * The CSP hygiene gate stays on for every test here.
 */
import type { Page, Response } from '@playwright/test';
import { expect, test } from './test';

const NATURAL_LOADS = ['/', '/lead-queue'] as const;
const SOURCE_MAP_COMMENT = /^\/\/[#@]\s*sourceMappingURL=/m;

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
