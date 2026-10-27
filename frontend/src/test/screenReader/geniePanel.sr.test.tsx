// @vitest-environment happy-dom
/**
 * Virtual screen-reader pass: the floating Genie panel's answer and its
 * provenance (WCAG 4.1.3, 1.3.1, 4.1.2, 2.4.3; audit a11y-06;
 * docs/accessibility/VPAT.md). Harness as GenieChat.reading.test.tsx: the
 * Genie start / submit / progress / complete reads are mocked, and the
 * answer is synthetic (a trusted SELECT over a curated gold asset).
 *
 * What this pins is the a11y-06 contract as a reader hears it: the panel's
 * ONE persistent polite announcer speaks stage changes and "Answer ready"
 * (never the 1-second elapsed ticker: useGenieAnnouncer.test.tsx proves that
 * over seconds of fake time), the answer is read in place, and
 * "Show proof" opens a named dialog whose provenance (trust, rows, latency,
 * source Unity Catalog assets, the SQL) is readable, with focus handed back.
 *
 * Not modelled (virtualReader.ts): speech queueing, so whether a real reader
 * finishes or interrupts a stage announcement is unverified; and the proof
 * dialog's inert page (showModal). The a11y-06 audit asks for one NVDA and
 * one VoiceOver pass over both Genie surfaces: still open (VPAT).
 */
import { act } from 'react';
import { QueryClientProvider, type QueryClient } from '@tanstack/react-query';
import { MemoryRouter } from 'react-router';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import type { GenieLiveProgress, GenieSubmitResult } from '../../lib/api';
import { clearGenieTurns } from '../../lib/genieConversationStore';
import { __resetGenieTurnStoreForTests } from '../../lib/genieInFlightTurn';
import { createMipQueryClient } from '../../lib/queryClient';
import type { GenieAnswer, GenieStartResult } from '../../types';
import { __resetGenieAnnouncerForTests } from '../../components/mortgage/useGenieAnnouncer';
import { mount } from '../render';
import { expectTranscript, nextUntil, registerReaderHooks, settle, startReader } from './virtualReader';

const mocks = vi.hoisted(() => ({
  genieAction: vi.fn(),
  genieFeedback: vi.fn(),
  genieStart: vi.fn(),
  genieSubmit: vi.fn(),
  genieProgress: vi.fn(),
  genieComplete: vi.fn(),
  setDrawer: vi.fn(),
}));

vi.mock('../../lib/api', async (importOriginal) => ({
  ...(await importOriginal<typeof import('../../lib/api')>()),
  api: {
    genieAction: mocks.genieAction,
    genieFeedback: mocks.genieFeedback,
    genieStart: mocks.genieStart,
    genieSubmit: mocks.genieSubmit,
    genieProgress: mocks.genieProgress,
    genieComplete: mocks.genieComplete,
  },
}));

vi.mock('../../components/AppContext', () => ({
  useApp: () => ({
    genieOpen: true,
    setGenieOpen: vi.fn(),
    lender: 'Summit Mortgage',
    refreshWorkspace: vi.fn(),
    setDrawer: mocks.setDrawer,
    // The workspace default: evidence chips are shown.
    showEvidence: true,
  }),
}));

import { GenieChat } from '../../components/mortgage/GenieChat';

registerReaderHooks();

const START: GenieStartResult = { conversation_id: null, trusted_assets: ['mip.gold.borrower_360'], sample_questions: [] };
const SUBMITTED: GenieSubmitResult = {
  completed: false,
  conversation_id: 'conv-sr',
  message_id: 'msg-sr',
  progress_token: 'tok-sr',
  question_hash: 'hash-sr',
};
const RUNNING: GenieLiveProgress = {
  status: 'EXECUTING_QUERY',
  stage: 'executing',
  stage_label: 'Running the governed SQL',
  terminal: false,
  failed: false,
  reasoning_trace: [],
  sql_preview: null,
  error_hint: null,
};
const TERMINAL: GenieLiveProgress = {
  ...RUNNING,
  status: 'COMPLETED',
  stage: 'complete',
  stage_label: 'Verifying the answer against its rows',
  terminal: true,
};
const QUESTION = 'How many borrowers are in the money in Illinois?';
const ANSWER: GenieAnswer = {
  answer: 'There are 1,204 in-the-money borrowers in Illinois.',
  question: QUESTION,
  source: 'trusted_sql',
  trusted_assets: ['mip.gold.borrower_360'],
  conversation_id: 'conv-sr',
  message_id: 'msg-sr',
  genie_status: 'COMPLETED',
  follow_up_questions: [],
  proof: {
    trusted: true,
    row_count: 1,
    elapsed_ms: 840,
    source_assets: ['mip.gold.borrower_360'],
    sql_query: "SELECT COUNT(*) AS borrowers FROM mip.gold.borrower_360 WHERE state = 'IL' AND in_the_money",
  },
} as GenieAnswer;

function deferred<T>() {
  let resolve: (value: T) => void = () => undefined;
  const promise = new Promise<T>((r) => {
    resolve = r;
  });
  return { promise, resolve };
}

let queryClient: QueryClient;

beforeEach(() => {
  Object.values(mocks).forEach((mock) => mock.mockReset());
  clearGenieTurns();
  queryClient = createMipQueryClient();
  queryClient.setDefaultOptions({ queries: { ...queryClient.getDefaultOptions().queries, retry: false } });
  mocks.genieStart.mockResolvedValue(START);
  mocks.genieFeedback.mockResolvedValue({ accepted: true });
  Object.defineProperty(Element.prototype, 'scrollIntoView', { configurable: true, writable: true, value: () => undefined });
  Object.defineProperty(window, 'matchMedia', {
    configurable: true,
    writable: true,
    value: (query: string) => ({ matches: false, media: query, addEventListener: () => undefined, removeEventListener: () => undefined }),
  });
});

