/**
 * @vitest-environment happy-dom
 *
 * BorrowerTitle, the borrower-id morph target (deviation:borrower-id-morph):
 * it takes the shared name only for the id a row click just marked, reads it
 * once at mount, and lets it go after the release time, so no duplicate name
 * can survive into a later transition (one would abort it).
 */
import { act } from 'react';
import { createRoot, type Root } from 'react-dom/client';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';

const morph = vi.hoisted(() => ({
  nameFor: vi.fn<(id: string | undefined) => string | undefined>(),
  clear: vi.fn(),
}));

vi.mock('../lib/borrowerMorph', () => ({
  borrowerMorphNameFor: morph.nameFor,
  clearBorrowerMorph: morph.clear,
}));

import { BorrowerTitle } from './borrower-360.title';

(globalThis as unknown as { IS_REACT_ACT_ENVIRONMENT: boolean }).IS_REACT_ACT_ENVIRONMENT = true;

const ID = 'B-0123456789ABC';
let root: Root;

beforeEach(() => {
  vi.useFakeTimers();
  morph.nameFor.mockReset();
  morph.clear.mockReset();
  // The release reads --dur-base at runtime: 200ms + a 100 ms margin.
  document.documentElement.style.setProperty('--dur-base', '200ms');
  document.body.innerHTML = '<h1 id="title"></h1>';
  root = createRoot(document.getElementById('title') as HTMLElement);
});

afterEach(() => {
  act(() => root.unmount());
  vi.useRealTimers();
  document.documentElement.style.removeProperty('--dur-base');
  document.body.innerHTML = '';
});

const span = () => document.querySelector<HTMLElement>('.page-title__id');
const nameOf = () => span()?.style.viewTransitionName ?? '';

describe('BorrowerTitle', () => {
  it('reads "Borrower B-…" and carries no name when nothing was marked', () => {
    act(() => root.render(<BorrowerTitle id={ID} />));
    expect(document.getElementById('title')?.textContent).toBe(`Borrower ${ID}`);
    expect(span()?.textContent).toBe(ID);
    expect(nameOf()).toBe('');
    expect(morph.nameFor).toHaveBeenCalledWith(ID);
  });

  it('takes the marked name once and lets it go after the release (duplicate-name guard)', () => {
    morph.nameFor.mockReturnValue('mip-borrower-id');
    act(() => root.render(<BorrowerTitle id={ID} />));
    expect(nameOf()).toBe('mip-borrower-id');

    // A branch change re-renders the same element: the name is not re-read.
    act(() => root.render(<BorrowerTitle id={ID} />));
    expect(morph.nameFor).toHaveBeenCalledTimes(1);

    act(() => vi.advanceTimersByTime(299)); // --dur-base 200ms + 100
    expect(nameOf()).toBe('mip-borrower-id');
    act(() => vi.advanceTimersByTime(1));
    expect(nameOf()).toBe('');
    expect(morph.clear).toHaveBeenCalledTimes(1);
  });
});
