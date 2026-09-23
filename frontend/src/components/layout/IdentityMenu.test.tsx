/**
 * @vitest-environment happy-dom
 *
 * Topbar identity menu (audit 2026-09-21 shell-06): a WAI-ARIA menu button
 * that names the signed-in actor from /api/session and offers appearance,
 * keyboard shortcuts and the glossary. Rendered and driven from the keyboard
 * here; the fixture spec (shell-wayfinding.fixture.spec.ts) proves the popup
 * geometry in the built app.
 */

import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { act } from 'react';
import { createRoot, type Root } from 'react-dom/client';
import { MemoryRouter, Route, Routes } from 'react-router';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import type { SessionResponse } from '../../types';

(globalThis as unknown as { IS_REACT_ACT_ENVIRONMENT: boolean }).IS_REACT_ACT_ENVIRONMENT = true;

const { appContext } = vi.hoisted(() => ({
  appContext: {
    themePreference: 'system',
    setThemePreference: vi.fn(),
  },
}));

vi.mock('../AppContext', () => ({
  useApp: () => appContext,
}));

import { IdentityMenu, OPEN_SHORTCUTS_EVENT, identityView } from './IdentityMenu';

const SESSION: SessionResponse = {
  can_access_admin: false,
  can_approve: true,
  actor_email: 'jane.doe@summit-mortgage.example',
  actor_display_name: 'Jane Doe',
  role_labels: ['Approver'],
};

