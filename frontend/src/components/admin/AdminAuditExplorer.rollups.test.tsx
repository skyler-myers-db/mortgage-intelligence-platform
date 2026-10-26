/**
 * @vitest-environment happy-dom
 *
 * The explorer's weekly rollups read the wire's fail-closed event_type
 * (audit quality-04 P2, W5e w5-wire-types A6a): a rollup whose event type
 * was not recorded (null on the wire) renders the neutral 'Event type not
 * recorded' with an em dash for its code. It never borrows a default type
 * or another row's label. The rollups stay the explorer's natural-load read.
 */
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { act } from 'react';
import { createRoot, type Root } from 'react-dom/client';
import { MemoryRouter } from 'react-router';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import type { AuditRollupResponse } from '../../lib/apiClients/audit';

const mocks = vi.hoisted(() => ({
  rollups: [] as AuditRollupResponse[],
  rollupCalls: 0,
}));

vi.mock('../AppContext', () => ({ useApp: () => ({ canAccessAdmin: true, lender: 'Summit Mortgage' }) }));

vi.mock('../../lib/api', async (importOriginal) => {
  const actual = await importOriginal<typeof import('../../lib/api')>();
  return {
    ...actual,
    api: {
      auditRollups: () => {
        mocks.rollupCalls += 1;
        return Promise.resolve(mocks.rollups);
      },
    },
  };
});

vi.mock('../../lib/apiClients/auditExplorer', async (importOriginal) => {
  const actual = await importOriginal<typeof import('../../lib/apiClients/auditExplorer')>();
  return {
    ...actual,
    auditEventPage: () => Promise.resolve({ items: [], next_cursor: null }),
  };
});

import { AdminAuditExplorer } from './AdminAuditExplorer';

function rollup(overrides: Partial<AuditRollupResponse>): AuditRollupResponse {
  return {
    bucket_start: '2026-10-05T00:00:00Z',
    event_type: 'LEAD_EXPORT',
    event_count: 3,
    group_by: 'event_type',
    group_key: 'LEAD_EXPORT',
    ...overrides,
  };
}

let root: Root;
let container: HTMLDivElement;

async function render(): Promise<void> {
  const queryClient = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  await act(async () => {
    root.render(
      <QueryClientProvider client={queryClient}>
        <MemoryRouter initialEntries={['/audit-ledger']}>
          <AdminAuditExplorer />
        </MemoryRouter>
      </QueryClientProvider>,
    );
  });
  for (let index = 0; index < 3; index += 1) {
    await act(async () => {
      await new Promise((resolve) => window.setTimeout(resolve, 0));
    });
  }
}

function tiles(): HTMLElement[] {
  return [...container.querySelectorAll<HTMLElement>('.admin-rollups__grid .admin-rollup')];
}

describe('AdminAuditExplorer rollups and the fail-closed event_type', () => {
  beforeEach(() => {
    mocks.rollupCalls = 0;
    container = document.createElement('div');
    document.body.appendChild(container);
    root = createRoot(container);
  });

  afterEach(() => {
    act(() => root.unmount());
    container.remove();
  });

  it('renders a null event type as not recorded, with an em dash for its code', async () => {
    mocks.rollups = [
      rollup({ event_type: null, group_key: null, event_count: 4 }),
      rollup({ event_type: 'LEAD_EXPORT', event_count: 3 }),
    ];
    await render();

    const [unrecorded, recorded] = tiles();
    expect(tiles()).toHaveLength(2);
    const [label, code] = [...unrecorded.querySelectorAll('span')];
    expect(label.textContent).toBe('Event type not recorded');
    expect(code.className).toContain('mono');
    expect(code.textContent).toBe('—');
    expect(unrecorded.querySelector('strong')?.textContent).toBe('4');
    // The recorded row keeps its own code; the unrecorded one never borrows it.
    expect(recorded.textContent).toContain('LEAD_EXPORT');
    expect(unrecorded.textContent).not.toContain('LEAD_EXPORT');
    expect(unrecorded.textContent).not.toMatch(/null|undefined/);
    expect(mocks.rollupCalls).toBe(1);
  });

  it('keeps two unrecorded rows in different weeks apart', async () => {
    mocks.rollups = [
      rollup({ event_type: null, group_key: null, bucket_start: '2026-10-05T00:00:00Z' }),
      rollup({ event_type: null, group_key: null, bucket_start: '2026-09-28T00:00:00Z' }),
    ];
    await render();

    expect(tiles()).toHaveLength(2);
    expect(tiles().map((tile) => tile.querySelector('span')?.textContent)).toEqual([
      'Event type not recorded',
      'Event type not recorded',
    ]);
  });
});
