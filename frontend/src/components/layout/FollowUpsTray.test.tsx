// @vitest-environment happy-dom
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { act, createElement } from 'react';
import { MemoryRouter, useLocation } from 'react-router';
import { afterAll, afterEach, beforeAll, beforeEach, describe, expect, it, vi } from 'vitest';
import type { FollowupItem, FollowupList } from '../../lib/apiClients/followUps';
import { installLocalStorage } from '../../test/installLocalStorage';
import { mount } from '../../test/render';

const api = vi.hoisted(() => ({ list: vi.fn(), snooze: vi.fn() }));
// Synchronous factory: the tray chunk imports this module lazily.
vi.mock('../../lib/apiClients/followUps', () => ({
  fetchFollowupList: (windowDays: number) => api.list(windowDays),
  postLeadSnooze: (borrowerId: string, body: unknown) => api.snooze(borrowerId, body),
}));

import FollowUpsRailItem from './FollowUpsRailItem';
import { SNOOZE_HINT } from './FollowUpsTrayRow';

declare const process: { env: Record<string, string | undefined> };

/**
 * The Follow-ups tray (D-shell-deviations-a, wow-power-3;
 * deviation:followups-tray), driven through its real rail trigger: local-day
 * sections in order with empty ones omitted, withheld rows only in their own
 * group with the closed copy and no Snooze, plain links (20-id queue cap,
 * contactable ids only), Escape / outside pointerdown / route change closing
 * onto the trigger, and the Snooze group's focus order.
 */

const TZ = 'America/Chicago';
const NOW = Date.parse('2026-10-08T02:30:00Z'); // 21:30 CDT on Oct 7
let previousTz: string | undefined;
beforeAll(() => {
  previousTz = process.env.TZ;
  process.env.TZ = TZ;
});
afterAll(() => {
  process.env.TZ = previousTz;
});

function id(index: number): string {
  return `B-${String(index).padStart(13, '0')}`;
}

function item(overrides: Partial<FollowupItem>): FollowupItem {
  return {
    kind: 'callback', borrower_id: id(1), due_at: null, since_at: '2026-10-01T15:00:00Z', attempt_number: 2,
    owner_label: null, owner_state: null, contactable: true, contact_block_reason: null,
    earliest_recontact_at: null, woke_on_signal: false, ...overrides,
  };
}

const WITHHELD: FollowupItem[] = [
  item({ borrower_id: id(91), contactable: false, contact_block_reason: 'frequency_cap', due_at: '2026-10-07T20:00:00Z', earliest_recontact_at: '2026-10-20T15:00:00Z' }),
  item({ borrower_id: id(92), contactable: false, contact_block_reason: 'not_in_coverage', kind: 'new_assignment' }),
  item({ borrower_id: id(93), contactable: false, contact_block_reason: 'suppressed', kind: 'approval_follow_up', due_at: '2026-10-06T20:00:00Z' }),
];

const FULL: FollowupItem[] = [
  item({ borrower_id: id(1), due_at: '2026-10-07T04:00:00Z' }), // overdue: last night, local
  item({ borrower_id: id(2), due_at: '2026-10-08T04:30:00Z', kind: 'approval_follow_up' }), // today, local
  item({ borrower_id: id(3), due_at: '2026-10-09T15:00:00Z', woke_on_signal: true }), // this week
  item({ borrower_id: id(4), kind: 'requested_follow_up', due_at: '2026-10-07T12:00:00Z', owner_state: 'unassigned' }),
  item({ borrower_id: id(5), kind: 'new_assignment', since_at: '2026-10-07T13:00:00Z' }),
  item({ borrower_id: id(6), due_at: '2026-09-01T15:00:00Z' }), // older than 14 days
  ...WITHHELD,
];

function list(items: FollowupItem[]): FollowupList {
  return { items, generated_at: '2026-10-08T02:30:00Z', truncated: false };
}

function LocationProbe() {
  const location = useLocation();
  return createElement('output', { 'data-testid': 'location' }, `${location.pathname}${location.search}`);
}

let client: QueryClient;
beforeEach(() => {
  installLocalStorage();
  client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  api.list.mockReset();
  api.snooze.mockReset();
  vi.useFakeTimers({ toFake: ['Date'] });
  vi.setSystemTime(NOW);
});
afterEach(() => {
  vi.useRealTimers();
  client.clear();
});

async function settle(until: () => boolean): Promise<void> {
  for (let tick = 0; tick < 400 && !until(); tick += 1) {
    await act(async () => {
      await new Promise((resolve) => window.setTimeout(resolve, 10));
    });
  }
}

