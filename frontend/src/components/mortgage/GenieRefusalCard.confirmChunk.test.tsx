/**
 * @vitest-environment happy-dom
 *
 * A consented-capture step whose chunk fails to load (a retired chunk after
 * a deploy): the card shows its fixed error line and never files a report
 * on the reporter's behalf (D-audit-reads-d). Its own file, because the
 * confirm module is mocked to throw at import here.
 */
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { act } from 'react';
import { createRoot, type Root } from 'react-dom/client';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';

(globalThis as unknown as { IS_REACT_ACT_ENVIRONMENT: boolean }).IS_REACT_ACT_ENVIRONMENT = true;

const report = vi.fn();
vi.mock('../../lib/apiClients/genieRefusalReport', () => ({
  genieRefusalReportApi: { report: (...args: unknown[]) => report(...args) },
}));
vi.mock('./GenieRefusalReportConfirm', () => {
  throw new Error('Failed to fetch dynamically imported module');
});

import { GenieRefusalCard } from './GenieRefusalCard';

describe('GenieRefusalCard with an unloadable confirm chunk', () => {
  let container: HTMLDivElement;
  let root: Root;
  beforeEach(() => {
    report.mockReset();
    container = document.createElement('div');
    document.body.appendChild(container);
    root = createRoot(container);
  });
  afterEach(() => {
    act(() => root.unmount());
    container.remove();
  });

  it('shows the fixed line and files nothing', async () => {
    const client = new QueryClient();
    client.setQueryData(['session', 'access'], { can_access_admin: false, can_approve: false, refusal_text_capture_enabled: true });
    act(() => root.render(
      <QueryClientProvider client={client}>
        <GenieRefusalCard
          payload={{
            answer: 'I cannot select or rank borrowers on that criterion.',
            question: '',
            source: 'refused',
            trusted_assets: [],
            refusal_reason: 'unreviewed_criterion',
            refusal_report_hash: 'a'.repeat(64),
          }}
          question="Which zyrplax borrowers are eligible for a HELOC?"
        />
      </QueryClientProvider>,
    ));
    act(() => container.querySelector<HTMLButtonElement>('[data-testid="genie-refusal-report"]')?.click());
    for (let i = 0; i < 4; i += 1) {
      await act(async () => {
        await new Promise((resolve) => setTimeout(resolve, 0));
      });
    }
    expect(container.querySelector('[role="alert"]')?.textContent).toBe(
      'The report options could not load. Please try again.',
    );
    expect(container.querySelector('[data-testid="genie-refusal-confirm"]')).toBeNull();
    expect(report).not.toHaveBeenCalled();
  });
});
