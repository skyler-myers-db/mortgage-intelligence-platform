/**
 * @vitest-environment happy-dom
 *
 * The ranked table's footer on a server-paged view (W5c, audit tables-02 /
 * delivery-02, D-audit-reads-a; deviation:lead-queue-load-next). Pins, on
 * the rendered footer:
 *
 * 1. without `paging` (Segment Intelligence) the footer is byte-identical:
 *    "capped at" and the loaded-rows sort scope stay;
 * 2. with `paging`, ONE explicit Load next per click (aria-disabled, never
 *    `disabled`, while a page is on the wire), labelled with the next page's
 *    size, and no "capped at";
 * 3. a server sort names the column, never "within the loaded";
 * 4. a failed Load next keeps a Retry; ten pages or an unpaged server say
 *    "Narrow the filters to see more" instead of a button;
 * 5. the polite status line is always mounted and announces a page only when
 *    the SAME view grows, never for a new view;
 * 6. focus never falls to <body>: Load next and Retry are one button, and
 *    the count line takes focus when that button leaves while focused.
 */

import { act } from 'react';
import { createRoot, type Root } from 'react-dom/client';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { LeadTableFooter, type LeadTableFooterProps } from './LeadTableFooter';
import type { LeadTablePaging } from './LeadTable.types';

(globalThis as unknown as { IS_REACT_ACT_ENVIRONMENT: boolean }).IS_REACT_ACT_ENVIRONMENT = true;

function paging(overrides: Partial<LeadTablePaging> = {}): LeadTablePaging {
  return {
    viewId: 'view-1',
    pagesLoaded: 1,
    hasMore: true,
    unavailable: false,
    capped: false,
    fetchingNext: false,
    nextError: false,
    queueUpdated: false,
    loadNext: vi.fn(),
    retryNext: vi.fn(),
    ...overrides,
  };
}

const BASE: LeadTableFooterProps = {
  loadedCount: 500,
  totalMatching: 1_284,
  truncatedAt: 500,
  sortKey: 'rank',
  sortedCount: 500,
  onResetSort: () => undefined,
};

