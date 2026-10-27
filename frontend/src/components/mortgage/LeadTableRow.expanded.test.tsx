/**
 * @vitest-environment happy-dom
 *
 * The expanded row's lazy body (LeadTableRow.expanded: the Borrower 360
 * preview and the workflow strip), cut out of the shared leadsQuery
 * (LeadTable) chunk to fund tail T1c (2026-10-26):
 *  - a closed row never loads the body's chunk; an expand loads it, with one
 *    status line until it lands, then renders the body with the row's props;
 *  - once loaded, a later expand renders the body at once;
 *  - the table warms the chunk on idle after it mounts, and when the cursor
 *    lands on a row, before any expand (code only, no read);
 *  - no module outside the body's own chunk (and the lazy Triage deck)
 *    imports the preview, the workflow strip or the lifecycle control.
 * The body here is a stub that counts its module's evaluation. The real
 * body's content is pinned by the LeadTableRow and LeadTable suites (which
 * load it up front); a chunk that fails is LeadTableRow.expanded.failure.
 */
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { act } from 'react';
import { createRoot, type Root } from 'react-dom/client';
import { MemoryRouter } from 'react-router';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
// @ts-expect-error Frontend app types intentionally exclude Node globals; this
// test reads the component sources under Vitest only.
import { readFileSync, readdirSync } from 'node:fs';
import type { LeadSummary } from '../../types';
import { installLocalStorage } from '../../test/installLocalStorage';

declare const process: { cwd(): string };

const chunk = vi.hoisted(() => ({ evaluations: 0, rendered: [] as Array<{ id: string; approval: string | undefined }> }));
const warm = vi.hoisted(() => ({ onIdle: vi.fn(), onEngage: vi.fn() }));

vi.mock('./LeadRowExpanded', () => {
  chunk.evaluations += 1;
  return {
    LeadRowExpanded: ({ lead, approval }: { lead: LeadSummary; approval: string | undefined }) => {
      chunk.rendered.push({ id: lead.borrower_id, approval });
      return <div data-testid={`expanded-body-${lead.borrower_id}`}>body</div>;
    },
  };
});

// The table's two warm-up calls, observed (they load nothing here, so the
// slot tests below own the chunk's one evaluation). The slot is the real one.
vi.mock('./LeadTableRow.expanded', async (importOriginal) => ({
  ...(await importOriginal<typeof import('./LeadTableRow.expanded')>()),
  preloadLeadRowExpanded: () => {
    warm.onEngage();
    return Promise.resolve();
  },
  preloadLeadRowExpandedOnIdle: () => {
    warm.onIdle();
    return () => undefined;
  },
}));

vi.mock('../AppContext', () => ({
  useApp: () => ({
    approvals: {},
    setApproval: vi.fn(),
    openConsoleRecentActivity: vi.fn(),
    setLastBorrowerId: vi.fn(),
    saveLead: vi.fn(),
    isLeadSaved: () => false,
    setDrawer: vi.fn(),
    showEvidence: true,
    showConfidence: true,
    canApprove: true,
    actorEmail: 'approver.one@summit.example',
    sessionStatus: 'ready',
    density: 'comfortable',
  }),
}));

vi.mock('../../lib/api', async (importOriginal) => ({
  ...(await importOriginal<typeof import('../../lib/api')>()),
  api: {
    draftOutreach: vi.fn(),
    approve: vi.fn(),
    reject: vi.fn(),
    campaign: vi.fn(),
    auditReceipt: () => new Promise(() => {}),
    salesTeam: () => Promise.resolve({ members: [] }),
  },
}));

import { LeadTable } from './LeadTable';
import { LeadTableRow } from './LeadTableRow';

const IDS = ['B-EXPANDCHUNK01', 'B-EXPANDCHUNK02'];

function lead(borrowerId: string, index: number): LeadSummary {
  return {
    borrower_id: borrowerId,
    clip: `clip_${borrowerId}`,
    display_name: `Owner ${borrowerId}`,
    city: 'Chicago',
    state: 'IL',
    zip: '60611',
    segment_codes: ['itm'],
    equity_estimate: 250000,
    rate_spread_bps: 120,
    opportunity_score: 90 - index,
    confidence: 80,
    recommended_offer_code: 'refi',
    recommended_offer: 'Refinance',
    why_now: 'test',
    evidence_ids: ['ev-1'],
    approval_status: 'pending',
  } as unknown as LeadSummary;
}

const LEADS = IDS.map(lead);
const noop = vi.fn();

async function flush() {
  await act(async () => {
    await new Promise((resolve) => setTimeout(resolve, 0));
  });
}

