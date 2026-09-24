/**
 * @vitest-environment happy-dom
 */
import { act } from 'react';
import { createRoot, type Root } from 'react-dom/client';
import { MemoryRouter } from 'react-router';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import {
  LEAD_QUEUE_PRESETS,
  LeadQueueViews,
  activeLeadQueuePreset,
  searchParamsWithPreset,
} from './lead-queue.views';

(globalThis as unknown as { IS_REACT_ACT_ENVIRONMENT: boolean }).IS_REACT_ACT_ENVIRONMENT = true;

const sp = (query: string) => new URLSearchParams(query);

describe('queue presets (audit tables-09 phase 1)', () => {
  it('lists the four system views in order, on existing params only', () => {
    expect(LEAD_QUEUE_PRESETS.map((preset) => [preset.label, preset.param])).toEqual([
      ['All', null],
      ['Pending approval', ['approval_status', 'pending']],
      ['Approved, no outreach >7d', ['aged_days', '7']],
      ['Assigned to me', ['assigned_to', 'me']],
    ]);
  });

  it('sets its own key, drops the other two and a workflow funnel stage, keeps everything else', () => {
    const base = sp('state=IL&segment=itm&sort=equity&dir=asc&view=sales-ops&aged_days=14&assigned_to=lo%40summit.example&funnel_stage=approved');
    expect(searchParamsWithPreset(base, 'pending').toString()).toBe(
      'state=IL&segment=itm&sort=equity&dir=asc&view=sales-ops&approval_status=pending',
    );
    expect(searchParamsWithPreset(sp('approval_status=pending&funnel_stage=actioned'), 'aged').toString()).toBe('aged_days=7');
    expect(searchParamsWithPreset(sp('approval_status=rejected'), 'mine').toString()).toBe('assigned_to=me');
    expect(searchParamsWithPreset(sp('state=TX&approval_status=pending&aged_days=7'), 'all').toString()).toBe('state=TX');
    // A non-workflow funnel stage is a scope, not a status: it stays.
    expect(searchParamsWithPreset(sp('funnel_stage=in_the_money'), 'pending').toString()).toBe(
      'funnel_stage=in_the_money&approval_status=pending',
    );
    // Never the column preset param.
    expect(searchParamsWithPreset(sp('view=sales-ops'), 'mine').get('view')).toBe('sales-ops');
  });

  it('is active only when its key holds its value and the other two keys are absent', () => {
    expect(activeLeadQueuePreset(sp('state=IL&funnel_stage=approved'))).toBe('all');
    expect(activeLeadQueuePreset(sp('approval_status=pending'))).toBe('pending');
    expect(activeLeadQueuePreset(sp('aged_days=7'))).toBe('aged');
    expect(activeLeadQueuePreset(sp('assigned_to=ME'))).toBe('mine');
    expect(activeLeadQueuePreset(sp('approval_status=approved'))).toBeNull();
    expect(activeLeadQueuePreset(sp('aged_days=14'))).toBeNull();
    expect(activeLeadQueuePreset(sp('assigned_to=lo%40summit.example'))).toBeNull();
    expect(activeLeadQueuePreset(sp('approval_status=pending&aged_days=7'))).toBeNull();
  });
});

describe('LeadQueueViews (rendered)', () => {
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

  function mount(query: string, showAssignedToMe: boolean, onCopyLink = vi.fn()) {
    act(() => {
      root.render(
        <MemoryRouter initialEntries={[`/lead-queue?${query}`]}>
          <LeadQueueViews searchParams={sp(query)} showAssignedToMe={showAssignedToMe} onCopyLink={onCopyLink} />
        </MemoryRouter>,
      );
    });
    return onCopyLink;
  }

  const pills = () => Array.from(container.querySelectorAll<HTMLAnchorElement>('[aria-label="Queue presets"] a.filter'));

  it('renders anchor pills in a labelled group and marks the active one with aria-current', () => {
    mount('state=IL&approval_status=pending', true);

    expect(container.querySelector('[role="group"][aria-label="Queue presets"]')?.classList.contains('filter-row')).toBe(true);
    expect(pills().map((pill) => pill.textContent)).toEqual([
      'All', 'Pending approval', 'Approved, no outreach >7d', 'Assigned to me',
    ]);
    const current = pills().filter((pill) => pill.getAttribute('aria-current') === 'true');
    expect(current.map((pill) => pill.textContent)).toEqual(['Pending approval']);
    expect(current[0].classList.contains('is-active')).toBe(true);
    expect(pills()[3].getAttribute('href')).toBe('/lead-queue?state=IL&assigned_to=me');
    expect(pills()[0].getAttribute('href')).toBe('/lead-queue?state=IL');
  });

  it('hides Assigned to me unless the actor is a listed loan officer', () => {
    mount('', false);
    expect(pills().map((pill) => pill.textContent)).toEqual(['All', 'Pending approval', 'Approved, no outreach >7d']);
    expect(pills()[0].getAttribute('aria-current')).toBe('true');
  });

  it('offers Copy link as a ghost button', () => {
    const onCopyLink = mount('', true);
    const button = container.querySelector<HTMLButtonElement>('[data-testid="lead-queue-copy-link"]');
    expect(button?.className).toBe('btn btn--ghost btn--sm lead-queue-views__copy');
    act(() => button?.click());
    expect(onCopyLink).toHaveBeenCalledTimes(1);
  });
});
