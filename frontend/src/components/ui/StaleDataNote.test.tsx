/**
 * @vitest-environment happy-dom
 */
/**
 * StaleDataNote (audit delivery-06 client half): the visible marker for
 * counts the server retained after a failed refresh.
 */
import { act } from 'react';
import { createRoot, type Root } from 'react-dom/client';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { StaleDataNote } from './StaleDataNote';

(globalThis as unknown as { IS_REACT_ACT_ENVIRONMENT: boolean }).IS_REACT_ACT_ENVIRONMENT = true;

const NOW = Date.parse('2026-10-01T12:00:00Z');

describe('StaleDataNote', () => {
  let root: Root;

  beforeEach(() => {
    vi.useFakeTimers({ toFake: ['Date'] });
    vi.setSystemTime(NOW);
    document.body.innerHTML = '<div id="root"></div>';
    root = createRoot(document.getElementById('root') as HTMLElement);
  });

  afterEach(() => {
    act(() => root.unmount());
    document.body.innerHTML = '';
    vi.useRealTimers();
  });

  const note = () => document.querySelector('[data-testid="stale-data-note"]');
  const render = (lastGoodAt: string | number | null) => act(() => root.render(<StaleDataNote lastGoodAt={lastGoodAt} />));

  it('renders nothing for current counts', () => {
    render(null);
    expect(note()).toBeNull();
    expect(document.getElementById('root')?.innerHTML).toBe('');
  });

  it('says how old the counts are, relative within a day, as a status chip', () => {
    render('2026-10-01T09:00:00Z');
    const el = note();
    expect(el?.getAttribute('role')).toBe('status');
    expect(el?.className).toBe('chip chip--warning stale-note');
    expect(el?.textContent).toBe(
      'Showing counts last read 3 hours ago. The latest refresh failed; the counts update on a later refresh once the warehouse responds.',
    );
    expect(el?.querySelector('time')?.getAttribute('dateTime')).toBe('2026-10-01T09:00:00.000Z');
  });

  it('uses an absolute date and time past 24 hours, and accepts epoch ms', () => {
    render(Date.parse('2026-09-29T08:00:00Z'));
    const text = note()?.textContent ?? '';
    expect(text).toContain('Showing counts last read Sep 29');
    expect(text).not.toContain('ago');
  });

  it('never offers a refresh of its own', () => {
    render('2026-10-01T09:00:00Z');
    expect(note()?.textContent).not.toMatch(/refresh to try again/i);
    expect(note()?.querySelector('button')).toBeNull();
  });

  it('clears when the next response carries no header', () => {
    render('2026-10-01T09:00:00Z');
    expect(note()).not.toBeNull();
    render(null);
    expect(note()).toBeNull();
  });

  it('ignores an unparseable instant rather than guessing an age', () => {
    render('not a time');
    expect(note()).toBeNull();
  });
});
