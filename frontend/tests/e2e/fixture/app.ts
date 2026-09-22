/**
 * App driver for fixture specs: navigation that waits for the route to
 * settle, theme selection through the app's own storage key, and the shell
 * interactions later lanes assert against (Console, Genie, command palette,
 * expanded lead row).
 *
 * Selectors are the accessible names and prototype BEM classes the app
 * already ships; nothing here adds a test-only hook to `frontend/src`.
 */
import { expect, type Locator, type Page } from '@playwright/test';
import type { DegradeOptions, MockApi } from './mockApi';

export type FixtureTheme = 'dark' | 'light';

/** Surfaces the app renders when something is wrong. Healthy routes show none. */
export const ERROR_SURFACE_SELECTOR = [
  '.degraded-banner',
  '.warming-block',
  '.status-callout--danger',
  '[role="alert"]',
].join(', ');

const THEME_STORAGE_KEY = 'mip.theme';
const THEME_SEED_MARKER = 'mip.fixture.themeSeed';
const QUIET_WINDOW_MS = 300;

interface SettleState {
  heading: boolean;
  busy: number;
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
    await this.page.addInitScript(
      ([key, value, marker]) => {
        try {
          if (window.sessionStorage.getItem(marker) === value) return;
          window.localStorage.setItem(key, value);
          window.sessionStorage.setItem(marker, value);
        } catch {
          // Storage is unavailable on about:blank; the next document seeds it.
        }
      },
      [THEME_STORAGE_KEY, theme, THEME_SEED_MARKER] as const,
    );
  }

  /** Navigate and wait until the route has finished loading its data. */
  async gotoRoute(path: string, options: { timeoutMs?: number } = {}): Promise<void> {
    await this.page.goto(path, { waitUntil: 'domcontentloaded' });
    await this.settle(options.timeoutMs);
  }

  /**
   * Settled means: an `<h1>` is rendered in `<main>`, no `aria-busy` region
   * remains inside it, the mock API has no request in flight and has been
   * quiet for a short window, and webfonts are ready. Polls instead of
   * sleeping so it is as fast as the machine allows and still holds under load.
   */
  async settle(timeoutMs = 30_000): Promise<void> {
    const deadline = Date.now() + timeoutMs;
    let state: SettleState = { heading: false, busy: -1 };
    while (Date.now() < deadline) {
      state = await this.page
        .evaluate(() => {
          const main = document.querySelector('#main-content');
          return {
            heading: Boolean(main?.querySelector('h1')),
            busy: main ? main.querySelectorAll('[aria-busy="true"]').length : -1,
          };
        })
        .catch(() => ({ heading: false, busy: -1 }));
      const quiet = this.mockApi.inflight === 0 && this.mockApi.idleMs >= QUIET_WINDOW_MS;
      if (state.heading && state.busy === 0 && quiet) {
        await this.page.evaluate(() => document.fonts.ready.then(() => undefined));
        return;
      }
      await this.page.waitForTimeout(100);
    }
    throw new Error(
      `Route did not settle within ${timeoutMs} ms at ${this.page.url()}: ` +
        `h1 rendered=${state.heading}, aria-busy regions in <main>=${state.busy}, ` +
        `API requests in flight=${this.mockApi.inflight}.`,
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

  /** The evidence drawer (`aside.drawer`), whether or not it is open. */
  evidenceDrawer(): Locator {
    return this.page.locator('aside.drawer[role="dialog"]');
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
