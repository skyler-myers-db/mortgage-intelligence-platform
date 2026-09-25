/**
 * @vitest-environment happy-dom
 */
import { act } from 'react';
import { createRoot, type Root } from 'react-dom/client';
import { afterEach, beforeEach, describe, expect, it } from 'vitest';
import { EmptyState, type EmptyCause } from './EmptyState';

(globalThis as unknown as { IS_REACT_ACT_ENVIRONMENT: boolean }).IS_REACT_ACT_ENVIRONMENT = true;

/** A measured zero, with its cause and at most two next steps (audit states-v2). */

let root: Root;

beforeEach(() => {
  document.body.innerHTML = '<div id="root"></div>';
  root = createRoot(document.getElementById('root') as HTMLElement);
});

afterEach(() => {
  act(() => root.unmount());
  document.body.innerHTML = '';
});

const empty = () => document.querySelector('.empty');

describe('EmptyState', () => {
  it.each([
    ['filtered', 'No leads match this filter.', null],
    ['coverage', 'No ZIP-level rollup for this county in the current Cotality data coverage.', null],
    ['intersection', '0 borrowers sit in every selected segment — a real intersection result from the live query, not an error.', 'Remove a segment chip to widen the cohort.'],
    ['day-zero', 'No segment rows yet: the first data refresh has not landed.', null],
  ] as const)('%s: its default sentence, as a status', (cause: EmptyCause, title, secondary) => {
    act(() => root.render(<EmptyState cause={cause} />));
    expect(empty()?.getAttribute('role')).toBe('status');
    expect(empty()?.getAttribute('data-empty-cause')).toBe(cause);
    expect(document.querySelector('.empty__title')?.textContent).toBe(title);
    expect(document.querySelector('.empty__copy')?.textContent ?? null).toBe(secondary);
    expect(document.querySelector('.empty__icon')?.getAttribute('aria-hidden')).toBe('true');
    expect(document.querySelector('.empty__actions')).toBeNull();
  });

  it('takes a title and a secondary line, or drops the default one', () => {
    act(() => root.render(<EmptyState cause="filtered" title="Nothing here." secondary="The default view lists marketing-eligible borrowers only." />));
    expect(document.querySelector('.empty__title')?.textContent).toBe('Nothing here.');
    expect(document.querySelector('.empty__copy')?.textContent).toBe('The default view lists marketing-eligible borrowers only.');
    act(() => root.render(<EmptyState cause="intersection" secondary={null} />));
    expect(document.querySelector('.empty__copy')).toBeNull();
  });

  it('renders at most two actions', () => {
    const three = [<button key="a">One</button>, <button key="b">Two</button>, <button key="c">Three</button>];
    // @ts-expect-error — the actions type is capped at two.
    act(() => root.render(<EmptyState cause="filtered" actions={three} />));
    const labels = [...document.querySelectorAll('.empty__actions button')].map((button) => button.textContent);
    expect(labels).toEqual(['One', 'Two']);
  });

  it('skips an absent action', () => {
    act(() => root.render(<EmptyState cause="filtered" actions={[<button key="a">Clear filters</button>, null]} />));
    expect(document.querySelectorAll('.empty__actions button')).toHaveLength(1);
  });
});
