/**
 * @vitest-environment happy-dom
 *
 * The ranked table's header (LeadTableHeader), rendered on its own:
 *  - flow-04 / critic-03: the export receipt's audit id links to its ledger
 *    row for an administrator AND for a read-only auditor (the session's
 *    can_read_audit, the same decision require_audit_reader makes); anyone
 *    else sees the plain id. A header with no finished export reads no
 *    session, so it renders without a QueryClientProvider.
 */
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import type { ReactNode } from 'react';
import { MemoryRouter } from 'react-router';
import { describe, expect, it } from 'vitest';
import { mount } from '../../test/render';
import type { LeadExportReceipt } from '../../lib/apiClients/leadExport';
import { sessionQueryOptions } from '../../lib/sessionQuery';
import type { SessionResponse } from '../../types';
import { LeadTableHeader, type LeadTableHeaderProps } from './LeadTableHeader';
import type { LeadCsvExportState } from './useLeadCsvExport';

const AUDIT_ID = '0f8fad5b-d9cb-469f-a165-70867728950e';

function receipt(): LeadExportReceipt {
  return {
    audit_event_id: AUDIT_ID,
    event_type: 'LEAD_EXPORT',
    actor: 'lo.one@summit.example',
    scope: 'loaded',
    row_count: 2,
    csv_sha256: 'a'.repeat(64),
    borrower_ids_sha256: 'b'.repeat(64),
    filter_fingerprint: 'c'.repeat(64),
  } as LeadExportReceipt;
}

const DONE: LeadCsvExportState = {
  status: 'done', rowCount: 2, notice: null, receipt: receipt(), truncatedOf: null, campaignHref: null,
} as LeadCsvExportState;

function props(overrides: Partial<LeadTableHeaderProps> = {}): LeadTableHeaderProps {
  return {
    singleKeysOn: true,
    approverActive: false,
    actorEmail: null,
    view: 'default',
    exportState: { status: 'idle' },
    canAccessAdmin: false,
    csvExportCount: 2,
    csvExportNoun: 'leads',
    csvExportExcluded: 0,
    exportBlockedReason: null,
    onExport: () => undefined,
    ...overrides,
  };
}

function withSession(session: Partial<SessionResponse> | null, ui: ReactNode) {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  if (session) client.setQueryData(sessionQueryOptions().queryKey, session as SessionResponse);
  return (
    <QueryClientProvider client={client}>
      <MemoryRouter>{ui}</MemoryRouter>
    </QueryClientProvider>
  );
}

const receiptLine = (container: HTMLElement) => container.querySelector<HTMLElement>('[data-testid="lead-export-receipt"]');

describe('LeadTableHeader export receipt', () => {
  it('links the audit id for an administrator', async () => {
    const { container } = await mount(withSession(
      { can_access_admin: true, can_read_audit: true },
      <LeadTableHeader {...props({ exportState: DONE, canAccessAdmin: true })} />,
    ));
    const link = receiptLine(container)!.querySelector('a');
    expect(link?.textContent).toBe(AUDIT_ID);
    expect(link?.getAttribute('href')).toContain(AUDIT_ID);
  });

  it('links the audit id for a read-only auditor who is not an administrator', async () => {
    const { container } = await mount(withSession(
      { can_access_admin: false, can_read_audit: true },
      <LeadTableHeader {...props({ exportState: DONE, canAccessAdmin: false })} />,
    ));
    const link = receiptLine(container)!.querySelector('a');
    expect(link?.textContent).toBe(AUDIT_ID);
    expect(link?.getAttribute('href')).toContain(AUDIT_ID);
  });

  it('shows the plain id to someone who may not read the ledger', async () => {
    const { container } = await mount(withSession(
      { can_access_admin: false, can_read_audit: false },
      <LeadTableHeader {...props({ exportState: DONE, canAccessAdmin: false })} />,
    ));
    const line = receiptLine(container)!;
    expect(line.querySelector('a')).toBeNull();
    expect(line.querySelector('.mono')?.textContent).toBe(AUDIT_ID);
    expect(line.textContent).toBe(`Exported 2 rows · audit ${AUDIT_ID}`);
  });

  it('renders with no QueryClientProvider while no export is done', async () => {
    const { container } = await mount(<MemoryRouter><LeadTableHeader {...props()} /></MemoryRouter>);
    expect(receiptLine(container)).toBeNull();
    expect(container.querySelector('[data-testid="lead-export"]')).not.toBeNull();
  });
});
