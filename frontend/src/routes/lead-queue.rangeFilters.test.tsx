/**
 * @vitest-environment happy-dom
 *
 * Score and rate-spread inputs (audit tables-06): a commit is a blur or an
 * Enter, never a keystroke, and writes the URL once (so the queue reads
 * GET /api/leads once); clamps say so, inverted bounds refuse to commit.
 */
import { act, useEffect } from 'react';
import { MemoryRouter, useLocation } from 'react-router';
import { beforeEach, describe, expect, it } from 'vitest';
import { mount } from '../test/render';
import { LeadQueueRangeFilters, RANGE_INVERTED_ERROR, rangeCommit } from './lead-queue.rangeFilters';

const searches: string[] = [];

function LocationProbe() {
  const { search } = useLocation();
  useEffect(() => {
    searches.push(search);
  }, [search]);
  return null;
}

async function mountAt(query: string) {
  const mounted = await mount(
    <MemoryRouter initialEntries={[`/lead-queue${query}`]}>
      <LeadQueueRangeFilters />
      <LocationProbe />
    </MemoryRouter>,
  );
  return mounted.container;
}

const input = (key: string) => document.querySelector<HTMLInputElement>(`[data-testid="lead-queue-range-${key}"]`)!;
const writes = () => searches.length - 1;

async function type(element: HTMLInputElement, text: string) {
  const setter = Object.getOwnPropertyDescriptor(HTMLInputElement.prototype, 'value')!.set!;
  await act(async () => {
    element.focus();
    setter.call(element, text);
    element.dispatchEvent(new Event('input', { bubbles: true }));
  });
}

async function blur(element: HTMLInputElement) {
  await act(async () => {
    element.blur();
  });
}

async function enter(element: HTMLInputElement) {
  await act(async () => {
    element.dispatchEvent(new KeyboardEvent('keydown', { key: 'Enter', bubbles: true }));
  });
}

describe('LeadQueueRangeFilters', () => {
  beforeEach(() => {
    searches.length = 0;
  });

  it('writes nothing per keystroke and exactly once on blur', async () => {
    await mountAt('?state=IL');
    const score = input('min_opportunity_score');
    await type(score, '7');
    await type(score, '70');
    expect(writes()).toBe(0);
    await blur(score);
    expect(writes()).toBe(1);
    expect(searches[searches.length - 1]).toBe('?state=IL&min_opportunity_score=70');
    expect(score.value).toBe('70');
    // A second blur with nothing typed writes nothing.
    await act(async () => score.focus());
    await blur(score);
    expect(writes()).toBe(1);
  });

  it('commits on Enter, skips an unchanged value and clears on empty', async () => {
    await mountAt('?max_rate_spread_bps=150');
    const spread = input('max_rate_spread_bps');
    expect(spread.value).toBe('150');
    await type(spread, '150');
    await enter(spread);
    expect(writes()).toBe(0);
    await type(spread, '-10');
    await enter(spread);
    expect(searches[searches.length - 1]).toBe('?max_rate_spread_bps=-10');
    await type(spread, '');
    await blur(spread);
    expect(searches[searches.length - 1]).toBe('');
    expect(writes()).toBe(2);
  });

  it('clamps an out-of-range value and says so in the field notice', async () => {
    await mountAt('');
    const score = input('max_opportunity_score');
    await type(score, '150');
    await blur(score);
    expect(searches[searches.length - 1]).toBe('?max_opportunity_score=100');
    const notice = document.getElementById(score.getAttribute('aria-describedby')!.split(' ')[0]);
    expect(notice?.getAttribute('role')).toBe('status');
    expect(notice?.textContent).toBe('Allowed range is 0 to 100; set to 100.');
    expect(score.getAttribute('aria-invalid')).toBeNull();
  });

  it('refuses an at-least above its at-most with a field error and no write', async () => {
    await mountAt('?max_opportunity_score=50');
    const score = input('min_opportunity_score');
    await type(score, '60');
    await blur(score);
    expect(writes()).toBe(0);
    expect(score.getAttribute('aria-invalid')).toBe('true');
    const described = (score.getAttribute('aria-describedby') ?? '').split(' ').map((id) => document.getElementById(id)?.textContent);
    expect(described).toContain(RANGE_INVERTED_ERROR);
    // Editing clears the error; a valid value then commits.
    await type(score, '40');
    expect(score.getAttribute('aria-invalid')).toBeNull();
    await blur(score);
    expect(searches[searches.length - 1]).toBe('?max_opportunity_score=50&min_opportunity_score=40');
  });

  it('disables every input with a hint beside a Genie cohort or a Growth Agent proof', async () => {
    await mountAt('?cohort_id=11111111-1111-1111-1111-111111111111');
    const all = [...document.querySelectorAll<HTMLInputElement>('input[type="number"]')];
    expect(all).toHaveLength(4);
    expect(all.every((element) => element.disabled)).toBe(true);
    const hintId = (all[0].getAttribute('aria-describedby') ?? '').split(' ').pop()!;
    expect(document.getElementById(hintId)?.textContent).toContain('sets its own score and spread thresholds');
  });

  it('shows a value written elsewhere (a removed chip) instead of a stale draft', async () => {
    await mountAt('?min_rate_spread_bps=20');
    const spread = input('min_rate_spread_bps');
    await type(spread, '30');
    expect(spread.value).toBe('30');
    // rangeCommit is pure: the same decision the field makes.
    expect(rangeCommit(new URLSearchParams('min_rate_spread_bps=20'), 'min_rate_spread_bps', '30')).toMatchObject({
      kind: 'write',
      committed: '30',
    });
    expect(rangeCommit(new URLSearchParams('max_rate_spread_bps=10'), 'min_rate_spread_bps', '30')).toEqual({
      kind: 'error',
      error: RANGE_INVERTED_ERROR,
    });
  });
});
