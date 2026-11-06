/**
 * App driver for fixture specs: navigation that waits for the route to
 * settle, theme / accent / density selection through the app's own storage
 * keys, and the shell
 * interactions later lanes assert against (Console, Genie, command palette,
 * expanded lead row).
 *
 * Selectors are the accessible names and prototype BEM classes the app
 * already ships. The one exception is settle()'s read of the committed-route
 * marker `data-route-path` (app.tsx RouteTransition): during a held
 * navigation nothing accessible says which route is painted.
 */
import { expect, type Locator, type Page } from '@playwright/test';
import { PAGE_API_PROBE, type DegradeOptions, type MockApi, type PageApiProbe } from './mockApi';

export type FixtureTheme = 'dark' | 'light';

/**
 * The accents and densities the app accepts. They mirror ACCENTS and
 * DENSITIES in frontend/public/theme-boot.js (safety-net.fixture.spec.ts
 * reads that file and fails when they drift).
 */
export const FIXTURE_ACCENTS = ['bright', 'teal', 'navy', 'red'] as const;
export type FixtureAccent = (typeof FIXTURE_ACCENTS)[number];
export const FIXTURE_DENSITIES = ['comfortable', 'compact'] as const;
export type FixtureDensity = (typeof FIXTURE_DENSITIES)[number];

/** Surfaces the app renders when something is wrong. Healthy routes show none. */
export const ERROR_SURFACE_SELECTOR = [
  '.degraded-banner',
  '.warming-block',
  '.status-callout--danger',
  '[role="alert"]',
].join(', ');

/**
 * The app's own storage key per preference, its choice marker (a seeded theme
 * or accent is a user's explicit pick: lib/themePreference only honours a
 * stored 'dark' / 'system' theme or 'bright' accent with the marker), and
 * this driver's once-per-tab seed marker.
 */
const SEEDS = {
  theme: { key: 'mip.theme', choice: 'mip.themeChosen', marker: 'mip.fixture.themeSeed' },
  accent: { key: 'mip.accent', choice: 'mip.accentChosen', marker: 'mip.fixture.accentSeed' },
  density: { key: 'mip.density', choice: null, marker: 'mip.fixture.densitySeed' },
} as const;
const QUIET_WINDOW_MS = 300;

export interface SettleState {
  heading: boolean;
  busy: number;
  /** `data-route-path` of the painted route wrapper (null when none), and the URL's pathname. */
  paintedPath: string | null;
  urlPath: string;
  /** The painted route is the URL's route, or the route error surface stands in for it. */
  painted: boolean;
  /** The document's own API fetch count (MockApi's PAGE_API_PROBE); null when the probe is missing. */
  fetches: PageApiProbe | null;
}

/** What the mock API reports at a poll: requests in its handler, and how long it has been quiet. */
export interface MockQuiet {
  inflight: number;
  idleMs: number;
}

/** The painted route, its h1, no aria-busy in <main>, and no API fetch of the page's own in flight. */
function routeReady(state: SettleState): boolean {
  return state.painted && state.heading && state.busy === 0
    && state.fetches !== null && state.fetches.started === state.fetches.settled;
}

/**
 * settle()'s verdict for one poll, given the poll before it. Pure.
 *
 * A route starts the reads it makes at mount (the Offer Orchestrator's
 * borrower, recommend and draft; TopLeadsQuickPick's GET /leads) in the
 * effects React runs AFTER the commit that paints its h1, and nothing on those
 * routes is aria-busy. So one poll can see the route painted, its h1 and no
 * aria-busy before the page has called fetch(), and a request the page has
 * called fetch() for reaches the mock's route handler only once the engine
 * hands it over (from ~100 ms to several seconds on a loaded host): in both
 * gaps the mock was quiet, often for seconds, since the shell's own boot
 * reads, which is how a route's natural load landed after markNaturalLoad
 * (main CI run 41881146873). Settled therefore needs:
 *   - the route ready on THIS poll and the one before it, the same route in
 *     the same document, with no API fetch started by the page in between
 *     (the second poll is at least 100 ms later, and the rule takes it that
 *     the mount effects have called fetch() by then; settle-natural-load's
 *     ready-poll hold pins that a read started by then is caught), and none
 *     in flight in the page (counted from the fetch() call, PAGE_API_PROBE);
 *   - the mock API with nothing in its handler and quiet for QUIET_WINDOW_MS.
 */
export function settleVerdict(previous: SettleState | null, current: SettleState, mock: MockQuiet): boolean {
  if (!previous || !routeReady(previous) || !routeReady(current)) return false;
  const sameRoute = previous.paintedPath === current.paintedPath && previous.urlPath === current.urlPath;
  const noFetchBetween = previous.fetches?.doc === current.fetches?.doc && previous.fetches?.started === current.fetches?.started;
  return sameRoute && noFetchBetween && mock.inflight === 0 && mock.idleMs >= QUIET_WINDOW_MS;
}

export class AppDriver {
  constructor(
    private readonly page: Page,
    private readonly mockApi: MockApi,
  ) {}

