// @vitest-environment happy-dom
import { act, createElement, type ComponentType } from 'react';
import { afterAll, afterEach, beforeAll, beforeEach, describe, expect, it, vi } from 'vitest';
import type { FollowupSummary } from '../../lib/apiClients/followUpsSummary';
import { installLocalStorage } from '../../test/installLocalStorage';
import { mount } from '../../test/render';

const trayLoads = vi.hoisted(() => ({ count: 0 }));
// Synchronous factory (a lazily imported module): counts every load of the
// tray chunk and renders a marker carrying the id the trigger controls.
vi.mock('./FollowUpsTray', () => {
  trayLoads.count += 1;
  return {
    default: ({ id }: { id: string }) => createElement('div', { id, 'data-testid': 'tray' }, 'tray'),
  };
});

// Synchronous factories (no importOriginal): the preload seams the item uses.
const preload = vi.hoisted(() => ({
  loader: null as null | (() => Promise<unknown>),
  timeout: 0,
  start: vi.fn(() => () => undefined),
  bestEffort: vi.fn(),
}));
vi.mock('../../lib/prefetch', () => ({
  saveDataRequested: () => false,
  createIdlePreloader: (loader: () => Promise<unknown>, timeout: number) => {
    preload.loader = loader;
    preload.timeout = timeout;
    return preload.start;
  },
}));
vi.mock('../../lib/lazyPreload', () => ({
  preloadBestEffort: (loader: () => Promise<unknown>) => preload.bestEffort(loader),
}));

declare const process: { env: Record<string, string | undefined> };

/**
 * The rail's "Follow-ups due" item (D-shell-deviations-a, shell-09;
 * deviation:followups-rail-item): a dot iff something is due today or
 * overdue in the viewer's own day, named by its visible text, and a
 * disclosure button whose tray chunk is warmed at idle and on hover / focus
 * without any data fetch. Rail.test.tsx pins that the rail renders it only
 * for an eligible summary (never while pending or errored).
 */

const TZ = 'America/Chicago';
const NOW = Date.parse('2026-10-08T02:30:00Z'); // 21:30 CDT on Oct 7
let previousTz: string | undefined;
beforeAll(() => {
  previousTz = process.env.TZ;
  process.env.TZ = TZ;
});
afterAll(() => {
  process.env.TZ = previousTz;
});

let fetchSpy: ReturnType<typeof vi.fn>;
beforeEach(() => {
  installLocalStorage();
  fetchSpy = vi.fn(() => new Promise<Response>(() => undefined));
  vi.stubGlobal('fetch', fetchSpy);
  vi.useFakeTimers({ toFake: ['Date'] });
  vi.setSystemTime(NOW);
});
afterEach(() => {
  vi.useRealTimers();
  vi.unstubAllGlobals();
});

async function settle(until: () => boolean): Promise<void> {
  for (let tick = 0; tick < 200 && !until(); tick += 1) {
    await act(async () => {
      await new Promise((resolve) => window.setTimeout(resolve, 10));
    });
  }
}

async function loadItem(): Promise<ComponentType<{ summary: FollowupSummary }>> {
  return (await import('./FollowUpsRailItem')).default;
}

async function renderItem(summary: FollowupSummary): Promise<HTMLButtonElement> {
  const Item = await loadItem();
  const { container } = await mount(createElement('nav', { className: 'rail' }, createElement(Item, { summary })));
  const button = container.querySelector<HTMLButtonElement>('button.rail__item');
  if (!button) throw new Error('no rail item');
  return button;
}

describe('the Follow-ups rail item', () => {
  it('is named by its visible text, with no aria-label and no title', async () => {
    const button = await renderItem({ eligible: true, next_due_at: null });
    expect(button.textContent).toBe('Due follow-ups');
    expect(button.querySelector('.mod')?.textContent).toBe('Due');
    expect(button.hasAttribute('aria-label')).toBe(false);
    expect(button.hasAttribute('title')).toBe(false);
    expect(button.querySelector('svg')?.getAttribute('aria-hidden')).toBe('true');
  });

  it.each([
    ['earlier today', '2026-10-07T14:00:00Z', true],
    ['tonight, already tomorrow in UTC', '2026-10-08T04:30:00Z', true],
    ['overdue', '2026-09-30T14:00:00Z', true],
    ['tomorrow, local', '2026-10-08T05:00:00Z', false],
    ['nothing due', null, false],
  ])('shows the dot iff next_due_at is before the next local midnight: %s', async (_case, due, dot) => {
    const button = await renderItem({ eligible: true, next_due_at: due });
    expect(button.classList.contains('has-due')).toBe(dot);
    const described = button.getAttribute('aria-describedby');
    if (dot) {
      expect(document.getElementById(described ?? '')?.textContent).toBe('Something is due today or overdue');
    } else {
      expect(described).toBeNull();
    }
  });

  it('is a disclosure: aria-expanded toggles and aria-controls names the tray it renders', async () => {
    const button = await renderItem({ eligible: true, next_due_at: null });
    expect(button.getAttribute('aria-expanded')).toBe('false');
    expect(button.hasAttribute('aria-controls')).toBe(false);

    await act(async () => button.click());
    await settle(() => document.querySelector('[data-testid="tray"]') !== null);

    expect(button.getAttribute('aria-expanded')).toBe('true');
    const tray = document.querySelector('[data-testid="tray"]');
    expect(tray?.id).toBe(button.getAttribute('aria-controls'));
    expect(button.nextElementSibling, 'the tray follows the button in the DOM').toBe(tray);

    await act(async () => button.click());
    expect(button.getAttribute('aria-expanded')).toBe('false');
    expect(document.querySelector('[data-testid="tray"]')).toBeNull();
  });

  it('warms the tray chunk at idle (5 s) and on hover and focus: chunks only, never data', async () => {
    const button = await renderItem({ eligible: true, next_due_at: null });
    expect(preload.timeout).toBe(5000);
    expect(preload.start, 'the idle preload is scheduled on mount').toHaveBeenCalled();

    preload.bestEffort.mockClear();
    await act(async () => {
      // React derives onPointerEnter from pointerover and onFocus from focusin.
      button.dispatchEvent(new PointerEvent('pointerover', { bubbles: true }));
      button.focus();
    });
    expect(preload.bestEffort).toHaveBeenCalledTimes(2);
    expect(preload.bestEffort.mock.calls.every(([loader]) => loader === preload.loader)).toBe(true);

    // The loader is the tray chunk itself: it resolves to the tray module.
    const loaded = (await preload.loader?.()) as { default: unknown } | undefined;
    expect(typeof loaded?.default).toBe('function');
    expect(trayLoads.count).toBeGreaterThan(0);
    expect(fetchSpy, 'no data is fetched by a preload').not.toHaveBeenCalled();
  });
});
