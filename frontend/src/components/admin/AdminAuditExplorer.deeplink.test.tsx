/**
 * @vitest-environment happy-dom
 */

import { act } from 'react';
import { createRoot, type Root } from 'react-dom/client';
import { MemoryRouter, useLocation } from 'react-router';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import type { AuditEventRow } from '../../lib/api';

(globalThis as unknown as { IS_REACT_ACT_ENVIRONMENT: boolean }).IS_REACT_ACT_ENVIRONMENT = true;

/**
 * The Decision receipt deep-links the explorer as `?audit_event_id=<id>`.
 * Dropping that pin by the chip's dismiss must reset the explorer the way
 * Clear does (first page, nothing expanded), not only the URL. The data hook
 * is stubbed so the explorer's own state is what is under test.
 */
const PINNED_ID = 'evt-receipt-0001';

const hook = vi.hoisted(() => ({
  keys: [] as ReadonlyArray<unknown>[],
  rows: [] as AuditEventRow[],
}));

vi.mock('../../lib/useWarmingUpRetry', () => ({
  useWarmingUpRetry: (_loader: unknown, _dependencies: unknown, options?: { queryKey?: readonly unknown[] }) => {
    const key = options?.queryKey ?? [];
    hook.keys.push(key);
    const secondPage = key.includes('cursor-page-2');
    const data = key.includes('explorer')
      ? {
          items: secondPage ? hook.rows.slice(25) : hook.rows.slice(0, 25),
          next_cursor: hook.rows.length > 25 && !secondPage ? 'cursor-page-2' : null,
        }
      : [];
    return { data, warmingUp: null, error: null, isFetching: false, isPlaceholderData: false, manualRetry: vi.fn() };
  },
}));

vi.mock('../../lib/api', () => ({ api: {} }));

import { AdminAuditExplorer } from './AdminAuditExplorer';

function row(eventId: string): AuditEventRow {
  return {
    event_id: eventId,
    actor: 'vera@summit.example',
    action: 'outreach.approve',
    entity_type: 'approval',
    entity_id: `approval-${eventId}`,
    payload_json: { channel: 'email', offer_code: 'refi' },
    evidence_ids: ['ev-001'],
    created_at: '2026-07-13T14:30:00Z',
    event_type: 'APPROVE',
  };
}

function LocationProbe() {
  const location = useLocation();
  return <output data-testid="location">{`${location.pathname}${location.search}${location.hash}`}</output>;
}

describe('AdminAuditExplorer receipt deep link', () => {
  let root: Root;

  beforeEach(() => {
    document.body.innerHTML = '<div id="root"></div>';
    root = createRoot(document.getElementById('root') as HTMLElement);
    hook.keys.length = 0;
  });

  afterEach(() => {
    act(() => root.unmount());
    document.body.innerHTML = '';
  });

  function render() {
    act(() => {
      root.render(
        <MemoryRouter initialEntries={[`/admin-config?audit_event_id=${PINNED_ID}#audit`]}>
          <AdminAuditExplorer />
          <LocationProbe />
        </MemoryRouter>,
      );
    });
  }

  function button(label: RegExp): HTMLButtonElement {
    const match = [...document.querySelectorAll('button')].find((candidate) => (
      label.test(candidate.getAttribute('aria-label') ?? candidate.textContent?.trim() ?? '')
    ));
    if (!(match instanceof HTMLButtonElement)) throw new Error(`button not found: ${label}`);
    return match;
  }

  const dropPin = {
    Clear: () => button(/^Clear$/).click(),
    'the chip dismiss': () => button(/^Remove audit event filter$/).click(),
  } as const;
  const location = () => document.querySelector('[data-testid="location"]')?.textContent;
  const lastExplorerKey = () => [...hook.keys].reverse().find((key) => key.includes('explorer'));

  it.each(Object.keys(dropPin) as Array<keyof typeof dropPin>)(
    '%s collapses the expanded pinned row',
    (control) => {
      hook.rows = [row(PINNED_ID)];
      render();
      expect(lastExplorerKey()).toContain(PINNED_ID);
      const expand = button(new RegExp(`^Expand audit event ${PINNED_ID}$`));
      act(() => expand.click());
      expect(expand.getAttribute('aria-expanded')).toBe('true');

      act(() => dropPin[control]());

      expect(location()).toBe('/admin-config#audit');
      expect(lastExplorerKey()).not.toContain(PINNED_ID);
      // The same row comes back unpinned, collapsed like after Clear.
      const toggle = button(new RegExp(`^(Expand|Collapse) audit event ${PINNED_ID}$`));
      expect(toggle.getAttribute('aria-expanded')).toBe('false');
    },
  );

  it.each(Object.keys(dropPin) as Array<keyof typeof dropPin>)(
    '%s returns the explorer to the first page',
    (control) => {
      hook.rows = Array.from({ length: 26 }, (_, index) => row(`evt-page-${index}`));
      render();
      act(() => button(/^Next$/).click());
      expect(lastExplorerKey()).toContain('cursor-page-2');

      act(() => dropPin[control]());

      expect(location()).toBe('/admin-config#audit');
      const key = lastExplorerKey()!;
      expect(key).not.toContain('cursor-page-2');
      expect(key[key.length - 1]).toBeNull();
      expect(document.body.textContent).toContain('page 1 · 25 rows');
    },
  );
});
