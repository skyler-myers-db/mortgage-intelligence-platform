/**
 * @vitest-environment happy-dom
 *
 * Saved Lead Queue views (audit tables-09 phase 2, flow-08 slice 1): the
 * queue mounts with no request; the first open loads the panel, then reads
 * the list; save and delete are pessimistic; errors are fixed copy.
 */
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { act } from 'react';
import { MemoryRouter } from 'react-router';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { ApiError } from '../lib/apiTransport';
import { mount } from '../test/render';
import type { SavedView } from '../types/leadFilters';
import {
  SAVED_VIEW_COPY,
  isCurrentSavedView,
  saveErrorCopy,
  savedViewDraft,
} from './lead-queue.savedViews';
import { LeadQueueViews } from './lead-queue.views';

vi.mock('../lib/configOptionsQuery', () => {
  const STABLE = { data: { target_lender_refs: ['All', 'Competitor B'] }, isError: false };
  return { useConfigOptionsQuery: () => STABLE };
});

interface Call {
  method: string;
  path: string;
  body: unknown;
}

let calls: Call[] = [];
let views: SavedView[] = [];
let respond: (call: Call) => Response | Promise<Response> = () => new Response('{}');

function json(body: unknown, status = 200): Response {
  return new Response(JSON.stringify(body), { status, headers: { 'Content-Type': 'application/json' } });
}

function view(id: string, name: string, params: string): SavedView {
  return { view_id: id, name, params, created_at: '2026-09-30T00:00:00Z', updated_at: '2026-09-30T00:00:00Z' };
}

beforeEach(() => {
  calls = [];
  views = [view('11111111-1111-4111-8111-111111111111', 'IL pending', 'approval_status=pending&state=IL')];
  respond = (call) => {
    if (call.method === 'GET') return json({ saved_views: views });
    if (call.method === 'POST') {
      const body = call.body as { name: string; params: string };
      views = [view('22222222-2222-4222-8222-222222222222', body.name, body.params), ...views];
      return json({ ok: true, view_id: views[0].view_id, audit_event_id: 'a-1' });
    }
    const id = call.path.split('/').pop()!;
    views = views.filter((candidate) => candidate.view_id !== id);
    return json({ ok: true, view_id: id, audit_event_id: 'a-2' });
  };
  vi.stubGlobal('fetch', async (path: string, init?: RequestInit) => {
    const call = { method: init?.method ?? 'GET', path, body: init?.body ? JSON.parse(String(init.body)) : null };
    calls.push(call);
    return respond(call);
  });
});

afterEach(() => {
  vi.unstubAllGlobals();
});

async function mountAt(query: string) {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  const sp = new URLSearchParams(query);
  await mount(
    <QueryClientProvider client={client}>
      <MemoryRouter initialEntries={[`/lead-queue?${query}`]}>
        <LeadQueueViews searchParams={sp} showAssignedToMe onCopyLink={() => undefined} />
      </MemoryRouter>
    </QueryClientProvider>,
  );
}

const byTestId = <T extends HTMLElement>(id: string) => document.querySelector<T>(`[data-testid="${id}"]`);
const trigger = () => byTestId<HTMLButtonElement>('lead-queue-saved-views')!;

async function settle() {
  await act(async () => {
    for (let i = 0; i < 8; i += 1) await new Promise((resolve) => window.setTimeout(resolve, 0));
  });
}

async function open() {
  await act(async () => trigger().click());
  await settle();
}

async function typeName(text: string) {
  const input = byTestId<HTMLInputElement>('lead-queue-saved-views-name')!;
  const setter = Object.getOwnPropertyDescriptor(HTMLInputElement.prototype, 'value')!.set!;
  await act(async () => {
    setter.call(input, text);
    input.dispatchEvent(new Event('input', { bubbles: true }));
  });
}

async function save() {
  await act(async () => byTestId<HTMLButtonElement>('lead-queue-saved-views-save')!.click());
  await settle();
}

