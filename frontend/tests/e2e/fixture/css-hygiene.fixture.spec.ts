/**
 * Rendered-layer proofs for the wave-2 design-system CSS hygiene lane
 * (2026-09-21 audit motion-04, motion-05, css-09, motion-09, a11y-01,
 * css-06 and the wave-1c follow-up #14). The OS contrast modes are proven in
 * css-hygiene.modes.fixture.spec.ts.
 *
 * The harness runs with `prefers-reduced-motion: reduce`; the first describe
 * opts into real motion. No test opens the proof drawer, drafts outreach or
 * reads a borrower dossier: class probes stand in where a state is only
 * reachable through an audited read.
 */
import type { Locator, Page } from '@playwright/test';
import { PRIMARY_ASSET_KEY } from './data/dataEstate';
import { asComputedRgb, contrastRatio, parseRgb, renderedColors, settleTransitions, tokenValue } from './renderedColor';
import { expect, test, type FixtureTheme } from './test';

const THEMES: readonly FixtureTheme[] = ['dark', 'light'];

/** A CSS time (`0.04s`, `110ms`) in milliseconds. */
function ms(value: string): number {
  const text = value.trim();
  return text.endsWith('ms') ? Number(text.slice(0, -2)) : Number(text.slice(0, -1)) * 1000;
}

/** A px custom property on :root, as a number. */
async function rootPx(page: Page, name: string): Promise<number> {
  return Number.parseFloat(await tokenValue(page.locator('html'), name));
}

/**
 * Record every CSS transition the element (or one of its pseudo-elements)
 * starts from now on, by its `transitionrun` event. An 80-120 ms transition
 * can finish before a poll of getAnimations() first runs on a loaded host;
 * the event is dispatched however short the transition was. Returns a reader
 * of `propertyName` values for the given pseudo-element ('' is the element).
 */
async function recordTransitions(target: Locator): Promise<(pseudo?: string) => Promise<string[]>> {
  const key = `__cssHygieneRuns${Math.random().toString(36).slice(2)}`;
  await target.evaluate((el, name) => {
    const runs: Array<{ property: string; pseudo: string }> = [];
    (window as unknown as Record<string, unknown>)[name] = runs;
    el.addEventListener('transitionrun', (event) => {
      const run = event as TransitionEvent;
      if (run.target === el) runs.push({ property: run.propertyName, pseudo: run.pseudoElement });
    });
  }, key);
  return (pseudo = '') =>
    target.evaluate(
      (_el, [name, pseudoElement]) =>
        ((window as unknown as Record<string, Array<{ property: string; pseudo: string }>>)[name] ?? [])
          .filter((run) => run.pseudo === pseudoElement)
          .map((run) => run.property),
      [key, pseudo] as const,
    );
}

