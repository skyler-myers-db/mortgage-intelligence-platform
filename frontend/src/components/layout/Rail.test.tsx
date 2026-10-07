// @vitest-environment happy-dom
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { act } from 'react';
import { createRoot, type Root } from 'react-dom/client';
import { MemoryRouter, useLocation } from 'react-router';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { installLocalStorage } from '../../test/installLocalStorage';
import type { SessionResponse } from '../../types';

const apiMocks = vi.hoisted(() => ({ session: vi.fn() }));
const roadmapLoads = vi.hoisted(() => {
  let markLoaded: () => void = () => undefined;
  const loaded = new Promise<void>((resolve) => {
    markLoaded = resolve;
  });
  return { count: 0, loaded, markLoaded };
});

vi.mock('../../lib/api', async (importOriginal) => ({
  ...(await importOriginal<typeof import('../../lib/api')>()),
  api: apiMocks,
}));

// Counts every load of the lazy roadmap chunk (the real module is served) and
// signals when Rail's own request for it has been transformed and evaluated.
vi.mock('./RailRoadmap', async (importOriginal) => {
  roadmapLoads.count += 1;
  const roadmap = await importOriginal<typeof import('./RailRoadmap')>();
  roadmapLoads.markLoaded();
  return roadmap;
});

import { Rail } from './Rail';

(globalThis as unknown as { IS_REACT_ACT_ENVIRONMENT: boolean }).IS_REACT_ACT_ENVIRONMENT = true;

/**
 * The module rail (critic-05, shell-09; D-shell-deviations-e2,
 * deviation:rail-roadmap-presenter-only): M1-M4 render only in presenter
 * mode, from a lazy chunk a customer session never requests, as focusable
 * aria-disabled buttons whose roadmap note is a Tooltip description (no
 * DOM title=, critic-08) and whose activation goes nowhere.
 */

const SESSION_QUERY_KEY = ['session', 'access'] as const;
const CUSTOMER: SessionResponse = { can_access_admin: true, can_approve: true, can_read_audit: true, presenter_mode: false };
const PRESENTER: SessionResponse = { ...CUSTOMER, presenter_mode: true };

let root: Root;
let queryClient: QueryClient;
// The Follow-ups summary poll (D-shell-deviations-a) is the rail's only
// fetch; a session without actor_email never sends it.
let fetchSpy: ReturnType<typeof vi.fn>;

function LocationProbe() {
  const current = useLocation();
  return <output data-testid="location">{`${current.pathname}${current.search}${current.hash}`}</output>;
}

const location = () => document.querySelector('[data-testid="location"]')?.textContent ?? '';

async function settle(until: () => boolean = () => false): Promise<void> {
  for (let tick = 0; tick < 40 && !until(); tick += 1) {
    await act(async () => {
      await new Promise((resolve) => window.setTimeout(resolve, 5));
    });
  }
}

/** Only turns a chunk Rail never requests into a named failure; a load that arrives ends the wait. */
const ROADMAP_LOAD_LIMIT_MS = 10_000;
/** The presenter tests' budget: the first chunk load transforms RailRoadmap and Tooltip. */
const PRESENTER_TEST_TIMEOUT_MS = 15_000;

/**
 * Waits on Rail's own request for the lazy roadmap chunk, not on a tick
 * budget: the first load runs the React Compiler transform of RailRoadmap
 * and Tooltip, which outran a 40-tick wait under machine load. The test never
 * imports the chunk itself, so the load count stays Rail's alone.
 */
async function awaitRoadmapChunk(): Promise<void> {
  let timer = 0;
  const limit = new Promise<never>((_, reject) => {
    timer = window.setTimeout(
      () => reject(new Error('presenter mode never loaded the RailRoadmap chunk')),
      ROADMAP_LOAD_LIMIT_MS,
    );
  });
  try {
    await act(async () => {
      await Promise.race([roadmapLoads.loaded, limit]);
    });
  } finally {
    window.clearTimeout(timer);
  }
}

