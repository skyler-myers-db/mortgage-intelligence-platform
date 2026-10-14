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

const genieMocks = vi.hoisted(() => ({ openGenie: vi.fn(), requestGenieContextScope: vi.fn() }));
vi.mock('../../lib/genieOpen', () => ({ openGenie: genieMocks.openGenie }));
vi.mock('../../lib/apiClients/genieContextScope', () => ({ requestGenieContextScope: genieMocks.requestGenieContextScope }));

/**
 * The Lead Queue's measured zero (audit states-v2): its cause, Clear filters,
 * and "Ask Genie" only through a battery-proven template: a client template
 * (phase 1) or the server-rendered scope (phase 2, genie-04 states-v2).
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
  ])('%s never reach a client template', (_label, query) => {
    expect(queueFilterGeniePrompt(filters(query))).toBeNull();
  });

  it('never lets a county, ZIP, city or borrower id reach a client template', () => {
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

  it('an empty all-mode intersection says it is a real result, and now offers the scoped Ask (deliberate flip, genie-04 phase 2)', () => {
    renderEmpty('segment_codes=itm,equity&segment_mode=all', { intersection: true });
    expect(cause()).toBe('intersection');
    expect(document.querySelector('.empty__copy')?.textContent).toBe('Remove a segment chip to widen the cohort.');
    expect(clear()).not.toBeNull();
    const button = ask();
    expect(button?.textContent).toBe('Ask Genie');
    expect(button?.className).toContain('genie-ask-about');
    expect(button?.hasAttribute('title')).toBe(false);
    // Rendering never asks the server: the POST waits for a click.
    expect(genieMocks.requestGenieContextScope).not.toHaveBeenCalled();
  });

  it('an intersection with an unregistered chip offers no Ask at all', () => {
    renderEmpty('segment_codes=itm,heloc&segment_mode=all', { intersection: true });
    expect(cause()).toBe('intersection');
    expect(ask()).toBeNull();
  });

  async function settle() {
    await act(async () => {
      await Promise.resolve();
      await Promise.resolve();
    });
  }

  it('a click sends ONE closed-code POST and prefills (never submits) the server prompt', async () => {
    const prompt = 'How many borrowers across the current Cotality data coverage are in the prime refi candidates segment and the home equity candidate segment, and what is the average rate spread?';
    let resolve: (value: unknown) => void = () => {};
    genieMocks.requestGenieContextScope.mockImplementation(() => new Promise((done) => { resolve = done; }));
    renderEmpty('segment_codes=itm,equity&segment_mode=all', { intersection: true });

    act(() => ask()?.click());
    expect(genieMocks.requestGenieContextScope).toHaveBeenCalledTimes(1);
    const [body, signal] = genieMocks.requestGenieContextScope.mock.calls[0] as [unknown, AbortSignal];
    expect(body).toEqual({ route: 'lead-queue', segment_codes: ['itm', 'equity'], segment_mode: 'all' });
    expect(signal).toBeInstanceOf(AbortSignal);
    // Pending: the shared Button loading state, and a second click is swallowed.
    expect(ask()?.classList.contains('btn--loading')).toBe(true);
    expect(ask()?.getAttribute('aria-busy')).toBe('true');
    act(() => ask()?.click());
    expect(genieMocks.requestGenieContextScope).toHaveBeenCalledTimes(1);

    resolve({ prompt, withheld: null, omitted: [] });
    await settle();
    expect(genieMocks.openGenie).toHaveBeenCalledTimes(1);
    expect(genieMocks.openGenie).toHaveBeenCalledWith({ prompt });
    expect(ask()?.classList.contains('btn--loading')).toBe(false);
  });

  it('names the assignee filter it leaves out and expects the server to say so', async () => {
    genieMocks.requestGenieContextScope.mockResolvedValue({ prompt: 'How many borrowers are in Texas, and what is the average rate spread?', withheld: null, omitted: ['assignee'] });
    renderEmpty('state=TX&assigned_to=me');
    const button = document.querySelector<HTMLButtonElement>('button[aria-label="Ask Genie about this filter selection without the assignee filter"]');
    expect(button?.textContent).toBe('Ask Genie without the assignee filter');
    act(() => button?.click());
    await settle();
    expect(genieMocks.requestGenieContextScope.mock.calls[0]?.[0]).toEqual({ route: 'lead-queue', state: 'TX', assignee: 'me' });
    expect(genieMocks.openGenie).toHaveBeenCalledTimes(1);
  });

  it('an assignee email offers no Ask and sends nothing', () => {
    renderEmpty('state=TX&segment_codes=itm,equity&assigned_to=lo@summit.example');
    expect(document.querySelector('.genie-ask-about')).toBeNull();
    expect(genieMocks.requestGenieContextScope).not.toHaveBeenCalled();
  });

  it.each([
    ['a withheld render', () => Promise.resolve({ prompt: null, withheld: 'refused', omitted: [] })],
    ['an omitted list the client did not expect', () => Promise.resolve({ prompt: 'How many borrowers are in ZIP code 60601, and what is the average rate spread?', withheld: null, omitted: ['assignee'] })],
    ['a transport failure', () => Promise.reject(new Error('503'))],
  ])('%s replaces the button with the fixed line inside the status region', async (_label, answer) => {
    genieMocks.requestGenieContextScope.mockImplementation(answer);
    renderEmpty('zip=60601');
    act(() => ask()?.click());
    await settle();
    expect(genieMocks.openGenie).not.toHaveBeenCalled();
    expect(ask()).toBeNull();
    const line = document.querySelector('.empty[role="status"] .muted.fs-12');
    expect(line?.textContent).toBe("Genie can't take this filter set as a question.");
  });

  it.each([
    ['a withheld render', () => Promise.resolve({ prompt: null, withheld: 'refused', omitted: [] })],
    ['an omitted list the client did not expect', () => Promise.resolve({ prompt: 'How many borrowers are in ZIP code 60601, and what is the average rate spread?', withheld: null, omitted: ['assignee'] })],
    ['a transport failure', () => Promise.reject(new Error('503'))],
  ])('%s: a focused Ask hands focus to Clear filters, never to <body>', async (_label, answer) => {
    genieMocks.requestGenieContextScope.mockImplementation(answer);
    renderEmpty('zip=60601');
    act(() => ask()?.focus());
    expect(document.activeElement).toBe(ask());
    act(() => ask()?.click());
    // Pending keeps focus on the Ask (aria-disabled, never native disabled).
    expect(document.activeElement).toBe(ask());
    await settle();
    expect(ask()).toBeNull();
    expect(document.activeElement).toBe(clear());
  });

  it('an Ask that did not hold focus moves nothing when it is replaced', async () => {
    genieMocks.requestGenieContextScope.mockResolvedValue({ prompt: null, withheld: 'refused', omitted: [] });
    renderEmpty('zip=60601');
    const elsewhere = document.createElement('input');
    elsewhere.setAttribute('aria-label', 'elsewhere');
    document.body.appendChild(elsewhere);
    act(() => elsewhere.focus());
    act(() => ask()?.click());
    await settle();
    expect(ask()).toBeNull();
    expect(document.activeElement).toBe(elsewhere);
  });

  it('aborts the pending render on unmount and stays silent', async () => {
    genieMocks.requestGenieContextScope.mockImplementation((_body: unknown, signal: AbortSignal) => new Promise((_done, fail) => {
      signal.addEventListener('abort', () => fail(new DOMException('aborted', 'AbortError')));
    }));
    renderEmpty('county=17031');
    act(() => ask()?.click());
    const signal = genieMocks.requestGenieContextScope.mock.calls[0]?.[1] as AbortSignal;
    act(() => root.render(<div />));
    await settle();
    expect(signal.aborted).toBe(true);
    expect(genieMocks.openGenie).not.toHaveBeenCalled();
  });

  it('an unfiltered zero says what the default view lists, with no Clear and no Ask', () => {
    renderEmpty('view=compact&sort=equity');
    expect(cause()).toBe('filtered');
    expect(document.querySelector('.empty__copy')?.textContent).toBe('The default view lists marketing-eligible borrowers only.');
    expect(clear()).toBeNull();
    expect(ask()).toBeNull();
  });
});
