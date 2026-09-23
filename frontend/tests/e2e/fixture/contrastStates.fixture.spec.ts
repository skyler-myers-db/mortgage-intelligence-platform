/**
 * Light-theme states the axe colour-contrast loop in theme.fixture.spec.ts
 * never renders on a healthy / or /lead-queue, proven at the rendered layer
 * (2026-09-21 audit a11y-01):
 *
 *  a. warning copy paints `--signal-warning-ink`, not the amber FILL hue
 *     (#F59E0B on white was 2.15:1): the topbar search error and the Genie
 *     history error, each rendered from a degraded endpoint, plus a computed
 *     sweep over every partial rule that paints warning text or an amber
 *     glyph, because some of them have no state the fixture population can
 *     reach (`.seg-card__meta--pending`, `.genie-proof__gap`,
 *     `.bulk-actions__toast--warn`, `.audit__ico.amber`);
 *  b. amber icon glyphs clear WCAG 1.4.11 3:1 on their tinted tiles: the
 *     Home approval queue and the degraded-dependency banner;
 *  c. the active evidence-drawer tab paints `--accent-ink` on its fill for
 *     every accent (it painted `--accent`, 1.75:1 in light + bright);
 *  d. the three text inputs whose rules switched the outline off (Genie
 *     composer, property lookup `.form-input`, admin audit
 *     `.admin-filter-input`) show the shared `--focus-ring-*` ring at 3:1;
 *  e. the Analytics activation-funnel Sankey node, whose SVG focus ring is a
 *     stroke, strokes the ring colour at 3:1 (it stroked `--accent`, 1.9:1);
 *  f. in every theme x accent, the text and glyph rules that painted the
 *     `--accent` fill hue (not-found tile, growth-agent card icon, glossary
 *     term hover, scatter cluster overflow and legend count) paint
 *     `--accent-ink` at AA.
 */
import type { Locator, Page } from '@playwright/test';
import type { HealthPayload } from '../../../src/lib/apiTypes';
import type { AppDriver } from './app';
import { HEALTH_OK } from './data/shell';
import { json } from './mockApi';
import { asComputedRgb, contrastRatio, parseRgb, renderedColors, settleTransitions, tokenValue } from './renderedColor';
import { expect, test, type FixtureTheme } from './test';

const AA_TEXT = 4.5;
const AA_UI = 3;
const ACCENTS = ['bright', 'teal', 'navy', 'red'] as const;
const THEMES: readonly FixtureTheme[] = ['dark', 'light'];

/**
 * Every partial rule that paints warning copy or an amber glyph (the list
 * tokenUsage.test.ts pins at source level), as the class list of a probe.
 */
const WARNING_INK_CONSUMERS: readonly string[] = [
  'topbar__search-status topbar__search-status--error',
  'seg-card__meta seg-card__meta--pending',
  'bulk-actions__toast bulk-actions__toast--warn',
  'genie-history__state genie-history__state--error',
  'genie-proof__gap',
  'approval__ico',
  'degraded-banner__ico',
  'audit__ico amber',
];

/** Assert the element paints the warning ink and clears `min` against what is really behind it. */
async function expectWarningInk(page: Page, target: Locator, min: number): Promise<void> {
  const painted = await renderedColors(target);
  const ratio = contrastRatio(painted.fg, painted.bg);
  expect(ratio, `${painted.color} on rgb(${painted.bg.join(', ')}) = ${ratio.toFixed(2)}:1`).toBeGreaterThanOrEqual(min);
  const ink = await asComputedRgb(page, await tokenValue(target, '--signal-warning-ink'));
  expect(painted.color, 'paints --signal-warning-ink, not the amber fill').toBe(ink);
}

async function seedAccent(page: Page, accent: string): Promise<void> {
  await page.addInitScript((value) => {
    try {
      window.localStorage.setItem('mip.accent', value);
    } catch {
      // about:blank has no storage; the next document seeds it.
    }
  }, accent);
}

