/**
 * @vitest-environment happy-dom
 *
 * The borrower proof drawer's Math / Evidence / Lineage / Reproduce tabs follow
 * the APG tabs pattern (2026-09-21 audit a11y-02). They used to be click-only
 * buttons with role=tab, every one in the tab order, with no aria-controls and
 * no tabpanel. The drawer body is now the tabpanel the selected tab controls.
 */

import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { act } from 'react';
import { createRoot, type Root } from 'react-dom/client';
import { MemoryRouter } from 'react-router';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { BorrowerProofDrawer } from './BorrowerProofDrawer';

(globalThis as unknown as { IS_REACT_ACT_ENVIRONMENT: boolean }).IS_REACT_ACT_ENVIRONMENT = true;

// The tabs render before the proof read resolves; keep it pending.
vi.mock('../../lib/api', () => ({
  api: { borrowerProof: () => new Promise(() => undefined) },
}));

describe('BorrowerProofDrawer tabs keyboard', () => {
  let root: Root;
  let queryClient: QueryClient;

  beforeEach(async () => {
    document.body.innerHTML = '<div id="root"></div>';
    root = createRoot(document.getElementById('root') as HTMLElement);
    queryClient = new QueryClient({ defaultOptions: { queries: { retry: false } } });
    await act(async () => {
      root.render(
        <QueryClientProvider client={queryClient}>
          <MemoryRouter>
            <BorrowerProofDrawer borrowerId="B-TEST000000001" open onClose={() => undefined} />
          </MemoryRouter>
        </QueryClientProvider>,
      );
    });
  });

  afterEach(() => {
    act(() => root.unmount());
    queryClient.clear();
    document.body.innerHTML = '';
  });

  const tab = (label: string): HTMLButtonElement => {
    const found = [...document.querySelectorAll<HTMLButtonElement>('[role="tab"]')].find(
      (node) => node.textContent?.trim() === label,
    );
    if (!found) throw new Error(`tab ${label} not rendered`);
    return found;
  };
  const panel = (): HTMLElement | null => document.querySelector<HTMLElement>('[role="tabpanel"]');

  function press(key: string): void {
    act(() => {
      document.activeElement?.dispatchEvent(new KeyboardEvent('keydown', { key, bubbles: true, cancelable: true }));
    });
  }

  it('selects tabs with the arrow keys, Home and End, and the body follows as the tabpanel', () => {
    expect(tab('Math').tabIndex).toBe(0);
    expect(tab('Evidence').tabIndex).toBe(-1);
    expect(tab('Math').getAttribute('aria-controls')).toBe(panel()?.id);
    expect(panel()?.classList.contains('drawer__body')).toBe(true);

    tab('Math').focus();
    press('ArrowRight');
    expect(document.activeElement).toBe(tab('Evidence'));
    expect(tab('Evidence').getAttribute('aria-selected')).toBe('true');
    expect(panel()?.getAttribute('aria-labelledby')).toBe(tab('Evidence').id);

    press('End');
    expect(document.activeElement).toBe(tab('Reproduce'));
    press('ArrowRight');
    expect(document.activeElement).toBe(tab('Math'));
    press('ArrowLeft');
    expect(tab('Reproduce').getAttribute('aria-selected')).toBe('true');
    press('Home');
    expect(document.activeElement).toBe(tab('Math'));
    expect(tab('Math').tabIndex).toBe(0);
    expect(tab('Reproduce').tabIndex).toBe(-1);
  });
});
