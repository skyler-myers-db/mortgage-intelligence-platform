/**
 * @vitest-environment happy-dom
 */

import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { act } from 'react';
import { createRoot, type Root } from 'react-dom/client';
import { MemoryRouter } from 'react-router-dom';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';

(globalThis as unknown as { IS_REACT_ACT_ENVIRONMENT: boolean }).IS_REACT_ACT_ENVIRONMENT = true;

const { myAuditEvents, appContext } = vi.hoisted(() => ({
  myAuditEvents: vi.fn(),
  appContext: {
    consoleOpen: true,
    setConsoleOpen: vi.fn(),
    theme: 'dark',
    setTheme: vi.fn(),
    accent: 'bright',
    setAccent: vi.fn(),
    density: 'compact',
    setDensity: vi.fn(),
    lender: 'Summit Mortgage',
    showEvidence: true,
    setShowEvidence: vi.fn(),
    showConfidence: true,
    setShowConfidence: vi.fn(),
    setGenieOpen: vi.fn(),
    savedLeads: {},
    savedDrafts: {},
    workspaceStatus: 'ready',
    workspaceError: null,
    refreshWorkspace: vi.fn(),
    recentActivityFocusRequest: 1,
    acknowledgeRecentActivityFocus: vi.fn(),
  },
}));

vi.mock('../AppContext', () => ({
  useApp: () => appContext,
}));

vi.mock('../../lib/api', async (importOriginal) => {
  const actual = await importOriginal<typeof import('../../lib/api')>();
  return {
    ...actual,
    api: {
      ...actual.api,
      myAuditEvents: (...args: unknown[]) => myAuditEvents(...args),
    },
  };
});

import { Console } from './Console';

describe('Console actor-scoped recent activity', () => {
  let container: HTMLDivElement;
  let root: Root;

  beforeEach(() => {
    vi.clearAllMocks();
    myAuditEvents.mockImplementation(async (
      _limit: number,
      _signal: AbortSignal,
      cursor: string | null,
    ) => {
      if (cursor === 'cursor-page-2') {
        return {
          items: [{
            event_type: 'OUTREACH_REJECT',
            entity_type: 'borrower',
            subject_id: 'B-BBBBBBBBBBBB2',
            created_at: '2026-07-14T13:00:00Z',
          }],
          next_cursor: null,
        };
      }
      return {
        items: [{
          event_type: 'OUTREACH_APPROVE',
          entity_type: 'borrower',
          subject_id: 'B-AAAAAAAAAAAA1',
          created_at: '2026-07-14T12:00:00Z',
          actor: 'private.operator@example.com',
          payload_json: { borrower_email: 'private.borrower@example.com' },
        }],
        next_cursor: 'cursor-page-2',
      };
    });
    container = document.createElement('div');
    document.body.appendChild(container);
    root = createRoot(container);
  });

  afterEach(() => {
    act(() => root.unmount());
    container.remove();
  });

  async function waitFor(assertion: () => void) {
    let lastError: unknown = null;
    for (let attempt = 0; attempt < 20; attempt += 1) {
      try {
        assertion();
        return;
      } catch (error) {
        lastError = error;
        await act(async () => {
          await new Promise((resolve) => setTimeout(resolve, 0));
        });
      }
    }
    throw lastError;
  }

  it('renders, focuses, and pages safe summaries without exposing Admin or payload data', async () => {
    const queryClient = new QueryClient({
      defaultOptions: { queries: { retry: false } },
    });
    act(() => {
      root.render(
        <QueryClientProvider client={queryClient}>
          <MemoryRouter>
            <Console />
          </MemoryRouter>
        </QueryClientProvider>,
      );
    });
    await waitFor(() => {
      expect(container.textContent).toContain('Outreach approved');
    });

    const activity = container.querySelector<HTMLElement>('#console-recent-activity');
    expect(activity).not.toBeNull();
    expect(document.activeElement).toBe(activity);
    expect(appContext.acknowledgeRecentActivityFocus).toHaveBeenCalledTimes(1);
    expect(activity?.textContent).toContain('My recent activity');
    expect(activity?.textContent).toContain('Outreach approved');
    expect(activity?.textContent).toContain('B-AAAAAAAAAAAA1');
    expect(container.textContent).not.toContain('private.operator@example.com');
    expect(container.textContent).not.toContain('private.borrower@example.com');
    expect(container.textContent).not.toContain('Admin audit');

    const loadOlder = [...container.querySelectorAll('button')]
      .find((button) => button.textContent?.trim() === 'Load older');
    expect(loadOlder).toBeDefined();
    act(() => loadOlder?.click());
    await waitFor(() => {
      expect(activity?.textContent).toContain('Outreach rejected');
    });

    expect(myAuditEvents).toHaveBeenLastCalledWith(
      8,
      expect.any(AbortSignal),
      'cursor-page-2',
    );
    expect(activity?.textContent).toContain('B-BBBBBBBBBBBB2');
  });
});