test.describe('with motion allowed', () => {
  // Real motion animates continuously; skip the per-frame trace screencast.
  test.use({ contextOptions: { reducedMotion: 'no-preference' }, traceScreenshots: false });

  test('opening the Console snaps the .main gutter in the first frame; nothing tweens padding-right', async ({ app, page }) => {
    await app.gotoRoute('/');
    const toggle = page.getByRole('banner').getByRole('button', { name: 'Toggle console' });
    const first = await toggle.evaluate(async (button) => {
      const main = document.querySelector<HTMLElement>('.main');
      if (!main) throw new Error('no .main');
      const opened = new Promise<{ padding: string; tweens: number }>((resolve) => {
        const read = () => ({
          padding: getComputedStyle(main).paddingRight,
          tweens: main.getAnimations().filter((a) => a instanceof CSSTransition && a.transitionProperty === 'padding-right').length,
        });
        const observer = new MutationObserver(() => {
          if (!document.querySelector('[data-console="open"]')) return;
          observer.disconnect();
          resolve(read());
        });
        observer.observe(document.documentElement, { attributes: true, subtree: true, attributeFilter: ['data-console'] });
      });
      (button as HTMLButtonElement).click();
      const atOpen = await opened;
      await new Promise((resolve) => requestAnimationFrame(() => resolve(null)));
      return { atOpen, frameLater: getComputedStyle(main).paddingRight };
    });
    const gutter = (await rootPx(page, '--console-w')) + (await rootPx(page, '--sp-6'));
    expect(gutter, 'precondition: the 1440x900 gutter').toBe(324);
    expect(first.atOpen.padding, 'the gutter is in place the moment the Console opens').toBe(`${gutter}px`);
    expect(first.atOpen.tweens, 'no padding-right transition').toBe(0);
    expect(first.frameLater).toBe(`${gutter}px`);
    await app.openConsole();
    expect(await page.locator('.main').evaluate((main) => main.getAnimations().map((a) => (a as CSSTransition).transitionProperty))).not.toContain(
      'padding-right',
    );
  });

  test('a held .btn runs a translate transition, and no former `transition: all` site transitions all', async ({ app, page }) => {
    await app.gotoRoute('/');
    const button = page.locator('#main-content .btn:not([disabled])').first();
    await expect(button).toBeVisible();
    const box = await button.boundingBox();
    if (!box) throw new Error('the button has no box');
    const runs = await recordTransitions(button);
    await page.mouse.move(box.x + box.width / 2, box.y + box.height / 2);
    await page.mouse.down();
    await expect.poll(() => runs()).toContain('translate');
    // Never released over the button, so nothing is clicked.

    const sites: Array<[string, Locator]> = [
      ['.topbar__icon-btn', page.locator('.topbar__icon-btn').first()],
      ['.rail__item', page.locator('.rail__item').first()],
      ['.btn', button],
      ['.evidence-chip', page.locator('.kpi .evidence-chip').first()],
      ['.kpi', page.locator('.kpi').first()],
      ['.filter', page.locator('.route-nav .filter').first()],
    ];
    for (const [name, site] of sites) {
      const property = await site.evaluate((el) => getComputedStyle(el).transitionProperty);
      expect(property, name).not.toMatch(/(^|,\s*)all(,|$)/);
      expect(property, `${name} names what it changes`).toMatch(/border-color/);
    }
    await page.mouse.up();
  });

  test('the remaining sites (segment card, swatch, switch knob) name their properties too', async ({ app, page }) => {
    await app.gotoRoute('/segment-intelligence');
    const card = page.locator('.seg-grid .seg-card').first();
    await expect(card).toBeVisible();
    const cardProperty = await card.evaluate((el) => getComputedStyle(el).transitionProperty);
    expect(cardProperty).toBe('background-color, border-color, box-shadow, transform, scale');
    const consolePanel = await app.openConsole();
    const swatch = consolePanel.locator('.tweak-row .sw').first();
    expect(await swatch.evaluate((el) => getComputedStyle(el).transitionProperty)).toBe('border-color, scale');
    const knob = consolePanel.getByRole('button', { name: 'Toggle signal strength meters' });
    expect(await knob.evaluate((el) => getComputedStyle(el, '::after').transitionProperty)).toBe('translate, background-color');
  });

  test('toggling a Console switch slides the knob by translate, never left', async ({ app }) => {
    await app.gotoRoute('/');
    const consolePanel = await app.openConsole();
    const control = consolePanel.getByRole('button', { name: 'Toggle signal strength meters' });
    const knob = () => control.evaluate((el) => {
      const style = getComputedStyle(el, '::after');
      return { translate: style.translate, left: style.left };
    });
    const before = await knob();
    const runs = await recordTransitions(control);
    await control.click();
    await expect.poll(() => runs('::after')).toContain('translate');
    // A `left` transition would have started in the same style change.
    expect(await runs('::after')).not.toContain('left');
    expect(['16px', 'none'], 'precondition: the knob rests at one end').toContain(before.translate);
    const end = before.translate === 'none' ? '16px' : 'none';
    // Mid-slide values are fractions of 16px; wait for the far end.
    await expect.poll(async () => (await knob()).translate).toBe(end);
    expect((await knob()).left, 'the knob never moves by left').toBe(before.left);
  });

  test('hovering a KPI card changes only its border: no lift, no glow', async ({ app, page }) => {
    await app.gotoRoute('/');
    const kpi = page.locator('.kpi').first();
    const read = () => kpi.evaluate((el) => {
      const style = getComputedStyle(el);
      return { transform: style.transform, boxShadow: style.boxShadow, borderColor: style.borderTopColor };
    });
    const before = await read();
    await kpi.locator('.kpi__label').hover();
    await expect.poll(async () => (await read()).borderColor).not.toBe(before.borderColor);
    await settleTransitions(kpi);
    const after = await read();
    expect(after.transform).toBe('none');
    expect(after.boxShadow).toBe(before.boxShadow);
    expect(after.borderColor).toBe(await asComputedRgb(page, await tokenValue(kpi, '--line-2')));
  });

  test('the activation-funnel ribbons stagger at 40 + 70*i ms', async ({ app, page }) => {
    await app.gotoRoute('/analytics');
    const ribbons = page.locator('#main-content .funnel-sankey--enter .funnel-sankey__ribbon');
    await expect(ribbons.first()).toBeAttached();
    const delays = await ribbons.evaluateAll((paths) =>
      paths.map((path) => ({ delay: getComputedStyle(path).animationDelay, index: getComputedStyle(path).getPropertyValue('--ribbon-i').trim() })),
    );
    expect(delays.length, 'precondition: several ribbons').toBeGreaterThanOrEqual(4);
    delays.forEach((ribbon, index) => {
      expect(ribbon.index).toBe(String(index));
      expect(ms(ribbon.delay), `ribbon ${index}`).toBeCloseTo(40 + 70 * index, 3);
    });
  });

  test('a ZIP tile settles in after tile-i x 16 ms', async ({ app, page }) => {
    await app.gotoRoute('/?geo_state=IL');
    const tiles = page.locator('button.zip-tile');
    await expect(tiles.first()).toBeVisible();
    const delays = await tiles.evaluateAll((buttons) =>
      buttons.map((button) => ({ delay: getComputedStyle(button).animationDelay, index: Number(getComputedStyle(button).getPropertyValue('--tile-i')) })),
    );
    expect(delays.length).toBeGreaterThan(2);
    expect(new Set(delays.map((tile) => tile.index)).size, 'precondition: tiles carry distinct indexes').toBeGreaterThan(2);
    for (const tile of delays) expect(ms(tile.delay), `tile ${tile.index}`).toBeCloseTo(tile.index * 16, 3);
  });
});

