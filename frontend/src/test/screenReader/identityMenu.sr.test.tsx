// @vitest-environment happy-dom
/**
 * Virtual screen-reader pass: the Topbar identity menu (a WAI-ARIA menu
 * button; WCAG 4.1.2, 2.1.1, 2.4.3; docs/accessibility/VPAT.md).
 *
 * Driven from the keyboard as IdentityMenu.test.tsx does, with the reader
 * recording each focus move in transcripts/identity-menu.txt. Not modelled
 * (virtualReader.ts): the popup's geometry and the presence exit, which the
 * fixture spec shell-wayfinding.fixture.spec.ts proves in the built app.
 */
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { act } from 'react';
import { MemoryRouter, Route, Routes } from 'react-router';
import { beforeAll, describe, expect, it, vi } from 'vitest';
import type { SessionResponse } from '../../types';
import { mount } from '../render';
import { expectTranscript, press, registerReaderHooks, reportFocus, settle, startReader } from './virtualReader';

const { appContext } = vi.hoisted(() => ({
  appContext: { themePreference: 'system', setThemePreference: vi.fn() },
}));

vi.mock('../../components/AppContext', () => ({
  useApp: () => appContext,
}));

const apiMocks = vi.hoisted(() => ({ session: vi.fn() }));
vi.mock('../../lib/api', async (importOriginal) => ({
  ...(await importOriginal<typeof import('../../lib/api')>()),
  api: apiMocks,
}));

import { IdentityMenu } from '../../components/layout/IdentityMenu';

registerReaderHooks();

/** Synthetic actor (no real person): the fixture harness's approver shape. */
const SESSION: SessionResponse = {
  can_access_admin: false,
  can_approve: true,
  actor_email: 'jane.doe@summit-mortgage.example',
  actor_display_name: 'Jane Doe',
  role_labels: ['Approver'],
};

// The popup waits for its on-demand stylesheet: load it once up front.
beforeAll(async () => {
  await import('../../components/layout/IdentityMenuPanel.css');
});

const trigger = () => document.querySelector<HTMLButtonElement>('[data-testid="identity-menu-trigger"]')!;
const menu = () => document.querySelector<HTMLElement>('[role="menu"]');

async function renderMenu() {
  const queryClient = new QueryClient({ defaultOptions: { queries: { retry: false, staleTime: Infinity } } });
  queryClient.setQueryData(['session', 'access'], SESSION);
  await mount(
    <QueryClientProvider client={queryClient}>
      <MemoryRouter initialEntries={['/lead-queue']}>
        <Routes>
          <Route path="/lead-queue" element={<IdentityMenu />} />
        </Routes>
      </MemoryRouter>
    </QueryClientProvider>,
  );
}

/** Wait until the popup mounted (its stylesheet is a dynamic import). */
async function waitForMenu() {
  await vi.waitFor(async () => {
    await settle(1);
    expect(menu(), 'the popup mounted').not.toBeNull();
  }, { timeout: 5000, interval: 10 });
  await settle();
}

describe('virtual screen reader: identity menu', () => {
  it('names the actor on a collapsed menu button, reads each item with its state and returns to the trigger', async () => {
    await renderMenu();
    const reader = await startReader();

    const focusTrigger = await reader.step('Tab to the account menu button', async () => {
      await press('Tab');
    });
    expect(focusTrigger).toEqual(['button, Account menu, signed in as Jane Doe, not expanded, has popup menu']);

    const open = await reader.step('ArrowDown opens the menu on its first item', async () => {
      await press('ArrowDown');
      await waitForMenu();
    });
    expect(trigger().getAttribute('aria-expanded')).toBe('true');
    // The theme choices are radio items: their checked state is spoken.
    expect(open.at(-1)).toBe('menuitemradio, Dark, not checked, position 1, set size 3');

    const items = await reader.step('ArrowDown through every item', async () => {
      for (let move = 0; move < 4; move += 1) {
        await act(async () => {
          document.activeElement!.dispatchEvent(new KeyboardEvent('keydown', { key: 'ArrowDown', bubbles: true, cancelable: true }));
        });
        await settle();
      }
    });
    expect(items).toContain('menuitemradio, System, checked, position 3, set size 3');
    expect(items.some((phrase) => phrase.startsWith('menuitem, Keyboard shortcuts'))).toBe(true);
    expect(items.some((phrase) => phrase.startsWith('menuitem, Glossary'))).toBe(true);

    // The menu focuses its button in the same handler that closes it, so the
    // reader (which reads a focus target one microtask later, before React
    // commits) first hears the button still expanded. A browser serializes its
    // accessibility tree after the task, so a real reader is expected to hear
    // the settled state that reportFocus() records next (VPAT open item: confirm
    // in the NVDA / VoiceOver pass).
    const close = await reader.step('Escape closes the menu onto its button; report focus once settled', async () => {
      await act(async () => {
        document.activeElement!.dispatchEvent(new KeyboardEvent('keydown', { key: 'Escape', bubbles: true, cancelable: true }));
      });
      await settle();
      await reportFocus();
    });
    expect(menu()).toBeNull();
    expect(document.activeElement).toBe(trigger());
    expect(trigger().getAttribute('aria-expanded')).toBe('false');
    expect(close.at(-1)).toBe('button, Account menu, signed in as Jane Doe, not expanded, has popup menu');

    await expectTranscript('identity-menu', reader);
  }, 30_000);
});
