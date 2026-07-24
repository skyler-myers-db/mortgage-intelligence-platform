/**
 * @vitest-environment happy-dom
 */

import { act } from 'react';
import { createRoot, type Root } from 'react-dom/client';
import { MemoryRouter } from 'react-router';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { ApiError } from '../../lib/api';
import type { GenieAnswer, GenieStartResult } from '../../types';

const mocks = vi.hoisted(() => ({
  genie: vi.fn(),
  genieAction: vi.fn(),
  genieFeedback: vi.fn(),
  genieStart: vi.fn(),
  refreshWorkspace: vi.fn(),
  setDrawer: vi.fn(),
  setGenieOpen: vi.fn(),
}));

vi.mock('../../lib/api', async () => {
  const actual = await vi.importActual<typeof import('../../lib/api')>('../../lib/api');
  return {
    ...actual,
    api: {
      genie: mocks.genie,
      genieAction: mocks.genieAction,
      genieFeedback: mocks.genieFeedback,
      genieStart: mocks.genieStart,
    },
  };
});

vi.mock('../AppContext', () => ({
  useApp: () => ({
    genieOpen: true,
    setGenieOpen: mocks.setGenieOpen,
    lender: 'Test Lender',
    refreshWorkspace: mocks.refreshWorkspace,
    setDrawer: mocks.setDrawer,
  }),
}));

import { GenieChat } from './GenieChat';

(globalThis as unknown as { IS_REACT_ACT_ENVIRONMENT: boolean }).IS_REACT_ACT_ENVIRONMENT = true;

const START: GenieStartResult = {
  conversation_id: 'conv-bootstrap',
  trusted_assets: ['mip.gold.borrower_360'],
  sample_questions: ['Show current opportunity volume'],
};

function answer(overrides: Partial<GenieAnswer> = {}): GenieAnswer {
  return {
    answer: 'The governed opportunity result is ready.',
    source: 'genie',
    trusted_assets: ['mip.gold.borrower_360'],
    conversation_id: 'conv-current',
    message_id: 'msg-current',
    genie_status: 'COMPLETED',
    follow_up_questions: [],
    ...overrides,
  };
}

function setInputValue(input: HTMLInputElement, value: string) {
  const setter = Object.getOwnPropertyDescriptor(HTMLInputElement.prototype, 'value')?.set;
  if (!setter) throw new Error('missing input value setter');
  setter.call(input, value);
  input.dispatchEvent(new Event('input', { bubbles: true }));
  input.dispatchEvent(new Event('change', { bubbles: true }));
}

function installLocalStorage() {
  const values = new Map<string, string>();
  Object.defineProperty(window, 'localStorage', {
    configurable: true,
    value: {
      getItem: (key: string) => values.get(key) ?? null,
      setItem: (key: string, value: string) => values.set(key, value),
      removeItem: (key: string) => values.delete(key),
      clear: () => values.clear(),
    },
  });
}

function setViewport(width: number, height: number) {
  Object.defineProperties(window, {
    innerWidth: { configurable: true, value: width },
    innerHeight: { configurable: true, value: height },
  });
}