test('light: the topbar search error paints the warning ink at AA', async ({ app, page }) => {
  app.degrade('/api/borrowers/search', { status: 500, body: { detail: 'fixture: search is down' } });
  await app.setTheme('light');
  await app.gotoRoute('/');

  await page.getByRole('banner').getByRole('textbox', { name: 'Search borrowers' }).fill('B-');
  const status = page.locator('.topbar__search-status--error');
  await expect(status).toBeVisible();
  await expectWarningInk(page, status, AA_TEXT);
});

test('light: the Genie history error paints the warning ink at AA', async ({ app, page }) => {
  app.degrade('/api/genie/sessions', { status: 500, body: { detail: 'fixture: history is down' } });
  await app.setTheme('light');
  await app.gotoRoute('/');

  const genie = await app.openGenie();
  await genie.getByRole('button', { name: 'Genie conversation history' }).click();
  const state = genie.locator('.genie-history__state--error');
  await expect(state).toHaveText('History unavailable');
  await expectWarningInk(page, state, AA_TEXT);
});

test('light: every warning-ink consumer computes the ink, not the amber fill', async ({ app, page }) => {
  await app.setTheme('light');
  await app.gotoRoute('/');
  const root = page.locator('html');
  const ink = await asComputedRgb(page, await tokenValue(root, '--signal-warning-ink'));
  const fill = await asComputedRgb(page, await tokenValue(root, '--signal-warning'));
  expect(ink, 'the light theme separates the text ink from the fill hue').not.toBe(fill);

  const painted = await page.evaluate((classLists) => {
    const host = document.createElement('div');
    document.body.appendChild(host);
    const colors = classLists.map((className) => {
      const probe = document.createElement('div');
      probe.className = className;
      probe.textContent = 'Warning';
      host.appendChild(probe);
      return [className, getComputedStyle(probe).color] as const;
    });
    host.remove();
    return Object.fromEntries(colors);
  }, WARNING_INK_CONSUMERS);
  expect(painted).toEqual(Object.fromEntries(WARNING_INK_CONSUMERS.map((className) => [className, ink])));

  const surface = parseRgb(await asComputedRgb(page, await tokenValue(root, '--bg-1')));
  expect(contrastRatio(parseRgb(ink), surface)).toBeGreaterThanOrEqual(AA_TEXT);
});

test('light: amber icon glyphs clear 3:1 on their tinted tiles', async ({ app, mockApi, page }) => {
  // Genie is a dependency Home does not read from, so the banner shows and
  // every panel still loads.
  mockApi.register('GET', '/api/health', () =>
    json<HealthPayload>({ ...HEALTH_OK, dependencies: { ...HEALTH_OK.dependencies, genie: 'down' } }),
  );
  await app.setTheme('light');
  await app.gotoRoute('/');

  const approvalIcon = page.getByRole('region', { name: 'Approval queue' }).locator('.approval__ico');
  await expect(approvalIcon).toBeVisible();
  await expectWarningInk(page, approvalIcon, AA_UI);

  const bannerIcon = page.locator('.degraded-banner[data-degraded-dependency="genie"] .degraded-banner__ico');
  await expect(bannerIcon).toBeVisible();
  await expectWarningInk(page, bannerIcon, AA_UI);
});

for (const accent of ACCENTS) {
  test(`light + ${accent}: the active evidence-drawer tab reads AA on its fill`, async ({ app, page }) => {
    // The drawer only opens from an evidence chip, so the axe loop never sees the tab.
    await app.setTheme('light');
    await seedAccent(page, accent);
    await app.gotoRoute('/');
    await expect(page.locator('html')).toHaveAttribute('data-accent', accent);
    await page.locator('.kpi__source .evidence-chip').first().click();
    const active = page.getByRole('dialog').locator('.drawer__tab.is-active');
    await expect(active).toBeVisible();
    await expect(active).toHaveAttribute('aria-selected', 'true');

    const painted = await renderedColors(active);
    const ratio = contrastRatio(painted.fg, painted.bg);
    expect(ratio, `${painted.color} on rgb(${painted.bg.join(', ')}) = ${ratio.toFixed(2)}:1`).toBeGreaterThanOrEqual(AA_TEXT);
    expect(painted.color, 'paints --accent-ink').toBe(await asComputedRgb(page, await tokenValue(active, '--accent-ink')));
  });
}