describe('saved-view helpers', () => {
  it('matches a saved view to the current share params regardless of order, ignoring the campaign binding', () => {
    expect(isCurrentSavedView('approval_status=pending&state=IL', 'state=IL&approval_status=pending')).toBe(true);
    expect(isCurrentSavedView('state=IL', 'state=IL&campaign_id=c1')).toBe(true);
    expect(isCurrentSavedView('state=IL', 'state=IL&sort=score&dir=desc')).toBe(false);
    expect(isCurrentSavedView('state=IL&state=TX', 'state=IL')).toBe(false);
  });

  it('drafts the Copy-link grammar minus the campaign binding and names what it leaves out', () => {
    const draft = savedViewDraft(
      new URLSearchParams('state=IL&row=B-0123456789ABC&assigned_to=lo%40summit.example&campaign_id=c1&variant_name=A&sort=score'),
      ['All'],
    );
    expect(draft.params).toBe('state=IL&sort=score&dir=desc');
    expect(draft.omitted).toEqual(['the open row', 'the assignee email', 'the campaign binding']);
    expect(savedViewDraft(new URLSearchParams('row=B-0123456789ABC'), ['All']).params).toBe('');
  });

  it('maps every failure to fixed copy', () => {
    const error = (status: number, message: string, field?: string) => new ApiError(message, {
      path: '/api/v1/workspace/saved-views',
      status,
      validationIssues: field ? [{ field, message: 'x', location: ['body', field] }] : [],
    });
    expect(saveErrorCopy(error(422, 'name: Value error, John Smith', 'name'))).toBe(SAVED_VIEW_COPY.name);
    expect(saveErrorCopy(error(422, 'params must be a Lead Queue view'))).toBe(SAVED_VIEW_COPY.params);
    expect(saveErrorCopy(error(409, SAVED_VIEW_COPY.duplicate))).toBe(SAVED_VIEW_COPY.duplicate);
    expect(saveErrorCopy(error(409, SAVED_VIEW_COPY.limit))).toBe(SAVED_VIEW_COPY.limit);
    expect(saveErrorCopy(error(409, 'anything else'))).toBe(SAVED_VIEW_COPY.changed);
    expect(saveErrorCopy(error(503, 'Lakebase down'))).toBe(SAVED_VIEW_COPY.unavailable);
    expect(saveErrorCopy(new Error('boom'))).toBe(SAVED_VIEW_COPY.unavailable);
  });
});

