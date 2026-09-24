/**
 * Shared harness for the /ask-genie turn and announcer suites (wave 2
 * `runtime-01`, `a11y-06`): the route, optionally with the floating panel
 * beside it, on the REAL in-flight turn store and transcript store, with the
 * Genie endpoints mocked at the api boundary.
 */
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { act } from 'react';
import type { Root } from 'react-dom/client';
import { MemoryRouter } from 'react-router';
import { vi } from 'vitest';
import type { GenieLiveProgress, GenieSubmitResult } from '../lib/api';
import type { GenieAnswer, GenieStartResult, GrowthAgentHomeResponse } from '../types';

export const genieStart = vi.fn();
export const genieSubmit = vi.fn();
export const genieProgress = vi.fn();
export const genieComplete = vi.fn();
export const genieAction = vi.fn();
export const genieSessions = vi.fn();
export const genieSession = vi.fn();
export const growthAgent = vi.fn();
export const growthAgentCapabilities = vi.fn();
export const navigate = vi.fn();
export const setGenieOpen = vi.fn();
export const appState = { genieOpen: false };

vi.mock('../lib/api', async () => {
  const actual = await vi.importActual<typeof import('../lib/api')>('../lib/api');
  return {
    ...actual,
    api: {
      genieStart: (...args: unknown[]) => genieStart(...args),
      genieSubmit: (...args: unknown[]) => genieSubmit(...args),
      genieProgress: (...args: unknown[]) => genieProgress(...args),
      genieComplete: (...args: unknown[]) => genieComplete(...args),
      genieAction: (...args: unknown[]) => genieAction(...args),
      genieSessions: (...args: unknown[]) => genieSessions(...args),
      genieSession: (...args: unknown[]) => genieSession(...args),
      genieFeedback: () => Promise.resolve({ accepted: true }),
      growthAgent: (...args: unknown[]) => growthAgent(...args),
      growthAgentCapabilities: (...args: unknown[]) => growthAgentCapabilities(...args),
    },
  };
});

vi.mock('../components/AppContext', () => ({
  useApp: () => ({
    genieOpen: appState.genieOpen,
    setGenieOpen: setGenieOpen,
    lender: 'Summit Mortgage',
    refreshWorkspace: vi.fn(),
    setDrawer: vi.fn(),
    showEvidence: true,
    showConfidence: true,
  }),
}));

vi.mock('react-router', async () => {
  const actual = await vi.importActual<typeof import('react-router')>('react-router');
  return { ...actual, useNavigate: () => navigate };
});

import { GenieChat } from '../components/mortgage/GenieChat';
import AskGenie from './ask-genie';

export const QUESTION = 'Which states have the most prime refi candidates?';
export const SAMPLE = 'How many borrowers are in the money?';

export const START: GenieStartResult = {
  conversation_id: null,
  trusted_assets: ['mip.gold.borrower_360'],
  sample_questions: [SAMPLE],
};

const HOME: GrowthAgentHomeResponse = { workflows: [], monitors: [], capabilities: [] };

export const LIVE_SUBMIT: GenieSubmitResult = {
  completed: false,
  conversation_id: 'conv-1',
  message_id: 'msg-1',
  progress_token: 'tok-1',
  question_hash: 'hash-1',
};

export function progress(terminal: boolean, label = 'Running the governed query'): GenieLiveProgress {
  return {
    status: terminal ? 'COMPLETED' : 'EXECUTING_QUERY',
    stage: terminal ? 'complete' : 'executing',
    stage_label: terminal ? 'Verifying the answer against its rows' : label,
    terminal,
    failed: false,
    reasoning_trace: [],
    sql_preview: null,
    error_hint: null,
  };
}

export function answer(overrides: Partial<GenieAnswer> = {}): GenieAnswer {
  return {
    answer: 'Illinois leads with 3,080 candidates.',
    source: 'genie',
    trusted_assets: ['mip.gold.borrower_360'],
    conversation_id: 'conv-1',
    message_id: 'msg-1',
    genie_status: 'COMPLETED',
    question_hash: 'hash-1',
    follow_up_questions: ['Which counties lead?'],
    ...overrides,
  };
}

export interface Deferred<T> {
  promise: Promise<T>;
  resolve: (value: T) => void;
  reject: (reason: unknown) => void;
}

export function deferred<T>(): Deferred<T> {
  let resolve: (value: T) => void = () => undefined;
  let reject: (reason: unknown) => void = () => undefined;
  const promise = new Promise<T>((res, rej) => {
    resolve = res;
    reject = rej;
  });
  return { promise, resolve, reject };
}