  /**
   * Select the theme the way a returning user has it: the app's own
   * `mip.theme` localStorage key plus a matching `prefers-color-scheme`.
   * Call before `gotoRoute`. The key is seeded once per value per tab, so a
   * theme the test later changes through the UI survives a reload.
   */
  async setTheme(theme: FixtureTheme): Promise<void> {
    await this.page.emulateMedia({ colorScheme: theme });
    await this.seed('theme', theme);
  }

  /**
   * Select the accent (`mip.accent`) the way setTheme selects the theme:
   * theme-boot.js applies it before the first paint. Call before `gotoRoute`.
   */
  async setAccent(accent: FixtureAccent): Promise<void> {
    await this.seed('accent', accent);
  }

  /** Select the density (`mip.density`); same contract as setAccent. */
  async setDensity(density: FixtureDensity): Promise<void> {
    await this.seed('density', density);
  }

  /**
   * Seed one stored preference once per value per tab: a sessionStorage
   * marker remembers the seeded value, so a preference the test later
   * changes through the UI survives a reload instead of being re-seeded.
   */
  private async seed(preference: keyof typeof SEEDS, value: string): Promise<void> {
    const { key, choice, marker } = SEEDS[preference];
    await this.page.addInitScript(
      ([storageKey, choiceKey, storedValue, seedMarker]) => {
        try {
          if (window.sessionStorage.getItem(seedMarker) === storedValue) return;
          window.localStorage.setItem(storageKey, storedValue);
          if (choiceKey) window.localStorage.setItem(choiceKey, 'true');
          window.sessionStorage.setItem(seedMarker, storedValue);
        } catch {
          // Storage is unavailable on about:blank; the next document seeds it.
        }
      },
      [key, choice, value, marker] as const,
    );
  }

  /** Navigate and wait until the route has finished loading its data. */
  async gotoRoute(path: string, options: { timeoutMs?: number } = {}): Promise<void> {
    await this.page.goto(path, { waitUntil: 'domcontentloaded' });
    await this.settle(options.timeoutMs);
  }

  /**
   * Settled means: the route painted in `<main>` is the URL's route, an `<h1>`
   * is rendered there, no `aria-busy` region remains inside it, and the page
   * has no API fetch in flight, on two consecutive polls with no API fetch
   * started between them; the mock API has no request in flight and has been
   * quiet for a short window; and webfonts are ready (settleVerdict says why
   * one poll is not enough). Polls instead of sleeping so it is as fast as the
   * machine allows and still holds under load.
   *
   * "The URL's route": an in-app navigation to a route whose chunk is still
   * loading holds the previous page (app.tsx RouteTransition, shell-05), so
   * the URL moves first while the old route stays painted with its h1 and no
   * aria-busy, and a chunk request is not API traffic. The painted route is
   * read from the app's committed-route marker, `data-route-path` on the
   * keyed `.route-transition` wrapper, and compared with `location.pathname`
   * through the URL parser so both are encoded alike. A route error surface
   * (`[data-error-boundary="route"]`, which resets on pathname) counts as the
   * URL's route.
   */
  async settle(timeoutMs = 30_000): Promise<void> {
    const deadline = Date.now() + timeoutMs;
    const unread: SettleState = { heading: false, busy: -1, paintedPath: null, urlPath: '', painted: false, fetches: null };
    let state: SettleState = unread;
    let previous: SettleState | null = null;
    while (Date.now() < deadline) {
      state = await this.page
        .evaluate((probeKey) => {
          const main = document.querySelector('#main-content');
          const paintedPath = main?.querySelector('.route-transition[data-route-path]')?.getAttribute('data-route-path') ?? null;
          const urlPath = window.location.pathname;
          const routeError = Boolean(main?.querySelector('[data-error-boundary="route"]'));
          const probe = (window as unknown as Record<string, PageApiProbe | undefined>)[probeKey];
          return {
            heading: Boolean(main?.querySelector('h1')),
            busy: main ? main.querySelectorAll('[aria-busy="true"]').length : -1,
            paintedPath,
            urlPath,
            painted: routeError || (paintedPath !== null && new URL(paintedPath, window.location.origin).pathname === urlPath),
            fetches: probe ? { doc: probe.doc, started: probe.started, settled: probe.settled } : null,
          };
        }, PAGE_API_PROBE)
        .catch(() => unread);
      if (settleVerdict(previous, state, { inflight: this.mockApi.inflight, idleMs: this.mockApi.idleMs })) {
        await this.page.evaluate(() => document.fonts.ready.then(() => undefined));
        return;
      }
      previous = state;
      await this.page.waitForTimeout(100);
    }
    const pageInFlight = state.fetches ? String(state.fetches.started - state.fetches.settled) : 'unknown (no in-page API probe)';
    throw new Error(
      `Route did not settle within ${timeoutMs} ms at ${this.page.url()}: ` +
        `route painted in <main>=${state.paintedPath ?? 'none'} (URL path ${state.urlPath}), ` +
        `h1 rendered=${state.heading}, aria-busy regions in <main>=${state.busy}, ` +
        `API fetches in flight in the page=${pageInFlight}, API requests in flight at the mock=${this.mockApi.inflight}.`,
    );
  }