afterEach(() => {
  // The panel is still mounted here (mount() unmounts after this hook): the
  // store resets notify it, so they run inside act.
  act(() => {
    queryClient.clear();
    __resetGenieTurnStoreForTests();
    __resetGenieAnnouncerForTests();
    clearGenieTurns();
  });
});

async function waitUntil(condition: () => boolean, timeoutMs = 10_000) {
  const startedAt = Date.now();
  while (!condition()) {
    if (Date.now() - startedAt > timeoutMs) throw new Error('waitUntil timeout');
    await act(async () => new Promise((resolve) => setTimeout(resolve, 10)));
  }
}

describe('virtual screen reader: Genie panel answer and provenance', () => {
  it('announces the stage and "Answer ready" once, reads the answer, and opens a readable proof', async () => {
    await mount(
      <MemoryRouter>
        <QueryClientProvider client={queryClient}>
          <GenieChat />
        </QueryClientProvider>
      </MemoryRouter>,
    );
    await settle();
    const reader = await startReader();

    const progress = deferred<GenieLiveProgress>();
    const terminal = deferred<GenieLiveProgress>();
    const complete = deferred<GenieAnswer>();
    mocks.genieSubmit.mockResolvedValueOnce(SUBMITTED);
    mocks.genieProgress.mockReturnValueOnce(progress.promise).mockReturnValueOnce(terminal.promise);
    mocks.genieComplete.mockReturnValueOnce(complete.promise);

    const asked = await reader.step('Type the question and press Ask', async () => {
      const input = document.querySelector<HTMLInputElement>('input[aria-label="Ask Genie"]')!;
      await act(async () => {
        Object.getOwnPropertyDescriptor(HTMLInputElement.prototype, 'value')?.set?.call(input, QUESTION);
        input.dispatchEvent(new Event('input', { bubbles: true }));
      });
      await act(async () => {
        document.querySelector<HTMLButtonElement>('button[aria-label="Ask"]')!.click();
        await new Promise((resolve) => setTimeout(resolve, 0));
      });
      await waitUntil(() => mocks.genieProgress.mock.calls.length > 0);
      await act(async () => {
        progress.resolve(RUNNING);
        await new Promise((resolve) => setTimeout(resolve, 0));
      });
      await settle(6);
    });
    const announcer = document.querySelector('[data-genie-announcer]')!;
    expect(announcer.getAttribute('role')).toBe('status');
    // A weak check: this step lasts milliseconds, so only a misplaced first
    // "0s" could trip it. Silence over seconds of a held stage is proven with
    // fake timers in components/mortgage/useGenieAnnouncer.test.tsx ("speaks
    // each stage change once, and nothing while the stage holds").
    expect(asked.every((phrase) => !/\d+\s*s\b/.test(phrase)), 'no elapsed-seconds ticker is spoken').toBe(true);

    const landed = await reader.step('The answer lands', async () => {
      await waitUntil(() => mocks.genieProgress.mock.calls.length > 1);
      await act(async () => {
        terminal.resolve(TERMINAL);
        await new Promise((resolve) => setTimeout(resolve, 0));
      });
      await waitUntil(() => mocks.genieComplete.mock.calls.length === 1);
      await act(async () => {
        complete.resolve(ANSWER);
        await new Promise((resolve) => setTimeout(resolve, 0));
      });
      await waitUntil(() => document.querySelectorAll('.genie__msg--ai .genie-answer').length === 1);
      await settle(6);
    });
    const ready = landed.filter((phrase) => phrase.startsWith('polite: Answer ready'));
    expect(ready).toHaveLength(1);

    const answer = await reader.step('Read the answer with the virtual cursor', async () => {
      await nextUntil(/^note, Answer source: Databricks Genie Conversation API/);
      await nextUntil(/^end of paragraph/);
    });
    expect(answer.join(' ')).toContain('There are 1,204 in-the-money borrowers in Illinois.');

    const toggle = Array.from(document.querySelectorAll<HTMLButtonElement>('.genie-proof-toggle button'))[0];
    const onToggle = await reader.step('Focus Show proof', async () => {
      await act(async () => toggle.focus());
    });
    expect(onToggle).toEqual(['button, Show proof, not expanded']);

    const proof = await reader.step('Press Show proof: the provenance dialog opens', async () => {
      await act(async () => toggle.click());
      await settle(6);
    });
    expect(proof[0]).toBe('dialog, Genie answer proof');
    expect(proof.at(-1)).toMatch(/^button, Close Genie proof/);

    const provenance = await reader.step('Read the provenance with the virtual cursor', async () => {
      await nextUntil(/^end of dialog, Genie answer proof/);
    });
    const spoken = provenance.join('\n');
    expect(spoken).toContain('Trusted SELECT on curated assets');
    expect(spoken).toContain('840 ms');
    // The source asset is its own evidence button (not only a word in the SQL).
    expect(provenance).toContain('button, mip.gold.borrower_360');
    expect(spoken).toContain('SELECT COUNT(*) AS borrowers');

    const closed = await reader.step('Escape closes the proof; focus returns to its toggle', async () => {
      await act(async () => {
        window.dispatchEvent(new KeyboardEvent('keydown', { key: 'Escape' }));
      });
      await settle(6);
    });
    expect(document.activeElement?.textContent).toContain('Show proof');
    expect(closed.at(-1)).toMatch(/^button, Show proof, not expanded/);

    await expectTranscript('genie-panel', reader);
  }, 60_000);
});
