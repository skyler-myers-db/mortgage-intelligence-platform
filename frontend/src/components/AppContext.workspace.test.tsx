/**
 * @vitest-environment happy-dom
 *
 * The Console workspace through the real AppProvider (audit states-04,
 * stack-09 item 3): every failure shows the fixed per-action copy (plus the
 * binding's sentence for a 409 actor_changed) and NEVER the error's own
 * message; the optimistic save or remove rolls back; the writes run on the
 * client's mutation cache. Only lib/api is mocked.
 */
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { act, useEffect } from 'react';
import { createRoot, type Root } from 'react-dom/client';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { ApiError } from '../lib/apiTransport';
import { WORKSPACE_WRITE_KEY } from '../lib/mutations/workspace';
import type { SavedDraft, SavedLead } from '../types';

const apiMocks = vi.hoisted(() => ({
  session: vi.fn(),
  workspace: vi.fn(),
  saveWorkspaceLead: vi.fn(),
  deleteWorkspaceLead: vi.fn(),
  saveWorkspaceDraft: vi.fn(),
  deleteWorkspaceDraft: vi.fn(),
}));

vi.mock('../lib/api', async (importOriginal) => ({
  ...(await importOriginal<typeof import('../lib/api')>()),
  api: apiMocks,
}));
vi.mock('../lib/configOptionsQuery', () => ({ useConfigOptionsQuery: () => ({ data: { lender_name: 'Summit Mortgage', rum_enabled: false } }) }));

import { AppProvider, useApp } from './AppContext';

const SENTINEL = 'SENTINEL raw transport detail 500 Internal Server Error';
const BORROWER = 'B-0000000000001';
const SAVED: SavedLead = {
  borrower_id: BORROWER,
  city: 'Chicago',
  state: 'IL',
  zip: '60611',
  recommended_offer: 'Rate-and-term refi',
  opportunity_score: 82,
  confidence: 0.8,
  saved_at: '2026-07-14T14:00:00Z',
  updated_at: '2026-07-14T14:00:00Z',
} as SavedLead;
const DRAFT = { borrower_id: BORROWER, channel: 'email' } as SavedDraft;
const ACTOR_CHANGED = 'Nothing was recorded. This tab resets for the new user.';

let app: ReturnType<typeof useApp> | null = null;
const seen: Array<string | null> = [];
/** Records the facade and every workspaceError it shows, after each commit. */
function Capture() {
  const context = useApp();
  useEffect(() => {
    app = context;
    if (seen[seen.length - 1] !== context.workspaceError) seen.push(context.workspaceError);
  }, [context]);
  return null;
}
const current = () => {
  if (!app) throw new Error('AppProvider did not render');
  return app;
};

let root: Root;
let queryClient: QueryClient;

beforeEach(() => {
  document.body.innerHTML = '<div id="root"></div>';
  root = createRoot(document.getElementById('root') as HTMLElement);
  queryClient = new QueryClient({ defaultOptions: { queries: { retry: false }, mutations: { retry: false } } });
  apiMocks.session.mockResolvedValue({ can_access_admin: false, can_approve: true });
  apiMocks.workspace.mockResolvedValue({ saved_leads: [SAVED], saved_drafts: [DRAFT] });
  seen.length = 0;
  app = null;
});

afterEach(() => {
  act(() => root.unmount());
  queryClient.clear();
  document.body.innerHTML = '';
  vi.clearAllMocks();
});

async function mount() {
  await act(async () => {
    root.render(
      <QueryClientProvider client={queryClient}>
        <AppProvider>
          <Capture />
        </AppProvider>
      </QueryClientProvider>,
    );
  });
  await settle();
}

async function settle() {
  for (let i = 0; i < 6; i += 1) {
    await act(async () => {
      await new Promise((resolve) => window.setTimeout(resolve, 0));
    });
  }
}

