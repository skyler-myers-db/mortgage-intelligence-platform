/**
 * @vitest-environment happy-dom
 *
 * FilterSelect's option-count status (tables-06 nits [7]/[8]), pinned on the
 * rendered menu with a static counts provider:
 *  - aria-busy rides the sr-only role=status region while counts load,
 *    never the role=listbox (a busy listbox hides its options);
 *  - the status region mounts EMPTY and fills "Counts unavailable" one frame
 *    later, so the failure is announced rather than inserted pre-filled.
 */
import { act } from 'react';
import { describe, expect, it } from 'vitest';
import { mount } from '../../test/render';
import { FilterSelect } from './FilterSelect';
import { FilterFacetContext, type FilterFacetCounts, type FilterFacetSource } from './FilterSelect.facets';

function source(status: FilterFacetCounts['status']): FilterFacetSource {
  const counts: FilterFacetCounts = { status, countFor: () => (status === 'ready' ? 3 : null) };
  return { setOpen: () => undefined, counts: () => counts };
}

async function openMenu(status: FilterFacetCounts['status']) {
  await mount(
    <FilterFacetContext.Provider value={source(status)}>
      <FilterSelect label="STATE" value="All states" options={['All states', 'IL', 'TX']} onChange={() => undefined} />
    </FilterFacetContext.Provider>,
  );
  const trigger = document.querySelector<HTMLButtonElement>('button[aria-haspopup="listbox"]')!;
  // A synchronous act: effects have run, the status's frame has not.
  act(() => trigger.click());
}

const listbox = () => document.querySelector<HTMLElement>('[role="listbox"]')!;
const status = () => document.querySelector<HTMLElement>('[role="status"].sr-only');

async function nextFrame() {
  await act(async () => {
    await new Promise<void>((resolve) => window.requestAnimationFrame(() => resolve()));
    await new Promise<void>((resolve) => window.setTimeout(resolve, 0));
  });
}

describe('FilterSelect count status', () => {
  it('puts aria-busy on the status region while counts load, never on the listbox', async () => {
    await openMenu('loading');
    expect(listbox().hasAttribute('aria-busy')).toBe(false);
    expect(status()?.getAttribute('aria-busy')).toBe('true');
    expect(status()?.textContent).toBe('');
  });

  it('mounts the status empty and announces Counts unavailable after the first paint', async () => {
    await openMenu('error');
    expect(status()).not.toBeNull();
    expect(status()!.textContent).toBe('');
    expect(listbox().hasAttribute('aria-busy')).toBe(false);
    await nextFrame();
    expect(status()!.textContent).toBe('Counts unavailable');
    expect(status()!.hasAttribute('aria-busy')).toBe(false);
  });

  it('says nothing once counts are ready', async () => {
    await openMenu('ready');
    await nextFrame();
    expect(status()!.textContent).toBe('');
    expect(status()!.hasAttribute('aria-busy')).toBe(false);
    expect(listbox().querySelectorAll('.filter-menu__count')).toHaveLength(3);
  });
});