test.describe('hover truth (motion-09)', () => {
  test('the asset schema rows are static: default cursor and no hover fill', async ({ app, page }) => {
    await app.gotoRoute(`/data-estate/assets/${PRIMARY_ASSET_KEY}`);
    const row = page.locator('table.tbl.tbl--static tbody tr').first();
    await expect(row).toBeVisible();
    const read = () => row.evaluate((el) => ({ cursor: getComputedStyle(el).cursor, background: getComputedStyle(el).backgroundColor }));
    const before = await read();
    await row.hover();
    await settleTransitions(row);
    const after = await read();
    expect(before.cursor).toBe('default');
    expect(after).toEqual(before);
  });

  test('an expanded detail row keeps its own fill; a hovered proof tab differs from the selected one', async ({ app, page }) => {
    await app.gotoRoute('/');
    // Class probes: the proof drawer is an audited borrower read, so the
    // tabs are rendered here from the same classes instead.
    await page.locator('#main-content').evaluate((main) => {
      const host = document.createElement('div');
      host.id = 'css-hygiene-probes';
      host.innerHTML =
        '<table class="tbl"><tbody><tr class="probe-row"><td>ranked</td></tr><tr class="tbl__expand"><td>detail</td></tr></tbody></table>' +
        '<div class="proof-tabs"><button type="button" class="proof-tab is-active">Score</button><button type="button" class="proof-tab">Offer</button></div>';
      main.prepend(host);
    });
    const expand = page.locator('#css-hygiene-probes tr.tbl__expand');
    const ranked = page.locator('#css-hygiene-probes tr.probe-row');
    const bg1 = await asComputedRgb(page, await tokenValue(expand, '--bg-1'));
    await expand.hover();
    await settleTransitions(expand);
    expect(await expand.evaluate((el) => getComputedStyle(el).cursor)).toBe('default');
    expect(await expand.evaluate((el) => getComputedStyle(el).backgroundColor)).toBe(bg1);
    expect(await ranked.evaluate((el) => getComputedStyle(el).cursor), 'ranked rows keep the prototype pointer').toBe('pointer');

    const active = page.locator('#css-hygiene-probes .proof-tab.is-active');
    const idle = page.locator('#css-hygiene-probes .proof-tab:not(.is-active)');
    const paint = (tab: Locator) => tab.evaluate((el) => ({ background: getComputedStyle(el).backgroundColor, border: getComputedStyle(el).borderTopColor }));
    await idle.hover();
    await settleTransitions(idle);
    const hovered = await paint(idle);
    const selected = await paint(active);
    expect(hovered.background).not.toBe(selected.background);
    expect(hovered.border).not.toBe(selected.border);
    await active.hover();
    expect(await paint(active), 'hovering the selected tab keeps it selected').toEqual(selected);
  });
});