async function openTray(items: FollowupItem[]): Promise<{ trigger: HTMLButtonElement; tray: HTMLElement }> {
  api.list.mockResolvedValue(list(items));
  const { container } = await mount(
    createElement(
      QueryClientProvider,
      { client },
      createElement(
        MemoryRouter,
        { initialEntries: ['/lead-queue'] },
        createElement('nav', { className: 'rail' }, createElement(FollowUpsRailItem, { summary: { eligible: true, next_due_at: null } })),
        createElement('button', { type: 'button', 'data-testid': 'outside' }, 'outside'),
        createElement(LocationProbe),
      ),
    ),
  );
  const trigger = container.querySelector<HTMLButtonElement>('button.rail__item')!;
  await act(async () => trigger.click());
  await settle(() => document.querySelector('.followups .followups__foot') !== null && api.list.mock.calls.length > 0
    && (document.querySelector('.followups__row') !== null || document.querySelector('.followups__empty:not([role])') !== null));
  const tray = document.querySelector<HTMLElement>('.followups');
  if (!tray) throw new Error('the tray never opened');
  return { trigger, tray };
}

const titles = (tray: HTMLElement) =>
  [...tray.querySelectorAll('.followups__section-title')].map((node) => node.textContent);
const idsIn = (node: Element | null | undefined) =>
  [...(node?.querySelectorAll('.followups__row') ?? [])].map((row) => row.getAttribute('data-borrower-id'));
const sectionOf = (tray: HTMLElement, title: string) =>
  [...tray.querySelectorAll('.followups__section')].find((section) =>
    section.querySelector('.followups__section-title')?.textContent?.startsWith(title));