export function installStorage(): void {
  for (const key of ['localStorage', 'sessionStorage'] as const) {
    const values = new Map<string, string>();
    Object.defineProperty(window, key, {
      configurable: true,
      value: {
        getItem: (k: string) => values.get(k) ?? null,
        setItem: (k: string, v: string) => values.set(k, v),
        removeItem: (k: string) => values.delete(k),
        clear: () => values.clear(),
      },
    });
  }
  Object.defineProperty(window, 'matchMedia', {
    configurable: true,
    writable: true,
    value: (query: string) => ({
      matches: false,
      media: query,
      addEventListener: () => undefined,
      removeEventListener: () => undefined,
    }),
  });
  Object.defineProperty(Element.prototype, 'scrollIntoView', {
    configurable: true,
    writable: true,
    value: () => undefined,
  });
}

export function resetMocks(): void {
  for (const mock of [
    genieStart,
    genieSubmit,
    genieProgress,
    genieComplete,
    genieAction,
    genieSessions,
    genieSession,
    growthAgent,
    growthAgentCapabilities,
    navigate,
    setGenieOpen,
  ]) {
    mock.mockReset();
  }
  appState.genieOpen = false;
  genieStart.mockResolvedValue(START);
  genieSessions.mockResolvedValue([]);
  genieSession.mockResolvedValue({ conversation_id: null, turns: [] });
  growthAgent.mockResolvedValue(HOME);
  growthAgentCapabilities.mockResolvedValue(HOME);
}

/** Mount the route (and/or the floating panel) at `path`. Re-rendering with
 *  `route: false` unmounts the route the way leaving the page does. */
export function mount(root: Root, options: { route?: boolean; panel?: boolean; path?: string } = {}): QueryClient {
  const { route = true, panel = false, path = '/ask-genie' } = options;
  const queryClient = new QueryClient({
    defaultOptions: { queries: { retry: false }, mutations: { retry: false } },
  });
  render(root, queryClient, { route, panel, path });
  return queryClient;
}

export function render(
  root: Root,
  queryClient: QueryClient,
  { route = true, panel = false, path = '/ask-genie' }: { route?: boolean; panel?: boolean; path?: string },
): void {
  act(() => {
    root.render(
      <QueryClientProvider client={queryClient}>
        <MemoryRouter initialEntries={[path]}>
          {route && <AskGenie />}
          {panel && <GenieChat />}
        </MemoryRouter>
      </QueryClientProvider>,
    );
  });
}

export function setTextAreaValue(el: HTMLTextAreaElement, value: string): void {
  const setter = Object.getOwnPropertyDescriptor(HTMLTextAreaElement.prototype, 'value')?.set;
  if (!setter) throw new Error('missing textarea value setter');
  setter.call(el, value);
  el.dispatchEvent(new Event('input', { bubbles: true }));
  el.dispatchEvent(new Event('change', { bubbles: true }));
}

export function setInputValue(el: HTMLInputElement, value: string): void {
  const setter = Object.getOwnPropertyDescriptor(HTMLInputElement.prototype, 'value')?.set;
  if (!setter) throw new Error('missing input value setter');
  setter.call(el, value);
  el.dispatchEvent(new Event('input', { bubbles: true }));
  el.dispatchEvent(new Event('change', { bubbles: true }));
}

export async function waitUntil(condition: () => boolean, timeoutMs = 10_000): Promise<void> {
  const startedAt = Date.now();
  while (!condition()) {
    if (Date.now() - startedAt > timeoutMs) throw new Error('waitUntil timeout');
    await act(async () => new Promise((resolve) => setTimeout(resolve, 10)));
  }
}

export async function flush(): Promise<void> {
  await act(async () => new Promise((resolve) => setTimeout(resolve, 0)));
}

/** The route's composer and the controls around it. */
export function routeControls(container: HTMLElement) {
  const main = () => {
    const panel = container.querySelector<HTMLElement>('section[role="tabpanel"][id$="ask"]');
    if (!panel) throw new Error('Ask tabpanel not rendered');
    return panel;
  };
  const byText = (root: HTMLElement, text: string) =>
    Array.from(root.querySelectorAll<HTMLButtonElement>('button')).find((b) => b.textContent?.trim() === text);
  return {
    main,
    composer: () => {
      const el = container.querySelector<HTMLTextAreaElement>('textarea[aria-label="Ask Genie — question"]');
      if (!el) throw new Error('route composer not rendered');
      return el;
    },
    ask: () => {
      const el = container.querySelector<HTMLButtonElement>('form.genie-composer button[type="submit"]');
      if (!el) throw new Error('route Ask not rendered');
      return el;
    },
    stop: () => main().querySelector<HTMLButtonElement>('button[aria-label="Stop this Genie turn"]'),
    sampleChips: () => Array.from(main().querySelectorAll<HTMLButtonElement>('.genie-composer__samples button')),
    followUps: () => Array.from(main().querySelectorAll<HTMLButtonElement>('.genie-answer__followups button')),
    newThread: () => byText(main(), 'New thread'),
    history: () => main().querySelector<HTMLButtonElement>('button[aria-label="Genie conversation history"]'),
    regenerate: () => main().querySelector<HTMLButtonElement>('button[aria-label="Regenerate answer"]'),
    userBubbles: () => Array.from(main().querySelectorAll('.genie__msg--user')).map((el) => el.textContent ?? ''),
  };
}
