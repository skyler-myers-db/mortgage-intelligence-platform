/**
 * @vitest-environment happy-dom
 */
import { act } from 'react';
import { createRoot, type Root } from 'react-dom/client';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import {
  US_STATE_NAME_BY_CODE,
  genieLeadPrompt,
  genieSegmentPrompt,
  genieStatePrompt,
} from '../../lib/genieContext';
import { leadQueueFilterParams } from '../../routes/lead-queue.activeFilters';
import { SEGMENT_CODES } from '../../routes/lead-queue.filters';
import { LeadQueueEmptyState, queueFilterGeniePrompt } from './LeadQueueEmptyState';

(globalThis as unknown as { IS_REACT_ACT_ENVIRONMENT: boolean }).IS_REACT_ACT_ENVIRONMENT = true;

const genieMocks = vi.hoisted(() => ({ openGenie: vi.fn() }));
vi.mock('../../lib/genieOpen', () => ({ openGenie: genieMocks.openGenie }));

/**
 * The Lead Queue's measured zero (audit states-v2): its cause, Clear filters,
 * and "Ask Genie" only through a battery-proven template.
 */

const STATES = Object.keys(US_STATE_NAME_BY_CODE);
const SEGMENTS = [...SEGMENT_CODES];
/** The route's view of a URL: its filter params, display params removed. */
const filters = (query: string) => leadQueueFilterParams(new URLSearchParams(query));

describe('queueFilterGeniePrompt', () => {
  it('only ever renders a reviewed template, over the full state x segment grid', () => {
    const reviewed = new Set<string>();
    for (const state of STATES) {
      const statePrompt = genieStatePrompt(state);
      if (statePrompt) reviewed.add(statePrompt);
      for (const segment of SEGMENTS) {
        const lead = genieLeadPrompt({ segmentCodes: [segment], stateCode: state });
        if (lead) reviewed.add(lead);
      }
    }
    for (const segment of SEGMENTS) {
      const segmentPrompt = genieSegmentPrompt(segment);
      if (segmentPrompt) reviewed.add(segmentPrompt);
    }

    const outputs: string[] = [];
    for (const state of STATES) {
      for (const query of [`state=${state}`, `states=${state}`, `state=${state.toLowerCase()}`]) {
        const prompt = queueFilterGeniePrompt(filters(query));
        if (prompt) outputs.push(prompt);
      }
      for (const segment of SEGMENTS) {
        for (const query of [`state=${state}&segment=${segment}`, `states=${state}&segment_codes=${segment}&segment_mode=all`]) {
          const prompt = queueFilterGeniePrompt(filters(query));
          if (prompt) outputs.push(prompt);
        }
      }
    }
    for (const segment of SEGMENTS) {
      const prompt = queueFilterGeniePrompt(filters(`segment=${segment}`));
      if (prompt) outputs.push(prompt);
    }

    // Non-vacuity: the grid really produced prompts.
    expect(outputs.length).toBeGreaterThan(STATES.length);
    for (const prompt of outputs) expect(reviewed.has(prompt)).toBe(true);
  });

  it('asks about one state and one segment with the lead template', () => {
    expect(queueFilterGeniePrompt(filters('state=TX&segment=itm'))).toBe(genieLeadPrompt({ segmentCodes: ['itm'], stateCode: 'TX' }));
    expect(queueFilterGeniePrompt(filters('segment=ITM'))).toBe(genieSegmentPrompt('itm'));
    expect(queueFilterGeniePrompt(filters('state=TX'))).toBe(genieStatePrompt('TX'));
    // The column preset and the table place are display state, not filters.
    expect(queueFilterGeniePrompt(filters('state=TX&view=compact&sort=equity&dir=desc&row=B-0123456789ABC'))).toBe(genieStatePrompt('TX'));
  });

  it.each([
    ['no filter at all', ''],
    ['a county', 'state=TX&segment=itm&county=48201'],
    ['a county list', 'counties=48201'],
    ['a ZIP', 'state=TX&zip=77002'],
    ['ZIPs', 'zips=77002,77003'],
    ['cities', 'cities=HOUSTON~TX'],
    ['borrower ids', 'borrower_ids=B-0123456789ABC'],
    ['a lender', 'state=TX&target_lender_ref=Competitor%20A'],
    ['an assignee', 'state=TX&assigned_to=me'],
    ['an approval status', 'segment=itm&approval_status=pending'],
    ['two states', 'states=TX,IL'],
    ['2+ segment chips (an intersection the lead template cannot state)', 'segment_codes=itm,heloc&segment_mode=all'],
    ['an unknown segment', 'segment=not-a-segment'],
    ['a state outside the federal list', 'state=ZZ'],
  ])('is null for %s', (_label, query) => {
    expect(queueFilterGeniePrompt(filters(query))).toBeNull();
  });

  it('never lets a county, ZIP, city or borrower id reach a prompt', () => {
    for (const query of ['state=TX&county=48201', 'state=TX&zip=77002', 'state=TX&cities=HOUSTON~TX', 'state=TX&borrower_ids=B-0123456789ABC']) {
      expect(queueFilterGeniePrompt(filters(query))).toBeNull();
    }
  });
});