async function renderRail(expectRoadmap = false): Promise<HTMLElement> {
  await act(async () => {
    root.render(
      <QueryClientProvider client={queryClient}>
        <MemoryRouter initialEntries={['/lead-queue']}>
          <Rail />
          <LocationProbe />
        </MemoryRouter>
      </QueryClientProvider>,
    );
  });
  if (expectRoadmap) await awaitRoadmapChunk();
  // A customer rail is settled after a few ticks, and so is a loaded chunk's render.
  await settle(() => !expectRoadmap || document.querySelectorAll('button.rail__item--disabled').length > 0);
  const rail = document.querySelector<HTMLElement>('nav.rail');
  if (!rail) throw new Error('no rail');
  return rail;
}

const moduleItems = (rail: HTMLElement) => [...rail.querySelectorAll<HTMLElement>('.rail__item .mod')].map((mod) => mod.textContent);
const roadmapButtons = (rail: HTMLElement) => [...rail.querySelectorAll<HTMLButtonElement>('button.rail__item--disabled')];

describe('the module rail', () => {
  beforeEach(() => {
    installLocalStorage();
    document.body.innerHTML = '<div id="root"></div>';
    root = createRoot(document.getElementById('root') as HTMLElement);
    queryClient = new QueryClient({ defaultOptions: { queries: { retry: false, staleTime: Infinity } } });
    apiMocks.session.mockReset();
    roadmapLoads.count = 0;
    fetchSpy = vi.fn(() => new Promise<Response>(() => undefined));
    vi.stubGlobal('fetch', fetchSpy);
  });

  afterEach(() => {
    act(() => root.unmount());
    queryClient.clear();
    document.body.innerHTML = '';
    vi.unstubAllGlobals();
  });

  it('shows a customer session M0 alone and never loads the roadmap chunk', async () => {
    queryClient.setQueryData<SessionResponse>(SESSION_QUERY_KEY, CUSTOMER);
    const rail = await renderRail();
    expect(moduleItems(rail)).toEqual(['M0']);
    expect(rail.querySelectorAll('.rail__item--disabled')).toHaveLength(0);
    expect(roadmapLoads.count, 'the RailRoadmap chunk is not requested').toBe(0);
    // Customer order: brand, M0, spacer, ledger, gear.
    expect([...rail.children].map((child) => child.className)).toEqual([
      'rail__brand', 'rail__item is-active', 'rail__spacer', 'rail__item', 'rail__item',
    ]);
  });

  it.each([
    ['pending', () => new Promise<SessionResponse>(() => undefined)],
    ['errored', () => Promise.reject(new Error('session read failed'))],
  ] as const)('treats a %s session as presenter mode off', async (_state, reply) => {
    apiMocks.session.mockImplementation(reply);
    const rail = await renderRail();
    expect(moduleItems(rail)).toEqual(['M0']);
    expect(roadmapLoads.count).toBe(0);
  });

  it('in presenter mode shows M1-M4 as focusable aria-disabled buttons described by their roadmap note', async () => {
    queryClient.setQueryData<SessionResponse>(SESSION_QUERY_KEY, PRESENTER);
    const rail = await renderRail(true);
    expect(roadmapLoads.count, 'non-vacuity: presenter mode loads the chunk').toBe(1);
    expect(moduleItems(rail)).toEqual(['M0', 'M1', 'M2', 'M3', 'M4']);
    const buttons = roadmapButtons(rail);
    expect(buttons).toHaveLength(4);
    const notes = buttons.map((button) => {
      expect(button.getAttribute('type')).toBe('button');
      expect(button.getAttribute('aria-disabled')).toBe('true');
      expect(button.hasAttribute('disabled'), 'focusable: aria-disabled, never disabled').toBe(false);
      expect(button.hasAttribute('title'), 'no DOM title= (critic-08)').toBe(false);
      expect(button.hasAttribute('role')).toBe(false);
      // The accessible name is the visible label (WCAG 2.5.3).
      expect(button.textContent).toMatch(/^M[1-4]$/);
      const described = button.getAttribute('aria-describedby') ?? '';
      return document.getElementById(described)?.textContent;
    });
    expect(notes).toEqual([
      'Module 1: Pipeline Optimization. Lead → app → approval throughput and stalls. On the roadmap; not part of this workspace.',
      'Module 2: LO Workbench. Officer assist with explainable borrower guidance. On the roadmap; not part of this workspace.',
      'Module 3: Underwriting Copilot. Condition handling and exception triage. On the roadmap; not part of this workspace.',
      'Module 4: Risk & Retention. Portfolio-level retention and recapture. On the roadmap; not part of this workspace.',
    ]);
    // Presenter order: brand, M0, M1..M4, spacer, ledger, gear.
    expect([...rail.children].map((child) => child.querySelector('.mod')?.textContent ?? child.className)).toEqual([
      'rail__brand', 'M0', 'M1', 'M2', 'M3', 'M4', 'rail__spacer', 'rail__item', 'rail__item',
    ]);
  }, PRESENTER_TEST_TIMEOUT_MS);

  it('a roadmap slot goes nowhere when clicked or pressed', async () => {
    queryClient.setQueryData<SessionResponse>(SESSION_QUERY_KEY, PRESENTER);
    const rail = await renderRail(true);
    const before = location();
    expect(before).toBe('/lead-queue');
    for (const button of roadmapButtons(rail)) {
      button.focus();
      expect(document.activeElement).toBe(button);
      await act(async () => {
        button.click();
        button.dispatchEvent(new KeyboardEvent('keydown', { key: 'Enter', bubbles: true }));
      });
    }
    expect(location()).toBe(before);
    expect(apiMocks.session).not.toHaveBeenCalled();
  }, PRESENTER_TEST_TIMEOUT_MS);

  describe('the Follow-ups due item (D-shell-deviations-a)', () => {
    const ACTOR: SessionResponse = { ...CUSTOMER, actor_email: 'lo01@summit.example' };
    const respond = (body: unknown, status = 200) =>
      Promise.resolve(new Response(JSON.stringify(body), { status, headers: { 'Content-Type': 'application/json' } }));
    const dueItem = (rail: HTMLElement) =>
      [...rail.querySelectorAll<HTMLElement>('.rail__item')].find((item) => item.querySelector('.mod')?.textContent === 'Due');

    async function renderPolling(reply: () => Promise<Response>, until: (rail: HTMLElement) => boolean): Promise<HTMLElement> {
      fetchSpy.mockImplementation(reply);
      queryClient.setQueryData<SessionResponse>(SESSION_QUERY_KEY, ACTOR);
      const rail = await renderRail();
      for (let tick = 0; tick < 400 && !(fetchSpy.mock.calls.length > 0 && until(rail)); tick += 1) {
        await act(async () => {
          await new Promise((resolve) => window.setTimeout(resolve, 25));
        });
      }
      expect(fetchSpy.mock.calls[0]?.[0]).toBe('/api/v1/sales/followups/summary');
      return rail;
    }

    it('never polls a session that names no actor', async () => {
      queryClient.setQueryData<SessionResponse>(SESSION_QUERY_KEY, CUSTOMER);
      await renderRail();
      expect(fetchSpy).not.toHaveBeenCalled();
    });

    it.each([
      ['pending', () => new Promise<Response>(() => undefined)],
      ['errored', () => respond({ detail: 'lakebase is temporarily unavailable' }, 503)],
      ['ineligible', () => respond({ eligible: false, next_due_at: null })],
    ] as const)('renders no item while the summary is %s, and the class sequence is unchanged', async (_state, reply) => {
      const rail = await renderPolling(reply, () => true);
      await settle();
      expect(dueItem(rail)).toBeUndefined();
      expect([...rail.children].map((child) => child.className)).toEqual([
        'rail__brand', 'rail__item is-active', 'rail__spacer', 'rail__item', 'rail__item',
      ]);
    });

    it('for an eligible actor orders spacer, follow-ups, ledger, gear', async () => {
      const rail = await renderPolling(() => respond({ eligible: true, next_due_at: null }), (r) => dueItem(r) !== undefined);
      const children = [...rail.children];
      const due = dueItem(rail);
      expect(due).toBeDefined();
      const spacer = children.findIndex((child) => child.className === 'rail__spacer');
      // The item's hidden hint, then its button, sit between the spacer and the ledger link.
      expect(children[spacer + 1]?.hasAttribute('hidden')).toBe(true);
      expect(children[spacer + 2]).toBe(due);
      expect(children[spacer + 3]?.getAttribute('aria-label')).toBe('Audit ledger');
      expect(children[spacer + 4]?.getAttribute('href')).toBe('/admin-config');
      expect(children).toHaveLength(spacer + 5);
    });
  });
});
