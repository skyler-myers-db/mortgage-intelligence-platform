/**
 * @vitest-environment happy-dom
 *
 * "Save build" contract (re-audit #3 P1, 2026-06-12): clicking Save
 * during an in-flight build froze the tab because the handler opened a
 * SYNCHRONOUS window.prompt() — a renderer-blocking native dialog that
 * also hangs any CDP/Playwright session — and the disabled-guard used
 * `preview === null` as its only "build running" signal, which is false
 * during a background refetch of an unchanged key. Pins:
 *
 * 1. while the build fetch is in flight, Save is disabled
 *    ("Build running…") — the audit's Run→Save race is unclickable;
 * 2. Save opens an IN-PAGE naming form — window.prompt is never called;
 * 3. submitting the form creates the campaign with the typed name.
 */

import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { act } from 'react';
import { createRoot, type Root } from 'react-dom/client';
import { MemoryRouter } from 'react-router-dom';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
// @ts-expect-error Frontend app types intentionally exclude Node globals; this
// unit test reads the route source under Vitest only (same pattern as
// design-system/components.test.ts).
import { readFileSync } from 'node:fs';
// @ts-expect-error see node:fs note above.
import { join } from 'node:path';
import type { PortfolioPreview } from '../types';

// Vitest executes on Node, but the app tsconfig (correctly) carries no Node
// globals — declare the one we use.
declare const process: { cwd(): string };

const portfolioPreview = vi.fn();
const portfolioCreate = vi.fn();
const campaigns = vi.fn();

vi.mock('../lib/api', () => ({
  api: {
    portfolioPreview: (...args: unknown[]) => portfolioPreview(...args),
    portfolioCreate: (...args: unknown[]) => portfolioCreate(...args),
    campaigns: (...args: unknown[]) => campaigns(...args),
  },
  ApiError: class extends Error {},
  isAbortError: () => false,
  isWarmingUpError: () => false,
}));

// Mock providers MUST return identity-stable values: the component keys
// memos and effects on these objects, and a fresh object per render is an
// unbounded re-render loop (this exact mock spun a vitest worker at 100%
// CPU before the identity-preserving guard landed in the component).
vi.mock('../lib/configOptionsQuery', () => {
  const STABLE = {
    data: {
      lender_name: 'Summit Mortgage',
      target_lender_refs: ['All'],
      target_lender_refs_status: 'live',
    },
    isError: false,
  };
  return { useConfigOptionsQuery: () => STABLE };
});

vi.mock('../components/FootprintProvider', () => {
  const STABLE = { ready: true, usingFallback: false, states: [] };
  return { useFootprint: () => STABLE };
});

vi.mock('../components/AppContext', () => ({
  useApp: () => ({
    setDrawer: vi.fn(),
    showEvidence: true,
    showConfidence: true,
  }),
}));

import PortfolioBuilder from './portfolio-builder';

function deferred<T>() {
  let resolve!: (value: T) => void;
  const promise = new Promise<T>((res) => {
    resolve = res;
  });
  return { promise, resolve };
}

const PREVIEW: PortfolioPreview = {
  marketable_population: 79730,
  avg_score: 71,
  top_tier_opportunities: 3990,
  offers_recommended: 44700,
  high_intent_leads: 1200,
} as unknown as PortfolioPreview;