  /** Opt an endpoint into an explicit degraded state (see MockApi.degrade); returns the restore function. */
  degrade(endpointPattern: string | RegExp, options: DegradeOptions): () => void {
    return this.mockApi.degrade(endpointPattern, options);
  }

  /** Open the Console right rail from the topbar and wait for its real body. */
  async openConsole(): Promise<Locator> {
    const panel = this.page.getByRole('complementary', { name: 'Workspace console' });
    if (!(await panel.locator('.tweaks__body').isVisible().catch(() => false))) {
      await this.page.getByRole('banner').getByRole('button', { name: 'Toggle console' }).click();
    }
    await expect(panel.locator('.tweaks__body'), 'Console body must replace its Suspense fallback').toBeVisible();
    return panel;
  }

  /**
   * Open the floating Genie panel. `.genie__fab` is display:none above 720px,
   * so the desktop entry point is the topbar toggle.
   */
  async openGenie(): Promise<Locator> {
    const dialog = this.page.getByRole('dialog', { name: 'Genie chat' });
    if (!(await dialog.isVisible().catch(() => false))) {
      await this.page.getByRole('banner').getByRole('button', { name: 'Toggle Genie chat' }).click();
    }
    await expect(dialog).toBeVisible();
    await expect(dialog.getByRole('textbox', { name: 'Ask Genie' })).toBeVisible();
    return dialog;
  }

  /** Open the Cmd/Ctrl-K command palette through its topbar button. */
  async openCommandPalette(): Promise<Locator> {
    const dialog = this.page.getByRole('dialog', { name: 'Command palette' });
    if (!(await dialog.isVisible().catch(() => false))) {
      await this.page.getByRole('banner').getByRole('button', { name: /^Open command palette/ }).click();
    }
    await expect(dialog).toBeVisible();
    return dialog;
  }

  /** Expand the first ranked row in the lead table; returns the expanded `<tr>`. */
  async expandFirstLeadRow(): Promise<Locator> {
    const toggle = this.page.locator('table.tbl tbody [aria-expanded]').first();
    await expect(toggle).toBeVisible();
    if ((await toggle.getAttribute('aria-expanded')) !== 'true') await toggle.click();
    await expect(toggle).toHaveAttribute('aria-expanded', 'true');
    const expanded = this.page.locator('table.tbl tbody tr.tbl__expand').first();
    await expect(expanded).toBeVisible();
    // The preview and workflow strip are a lazy chunk (LeadTableRow.expanded):
    // wait for it to land, so a one-shot read (axe, a measure) sees the real
    // preview, never its status line; a failed chunk keeps the line and fails here.
    await expect(expanded.locator('[data-testid^="lead-preview-pending-"]'), 'the expanded body landed').toHaveCount(0);
    return expanded;
  }

  /** The topbar's Genie launcher (rendered at every width; the FAB is not). */
  genieToggle(): Locator {
    return this.page.getByRole('banner').getByRole('button', { name: 'Toggle Genie chat' });
  }

  /** The floating Genie panel, whether or not it is open. */
  geniePanel(): Locator {
    return this.page.locator('.genie[role="dialog"]');
  }

  /** The evidence drawer (a native `dialog.drawer`), whether or not it is open. Borrower 360 mounts a second `.drawer.proof-drawer`. */
  evidenceDrawer(): Locator {
    return this.page.locator('dialog.drawer:not(.proof-drawer)');
  }

  /**
   * Open the evidence drawer from an evidence chip. Defaults to the first KPI
   * card's source chip on the current route. Returns the open drawer.
   */
  async openEvidenceDrawer(chip?: Locator): Promise<Locator> {
    const target = chip ?? this.page.locator('.kpi .kpi__source .evidence-chip').first();
    await expect(target).toBeVisible();
    await target.click();
    const drawer = this.evidenceDrawer();
    await expect(drawer).toHaveClass(/is-open/);
    await expect(drawer.getByRole('button', { name: 'Close drawer' })).toBeVisible();
    return drawer;
  }

  /** Open one FilterSelect / MultiFilterSelect menu by its label; returns the listbox. */
  async openFilterMenu(label: string): Promise<Locator> {
    const trigger = this.page.locator(`button[aria-haspopup="listbox"][aria-label^="${label}:"]`).first();
    await expect(trigger).toBeVisible();
    if ((await trigger.getAttribute('aria-expanded')) !== 'true') await trigger.click();
    const menu = this.page.getByRole('listbox', { name: label });
    await expect(menu).toBeVisible();
    return menu;
  }

  /**
   * Ask Genie from the floating panel. The caller registers the turn's
   * submit / progress / complete fixtures first (data/genieTurn.ts); nothing
   * answers a turn by default.
   */
  async askGenie(question: string): Promise<Locator> {
    const dialog = await this.openGenie();
    const input = dialog.getByRole('textbox', { name: 'Ask Genie' });
    await input.fill(question);
    await dialog.getByRole('button', { name: 'Ask', exact: true }).click();
    return dialog;
  }
}
