// @vitest-environment happy-dom
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { act } from 'react';
import { createRoot, type Root } from 'react-dom/client';
import { MemoryRouter, useLocation } from 'react-router';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import type { SessionResponse } from '../../types';

const apiMocks = vi.hoisted(() => ({ session: vi.fn() }));
const roadmapLoads = vi.hoisted(() => ({ count: 0 }));

vi.mock('../../lib/api', async (importOriginal) => ({
  ...(await importOriginal<typeof import('../../lib/api')>()),
  api: apiMocks,
}));

// Counts every load of the lazy roadmap chunk (the real module is served).
vi.mock('./RailRoadmap', async (importOriginal) => {
  roadmapLoads.count += 1;
  return importOriginal<typeof import('./RailRoadmap')>();
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
  // A customer rail is settled after a few ticks; the roadmap chunk resolves later.
  await settle(() => !expectRoadmap || document.querySelectorAll('button.rail__item--disabled').length > 0);
  const rail = document.querySelector<HTMLElement>('nav.rail');
  if (!rail) throw new Error('no rail');
  return rail;
}

const moduleItems = (rail: HTMLElement) => [...rail.querySelectorAll<HTMLElement>('.rail__item .mod')].map((mod) => mod.textContent);
const roadmapButtons = (rail: HTMLElement) => [...rail.querySelectorAll<HTMLButtonElement>('button.rail__item--disabled')];

describe('the module rail', () => {
  beforeEach(() => {
    document.body.innerHTML = '<div id="root"></div>';
    root = createRoot(document.getElementById('root') as HTMLElement);
    queryClient = new QueryClient({ defaultOptions: { queries: { retry: false, staleTime: Infinity } } });
    apiMocks.session.mockReset();
    roadmapLoads.count = 0;
  });

  afterEach(() => {
    act(() => root.unmount());
    queryClient.clear();
    document.body.innerHTML = '';
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
  });

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
  });
});
