/**
 * @vitest-environment happy-dom
 *
 * Topbar controls and glyphs (2026-09-30 rulings; harness copied from
 * Topbar.status.test.tsx):
 *  - the theme toggle (D-theme-nav-a) only asks the provider for the other
 *    theme: AppContext's setTheme stores the pick with its choice marker and
 *    paints it, so the button itself never writes data-theme or storage.
 */
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

import { Topbar } from './Topbar';

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