/**
 * Text and glyph rules that painted the `--accent` FILL hue (#66C5FF on
 * white is 1.9:1 in light + bright; navy on the dark surfaces is under 3:1
 * in dark + navy), as probe class lists with the WCAG floor each needs. The
 * not-found tile, the growth-agent card and a scatter cluster overflow have
 * no state one fixture route reaches, so these are probes; the two scatter
 * rules live in the route-scoped analytics.scatter.css, so the probes sit
 * in the mounted Analytics scatter panel.
 */
const ACCENT_INK_CONSUMERS: ReadonlyArray<{ className: string; min: number }> = [
  { className: 'not-found__icon', min: AA_UI },
  { className: 'growth-agent-card__icon', min: AA_UI },
  { className: 'analytics-scatter__cluster-more', min: AA_TEXT },
  { className: 'analytics-scatter-legend__cluster-count', min: AA_TEXT },
];

for (const theme of THEMES) {
  for (const accent of ACCENTS) {
    test(`${theme} + ${accent}: accent text and glyphs paint --accent-ink, not the fill hue`, async ({ app, page }) => {
      await app.setTheme(theme);
      await seedAccent(page, accent);
      await app.gotoRoute('/analytics?view=economics');
      await expect(page.locator('html')).toHaveAttribute('data-accent', accent);
      const panel = page.locator('.analytics-chart-panel--scatter');
      await expect(panel.locator('.analytics-scatter-legend__band').first()).toBeVisible();
      const ink = await asComputedRgb(page, await tokenValue(panel, '--accent-ink'));

      await panel.evaluate((host, cases) => {
        for (const { className } of cases) {
          const probe = document.createElement('span');
          probe.className = className;
          probe.dataset.accentInkProbe = className;
          probe.textContent = 'Accent';
          host.appendChild(probe);
        }
        const term = document.createElement('span');
        term.className = 'glossary-term';
        term.dataset.accentInkProbe = 'glossary-term';
        term.textContent = 'Glossary term';
        host.appendChild(term);
      }, ACCENT_INK_CONSUMERS);

      for (const { className, min } of ACCENT_INK_CONSUMERS) {
        const probe = panel.locator(`[data-accent-ink-probe="${className}"]`);
        const painted = await renderedColors(probe);
        expect(painted.color, `.${className} paints --accent-ink`).toBe(ink);
        const ratio = contrastRatio(painted.fg, painted.bg);
        expect(ratio, `.${className}: ${painted.color} on rgb(${painted.bg.join(', ')}) = ${ratio.toFixed(2)}:1`).toBeGreaterThanOrEqual(min);
      }

      // .glossary-term inherits its colour at rest and takes the accent on
      // hover / focus-visible; the colour change transitions under the
      // harness's reduced-motion reset, so read it settled.
      const term = panel.locator('[data-accent-ink-probe="glossary-term"]');
      await term.hover();
      await settleTransitions(term);
      const hovered = await renderedColors(term);
      expect(hovered.color, '.glossary-term:hover paints --accent-ink').toBe(ink);
      const termRatio = contrastRatio(hovered.fg, hovered.bg);
      expect(termRatio, `.glossary-term:hover: ${hovered.color} = ${termRatio.toFixed(2)}:1`).toBeGreaterThanOrEqual(AA_TEXT);
    });
  }
}

interface TextInputCase {
  name: string;
  route: string;
  locate: (app: AppDriver, page: Page) => Promise<Locator>;
}

