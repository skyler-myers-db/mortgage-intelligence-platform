/**
 * The ranked table's column visibility model (tables-05 step 2, W5e):
 *  - the hide vocabulary, per-view hideable sets, and the columns that are
 *    NEVER hideable (selection, expand, Borrower, Primary offer with its
 *    rules evidence chip, Score, Signal, Approval);
 *  - LeadTable.columns.css subtracts each hidden column's EXACT width from
 *    the view floor: every --lead-table-hidden-<token> equals the width the
 *    token's column has in 03-score-and-table.css, mapped through the
 *    column's own colClass (never through the token name: 'last-touch' is
 *    .lead-table__col-disposition and 'assigned' .lead-table__col-assignment);
 *  - the class name, the sort label and the shared visible-column identity.
 */
import { describe, expect, it } from 'vitest';
// @ts-expect-error Frontend app types intentionally exclude Node globals; this
// unit test reads stylesheet text under Vitest only.
import { readFileSync } from 'node:fs';
// @ts-expect-error see node:fs note above.
import { join } from 'node:path';
import {
  LEAD_TABLE_COLUMNS,
  LEAD_TABLE_HIDE_VOCABULARY,
  LEAD_TABLE_VIEW_LABELS,
  NO_HIDDEN_COLUMNS,
  leadTableClassName,
  leadTableColumns,
  leadTableHideableColumns,
  leadTableSortLabel,
  type LeadTableHideToken,
} from './LeadTable.columns';

declare const process: { cwd(): string };