/** Colour and contrast of one element against what is really painted behind it. */
async function ink(target: Locator): Promise<{ color: string; ratio: number }> {
  const painted = await renderedColors(target);
  return { color: painted.color, ratio: contrastRatio(painted.fg, painted.bg) };
}

test.describe('legible metadata and a ringed palette cursor (a11y-01)', () => {
  for (const theme of THEMES) {
    test(`${theme}: step meta, palette placeholder and gated count paint --text-3 at AA`, async ({ app, page }) => {
      await app.setTheme(theme);
      await app.gotoRoute('/segment-intelligence');
      const card = page.locator('.seg-grid .seg-card').first();
      await expect(card).toBeVisible();
      // No fixture state renders a Growth Agent run step or a gated card by
      // default, so both are probes inside a real card (its real surface).
      await card.evaluate((el) => {
        const meta = document.createElement('div');
        meta.className = 'growth-agent-step__meta';
        meta.textContent = 'fixture step metadata';
        const count = document.createElement('div');
        count.className = 'seg-card__count seg-card__count--gated';
        count.textContent = '—';
        el.append(meta, count);
      });
      const text3 = await asComputedRgb(page, await tokenValue(card, '--text-3'));
      for (const probe of ['.growth-agent-step__meta', '.seg-card__count--gated']) {
        const painted = await ink(card.locator(probe));
        expect(painted.color, probe).toBe(text3);
        expect(painted.ratio, `${probe}: ${painted.ratio.toFixed(2)}:1`).toBeGreaterThanOrEqual(4.5);
      }

      const palette = await app.openCommandPalette();
      const input = palette.locator('.cmdk__input');
      const placeholder = await input.evaluate((el) => getComputedStyle(el, '::placeholder').color);
      expect(placeholder).toBe(text3);
      const panel = await renderedColors(palette);
      const ratio = contrastRatio(parseRgb(placeholder), panel.bg);
      expect(ratio, `placeholder ${ratio.toFixed(2)}:1`).toBeGreaterThanOrEqual(4.5);
    });
  }

  test('ArrowDown in the palette moves a ringed cursor (3:1), with no search call', async ({ app, page }) => {
    await app.gotoRoute('/');
    const palette = await app.openCommandPalette();
    const input = palette.locator('.cmdk__input');
    await expect(input).toBeFocused();
    await page.keyboard.press('ArrowDown');
    const row = palette.locator('.cmdk__row.is-active');
    await expect(row).toHaveCount(1);
    await settleTransitions(row);
    const ring = await row.evaluate((el) => {
      const style = getComputedStyle(el);
      return { style: style.outlineStyle, width: style.outlineWidth, color: style.outlineColor, token: style.getPropertyValue('--focus-ring-color').trim() };
    });
    expect(ring.style).toBe('solid');
    expect(ring.width).toBe(await tokenValue(row, '--focus-ring-width'));
    expect(ring.color).toBe(await asComputedRgb(page, ring.token));
    const panel = await renderedColors(palette);
    expect(contrastRatio(parseRgb(ring.color), panel.bg)).toBeGreaterThanOrEqual(3);
  });
});