describe('the expanded row loads its body on the expand (LeadTableRow.expanded)', () => {
  let root: Root;

  beforeEach(() => {
    document.body.innerHTML = '<table><tbody id="root"></tbody></table>';
    root = createRoot(document.getElementById('root') as HTMLElement);
  });

  afterEach(() => {
    act(() => root.unmount());
    document.body.innerHTML = '';
  });

  function renderRow(row: LeadSummary, isOpen: boolean, approval?: string) {
    act(() => {
      root.render(
        <QueryClientProvider client={new QueryClient()}>
          <MemoryRouter>
            <LeadTableRow
              lead={row}
              virtualIndex={0}
              isOpen={isOpen}
              approval={approval}
              isSelected={false}
              isSelectable
              isApprovalEligible
              bulkApproving={false}
              salesBusy={false}
              salesTeamCount={1}
              onToggleRow={noop}
              onToggleSelect={noop}
              onApprove={noop}
              onReject={noop}
              onOpenDisposition={noop}
              onAssignmentUpdate={noop}
            />
          </MemoryRouter>
        </QueryClientProvider>,
      );
    });
  }

  const pending = (id: string) => document.querySelector(`[data-testid="lead-preview-pending-${id}"]`);
  const body = (id: string) => document.querySelector(`[data-testid="expanded-body-${id}"]`);

  it('a closed row never loads the chunk; the expand loads it, with one status line until it lands', async () => {
    renderRow(LEADS[0], false);
    await flush();
    expect(chunk.evaluations).toBe(0);
    expect(document.querySelector('tr.tbl__expand')).toBeNull();

    renderRow(LEADS[0], true, 'approved');
    // The first render after the expand: the chunk is on the wire.
    const line = pending(IDS[0]);
    expect(line?.getAttribute('role')).toBe('status');
    expect(line?.textContent).toBe('Loading the preview…');
    expect(line?.closest('tr.tbl__expand')).not.toBeNull();
    // Pinned to the scrollport's left edge like the preview it stands in for.
    expect(line?.classList.contains('tbl__expand-inner--pinned')).toBe(true);
    expect(body(IDS[0])).toBeNull();

    await vi.waitFor(async () => {
      await flush();
      expect(body(IDS[0])).not.toBeNull();
    });
    expect(pending(IDS[0])).toBeNull();
    expect(chunk.evaluations).toBe(1);
    expect(chunk.rendered.at(-1)).toEqual({ id: IDS[0], approval: 'approved' });
  });

  it('once loaded, a later expand renders the body at once', async () => {
    renderRow(LEADS[0], true);
    await vi.waitFor(async () => {
      await flush();
      expect(body(IDS[0])).not.toBeNull();
    });

    renderRow(LEADS[1], true);

    expect(pending(IDS[1])).toBeNull();
    expect(body(IDS[1])).not.toBeNull();
    expect(chunk.evaluations).toBe(1);
  });
});

describe('the table warms the body chunk before an expand (code only)', () => {
  let container: HTMLDivElement;
  let root: Root;

  beforeEach(() => {
    warm.onIdle.mockClear();
    warm.onEngage.mockClear();
    installLocalStorage();
    Element.prototype.scrollIntoView = vi.fn() as unknown as Element['scrollIntoView'];
    container = document.createElement('div');
    document.body.appendChild(container);
    root = createRoot(container);
    act(() => {
      root.render(
        <QueryClientProvider client={new QueryClient({ defaultOptions: { queries: { retry: false } } })}>
          <MemoryRouter initialEntries={['/lead-queue']}>
            <LeadTable leads={LEADS} />
          </MemoryRouter>
        </QueryClientProvider>,
      );
    });
  });

  afterEach(() => {
    act(() => root.unmount());
    container.remove();
  });

  it('schedules one idle preload as it mounts, and no engagement preload yet', () => {
    expect(warm.onIdle).toHaveBeenCalledTimes(1);
    expect(warm.onEngage).not.toHaveBeenCalled();
  });

  it('warms the chunk when the cursor lands on a row, before any expand', () => {
    const checkbox = container.querySelector<HTMLInputElement>(`[data-testid="lead-select-${IDS[1]}"]`)!;
    act(() => checkbox.focus());

    expect(container.querySelector('tr.is-cursor')?.getAttribute('data-borrower-row')).toBe(IDS[1]);
    expect(warm.onEngage).toHaveBeenCalledTimes(1);
    expect(container.querySelector('tr.tbl__expand')).toBeNull();
  });
});

describe('the body stays out of the leadsQuery chunk', () => {
  const BODY_MODULES = 'LeadRowPreview|LeadRowWorkflowPanel|AssignmentLifecycleAdvance|LeadRowExpanded';
  // A value import (never `import type`, which is erased) of one of the body's modules.
  const VALUE_IMPORT = new RegExp(`^\\s*import\\s+(?!type\\b)[^;]*from\\s+'[^']*/(${BODY_MODULES})'`, 'gm');

  function sources(dir: string): string[] {
    return (readdirSync(dir, { withFileTypes: true }) as Array<{ name: string; isDirectory(): boolean }>)
      .flatMap((entry) => (entry.isDirectory()
        ? sources(`${dir}${entry.name}/`)
        : /\.tsx?$/.test(entry.name) && !/\.test\.tsx?$/.test(entry.name) ? [`${dir}${entry.name}`] : []));
  }

  it('only the body chunk and the lazy Triage deck import its modules; the slot imports it lazily', () => {
    const root = `${process.cwd()}/src/`;
    const files = sources(root);
    const importers: Record<string, string[]> = {};
    for (const file of files) {
      const found = [...(readFileSync(file, 'utf8') as string).matchAll(VALUE_IMPORT)].map((match) => match[1]);
      if (found.length > 0) importers[file.slice(root.length)] = found.sort();
    }

    expect(files.length).toBeGreaterThan(300);
    expect(importers).toEqual({
      'components/mortgage/LeadRowExpanded.tsx': ['LeadRowPreview', 'LeadRowWorkflowPanel'],
      'components/mortgage/LeadRowWorkflowPanel.tsx': ['AssignmentLifecycleAdvance'],
      'components/mortgage/TriageDeck.tsx': ['LeadRowExpanded'],
    });
    expect(readFileSync(`${root}components/mortgage/LeadTableRow.expanded.tsx`, 'utf8') as string)
      .toContain("lazyModule(() => import('./LeadRowExpanded'))");
  });
});
