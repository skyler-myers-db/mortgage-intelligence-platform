/**
 * The Lead Queue's effective column state (W5e items 1d and 2a):
 *  - an explicit ?view= / ?hide= governs both; otherwise the actor's default
 *    saved view's columns; otherwise Review with nothing hidden. The default
 *    never injects a filter;
 *  - the writer pushes view (Sales ops only) and hide tokens in column
 *    order, and with a default writes hide=none for Review + nothing hidden;
 *  - hide is display state: it never reaches the leads request, the leads
 *    query key, the export filters or the Growth Agent proof key, and Copy
 *    link carries the EFFECTIVE columns, never the 'none' sentinel;
 *  - the hidden set is interned by value (a stable context value).
 */
import { hashKey } from '@tanstack/react-query';
import { describe, expect, it } from 'vitest';
import { leadsQuery } from '../lib/leadsQuery';
import { leadQueueColumnState, leadQueueColumns, searchParamsWithLeadTableColumns } from './lead-queue.columns';
import {
  GROWTH_AGENT_PROOF_PARAMS,
  buildLeadQueueExportFilters,
  leadQueueShareParams,
  parseLeadTableHide,
} from './lead-queue.filters';
import { leadQueueFilterInputFromSearchParams, leadsRequestFromSearchParams } from './lead-queue.request';

const sp = (query: string) => new URLSearchParams(query);
const DEFAULT = { id: 'v1', name: 'Ops desk', params: 'state=TX&view=sales-ops&hide=relationship,last-touch' };

describe('effective column state', () => {
  it('reads Review with nothing hidden when the URL names no columns and there is no default', () => {
    const state = leadQueueColumnState(sp('state=IL'), null);
    expect(state).toMatchObject({ view: 'default', explicit: false, hasDefault: false });
    expect([...state.hidden]).toEqual([]);
  });

  it("applies the default saved view's columns only, never its filters", () => {
    const state = leadQueueColumnState(sp('state=IL'), DEFAULT);
    expect(state).toMatchObject({ view: 'sales-ops', explicit: false, hasDefault: true });
    expect([...state.hidden]).toEqual(['relationship', 'last-touch']);
    // The default's state=TX is never read as a filter.
    expect(leadsRequestFromSearchParams(sp('state=IL'), [], null)).toEqual(leadsRequestFromSearchParams(sp('state=IL&hide=rate'), [], null));
  });

  it('lets an explicit ?view= or ?hide= govern both', () => {
    expect(leadQueueColumnState(sp('view=default'), DEFAULT)).toMatchObject({ view: 'default', explicit: true });
    expect([...leadQueueColumnState(sp('view=sales-ops'), DEFAULT).hidden]).toEqual([]);
    const none = leadQueueColumnState(sp('hide=none'), DEFAULT);
    expect(none).toMatchObject({ view: 'default', explicit: true });
    expect([...none.hidden]).toEqual([]);
    expect([...leadQueueColumnState(sp('hide=rate,segments,zyrplax,RATE'), DEFAULT).hidden]).toEqual(['segments', 'rate']);
  });

  it('keeps only the tokens the view can hide', () => {
    expect([...leadQueueColumnState(sp('hide=status,relationship'), null).hidden]).toEqual(['status']);
    expect([...leadQueueColumnState(sp('view=sales-ops&hide=status,relationship'), null).hidden]).toEqual(['relationship']);
  });

  it('interns the hidden set by value, so equal URLs share one Set', () => {
    const first = leadQueueColumnState(sp('state=IL&hide=segments,rate'), null).hidden;
    const second = leadQueueColumnState(sp('hide=rate,segments&sort=score'), null).hidden;
    expect(second).toBe(first);
    expect(leadQueueColumnState(sp('hide=segments'), null).hidden).not.toBe(first);
  });
});

describe('the column writer', () => {
  const write = (query: string, view: 'default' | 'sales-ops', hidden: string[], hasDefault = false) =>
    searchParamsWithLeadTableColumns(sp(query), { view, hidden: parseLeadTableHide(hidden.join(',')) }, { hasDefault }).toString();

  it('writes Sales ops and the hidden tokens in column order, keeping every other param', () => {
    expect(write('state=IL&sort=score&dir=desc', 'sales-ops', ['rate', 'assigned'])).toBe(
      'state=IL&sort=score&dir=desc&view=sales-ops&hide=assigned%2Crate',
    );
    expect(write('state=IL&view=sales-ops&hide=rate', 'default', [])).toBe('state=IL');
  });

  it('writes hide=none for Review with nothing hidden only when the actor has a default', () => {
    expect(write('state=IL&view=sales-ops', 'default', [], true)).toBe('state=IL&hide=none');
    expect(write('state=IL', 'sales-ops', [], true)).toBe('state=IL&view=sales-ops');
    expect(write('state=IL&hide=none', 'default', ['rate'], true)).toBe('state=IL&hide=rate');
  });
});

describe('hide is display state', () => {
  const BASE = 'state=IL&segment=itm&approval_status=pending&growth_agent_run_id=run-1';

  it('changes neither the leads request, its query key, the export filters nor the proof key', () => {
    const request = leadsRequestFromSearchParams(sp(BASE), [], null);
    const hidden = leadsRequestFromSearchParams(sp(`${BASE}&hide=segments,rate`), [], null);
    expect(hidden).toEqual(request);
    expect(hashKey(leadsQuery('lead-queue', hidden).queryKey)).toBe(hashKey(leadsQuery('lead-queue', request).queryKey));
    const exportOf = (query: string) => buildLeadQueueExportFilters(leadQueueFilterInputFromSearchParams(sp(query), []));
    expect(exportOf(`${BASE}&hide=none`)).toBe(exportOf(BASE));
    expect(GROWTH_AGENT_PROOF_PARAMS).not.toContain('hide');
  });

  it('Copy link carries the effective columns, never the none sentinel', () => {
    const input = leadQueueFilterInputFromSearchParams(sp('state=IL'), []);
    const withDefault = leadQueueColumns(leadQueueColumnState(sp('state=IL'), DEFAULT));
    expect(leadQueueShareParams(sp('state=IL'), input, withDefault).search).toBe(
      '?state=IL&view=sales-ops&hide=relationship%2Clast-touch',
    );
    const explicitNone = leadQueueColumns(leadQueueColumnState(sp('state=IL&hide=none'), DEFAULT));
    const share = leadQueueShareParams(sp('state=IL&hide=none'), input, explicitNone);
    expect(share.search).toBe('?state=IL');
    expect(share.omitted).toEqual([]);
    expect(leadQueueShareParams(sp('state=IL&hide=rate,segments'), input).search).toBe('?state=IL&hide=segments%2Crate');
  });
});