const TEXT_INPUTS: readonly TextInputCase[] = [
  {
    name: 'Genie composer',
    route: '/',
    locate: async (app) => (await app.openGenie()).getByRole('textbox', { name: 'Ask Genie' }),
  },
  {
    name: 'property lookup .form-input',
    route: '/lead-queue',
    locate: async (_app, page) =>
      page.locator('#main-content').getByRole('textbox', { name: 'Property lookup — street address' }),
  },
  {
    name: 'admin audit .admin-filter-input',
    route: '/admin-config',
    locate: async (_app, page) => page.locator('#main-content .admin-filter-input').first(),
  },
];

for (const input of TEXT_INPUTS) {
  test(`light: the ${input.name} shows the shared focus ring`, async ({ app, page }) => {
    await app.setTheme('light');
    await app.gotoRoute(input.route);
    const field = await input.locate(app, page);
    await expect(field).toBeVisible();

    await page.keyboard.press('Tab');
    await field.focus();
    // The ring transitions in from the unfocused outline (3px `medium`)
    // under the harness's reduced-motion reset: read it settled.
    await settleTransitions(field);
    const ring = await field.evaluate((el) => {
      const style = getComputedStyle(el);
      return {
        matchesFocusVisible: el.matches(':focus-visible'),
        outlineStyle: style.outlineStyle,
        outlineWidth: style.outlineWidth,
        outlineColor: style.outlineColor,
        token: style.getPropertyValue('--focus-ring-color').trim(),
      };
    });
    expect(ring.matchesFocusVisible).toBe(true);
    expect(ring.outlineStyle, 'the rule must not switch the global ring off').toBe('solid');
    expect(ring.outlineWidth).toBe('2px');
    expect(ring.outlineColor).toBe(await asComputedRgb(page, ring.token));
    // The ring is drawn outside the field, on its container's surface.
    const surface = await renderedColors(field.locator('xpath=..'));
    const ratio = contrastRatio(parseRgb(ring.outlineColor), surface.bg);
    expect(ratio, `${ring.outlineColor} on rgb(${surface.bg.join(', ')})`).toBeGreaterThanOrEqual(AA_UI);
  });
}

test('light: the activation-funnel Sankey node strokes the shared focus ring at 3:1', async ({ app, page }) => {
  // `outline` is unreliable on SVG <g>, so the node's ring is its bar's
  // stroke; the global :focus-visible ring never reaches it. The Sankey is
  // the Executive view's "Activation funnel" panel.
  await app.setTheme('light');
  await app.gotoRoute('/analytics');
  const node = page.locator('#main-content .funnel-sankey__node').first();
  await expect(node).toBeVisible();

  await page.keyboard.press('Tab');
  await node.focus();
  await settleTransitions(node.locator('.funnel-sankey__bar'));
  const ring = await node.evaluate((el) => {
    const bar = el.querySelector('.funnel-sankey__bar');
    if (!bar) throw new Error('.funnel-sankey__node has no .funnel-sankey__bar');
    const style = getComputedStyle(bar);
    return {
      matchesFocusVisible: el.matches(':focus-visible'),
      stroke: style.stroke,
      strokeWidth: style.strokeWidth,
      token: style.getPropertyValue('--focus-ring-color').trim(),
    };
  });
  expect(ring.matchesFocusVisible).toBe(true);
  expect(ring.strokeWidth).toBe('2px');
  expect(ring.stroke, 'strokes the resolved --focus-ring-color').toBe(await asComputedRgb(page, ring.token));
  const surface = await renderedColors(page.locator('#main-content svg.funnel-sankey').first());
  const ratio = contrastRatio(parseRgb(ring.stroke), surface.bg);
  expect(ratio, `${ring.stroke} on rgb(${surface.bg.join(', ')}) = ${ratio.toFixed(2)}:1`).toBeGreaterThanOrEqual(AA_UI);
});