describe('floating Genie conversation continuity', () => {
  let container: HTMLDivElement;
  let root: Root;

  beforeEach(() => {
    Object.values(mocks).forEach((mock) => mock.mockReset());
    installLocalStorage();
    setViewport(1_440, 900);
    mocks.genieStart.mockResolvedValue(START);
    mocks.genieFeedback.mockResolvedValue({ accepted: true });
    container = document.createElement('div');
    document.body.appendChild(container);
    root = createRoot(container);
  });

  afterEach(() => {
    act(() => root.unmount());
    container.remove();
  });

  function mount() {
    act(() => {
      root.render(
        <MemoryRouter>
          <GenieChat />
        </MemoryRouter>,
      );
    });
  }

  async function waitUntil(condition: () => boolean, timeoutMs = 10_000) {
    const startedAt = Date.now();
    while (!condition()) {
      if (Date.now() - startedAt > timeoutMs) throw new Error('waitUntil timeout');
      await act(async () => new Promise((resolve) => setTimeout(resolve, 10)));
    }
  }

  function input() {
    const field = container.querySelector<HTMLInputElement>('input[aria-label="Ask Genie"]');
    if (!field) throw new Error('floating Genie input not rendered');
    return field;
  }

  function button(ariaLabel: string) {
    const match = container.querySelector<HTMLButtonElement>(`button[aria-label="${ariaLabel}"]`);
    if (!match) throw new Error(`button not rendered: ${ariaLabel}`);
    return match;
  }

  async function clickAndFlush(target: HTMLButtonElement) {
    await act(async () => {
      target.click();
      await new Promise((resolve) => setTimeout(resolve, 0));
    });
  }

  it('preserves the non-modal window controls and fits restored geometry to the viewport', async () => {
    setViewport(390, 600);
    window.localStorage.setItem(
      'mip-genie-chat-size-v1',
      JSON.stringify({ w: 900, h: 900 }),
    );
    window.localStorage.setItem(
      'mip-genie-chat-pos-v1',
      JSON.stringify({ pos: { x: 1_000, y: 1_000 } }),
    );

    mount();

    const dialog = container.querySelector<HTMLElement>('[role="dialog"][aria-label="Genie chat"]');
    expect(dialog).not.toBeNull();
    expect(dialog?.getAttribute('aria-modal')).toBeNull();
    expect(dialog?.getAttribute('aria-hidden')).toBe('false');
    expect(dialog?.classList.contains('is-undocked')).toBe(true);
    expect(dialog?.style.width).toBe('358px');
    expect(dialog?.style.height).toBe('568px');
    expect(dialog?.style.left).toBe('16px');
    expect(dialog?.style.top).toBe('16px');

    expect(button(
      'Resize Genie panel (currently 358 by 568 pixels). Drag any edge or corner, or use arrow keys.',
    )).toBeTruthy();
    expect(button('Re-dock Genie panel to bottom-right')).toBeTruthy();
    expect(button('Start a new Genie thread')).toBeTruthy();
    expect(button('Close Genie')).toBeTruthy();
    expect(container.querySelectorAll('.genie__resize-edge')).toHaveLength(7);
    await waitUntil(() => document.activeElement === input());
  });

  it('continues restored, sample, and typed turns until New thread explicitly resets', async () => {
    window.localStorage.setItem('mip.genie.conversationId', 'conv-restored');
    mocks.genie
      .mockResolvedValueOnce(answer({ conversation_id: 'conv-current' }))
      .mockResolvedValueOnce(answer({ conversation_id: 'conv-next', message_id: 'msg-next' }))
      .mockResolvedValueOnce(answer({ conversation_id: 'conv-new', message_id: 'msg-new' }));

    mount();
    await waitUntil(() => container.textContent?.includes('Show current opportunity volume') ?? false);
    const sample = Array.from(container.querySelectorAll<HTMLButtonElement>('.genie-chat__sample'))
      .find((candidate) => candidate.textContent?.includes('Show current opportunity volume'));
    if (!sample) throw new Error('sample question not rendered');
    await clickAndFlush(sample);
    await waitUntil(() => mocks.genie.mock.calls.length === 1);
    expect(mocks.genie).toHaveBeenNthCalledWith(
      1,
      'Show current opportunity volume',
      'conv-restored',
    );

    await waitUntil(() => window.localStorage.getItem('mip.genie.conversationId') === 'conv-current');
    act(() => setInputValue(input(), 'Break this down by state'));
    await clickAndFlush(button('Ask'));
    await waitUntil(() => mocks.genie.mock.calls.length === 2);
    expect(mocks.genie).toHaveBeenNthCalledWith(2, 'Break this down by state', 'conv-current');

    await waitUntil(() => window.localStorage.getItem('mip.genie.conversationId') === 'conv-next');
    await waitUntil(() => !button('Start a new Genie thread').disabled);
    const resetListener = vi.fn();
    window.addEventListener('mip:genie-conversation-reset', resetListener);
    act(() => button('Start a new Genie thread').click());
    expect(window.localStorage.getItem('mip.genie.conversationId')).toBeNull();
    expect(container.querySelectorAll('.genie__msg')).toHaveLength(0);
    expect(resetListener).toHaveBeenCalledOnce();
    window.removeEventListener('mip:genie-conversation-reset', resetListener);

    act(() => setInputValue(input(), 'Start from the full portfolio'));
    await clickAndFlush(button('Ask'));
    await waitUntil(() => mocks.genie.mock.calls.length === 3);
    expect(mocks.genie).toHaveBeenNthCalledWith(3, 'Start from the full portfolio', null);
  });

  it('does not restore a late bootstrap response after New thread', async () => {
    let resolveStart: ((value: GenieStartResult) => void) | undefined;
    mocks.genieStart.mockReturnValue(new Promise((resolve) => { resolveStart = resolve; }));
    mocks.genie.mockResolvedValueOnce(answer({ conversation_id: 'conv-new' }));

    mount();
    act(() => button('Start a new Genie thread').click());
    await act(async () => {
      resolveStart?.({ ...START, conversation_id: 'conv-late-bootstrap' });
      await new Promise((resolve) => setTimeout(resolve, 0));
    });
    expect(window.localStorage.getItem('mip.genie.conversationId')).toBeNull();

    act(() => setInputValue(input(), 'Start from the full portfolio'));
    await clickAndFlush(button('Ask'));
    await waitUntil(() => mocks.genie.mock.calls.length === 1);
    expect(mocks.genie).toHaveBeenCalledWith('Start from the full portfolio', null);
  });

  it('clears and broadcasts a restored conversation after a forbidden follow-up', async () => {
    window.localStorage.setItem('mip.genie.conversationId', 'conv-restored');
    mocks.genie.mockRejectedValueOnce(new ApiError('conversation is no longer accessible', {
      path: '/api/ask-genie',
      status: 403,
    }));
    const resetListener = vi.fn();
    window.addEventListener('mip:genie-conversation-reset', resetListener);

    mount();
    act(() => setInputValue(input(), 'Continue the prior analysis'));
    await clickAndFlush(button('Ask'));
    await waitUntil(() => mocks.genie.mock.calls.length === 1);
    await waitUntil(() => container.textContent?.includes('Genie session reset') ?? false);

    expect(mocks.genie).toHaveBeenCalledWith('Continue the prior analysis', 'conv-restored');
    expect(window.localStorage.getItem('mip.genie.conversationId')).toBeNull();
    expect(resetListener).toHaveBeenCalledOnce();
    window.removeEventListener('mip:genie-conversation-reset', resetListener);
  });

  it('renders action completion as a governed action result, not a Genie API answer', async () => {
    mocks.genieStart.mockResolvedValue({ ...START, conversation_id: null, sample_questions: [] });
    mocks.genie.mockResolvedValueOnce(answer({
      actions: [{
        id: 'save-1',
        label: 'Save reviewed cohort',
        action_type: 'save_borrowers',
        description: 'Create the reviewed Lead Queue handoff.',
      }],
    }));
    mocks.genieAction.mockResolvedValue({
      ok: true,
      action_type: 'save_borrowers',
      audit_event_id: 'evt-1',
      message: 'Saved 12 borrowers to the reviewed Lead Queue handoff.',
    });

    mount();
    act(() => setInputValue(input(), 'Find the reviewed cohort'));
    await clickAndFlush(button('Ask'));
    await waitUntil(() => container.textContent?.includes('Save reviewed cohort') ?? false);

    await clickAndFlush(button('Run Save reviewed cohort'));
    await clickAndFlush(button('Confirm Save reviewed cohort'));
    await waitUntil(() => mocks.genieAction.mock.calls.length === 1);
    await waitUntil(() => container.textContent?.includes('Saved 12 borrowers') ?? false);

    const assistantMessages = container.querySelectorAll<HTMLElement>('.genie__msg--ai');
    const actionMessage = assistantMessages[assistantMessages.length - 1];
    const source = actionMessage.querySelector<HTMLElement>('.genie-answer__api-source');
    expect(source?.textContent).toContain('Governed action result');
    expect(source?.textContent).not.toContain('Databricks Genie Conversation API');
    expect(source?.getAttribute('aria-label')).toBe('Answer source: Governed action result');
    expect(mocks.refreshWorkspace).toHaveBeenCalledTimes(1);
  });
});