/** The outline of a focused element: style, width, colour, and its painted rectangle. */
async function outlineOf(target: Locator) {
  await settleTransitions(target);
  return target.evaluate((el) => {
    const style = getComputedStyle(el);
    const rect = el.getBoundingClientRect();
    const reach = Number.parseFloat(style.outlineOffset) + Number.parseFloat(style.outlineWidth);
    return {
      focusVisible: el.matches(':focus-visible'),
      style: style.outlineStyle,
      width: style.outlineWidth,
      color: style.outlineColor,
      token: style.getPropertyValue('--focus-ring-color').trim(),
      ring: { left: rect.left - reach, top: rect.top - reach, right: rect.right + reach, bottom: rect.bottom + reach },
    };
  });
}

test.describe('focus rings on the remaining inputs (css-06)', () => {
  test('the palette input and the Genie resize handle show the shared ring inside their panel', async ({ app, page }) => {
    await app.gotoRoute('/');
    const palette = await app.openCommandPalette();
    const input = palette.locator('.cmdk__input');
    await expect(input).toBeFocused();
    const inputRing = await outlineOf(input);
    const width = await tokenValue(input, '--focus-ring-width');
    expect(inputRing).toMatchObject({ focusVisible: true, style: 'solid', width });
    expect(inputRing.color).toBe(await asComputedRgb(page, inputRing.token));
    const panelBox = await palette.boundingBox();
    if (!panelBox) throw new Error('no palette panel');
    expect(inputRing.ring.left).toBeGreaterThanOrEqual(panelBox.x);
    expect(inputRing.ring.right).toBeLessThanOrEqual(panelBox.x + panelBox.width);
    await page.keyboard.press('Escape');
    await expect(palette).toBeHidden();

    const genie = await app.openGenie();
    const handle = genie.getByRole('button', { name: /^Resize Genie panel/ });
    await page.keyboard.press('Tab');
    await handle.focus();
    const handleRing = await outlineOf(handle);
    expect(handleRing).toMatchObject({ focusVisible: true, style: 'solid', width });
    expect(handleRing.color).toBe(await asComputedRgb(page, handleRing.token));
    const genieBox = await app.geniePanel().boundingBox();
    const handleBox = await handle.boundingBox();
    if (!genieBox || !handleBox) throw new Error('no Genie panel box');
    // The ring is inset, so it is the handle's own box: the part inside the
    // (overflow: hidden) panel is painted, with its inner edges on the panel.
    expect(handleRing.ring.right).toBeCloseTo(handleBox.x + handleBox.width, 0);
    expect(handleRing.ring.bottom).toBeCloseTo(handleBox.y + handleBox.height, 0);
    expect(handleRing.ring.right).toBeGreaterThan(genieBox.x);
    expect(handleRing.ring.right).toBeLessThanOrEqual(genieBox.x + genieBox.width);
    expect(handleRing.ring.bottom).toBeGreaterThan(genieBox.y);
    expect(handleRing.ring.bottom).toBeLessThanOrEqual(genieBox.y + genieBox.height);
  });

  test('the Admin Config appearance switches are styled: 40x24 and a knob that slides', async ({ app, page }) => {
    await app.gotoRoute('/admin-config');
    await page.getByRole('button', { name: /Workspace appearance/ }).click();
    const evidence = page.locator('.admin-row').getByRole('button', { name: 'Toggle evidence chips' });
    await expect(evidence).toBeVisible();
    await expect(evidence).toHaveClass(/(^|\s)switch(\s|$)/);
    const box = await evidence.boundingBox();
    if (!box) throw new Error('no switch box');
    expect(box.width).toBeGreaterThanOrEqual(40);
    expect(box.height).toBeGreaterThanOrEqual(24);
    const knob = () => evidence.evaluate((el) => getComputedStyle(el, '::after').translate);
    const before = await knob();
    expect(['16px', 'none'], 'precondition: the knob rests at one end').toContain(before);
    await evidence.click();
    await expect.poll(knob).toBe(before === 'none' ? '16px' : 'none');
  });
});