const refused = () => new ApiError(SENTINEL, { path: `/api/v1/workspace/leads/${BORROWER}`, status: 409, reason: 'actor_changed' });

describe('workspace failures never show an error message', () => {
  it('a failed load shows the fixed copy', async () => {
    apiMocks.workspace.mockRejectedValue(new ApiError(SENTINEL, { path: '/api/v1/workspace', status: 500 }));
    await mount();
    await vi.waitFor(() => expect(current().workspaceError).toBe("Couldn't load the saved workspace."));
    expect(seen.join('|')).not.toContain('SENTINEL');
    expect(current().workspaceStatus).toBe('error');
  });

  it('an actor_changed save reads as the user change and rolls the optimistic lead back', async () => {
    apiMocks.workspace.mockResolvedValue({ saved_leads: [], saved_drafts: [] });
    let fail: (error: unknown) => void = () => undefined;
    apiMocks.saveWorkspaceLead.mockReturnValue(new Promise((_resolve, reject) => {
      fail = reject;
    }));
    await mount();
    act(() => current().saveLead({ borrower_id: BORROWER, city: 'Chicago', state: 'IL', zip: '60611' } as SavedLead));
    expect(current().isLeadSaved(BORROWER), 'optimistic').toBe(true);
    expect(queryClient.isMutating({ mutationKey: [...WORKSPACE_WRITE_KEY] }), 'on the mutation cache').toBe(1);

    await act(async () => fail(refused()));
    await vi.waitFor(() => expect(current().workspaceError).toBe(`Couldn't save the lead. ${ACTOR_CHANGED}`));
    expect(current().isLeadSaved(BORROWER), 'rolled back').toBe(false);
    expect(seen.join('|')).not.toContain('SENTINEL');
  });

  it('a failed remove restores the lead, with copy built from the fixed sentence only', async () => {
    apiMocks.deleteWorkspaceLead.mockRejectedValue(new Error(SENTINEL));
    await mount();
    expect(current().isLeadSaved(BORROWER)).toBe(true);
    act(() => current().removeSavedLead(BORROWER));
    await vi.waitFor(() => expect(current().workspaceError).toBe("Couldn't remove the saved lead."));
    expect(current().savedLeads[BORROWER]).toEqual(SAVED);
    expect(seen.join('|')).not.toContain('SENTINEL');
  });

  it('a failed draft save rejects to its caller and shows the fixed copy', async () => {
    apiMocks.saveWorkspaceDraft.mockRejectedValue(refused());
    await mount();
    const draft = { borrower_id: BORROWER, generation_id: 'gen-1', response_hash: 'a'.repeat(64) };
    let caught: unknown = null;
    await act(async () => {
      await current().saveDraft(draft).catch((err: unknown) => {
        caught = err;
      });
    });
    expect(caught).toBeInstanceOf(ApiError);
    await vi.waitFor(() => expect(current().workspaceError).toBe(`Couldn't save the draft. ${ACTOR_CHANGED}`));
  });

  it('a failed draft remove restores the draft', async () => {
    apiMocks.deleteWorkspaceDraft.mockRejectedValue(new ApiError(SENTINEL, { path: '/api/v1/workspace/drafts/x', status: 503 }));
    await mount();
    act(() => current().removeSavedDraft(BORROWER, 'email'));
    await vi.waitFor(() => expect(current().workspaceError).toBe("Couldn't remove the draft."));
    expect(current().savedDrafts[`${BORROWER}::email`]).toEqual(DRAFT);
    expect(seen.join('|')).not.toContain('SENTINEL');
  });

  it('a draft save without audited proof is refused before any write', async () => {
    await mount();
    await expect(current().saveDraft({ borrower_id: BORROWER, generation_id: '', response_hash: 'x' })).rejects.toThrow(
      'A borrower and audited draft proof are required.',
    );
    expect(apiMocks.saveWorkspaceDraft).not.toHaveBeenCalled();
  });
});
