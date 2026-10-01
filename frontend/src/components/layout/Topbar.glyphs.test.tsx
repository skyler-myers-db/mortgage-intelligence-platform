/**
 * @vitest-environment happy-dom
 *
 * Topbar controls and glyphs (2026-09-30 rulings; harness copied from
 * Topbar.status.test.tsx):
 *  - the theme toggle (D-theme-nav-a) only asks the provider for the other
 *    theme: AppContext's setTheme stores the pick with its choice marker and
 *    paints it, so the button itself never writes data-theme or storage;
 *  - one glyph, one meaning (D-theme-nav-e, shell-06 / critic-12): the only
 *    topbar icon button that draws a navigation destination's glyph is the
 *    Genie toggle, and it draws the Ask Genie one (Topbar.tsx, routeMeta).
 *    The account trigger has its own suite (IdentityMenu.test.tsx).
 */
import { act } from 'react';
import { createRoot } from 'react-dom/client';
import { MemoryRouter } from 'react-router';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { installLocalStorage } from '../../test/installLocalStorage';
import { mount } from '../../test/render';

const app = vi.hoisted(() => ({
  theme: 'dark' as 'dark' | 'light',
  setTheme: vi.fn(),
}));

vi.mock('../../lib/api', async (importOriginal) => ({
  ...(await importOriginal<typeof import('../../lib/api')>()),
  api: { borrowerSearch: vi.fn().mockResolvedValue([]) },
}));
vi.mock('../AppContext', () => ({
  useApp: () => ({
    lender: 'Summit Mortgage',
    theme: app.theme,
    setTheme: app.setTheme,
    genieOpen: false,
    setGenieOpen: vi.fn(),
    consoleOpen: false,
    setConsoleOpen: vi.fn(),
  }),
}));
vi.mock('../HealthProvider', () => ({
  useHealth: () => ({ health: null, connection: 'online', warehouseResumingSince: null }),
}));
vi.mock('../FootprintProvider', () => ({ useFootprint: () => ({ usingFallback: false }) }));
vi.mock('./IdentityMenu', () => ({ IdentityMenu: () => null }));

import { NAVIGATION_ROUTE_IDS, ROUTES, type NavigationRouteId } from '../../lib/routeMeta';
import { Icon, type IconName } from '../Icon';
import { Topbar } from './Topbar';

/** The DOM markup of one glyph at the topbar's size. */
function glyphMarkup(name: IconName): string {
  const scratch = document.createElement('div');
  const root = createRoot(scratch);
  act(() => root.render(<Icon name={name} size={15} />));
  const html = scratch.innerHTML;
  act(() => root.unmount());
  return html;
}

describe('the topbar theme toggle', () => {
  beforeEach(() => {
    installLocalStorage();
    document.documentElement.removeAttribute('data-theme');
  });

  afterEach(() => {
    app.theme = 'dark';
    app.setTheme.mockReset();
  });

  for (const [painted, next] of [['dark', 'light'], ['light', 'dark']] as const) {
    it(`asks the provider for ${next} when ${painted} is painted, and writes nothing itself`, async () => {
      app.theme = painted;
      const { container } = await mount(<MemoryRouter><Topbar /></MemoryRouter>);
      const toggle = container.querySelector<HTMLButtonElement>('.topbar__icon-btn[aria-label="Toggle theme"]');
      expect(toggle).not.toBeNull();
      toggle!.click();
      expect(app.setTheme).toHaveBeenCalledTimes(1);
      expect(app.setTheme).toHaveBeenCalledWith(next);
      expect(document.documentElement.hasAttribute('data-theme')).toBe(false);
      expect(window.localStorage.length).toBe(0);
    });
  }
});

describe('topbar glyphs', () => {
  afterEach(() => {
    app.theme = 'dark';
  });

  for (const painted of ['dark', 'light'] as const) {
    it(`only the Genie toggle draws a navigation glyph, the Ask Genie one (${painted})`, async () => {
      app.theme = painted;
      const navigation = new Map<string, NavigationRouteId>(
        NAVIGATION_ROUTE_IDS.map((id) => [glyphMarkup(ROUTES[id].icon), id]),
      );
      const { container } = await mount(<MemoryRouter><Topbar /></MemoryRouter>);
      const buttons = [...container.querySelectorAll('.topbar__icon-btn')];
      expect(buttons.length).toBeGreaterThanOrEqual(3);
      const drawn = buttons.flatMap((button) => {
        const id = navigation.get(button.querySelector('svg')?.outerHTML ?? '');
        return id ? [[button.getAttribute('aria-label'), id]] : [];
      });
      expect(drawn).toEqual([['Toggle Genie chat', 'askGenie']]);
      expect(ROUTES.askGenie.icon).toBe('sparkle');
    });
  }
});