describe('PortfolioBuilder save-build flow', () => {
  let container: HTMLDivElement;
  let root: Root;
  let promptSpy: ReturnType<typeof vi.fn>;

  beforeEach(() => {
    vi.clearAllMocks();
    campaigns.mockResolvedValue({ campaigns: [] });
    promptSpy = vi.fn();
    // If ANY code path reaches for the native blocking dialog again, fail
    // loudly instead of freezing a renderer at the booth.
    vi.stubGlobal('prompt', promptSpy);
    container = document.createElement('div');
    document.body.appendChild(container);
    root = createRoot(container);
  });

  afterEach(() => {
    act(() => root.unmount());
    container.remove();
    vi.unstubAllGlobals();
  });

  function mount() {
    const queryClient = new QueryClient({
      defaultOptions: { queries: { retry: false } },
    });
    act(() => {
      root.render(
        <QueryClientProvider client={queryClient}>
          <MemoryRouter initialEntries={['/portfolio-builder']}>
            <PortfolioBuilder />
          </MemoryRouter>
        </QueryClientProvider>,
      );
    });
  }

  function saveButton(): HTMLButtonElement {
    const btn = container.querySelector<HTMLButtonElement>(
      '[data-testid="portfolio-save-build"]',
    );
    if (!btn) throw new Error('save button not rendered');
    return btn;
  }

  async function flush() {
    await act(async () => {
      await Promise.resolve();
    });
  }

  /** Poll until the react-query chain settles into `cond` (mock promises
   * resolve across several microtask turns; a single flush can land
   * mid-chain). */
  async function waitUntil(cond: () => boolean, ms = 4000) {
    const start = Date.now();
    while (!cond()) {
      if (Date.now() - start > ms) throw new Error('waitUntil timeout');
      await act(async () => {
        await new Promise((r) => setTimeout(r, 10));
      });
    }
  }

  it('disables Save while the build fetch is in flight', async () => {
    const inflight = deferred<PortfolioPreview>();
    portfolioPreview.mockReturnValue(inflight.promise);
    mount();
    await flush();

    expect(saveButton().disabled).toBe(true);
    expect(saveButton().textContent).toContain('Build running…');

    inflight.resolve(PREVIEW);
    await waitUntil(() => !saveButton().disabled);

    expect(saveButton().textContent).toContain('Save build');
  });

  it('opens an in-page naming form instead of window.prompt and saves with the typed name', async () => {
    portfolioPreview.mockResolvedValue(PREVIEW);
    portfolioCreate.mockResolvedValue({ campaign_id: 'c-1' });
    mount();
    await waitUntil(() => !saveButton().disabled);

    act(() => {
      saveButton().click();
    });

    expect(promptSpy).not.toHaveBeenCalled();
    const nameInput = container.querySelector<HTMLInputElement>(
      '[data-testid="portfolio-save-name"]',
    );
    expect(nameInput).not.toBeNull();
    expect(nameInput!.value).toContain('Portfolio build');

    act(() => {
      const setter = Object.getOwnPropertyDescriptor(
        HTMLInputElement.prototype,
        'value',
      )!.set!;
      setter.call(nameInput!, 'Booth build — Summit IL refi');
      nameInput!.dispatchEvent(new Event('input', { bubbles: true }));
    });
    act(() => {
      container
        .querySelector<HTMLButtonElement>('[data-testid="portfolio-save-confirm"]')!
        .click();
    });
    await waitUntil(() => portfolioCreate.mock.calls.length > 0);
    await flush();

    expect(portfolioCreate).toHaveBeenCalledTimes(1);
    expect(portfolioCreate.mock.calls[0][0]).toBe('Booth build — Summit IL refi');
    expect(promptSpy).not.toHaveBeenCalled();
    // The panel closes and the hint flips to saved.
    expect(
      container.querySelector('[data-testid="portfolio-save-name"]'),
    ).toBeNull();
    expect(saveButton().textContent).toContain('Build saved');
  });

  it('keeps the production source free of window.prompt', () => {
    // Belt-and-suspenders source pin: the route module must not reference
    // the blocking dialog API at all (catches a regression that the
    // behavioral mocks above might mask if the call became conditional).
    // happy-dom rewrites import.meta.url to an http:// URL, so resolve
    // from the vitest cwd (the frontend package root) instead.
    const source = readFileSync(
      join(process.cwd(), 'src', 'routes', 'portfolio-builder.tsx'),
      'utf-8',
    );
    expect(source).not.toContain('window.prompt');
  });

  it('bans the blocking-dialog class repo-wide via eslint config', () => {
    // Re-audit #4: the source pin above guards ONE file; this pins the
    // repo-wide eslint rule so no future surface can reintroduce
    // prompt/alert/confirm. If the rule is removed, this fails before a
    // regression can land.
    const config = readFileSync(join(process.cwd(), 'eslint.config.js'), 'utf-8');
    expect(config).toContain('no-restricted-globals');
    for (const banned of ['prompt', 'alert', 'confirm']) {
      expect(config).toContain(`name: "${banned}"`);
    }
  });
});