const css = (path: string) => readFileSync(join(process.cwd(), path), 'utf8').replace(/\/\*[\s\S]*?\*\//g, '');
const TABLE_CSS = css('src/design-system/components/03-score-and-table.css');
const HIDE_CSS = css('src/components/mortgage/LeadTable.columns.css');
const FLOOR_CSS = css('src/components/mortgage/LeadTable.css');

const keysOf = (view: 'default' | 'sales-ops') => leadTableHideableColumns(view).map((column) => column.key);
const set = (...tokens: LeadTableHideToken[]) => new Set<LeadTableHideToken>(tokens);

function colWidth(colClass: string): number {
  const match = TABLE_CSS.match(new RegExp(`\\.${colClass}\\s*\\{\\s*width:\\s*(\\d+)px;\\s*\\}`));
  expect(match, colClass).not.toBeNull();
  return Number(match![1]);
}

describe('the hide vocabulary', () => {
  it('names the Review and Sales ops hideable columns in column order', () => {
    expect(keysOf('default')).toEqual(['location', 'segments', 'equity', 'rate', 'status']);
    expect(keysOf('sales-ops')).toEqual([
      'location', 'relationship', 'assignment', 'outreach', 'lastTouch', 'segments', 'equity', 'rate',
    ]);
    expect(LEAD_TABLE_VIEW_LABELS.default).toBe('Review');
  });

  it('never offers the evidence, confidence, approval or identity columns', () => {
    const hideable = new Set(LEAD_TABLE_HIDE_VOCABULARY.map((entry) => entry.key as string));
    for (const key of ['select', 'expand', 'borrower', 'offer', 'score', 'confidence', 'approval']) {
      expect(hideable.has(key), key).toBe(false);
    }
    const everything = set(...LEAD_TABLE_HIDE_VOCABULARY.map((entry) => entry.token));
    for (const view of ['default', 'sales-ops'] as const) {
      expect(leadTableColumns(view, everything).map((column) => column.key), view).toEqual(
        ['select', 'expand', 'borrower', 'offer', 'score', 'confidence', 'approval'],
      );
    }
  });
});

describe('LeadTable.columns.css widths', () => {
  it('subtracts exactly each hidden column width from 03-score-and-table.css', () => {
    const byKey = new Map(
      [...LEAD_TABLE_COLUMNS.default, ...LEAD_TABLE_COLUMNS['sales-ops']].map((column) => [column.key, column]),
    );
    for (const { token, key } of LEAD_TABLE_HIDE_VOCABULARY) {
      const column = byKey.get(key);
      expect(column, key).toBeDefined();
      const rule = HIDE_CSS.match(new RegExp(`\\.lead-table__table--hide-${token}\\s*\\{\\s*--lead-table-hidden-${token}:\\s*(\\d+)px;\\s*\\}`));
      expect(rule, token).not.toBeNull();
      expect(Number(rule![1]), `${token} -> ${column!.colClass}`).toBe(colWidth(column!.colClass));
      expect(HIDE_CSS).toContain(`@property --lead-table-hidden-${token} { syntax: '<length>'; inherits: false; initial-value: 0px; }`);
    }
  });

  it('starts each view floor from LeadTable.css and subtracts every hideable column of that view, once', () => {
    for (const view of ['default', 'sales-ops'] as const) {
      const floor = FLOOR_CSS.match(new RegExp(`\\.lead-table__table--${view}\\s*\\{\\s*min-inline-size:\\s*(\\d+)px;`));
      const rule = HIDE_CSS.match(new RegExp(`\\.lead-table__table--hiding\\.lead-table__table--${view}\\s*\\{([^}]*)\\}`));
      expect(floor && rule, view).toBeTruthy();
      expect(rule![1]).toContain(`${floor![1]}px`);
      const subtracted = [...rule![1].matchAll(/var\(--lead-table-hidden-([a-z-]+), 0px\)/g)].map((match) => match[1]);
      const tokens = leadTableHideableColumns(view).map(
        (column) => LEAD_TABLE_HIDE_VOCABULARY.find((entry) => entry.key === column.key)!.token,
      );
      expect(subtracted).toEqual(tokens);
    }
  });

  it('writes no JS custom property and uses no :has()', () => {
    expect(HIDE_CSS).not.toContain(':has(');
  });
});

describe('visible columns, class name and sort label', () => {
  it('drops the hidden columns and keeps one array identity per hidden set', () => {
    const hidden = set('segments', 'rate');
    const visible = leadTableColumns('default', hidden);
    expect(visible.map((column) => column.key)).toEqual([
      'select', 'expand', 'borrower', 'location', 'equity', 'offer', 'score', 'confidence', 'status', 'approval',
    ]);
    expect(leadTableColumns('default', hidden)).toBe(visible);
    expect(leadTableColumns('default', NO_HIDDEN_COLUMNS)).toBe(LEAD_TABLE_COLUMNS.default);
    // A token the view cannot hide (Status in Sales ops) changes nothing.
    expect(leadTableColumns('sales-ops', set('status'))).toEqual(LEAD_TABLE_COLUMNS['sales-ops']);
  });

  it('adds --hiding and one --hide-<token> per hidden column, in column order', () => {
    expect(leadTableClassName('default', LEAD_TABLE_COLUMNS.default)).toBe('tbl lead-table__table lead-table__table--default');
    expect(leadTableClassName('default', leadTableColumns('default', set('rate', 'segments')))).toBe(
      'tbl lead-table__table lead-table__table--default lead-table__table--hiding '
      + 'lead-table__table--hide-segments lead-table__table--hide-rate',
    );
    expect(leadTableClassName('sales-ops', leadTableColumns('sales-ops', set('last-touch', 'assigned')))).toBe(
      'tbl lead-table__table lead-table__table--sales-ops lead-table__table--hiding '
      + 'lead-table__table--hide-assigned lead-table__table--hide-last-touch',
    );
  });

  it('names a hidden sorted column from the full view', () => {
    expect(leadTableSortLabel('default', 'rate')).toBe('Rate Δ (bps)');
    expect(leadTableSortLabel('sales-ops', 'assignment')).toBe('Assigned to');
    expect(leadTableSortLabel('default', 'rank')).toBe('rank');
  });
});