test.describe('stability primitives (css-09)', () => {
  test('.main reserves its gutter; the overlay scrollers contain overscroll; titles balance', async ({ app, page }) => {
    await app.gotoRoute('/');
    const style = (target: Locator, property: 'scrollbarGutter' | 'overscrollBehaviorY' | 'textWrapStyle') =>
      target.evaluate((el, name) => getComputedStyle(el)[name], property);
    expect(await style(page.locator('.main'), 'scrollbarGutter')).toBe('stable');
    expect(await style(page.locator('#main-content .proto-hero h1'), 'textWrapStyle')).toBe('balance');
    expect(await style(page.locator('#main-content .proto-hero .lede').first(), 'textWrapStyle')).toBe('pretty');

    const drawer = await app.openEvidenceDrawer();
    expect(await style(drawer.locator('.drawer__body'), 'overscrollBehaviorY')).toBe('contain');
    await drawer.getByRole('button', { name: 'Close drawer' }).click();

    const consolePanel = await app.openConsole();
    expect(await style(consolePanel.locator('.tweaks__body'), 'overscrollBehaviorY')).toBe('contain');
    const genie = await app.openGenie();
    expect(await style(genie.locator('.genie__body'), 'overscrollBehaviorY')).toBe('contain');
    const palette = await app.openCommandPalette();
    expect(await style(palette.locator('.cmdk__list'), 'overscrollBehaviorY')).toBe('contain');
  });

  test('a filter menu contains overscroll and segment titles balance', async ({ app, page }) => {
    await app.gotoRoute('/lead-queue');
    const menu = await app.openFilterMenu('STATE');
    expect(await menu.evaluate((el) => getComputedStyle(el).overscrollBehaviorY)).toBe('contain');
    await page.keyboard.press('Escape');
    await app.gotoRoute('/segment-intelligence');
    const title = page.locator('.seg-grid .seg-card__title').first();
    await expect(title).toBeVisible();
    expect(await title.evaluate((el) => getComputedStyle(el).textWrapStyle)).toBe('balance');
  });
});

/**
 * Follow-up #14: at 400% zoom a 1280x1024 screen is a 320x256 CSS viewport.
 * The wrapped route nav docked at the top of `.main` there covered the whole
 * scrollport. Each target must be hit-testable (the topmost element at some
 * point on it) at some scroll position, scanning `.main` in 16px steps.
 */
async function hitTestableWhileScrolling(page: Page, targets: Locator[]): Promise<boolean[]> {
  const handles = await Promise.all(targets.map((target) => target.elementHandle()));
  return page.evaluate((elements) => {
    const main = document.querySelector<HTMLElement>('.main');
    if (!main) throw new Error('no .main');
    const seen = elements.map(() => false);
    const max = main.scrollHeight - main.clientHeight;
    for (let top = 0; ; top = Math.min(top + 16, max)) {
      main.scrollTo({ top, behavior: 'instant' });
      elements.forEach((element, index) => {
        if (!element || seen[index]) return;
        const rect = element.getBoundingClientRect();
        for (let y = rect.top + 4; y < rect.bottom; y += 8) {
          const hit = document.elementFromPoint(rect.left + Math.min(rect.width / 2, 40), y);
          if (hit && (hit === element || element.contains(hit))) {
            seen[index] = true;
            break;
          }
        }
      });
      if (top >= max) break;
    }
    main.scrollTo({ top: 0, behavior: 'instant' });
    return seen;
  }, handles);
}

test.describe('route nav docks only with vertical room (follow-up #14)', () => {
  for (const path of ['/', '/lead-queue']) {
    test(`320x256 (400% zoom) on ${path}: the nav is in flow and the heading and first panel can be reached`, async ({ app, page }) => {
      await page.setViewportSize({ width: 320, height: 256 });
      await app.gotoRoute(path);
      expect(await page.locator('.route-nav').evaluate((el) => getComputedStyle(el).position)).toBe('static');
      const heading = page.locator('#main-content h1').first();
      const surface = page.locator('#main-content .surface').first();
      await expect(surface).toBeAttached();
      expect(await hitTestableWhileScrolling(page, [heading, surface])).toEqual([true, true]);
    });
  }

  for (const [width, height] of [[1440, 900], [1366, 768]] as const) {
    test(`${width}x${height}: the nav stays docked`, async ({ app, page }) => {
      await page.setViewportSize({ width, height });
      await app.gotoRoute('/lead-queue');
      expect(await page.locator('.route-nav').evaluate((el) => getComputedStyle(el).position)).toBe('sticky');
    });
  }
});