describe('Saved views panel', () => {
  it('reads nothing on mount; the first open loads the panel and reads the list once', async () => {
    await mountAt('state=IL&approval_status=pending');
    expect(calls).toEqual([]);
    expect(trigger().getAttribute('aria-expanded')).toBe('false');
    await open();
    expect(trigger().getAttribute('aria-expanded')).toBe('true');
    expect(calls.map((call) => `${call.method} ${call.path}`)).toEqual(['GET /api/v1/workspace/saved-views']);
    const link = document.querySelector<HTMLAnchorElement>('.lead-queue-saved-views__link')!;
    expect(link.textContent).toBe('IL pending');
    expect(link.getAttribute('href')).toBe('/lead-queue?approval_status=pending&state=IL');
    expect(link.getAttribute('aria-current')).toBe('true');
  });

  it('saves pessimistically: the view appears only after the POST and the list refetch', async () => {
    await mountAt('state=TX&max_rate_spread_bps=50&campaign_id=c1');
    await open();
    await typeName('TX refi');
    await save();
    expect(calls.map((call) => call.method)).toEqual(['GET', 'POST', 'GET']);
    expect(calls[1].body).toEqual({ name: 'TX refi', params: 'state=TX&max_rate_spread_bps=50' });
    const names = [...document.querySelectorAll('.lead-queue-saved-views__link')].map((link) => link.textContent);
    expect(names).toEqual(['TX refi', 'IL pending']);
    expect(byTestId<HTMLInputElement>('lead-queue-saved-views-name')!.value).toBe('');
  });

  it('disables Save when the queue has nothing to save', async () => {
    await mountAt('row=B-0123456789ABC');
    await open();
    await typeName('Anything');
    expect(byTestId<HTMLButtonElement>('lead-queue-saved-views-save')!.disabled).toBe(true);
  });

  it('shows fixed copy for a refused name and a duplicate, never the typed text', async () => {
    respond = (call) => (call.method === 'POST'
      ? json({ detail: [{ loc: ['body', 'name'], msg: 'Value error, saved view name must be a short public-safe label', type: 'value_error' }] }, 422)
      : json({ saved_views: views }));
    await mountAt('state=IL');
    await open();
    await typeName('John Smith leads');
    await save();
    const panel = byTestId('lead-queue-saved-views-panel')!;
    expect(panel.textContent).toContain(SAVED_VIEW_COPY.name);
    expect(panel.querySelector('.field__error')?.textContent).not.toContain('John');
    expect(byTestId<HTMLInputElement>('lead-queue-saved-views-name')!.getAttribute('aria-invalid')).toBe('true');

    respond = (call) => (call.method === 'POST' ? json({ detail: SAVED_VIEW_COPY.duplicate }, 409) : json({ saved_views: views }));
    await typeName('IL pending');
    await save();
    expect(panel.querySelector('.field__error')?.textContent).toBe(SAVED_VIEW_COPY.duplicate);
  });

  it('deletes pessimistically: aria-disabled while in flight, then a list refetch', async () => {
    let release: () => void = () => undefined;
    await mountAt('');
    await open();
    respond = (call) => {
      if (call.method !== 'DELETE') return json({ saved_views: views });
      return new Promise<Response>((resolve) => {
        release = () => {
          views = [];
          resolve(json({ ok: true, view_id: '11111111-1111-4111-8111-111111111111', audit_event_id: 'a-2' }));
        };
      });
    };
    const remove = document.querySelector<HTMLButtonElement>('.lead-queue-saved-views__item .filter__remove')!;
    expect(remove.getAttribute('aria-label')).toBe('Delete saved view: IL pending');
    await act(async () => remove.click());
    expect(remove.getAttribute('aria-disabled')).toBe('true');
    expect(document.querySelector('.lead-queue-saved-views__link')).not.toBeNull();
    await act(async () => release());
    await settle();
    expect(calls.map((call) => `${call.method} ${call.path}`)).toEqual([
      'GET /api/v1/workspace/saved-views',
      'DELETE /api/v1/workspace/saved-views/11111111-1111-4111-8111-111111111111',
      'GET /api/v1/workspace/saved-views',
    ]);
    expect(document.querySelector('.lead-queue-saved-views__link')).toBeNull();
    expect(byTestId('lead-queue-saved-views-panel')!.textContent).toContain('No saved views yet.');
  });

  it('says Saved views unavailable with an explicit Retry and no alert role', async () => {
    respond = () => json({ detail: 'Lakebase temporarily unavailable' }, 503);
    await mountAt('');
    await open();
    const panel = byTestId('lead-queue-saved-views-panel')!;
    expect(panel.textContent).toContain('Saved views unavailable');
    expect(panel.querySelector('[role="alert"]')).toBeNull();
    respond = () => json({ saved_views: views });
    const retry = [...panel.querySelectorAll('button')].find((button) => button.textContent === 'Retry')!;
    await act(async () => retry.click());
    await settle();
    expect(panel.querySelector('.lead-queue-saved-views__link')?.textContent).toBe('IL pending');
  });

  it('closes on a pointer-down outside without pulling focus back to the trigger', async () => {
    await mountAt('state=IL');
    await open();
    const outside = document.body.appendChild(document.createElement('button'));
    outside.focus();
    await act(async () => {
      outside.dispatchEvent(new PointerEvent('pointerdown', { bubbles: true }));
    });
    expect(byTestId('lead-queue-saved-views-panel')).toBeNull();
    expect(trigger().getAttribute('aria-expanded')).toBe('false');
    expect(document.activeElement).toBe(outside);
    outside.remove();
  });

  it('closes on Escape and hands focus back to the trigger', async () => {
    await mountAt('state=IL');
    await open();
    expect(byTestId('lead-queue-saved-views-panel')).not.toBeNull();
    byTestId<HTMLInputElement>('lead-queue-saved-views-name')!.focus();
    await act(async () => {
      window.dispatchEvent(new KeyboardEvent('keydown', { key: 'Escape', bubbles: true }));
    });
    expect(byTestId('lead-queue-saved-views-panel')).toBeNull();
    expect(document.activeElement).toBe(trigger());
    expect(trigger().getAttribute('aria-expanded')).toBe('false');
  });
});