describe('LeadTableFooter', () => {
  let container: HTMLDivElement;
  let root: Root;

  beforeEach(() => {
    container = document.createElement('div');
    document.body.appendChild(container);
    root = createRoot(container);
  });

  afterEach(() => {
    act(() => root.unmount());
    container.remove();
  });

  function render(props: Partial<LeadTableFooterProps>): void {
    act(() => root.render(<LeadTableFooter {...BASE} {...props} />));
  }

  const byTestId = (id: string) => container.querySelector<HTMLElement>(`[data-testid="${id}"]`);
  const text = () => container.textContent?.replace(/\s+/g, ' ').trim();

  it('is unchanged without paging: the cap and the loaded-rows sort scope stay', () => {
    render({ sortKey: 'equity', sortLabel: 'Equity' });
    // The W5b text, spacing included (its JSX runs the spans together).
    expect(text()).toBe(
      'Showing 500 ranked borrowers of 1,284 total matching filters · capped at 500'
      + '· sorted within the loaded 500, not across all 1,284 matchingReset to rank',
    );
    expect(byTestId('lead-load-next')).toBeNull();
    expect(byTestId('lead-paging-status')).toBeNull();
  });

  it('offers Load next with the next page size and drops "capped at"', () => {
    const view = paging();
    render({ paging: view });
    const button = byTestId('lead-load-next');
    expect(button?.textContent).toBe('Load next 500');
    expect(button?.className).toBe('btn btn--ghost btn--sm');
    expect(text()).not.toContain('capped at');

    act(() => button?.click());
    expect(view.loadNext).toHaveBeenCalledTimes(1);

    render({ loadedCount: 1_000, sortedCount: 1_000, paging: paging({ pagesLoaded: 2 }) });
    expect(byTestId('lead-load-next')?.textContent, 'the last page is the remainder').toBe('Load next 284');
  });

  it('keeps the button focusable but inert while a page is on the wire', () => {
    const view = paging({ fetchingNext: true });
    render({ paging: view });
    const button = byTestId('lead-load-next');
    expect(button?.getAttribute('aria-disabled')).toBe('true');
    expect(button?.getAttribute('aria-busy'), 'the pending-button rule (Primitives Button)').toBe('true');
    expect(button?.hasAttribute('disabled')).toBe(false);
    act(() => button?.click());
    expect(view.loadNext).not.toHaveBeenCalled();
  });

  it('names a server sort instead of claiming it covers only the loaded rows', () => {
    render({ sortKey: 'equity', sortLabel: 'Equity', sortScope: 'server', paging: paging() });
    expect(byTestId('lead-sort-scope')?.textContent).toBe(' · sorted by Equity');
    // A Lakebase-hydrated key still sorts only the loaded rows on a paged view.
    render({ sortKey: 'outreach', sortLabel: 'Outreach', sortScope: 'loaded', paging: paging() });
    expect(byTestId('lead-sort-scope')?.textContent).toContain('sorted within the loaded 500');
  });

  it('turns a failed Load next into Retry on the same button and keeps the loaded rows', () => {
    render({ paging: paging() });
    const button = byTestId('lead-load-next');
    const view = paging({ nextError: true });
    render({ paging: view });
    expect(byTestId('lead-load-next'), 'the same button, never remounted').toBe(button);
    expect(button?.textContent).toBe('Retry');
    expect(button?.getAttribute('aria-label'), 'the visible "Retry" starts the name').toBe('Retry: load the next 500');
    const failure = byTestId('lead-load-next-error');
    expect(failure?.getAttribute('role')).toBe('alert');
    expect(failure?.querySelector('button'), 'the alert holds no control').toBeNull();
    expect(text()).toContain("Couldn't load the next 500 · Retry");
    act(() => button?.click());
    expect(view.retryNext).toHaveBeenCalledTimes(1);
    expect(view.loadNext).not.toHaveBeenCalled();
    expect(text()).toContain('Showing 500 ranked borrowers of 1,284');

    render({ paging: paging({ nextError: true, fetchingNext: true }) });
    expect(button?.getAttribute('aria-disabled'), 'a Retry on the wire is inert too').toBe('true');
  });

  it('keeps focus on the paging button through a failure and a Retry, and hands it to the count at the end', () => {
    render({ paging: paging() });
    const button = byTestId('lead-load-next');
    act(() => button?.focus());
    expect(document.activeElement).toBe(button);

    render({ paging: paging({ nextError: true }) });
    expect(document.activeElement, 'a failed Load next').toBe(button);
    render({ loadedCount: 1_000, sortedCount: 1_000, paging: paging({ pagesLoaded: 2 }) });
    expect(document.activeElement, 'a successful Retry').toBe(button);
    expect(button?.textContent).toBe('Load next 284');
    expect(button?.hasAttribute('aria-label'), 'Load next is named by its text').toBe(false);

    render({ loadedCount: 1_284, sortedCount: 1_284, paging: paging({ pagesLoaded: 3, hasMore: false }) });
    expect(byTestId('lead-load-next')).toBeNull();
    const count = byTestId('lead-paging-count');
    expect(document.activeElement, 'the last page: the count line, never <body>').toBe(count);
    expect(count?.getAttribute('tabindex')).toBe('-1');
    expect(count?.textContent).toBe('Showing 1,284 ranked borrowers of 1,284 total matching filters');
  });

  it('leaves focus alone when the button leaves while focus is elsewhere', () => {
    const outside = document.createElement('button');
    document.body.appendChild(outside);
    try {
      render({ paging: paging() });
      act(() => outside.focus());
      render({ paging: paging({ pagesLoaded: 10, capped: true, hasMore: false }) });
      expect(document.activeElement).toBe(outside);
    } finally {
      outside.remove();
    }
  });

  it('says to narrow the filters at ten pages or when the server cannot page', () => {
    render({ loadedCount: 5_000, sortedCount: 5_000, totalMatching: 9_000, paging: paging({ pagesLoaded: 10, capped: true, hasMore: false }) });
    expect(byTestId('lead-load-next')).toBeNull();
    expect(byTestId('lead-paging-narrow')?.textContent).toBe(' · Narrow the filters to see more');

    render({ paging: paging({ unavailable: true, hasMore: false }) });
    expect(byTestId('lead-load-next')).toBeNull();
    expect(byTestId('lead-paging-narrow')).not.toBeNull();

    render({ loadedCount: 1_284, sortedCount: 1_284, paging: paging({ pagesLoaded: 3, hasMore: false }) });
    expect(byTestId('lead-paging-narrow'), 'everything loaded: nothing to narrow').toBeNull();
    expect(byTestId('lead-load-next')).toBeNull();
  });

  it('says when the view restarted because the queue changed', () => {
    render({ paging: paging({ queueUpdated: true }) });
    expect(byTestId('lead-paging-queue-updated')?.textContent).toBe(' · Queue updated: reloaded from the top');
  });

  it('announces a page only when the same view grows', () => {
    render({ paging: paging() });
    const status = byTestId('lead-paging-status');
    expect(status?.getAttribute('role')).toBe('status');
    expect(status?.getAttribute('aria-live')).toBe('polite');
    expect(status?.textContent).toBe('');

    render({ loadedCount: 1_000, sortedCount: 1_000, paging: paging({ pagesLoaded: 2 }) });
    expect(byTestId('lead-paging-status'), 'the same live region, never remounted').toBe(status);
    expect(status?.textContent).toBe('Loaded 500 more · showing 1,000 of 1,284');

    // A new view (a filter change) with more rows than before announces nothing.
    render({ loadedCount: 1_200, sortedCount: 1_200, paging: paging({ viewId: 'view-2', pagesLoaded: 1 }) });
    expect(status?.textContent).toBe('');
  });
});
