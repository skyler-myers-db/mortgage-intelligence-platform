// @vitest-environment happy-dom

import { afterEach, beforeEach, describe, expect, it } from 'vitest';
import { act } from 'react';
import { createRoot, type Root } from 'react-dom/client';
import { onlineManager } from '@tanstack/react-query';
import { leadTableColumns } from '../components/mortgage/LeadTable.columns';
import { LEAD_QUEUE_SKELETON_ROWS, LeadQueueTableSkeleton } from './lead-queue.skeleton';

/**
 * The Lead Queue skeleton is the ranked table's own markup (audit 2026-09-21
 * `states-10`): the rendered-height proof lives in
 * tests/e2e/fixture/session-recovery.fixture.spec.ts; this pins the structure
 * that makes it true.
 */

describe('LeadQueueTableSkeleton', () => {
  let container: HTMLDivElement;
  let root: Root;

  beforeEach(() => {
    onlineManager.setOnline(true);
    container = document.createElement('div');
    document.body.appendChild(container);
    root = createRoot(container);
  });

  afterEach(() => {
    act(() => {
      root.unmount();
    });
    container.remove();
    onlineManager.setOnline(true);
  });

  async function render() {
    await act(async () => {
      root.render(<LeadQueueTableSkeleton />);
    });
    return container.querySelector<HTMLElement>('.lead-queue-skeleton');
  }

  it('renders the Default view table: the same colgroup, header labels and --row-h rows', async () => {
    const skeleton = await render();
    const table = skeleton?.querySelector('table');
    expect(table?.classList.contains('tbl')).toBe(true);
    expect(table?.classList.contains('lead-table__table--default')).toBe(true);
    expect(table?.getAttribute('aria-hidden'), 'a placeholder, never read as the ranked table').toBe('true');

    const columns = leadTableColumns('default');
    expect(Array.from(table?.querySelectorAll('col') ?? [], (col) => col.className)).toEqual(
      columns.map((column) => column.colClass),
    );
    expect(Array.from(table?.querySelectorAll('thead th') ?? [], (th) => th.textContent)).toEqual(
      columns.map((column) => column.label),
    );
    const rows = table?.querySelectorAll('tbody tr') ?? [];
    expect(rows).toHaveLength(LEAD_QUEUE_SKELETON_ROWS);
    expect(rows[0].querySelectorAll('td')).toHaveLength(columns.length);
    expect(skeleton?.getAttribute('aria-busy')).toBe('true');
    expect(skeleton?.textContent).toContain('Loading ranked borrowers');
  });

  it('says it is waiting for the connection while offline', async () => {
    onlineManager.setOnline(false);
    const skeleton = await render();
    expect(skeleton?.getAttribute('aria-busy')).toBe('false');
    expect(skeleton?.textContent).toContain('Waiting for a connection');
    expect(skeleton?.textContent).not.toContain('Loading ranked borrowers');
  });
});
