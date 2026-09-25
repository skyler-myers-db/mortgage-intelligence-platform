/**
 * @vitest-environment happy-dom
 */
import { act } from 'react';
import { createRoot, type Root } from 'react-dom/client';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { FetchedAt } from './FetchedAt';

(globalThis as unknown as { IS_REACT_ACT_ENVIRONMENT: boolean }).IS_REACT_ACT_ENVIRONMENT = true;

/** The view's age and its one explicit re-read (audit states-09). */

let root: Root;
const T0 = new Date('2026-09-25T12:00:00Z').getTime();

beforeEach(() => {
  vi.useFakeTimers();
  vi.setSystemTime(T0);
  document.body.innerHTML = '<div id="root"></div>';
  root = createRoot(document.getElementById('root') as HTMLElement);
});

afterEach(() => {
  act(() => root.unmount());
  document.body.innerHTML = '';
  vi.useRealTimers();
});

const button = () => document.querySelector<HTMLButtonElement>('button[aria-label="Refresh ranked borrowers"]');

describe('FetchedAt', () => {
  it('says when the view was fetched, with a machine-readable instant', () => {
    act(() => root.render(<FetchedAt at={T0 - 3 * 60_000} subject="ranked borrowers" isFetching={false} onRefresh={vi.fn()} />));
    const time = document.querySelector('.fetched-at time');
    expect(document.querySelector('.fetched-at__label')?.textContent).toBe('Fetched 3m ago');
    expect(time?.getAttribute('dateTime')).toBe(new Date(T0 - 3 * 60_000).toISOString());
  });

  it('Refresh calls the query once per click', () => {
    const refresh = vi.fn();
    act(() => root.render(<FetchedAt at={T0} subject="ranked borrowers" isFetching={false} onRefresh={refresh} />));
    act(() => button()?.click());
    expect(refresh).toHaveBeenCalledTimes(1);
  });

  it('is aria-disabled, never native disabled, and reads "Refreshing…" while a read is in flight', () => {
    const refresh = vi.fn();
    act(() => root.render(<FetchedAt at={T0} subject="ranked borrowers" isFetching onRefresh={refresh} />));
    expect(button()?.getAttribute('aria-disabled')).toBe('true');
    expect(button()?.disabled).toBe(false);
    expect(button()?.textContent).toBe('Refreshing…');
    act(() => button()?.click());
    expect(refresh).not.toHaveBeenCalled();
  });

  it('renders nothing before the first fetch', () => {
    act(() => root.render(<FetchedAt at={null} subject="ranked borrowers" isFetching={false} onRefresh={vi.fn()} />));
    expect(document.querySelector('.fetched-at')).toBeNull();
  });
});