describe('LeadQueueEmptyState', () => {
  let root: Root;

  beforeEach(() => {
    document.body.innerHTML = '<div id="root"></div>';
    root = createRoot(document.getElementById('root') as HTMLElement);
  });

  afterEach(() => {
    act(() => root.unmount());
    document.body.innerHTML = '';
    vi.clearAllMocks();
  });

  function renderEmpty(query: string, overrides: Partial<Parameters<typeof LeadQueueEmptyState>[0]> = {}) {
    const onClearFilters = vi.fn();
    act(() => {
      root.render(
        <LeadQueueEmptyState
          filterParams={filters(query)}
          countyNoCoverage={false}
          intersection={false}
          onClearFilters={onClearFilters}
          {...overrides}
        />,
      );
    });
    return onClearFilters;
  }

  const cause = () => document.querySelector('.empty')?.getAttribute('data-empty-cause');
  const clear = () => document.querySelector<HTMLButtonElement>('button[aria-label="Clear lead queue filters"]');
  const ask = () => document.querySelector<HTMLButtonElement>('button[aria-label="Ask Genie about this filter selection"]');

  it('a filtered zero offers Clear filters and a prefilled (never submitted) Ask Genie', () => {
    const onClearFilters = renderEmpty('state=TX&segment=itm');
    expect(cause()).toBe('filtered');
    expect(document.querySelector('.empty__title')?.textContent).toBe('No leads match this filter.');
    act(() => clear()?.click());
    expect(onClearFilters).toHaveBeenCalledTimes(1);
    act(() => ask()?.click());
    expect(genieMocks.openGenie).toHaveBeenCalledWith({ prompt: genieLeadPrompt({ segmentCodes: ['itm'], stateCode: 'TX' }) });
  });

  it('a county with no ZIP coverage says so, with Clear filters and no Ask Genie', () => {
    renderEmpty('county=48201', { countyNoCoverage: true });
    expect(cause()).toBe('coverage');
    expect(clear()).not.toBeNull();
    expect(ask()).toBeNull();
  });

  it('an empty all-mode intersection says it is a real result, with Clear filters only', () => {
    renderEmpty('segment_codes=itm,heloc&segment_mode=all', { intersection: true });
    expect(cause()).toBe('intersection');
    expect(document.querySelector('.empty__copy')?.textContent).toBe('Remove a segment chip to widen the cohort.');
    expect(clear()).not.toBeNull();
    expect(ask()).toBeNull();
  });

  it('an unfiltered zero says what the default view lists, with no Clear and no Ask', () => {
    renderEmpty('view=compact&sort=equity');
    expect(cause()).toBe('filtered');
    expect(document.querySelector('.empty__copy')?.textContent).toBe('The default view lists marketing-eligible borrowers only.');
    expect(clear()).toBeNull();
    expect(ask()).toBeNull();
  });
});
