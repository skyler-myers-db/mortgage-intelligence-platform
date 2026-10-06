/**
 * @vitest-environment happy-dom
 *
 * A governed Genie action's receipt (audit 2026-09-21 flow-04, the Genie
 * confirmation half): `{message} Audit event <id>.` exactly; the id links to
 * its ledger row for administrators and auditors, and stays mono text for a
 * plain session and for a pending or errored session read. And the bubble's
 * client-only `action_audit_event_id` never leaves the client.
 */
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { act } from 'react';
import { createRoot, type Root } from 'react-dom/client';
import { MemoryRouter } from 'react-router';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import type { GenieActionSuggestion, GenieAnswer, SessionResponse } from '../../types';

const apiMocks = vi.hoisted(() => ({ session: vi.fn(), genieAction: vi.fn() }));

vi.mock('../../lib/api', () => {
  class ApiError extends Error {
    status: number | null;

    constructor(message: string, opts: { path: string; status?: number | null } = { path: '' }) {
      super(message);
      this.status = opts.status ?? null;
    }
  }
  return { api: apiMocks, ApiError };
});

import * as transport from '../../lib/apiTransport';
import { genieApi } from '../../lib/apiClients/genie';
import { auditEventHref } from '../../lib/auditLinks';
import { sessionQueryOptions } from '../../lib/sessionQuery';
import { GOVERNED_ACTION_SOURCE } from '../../lib/genieTurnOutcome';
import { runGenieActionRequest } from './GenieChat.helpers';
import { GenieActionReceipt } from './GenieActionReceipt';

(globalThis as unknown as { IS_REACT_ACT_ENVIRONMENT: boolean }).IS_REACT_ACT_ENVIRONMENT = true;

const EVENT = '0f1e2d3c-0000-4000-8000-0000000000e1';
const MESSAGE = 'Saved 12 borrowers.';

function session(partial: Partial<SessionResponse>): SessionResponse {
  return { can_access_admin: false, can_approve: false, can_read_audit: false, ...partial } as SessionResponse;
}

let container: HTMLDivElement;
let root: Root;

beforeEach(() => {
  container = document.createElement('div');
  document.body.appendChild(container);
  root = createRoot(container);
  Object.values(apiMocks).forEach((mock) => mock.mockReset());
});

afterEach(() => {
  act(() => root.unmount());
  container.remove();
  vi.restoreAllMocks();
});

async function renderReceipt(seed: SessionResponse | 'pending' | 'errored'): Promise<void> {
  const queryClient = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  if (seed === 'pending') apiMocks.session.mockImplementation(() => new Promise(() => undefined));
  else if (seed === 'errored') apiMocks.session.mockRejectedValue(new Error('session unavailable'));
  else queryClient.setQueryData(sessionQueryOptions().queryKey, seed);
  await act(async () => {
    root.render(
      <QueryClientProvider client={queryClient}>
        <MemoryRouter>
          <p>
            <GenieActionReceipt message={MESSAGE} auditEventId={EVENT} />
          </p>
        </MemoryRouter>
      </QueryClientProvider>,
    );
  });
  await act(async () => {
    await Promise.resolve();
  });
}

describe('GenieActionReceipt', () => {
  it.each([
    ['an administrator', session({ can_access_admin: true })],
    ['an auditor', session({ can_read_audit: true })],
  ] as const)('links the id to its ledger row for %s', async (_who, seed) => {
    await renderReceipt(seed);

    expect(container.textContent).toBe(`${MESSAGE} Audit event ${EVENT}.`);
    const link = container.querySelector('a');
    expect(link?.getAttribute('href')).toBe(auditEventHref(EVENT));
    expect(link?.className).toBe('mono');
    expect(link?.textContent).toBe(EVENT);
  });

  it.each([
    ['a plain session', session({})],
    ['a pending session read', 'pending'],
    ['an errored session read', 'errored'],
  ] as const)('keeps the id mono text for %s', async (_who, seed) => {
    await renderReceipt(seed);

    expect(container.textContent).toBe(`${MESSAGE} Audit event ${EVENT}.`);
    expect(container.querySelector('a')).toBeNull();
    expect(container.querySelector('span.mono')?.textContent).toBe(EVENT);
  });
});

describe('action_audit_event_id never leaves the client', () => {
  it('a governed action request built from such a bubble carries no trace of it', async () => {
    const bubble: GenieAnswer = {
      answer: MESSAGE,
      question: '',
      source: GOVERNED_ACTION_SOURCE,
      trusted_assets: [],
      conversation_id: 'conv-1',
      message_id: 'msg-1',
      action_audit_event_id: EVENT,
    };
    const action: GenieActionSuggestion = {
      id: 'save',
      label: 'Save these borrowers',
      action_type: 'save_borrowers',
      description: 'Save the result',
      borrower_ids: [],
      criteria: {},
    } as GenieActionSuggestion;
    apiMocks.genieAction.mockResolvedValue({ ok: true, message: MESSAGE, audit_event_id: EVENT });
    const post = vi.spyOn(transport, 'postJson').mockResolvedValue({ ok: true });

    await runGenieActionRequest(action, bubble, 'conv-1');
    await genieApi.genieAction({ ...action, ...bubble } as unknown as Parameters<typeof genieApi.genieAction>[0]);

    expect(JSON.stringify(apiMocks.genieAction.mock.calls)).not.toContain('action_audit_event_id');
    expect(JSON.stringify(post.mock.calls)).not.toContain('action_audit_event_id');
    expect(JSON.stringify(post.mock.calls)).not.toContain(EVENT);
  });
});