describe('IdentityMenu', () => {
  let container: HTMLDivElement;
  let root: Root;

  async function renderMenu(session: SessionResponse = SESSION) {
    const queryClient = new QueryClient({ defaultOptions: { queries: { retry: false, staleTime: Infinity } } });
    queryClient.setQueryData(['session', 'access'], session);
    await act(async () => {
      root.render(
        <QueryClientProvider client={queryClient}>
          <MemoryRouter initialEntries={['/lead-queue']}>
            <Routes>
              <Route path="/lead-queue" element={<><IdentityMenu /><button type="button">after</button></>} />
              <Route path="/glossary" element={<h1>Glossary page</h1>} />
            </Routes>
          </MemoryRouter>
        </QueryClientProvider>,
      );
    });
  }

  beforeEach(() => {
    container = document.createElement('div');
    document.body.appendChild(container);
    root = createRoot(container);
  });

  afterEach(() => {
    act(() => root.unmount());
    container.remove();
    vi.clearAllMocks();
  });

  const trigger = () => container.querySelector<HTMLButtonElement>('[data-testid="identity-menu-trigger"]')!;
  const menu = () => container.querySelector<HTMLElement>('[role="menu"]');
  const items = () => Array.from(container.querySelectorAll<HTMLElement>('[role="menuitem"], [role="menuitemradio"]'));
  const key = (target: Element, name: string) =>
    act(() => {
      target.dispatchEvent(new KeyboardEvent('keydown', { key: name, bubbles: true, cancelable: true }));
    });
  /** Open from the trigger with `name` and wait for the lazy popup to mount. */
  async function openWith(name: 'ArrowDown' | 'ArrowUp') {
    trigger().focus();
    key(trigger(), name);
    for (let attempt = 0; attempt < 40 && !menu(); attempt += 1) {
      await act(async () => {
        await new Promise((resolve) => setTimeout(resolve, 5));
      });
    }
    expect(menu(), 'the lazy popup mounted').not.toBeNull();
  }

  it('names the signed-in actor on a collapsed menu button', async () => {
    await renderMenu();
    expect(trigger().getAttribute('aria-haspopup')).toBe('menu');
    expect(trigger().getAttribute('aria-expanded')).toBe('false');
    expect(trigger().getAttribute('aria-label')).toBe('Account menu, signed in as Jane Doe');
    expect(menu()).toBeNull();
  });

  it('opens on ArrowDown with focus on the first item and shows identity and role chips', async () => {
    await renderMenu();
    await openWith('ArrowDown');
    expect(trigger().getAttribute('aria-expanded')).toBe('true');
    expect(trigger().getAttribute('aria-controls')).toBe(menu()?.id);
    expect(items().map((item) => item.textContent)).toEqual(['Dark', 'Light', 'System', 'Keyboard shortcuts', 'Glossary']);
    expect(document.activeElement).toBe(items()[0]);
    const who = container.querySelector('.identity-menu__who')!;
    expect(who.textContent).toContain('Jane Doe');
    expect(who.textContent).toContain('jane.doe@summit-mortgage.example');
    expect(Array.from(who.querySelectorAll('.chip')).map((chip) => chip.textContent)).toEqual(['Approver']);
    expect(menu()?.getAttribute('aria-describedby')).toBe(who.id);
  });

  it('marks the stored theme preference and moves with the arrow, Home and End keys', async () => {
    await renderMenu();
    await openWith('ArrowUp');
    expect(document.activeElement?.textContent).toBe('Glossary');
    const radios = items().filter((item) => item.getAttribute('role') === 'menuitemradio');
    expect(radios.map((radio) => radio.getAttribute('aria-checked'))).toEqual(['false', 'false', 'true']);
    key(menu()!, 'ArrowDown');
    expect(document.activeElement?.textContent).toBe('Dark');
    key(menu()!, 'End');
    expect(document.activeElement?.textContent).toBe('Glossary');
    key(menu()!, 'Home');
    key(menu()!, 'ArrowDown');
    expect(document.activeElement?.textContent).toBe('Light');
    // Roving tabindex: exactly one item is in the Tab order.
    expect(items().filter((item) => item.tabIndex === 0).map((item) => item.textContent)).toEqual(['Light']);
  });

  it('sets the theme preference and closes onto the trigger', async () => {
    await renderMenu();
    await openWith('ArrowDown');
    act(() => items()[1].click());
    expect(appContext.setThemePreference).toHaveBeenCalledWith('light');
    expect(menu()).toBeNull();
    expect(document.activeElement).toBe(trigger());
  });

  it('closes on Escape and returns focus to the trigger', async () => {
    await renderMenu();
    await openWith('ArrowDown');
    key(document.activeElement!, 'Escape');
    expect(menu()).toBeNull();
    expect(trigger().getAttribute('aria-expanded')).toBe('false');
    expect(document.activeElement).toBe(trigger());
  });

  it('asks the shortcut overlay to open through the window event, after focus is back on the trigger', async () => {
    await renderMenu();
    const seen: Array<Element | null> = [];
    const listener = () => seen.push(document.activeElement);
    window.addEventListener(OPEN_SHORTCUTS_EVENT, listener);
    try {
      await openWith('ArrowDown');
      act(() => items()[3].click());
    } finally {
      window.removeEventListener(OPEN_SHORTCUTS_EVENT, listener);
    }
    expect(seen).toEqual([trigger()]);
    expect(menu()).toBeNull();
  });

  it('follows the Glossary link and closes', async () => {
    await renderMenu();
    await openWith('ArrowDown');
    const glossary = items()[4];
    expect(glossary.getAttribute('href')).toBe('/glossary');
    act(() => glossary.click());
    expect(container.querySelector('h1')?.textContent).toBe('Glossary page');
  });

  it('closes when focus leaves the menu (Tab)', async () => {
    await renderMenu();
    await openWith('ArrowDown');
    const after = Array.from(container.querySelectorAll('button')).find((button) => button.textContent === 'after')!;
    act(() => after.focus());
    expect(menu()).toBeNull();
  });
});

describe('identityView', () => {
  it('never invents an identity', () => {
    expect(identityView(undefined, false)).toMatchObject({ status: 'loading', email: null, roles: [] });
    expect(identityView(undefined, true)).toMatchObject({ status: 'error', name: 'Identity not verified' });
    expect(identityView({ can_access_admin: false, can_approve: false, actor_email: null }, false))
      .toEqual({ name: 'No forwarded identity', email: null, roles: [], status: 'ready' });
    expect(identityView({ can_access_admin: false, can_approve: false, actor_email: 'a@b.example' }, false).name)
      .toBe('a@b.example');
  });
});