describe('the Follow-ups tray', () => {
  it('is a labelled region that reads the 7-day list once on open', async () => {
    const { trigger, tray } = await openTray(FULL);
    expect(api.list).toHaveBeenCalledTimes(1);
    expect(api.list).toHaveBeenCalledWith(7);
    expect(tray.getAttribute('role')).toBe('region');
    expect(document.getElementById(tray.getAttribute('aria-labelledby') ?? '')?.textContent).toBe('Follow-ups');
    expect(tray.id).toBe(trigger.getAttribute('aria-controls'));
    expect(tray.getAttribute('popover')).toBe('manual');
  });

  it('buckets by the local day in order, collapsed groups last, empty sections omitted', async () => {
    const { tray } = await openTray(FULL);
    expect(titles(tray)).toEqual([
      'Overdue', 'Today', 'This week', 'Follow-ups you requested', 'Newly assigned',
      'Older (1)', 'No longer contactable (3)',
    ]);
    expect(idsIn(sectionOf(tray, 'Overdue'))).toEqual([id(1)]);
    expect(idsIn(sectionOf(tray, 'Today'))).toEqual([id(2)]);
    expect(idsIn(sectionOf(tray, 'This week'))).toEqual([id(3)]);
    expect(idsIn(sectionOf(tray, 'Older'))).toEqual([id(6)]);
    expect(sectionOf(tray, 'Older')?.tagName).toBe('DETAILS');
    expect((sectionOf(tray, 'Older') as HTMLDetailsElement).open).toBe(false);
  });

  it('omits every empty section', async () => {
    const { tray } = await openTray([item({ borrower_id: id(1), due_at: '2026-10-08T04:30:00Z' })]);
    expect(titles(tray)).toEqual(['Today']);
  });

  it('says why each row is here', async () => {
    const { tray } = await openTray(FULL);
    const why = (borrower: string) => tray.querySelector(`[data-borrower-id="${borrower}"] .followups__why`)?.textContent;
    expect(why(id(1))).toBe('Callback scheduled · attempt 2');
    expect(why(id(2))).toBe('Approval follow-up');
    expect(why(id(3))).toBe('Callback scheduled · attempt 2 · new signal');
    expect(why(id(4))).toBe('Follow-up you requested · Routed, not yet assigned');
    expect(why(id(5))).toBe('Assigned to you');
  });

  it('writes every row in one grammar: the reason line, then a meta line led by the due time', async () => {
    // The geometry is pinned on the rendered build (followups-snooze.fixture.spec.ts (f)); this is the DOM it stands on.
    const { tray } = await openTray(FULL);
    const rows = [...tray.querySelectorAll<HTMLElement>('.followups__row')];
    expect(rows).toHaveLength(FULL.length);
    for (const row of rows) {
      const withheld = row.querySelector('.followups__block') !== null;
      const children = [...row.children].map((child) => child.className);
      expect(children).toEqual(['followups__id', 'followups__why', 'followups__meta', ...(withheld ? ['followups__block'] : [])]);
      const meta = [...row.querySelector('.followups__meta')!.children];
      expect(meta[0].matches('time.followups__due')).toBe(true);
      expect(meta.slice(1).map((child) => child.matches('button.followups__snooze'))).toEqual(withheld ? [] : [true]);
    }
    const qualifiers = (borrower: string) =>
      [...tray.querySelectorAll(`[data-borrower-id="${borrower}"] .followups__why .followups__qualifier`)].map((node) => node.textContent);
    expect(qualifiers(id(3))).toEqual(['attempt 2', 'new signal']);
    expect(qualifiers(id(4))).toEqual(['Routed, not yet assigned']);
    expect(qualifiers(id(5))).toEqual([]);
  });

  it('keeps every withheld row in its own group, with the closed copy and no Snooze', async () => {
    const { tray } = await openTray(FULL);
    const withheld = sectionOf(tray, 'No longer contactable');
    expect(idsIn(withheld)).toEqual([id(91), id(92), id(93)]);
    for (const title of ['Overdue', 'Today', 'This week', 'Newly assigned']) {
      expect(idsIn(sectionOf(tray, title)).some((value) => value?.startsWith('B-00000000000009'))).toBe(false);
    }
    const row = (borrower: string) => withheld?.querySelector(`[data-borrower-id="${borrower}"]`);
    expect(row(id(91))?.querySelector('.chip')?.textContent).toBe('Frequency cap');
    expect(row(id(91))?.querySelector('.followups__block')?.textContent).toMatch(/^Frequency capDo not call before Oct 20 \(contact frequency cap\)\.$/);
    expect(row(id(92))?.querySelector('.chip')?.textContent).toBe('Not in coverage');
    expect(row(id(92))?.textContent).toContain('Do not call: no longer in current coverage.');
    expect(row(id(93))?.querySelector('.chip')?.textContent).toBe('Suppressed');
    expect(row(id(93))?.textContent).toContain('Do not call: contact status changed since this was scheduled.');
    expect(withheld?.querySelectorAll('.followups__snooze')).toHaveLength(0);
    expect(tray.querySelector(`[data-borrower-id="${id(1)}"] .followups__snooze`)).not.toBeNull();
  });

  it('links rows plainly and hands only Overdue and Today contactable ids to the queue', async () => {
    const { tray } = await openTray(FULL);
    expect(tray.querySelector(`[data-borrower-id="${id(1)}"] a.followups__id`)?.getAttribute('href')).toBe(`/borrower-360/${id(1)}`);
    const links = [...tray.querySelectorAll<HTMLAnchorElement>('.followups__foot a')];
    expect(links.map((link) => link.textContent)).toEqual(['Work due items in the queue', 'My queue']);
    expect(links[0].getAttribute('href')).toBe(`/lead-queue?borrower_ids=${id(1)},${id(2)}`);
    expect(links[1].getAttribute('href')).toBe('/lead-queue?assigned_to=me');
  });

  it('caps the queue handoff at the queue parse cap (50 ids) and says so', async () => {
    const due = Array.from({ length: 53 }, (_, index) => item({ borrower_id: id(index + 1), due_at: '2026-10-07T20:00:00Z' }));
    const { tray } = await openTray(due);
    const [handoff] = [...tray.querySelectorAll<HTMLAnchorElement>('.followups__foot a')];
    expect(handoff.textContent).toBe('Work the first 50 due items in the queue');
    const ids = new URLSearchParams(handoff.getAttribute('href')?.split('?')[1]).get('borrower_ids')?.split(',');
    expect(ids).toHaveLength(50);
  });

  it('omits the queue handoff when nothing is due', async () => {
    const { tray } = await openTray([item({ borrower_id: id(5), kind: 'new_assignment' })]);
    expect([...tray.querySelectorAll('.followups__foot a')].map((link) => link.textContent)).toEqual(['My queue']);
  });

  it('says when nothing is there at all', async () => {
    const { tray } = await openTray([]);
    expect(tray.querySelector('.followups__body')?.textContent).toBe(
      'Nothing due. Callbacks, follow-ups and new assignments appear here.',
    );
  });

  it.each([
    ['Escape', async () => {
      document.dispatchEvent(new KeyboardEvent('keydown', { key: 'Escape', bubbles: true }));
    }],
    ['a pointerdown outside', async () => {
      document.querySelector('[data-testid="outside"]')?.dispatchEvent(new PointerEvent('pointerdown', { bubbles: true }));
    }],
    ['a route change', async () => {
      document.querySelector<HTMLAnchorElement>(`[data-borrower-id="${id(1)}"] a`)?.click();
    }],
  ])('closes on %s and returns focus to the trigger', async (_how, close) => {
    const { trigger } = await openTray(FULL);
    document.querySelector<HTMLElement>('.followups__title')?.focus();
    await act(close);
    expect(document.querySelector('.followups')).toBeNull();
    expect(trigger.getAttribute('aria-expanded')).toBe('false');
    expect(document.activeElement).toBe(trigger);
  });

  it('opens Snooze as a labelled group, focuses the first option, and Escape returns to Snooze', async () => {
    const { tray } = await openTray(FULL);
    const row = tray.querySelector(`[data-borrower-id="${id(2)}"]`)!;
    await act(async () => row.querySelector<HTMLButtonElement>('button.followups__snooze')?.click());

    const group = row.querySelector('[role="group"]');
    expect(group?.getAttribute('aria-label')).toBe(`Snooze ${id(2)} for`);
    const options = [...(group?.querySelectorAll('button') ?? [])].map((button) => button.textContent);
    expect(options).toEqual(['1 day', '3 days', '1 week', 'Cancel']);
    expect(document.activeElement?.textContent).toBe('1 day');
    expect(group?.textContent).toContain(SNOOZE_HINT);

    await act(async () => {
      document.dispatchEvent(new KeyboardEvent('keydown', { key: 'Escape', bubbles: true }));
    });
    expect(row.querySelector('[role="group"]')).toBeNull();
    expect(document.querySelector('.followups'), 'the group took the Escape, not the tray').not.toBeNull();
    expect(document.activeElement).toBe(row.querySelector('button.followups__snooze'));
  });

  it('keeps the row, with the chosen option loading, until the snooze answers', async () => {
    let answer: (value: unknown) => void = () => undefined;
    api.snooze.mockReturnValue(new Promise((resolve) => { answer = resolve; }));
    const { tray } = await openTray(FULL);
    const row = tray.querySelector(`[data-borrower-id="${id(2)}"]`)!;
    await act(async () => row.querySelector<HTMLButtonElement>('button.followups__snooze')?.click());
    const [oneDay, threeDays] = [...row.querySelectorAll<HTMLButtonElement>('[role="group"] button')];
    await act(async () => threeDays.click());
    // TanStack notifies observers on a macrotask: wait for the pending render.
    await settle(() => threeDays.getAttribute('aria-busy') === 'true');

    expect(api.snooze).toHaveBeenCalledWith(id(2), { snooze_days: 3, request_id: expect.any(String) });
    expect(Object.keys(api.snooze.mock.calls[0][1] as object).sort()).toEqual(['request_id', 'snooze_days']);
    expect(threeDays.getAttribute('aria-busy')).toBe('true');
    expect(oneDay.disabled).toBe(true);
    expect(tray.querySelector(`[data-borrower-id="${id(2)}"]`), 'pessimistic: the row stays').not.toBeNull();

    api.list.mockResolvedValue(list(FULL.filter((row2) => row2.borrower_id !== id(2))));
    await act(async () => answer({ borrower_id: id(2), wake_at: '2026-10-10T15:00:00Z', wake_clamped: false, audit_event_id: '11111111-1111-4111-8111-111111111111' }));
    await settle(() => tray.querySelector(`[data-borrower-id="${id(2)}"]`) === null);
    expect(tray.querySelector(`[data-borrower-id="${id(2)}"]`)).toBeNull();
    expect(document.activeElement, 'focus moves to the next row').toBe(tray.querySelector(`[data-borrower-id="${id(3)}"] a`));
  });

  it.each([
    ['collapsed', 'the tray heading', false],
    ['expanded', 'its first row', true],
  ])('after snoozing the last open row with Older %s, focus moves to %s', async (_state, _target, expandOlder) => {
    api.snooze.mockResolvedValue({ borrower_id: id(5), wake_at: '2026-10-08T15:00:00Z', wake_clamped: false, audit_event_id: '11111111-1111-4111-8111-111111111111' });
    const { tray } = await openTray(FULL);
    const older = sectionOf(tray, 'Older') as HTMLDetailsElement;
    if (expandOlder) await act(async () => { older.open = true; });
    const row = tray.querySelector(`[data-borrower-id="${id(5)}"]`)!;
    await act(async () => row.querySelector<HTMLButtonElement>('button.followups__snooze')?.click());
    api.list.mockResolvedValue(list(FULL.filter((entry) => entry.borrower_id !== id(5))));
    await act(async () => row.querySelector<HTMLButtonElement>('[role="group"] button')?.click());
    await settle(() => tray.querySelector(`[data-borrower-id="${id(5)}"]`) === null);

    // A link inside a collapsed group cannot take focus in a browser.
    const expected = expandOlder ? tray.querySelector(`[data-borrower-id="${id(6)}"] a`) : tray.querySelector('.followups__title');
    expect(document.activeElement).toBe(expected);
  });
});
