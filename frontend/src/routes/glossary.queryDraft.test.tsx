/**
 * @vitest-environment happy-dom
 *
 * The glossary search draft (W5d critic-07, fix round 2), on a real data
 * router, which applies setSearchParams in a transition. Controlled straight
 * from ?q=, the field was reset to the old value after every keystroke until
 * that transition committed: keys typed before it lost all but the last, and
 * a mid-word edit threw the caret to the end. The field is now a synchronous
 * local draft that writes ?q=; what the URL brings from OUTSIDE the field
 * (Back or Forward to an older entry, a link) replaces the draft. The same
 * typing is pinned on the production build in
 * print-glossary-sales-manager.fixture.spec.ts.
 *
 * Mutation checks (lane report): the input controlled from ?q= again fails
 * "keeps every key typed before the router commits" and "a mid-word edit
 * keeps the caret where it was typed"; treating every stamped location as
 * the field's own write (dropping the `stamp > draft.landed` bound) fails
 * "Back to an older search shows that search, not the newer draft"; never
 * treating one as the field's own write (always taking ?q= on a location
 * change) fails "an own write that lands after a newer keystroke keeps the
 * newer draft".
 */
import { act } from 'react';
import { createRoot, type Root } from 'react-dom/client';
import { createMemoryRouter, RouterProvider } from 'react-router';
import { afterEach, beforeEach, describe, expect, it } from 'vitest';
import GlossaryRoute from './glossary';

let container: HTMLDivElement;
let root: Root;
let router: ReturnType<typeof createMemoryRouter>;

const field = () => container.querySelector<HTMLInputElement>('input[type="search"]')!;
const shownQuery = () => new URLSearchParams(router.state.location.search).get('q');

/** The value as the browser sets it before React's input event. */
const setDomValue = (value: string) => Object.getOwnPropertyDescriptor(HTMLInputElement.prototype, 'value')!.set!.call(field(), value);

/** One edit, committed before the next: the DOM value changes, then React's input event. */
function type(value: string): void {
  act(() => {
    setDomValue(value);
    field().dispatchEvent(new Event('input', { bubbles: true }));
  });
}

/** One key typed at the caret; call it inside an act. */
function keyAtCaret(key: string): void {
  const input = field();
  const at = input.selectionStart ?? input.value.length;
  setDomValue(input.value.slice(0, at) + key + input.value.slice(at));
  input.setSelectionRange(at + 1, at + 1);
  input.dispatchEvent(new Event('input', { bubbles: true }));
}

/** Keys typed at the caret, all before the router's transition can commit (one act). */
async function typeFast(keys: string): Promise<void> {
  await act(async () => {
    for (const key of keys) keyAtCaret(key);
  });
}

async function navigate(to: string | number): Promise<void> {
  await act(async () => {
    await (typeof to === 'number' ? router.navigate(to) : router.navigate(to));
  });
}

beforeEach(async () => {
  container = document.createElement('div');
  document.body.appendChild(container);
  root = createRoot(container);
  router = createMemoryRouter([{ path: '/glossary', element: <GlossaryRoute /> }], { initialEntries: ['/glossary'] });
  await act(async () => root.render(<RouterProvider router={router} />));
});

afterEach(() => {
  act(() => root.unmount());
  container.remove();
});

describe('glossary search draft', () => {
  it('keeps every key typed before the router commits', async () => {
    field().focus();
    await typeFast('automated valuation');
    expect(field().value).toBe('automated valuation');
    expect(shownQuery()).toBe('automated valuation');
    expect(container.querySelector('.glossary-search__status')?.textContent).toBe('1 term matches');
  });

  it('a mid-word edit keeps the caret where it was typed', async () => {
    field().focus();
    await typeFast('equty');
    field().setSelectionRange(3, 3);
    await typeFast('i');
    expect(field().value).toBe('equity');
    expect(shownQuery()).toBe('equity');
    expect(field().selectionStart).toBe(4);
  });

  it('writes ?q= from what is typed, replacing the entry rather than pushing one', () => {
    type('equ');
    type('equity');
    expect(field().value).toBe('equity');
    expect(shownQuery()).toBe('equity');
    expect(router.state.historyAction).toBe('REPLACE');
  });

  it('Back to an older search shows that search, not the newer draft', async () => {
    type('equity');
    // A category chip pushes an entry that keeps ?q=.
    await navigate('/glossary?q=equity#mortgage');
    type('lien');
    expect(shownQuery()).toBe('lien');

    await navigate(-1);
    expect(shownQuery()).toBe('equity');
    expect(field().value).toBe('equity');

    await navigate(1);
    expect(shownQuery()).toBe('lien');
    expect(field().value).toBe('lien');
  });

  it('an own write that lands after a newer keystroke keeps the newer draft', async () => {
    // The app's navigations finish synchronously (no loaders: the
    // data-router-loaders ruling), so React renders every pending ?q= write
    // together and only the newest lands. A loader here makes each write land
    // late, so the first one commits while a second keystroke is already in
    // the field: an older own write must not pull the field back to it.
    act(() => root.unmount());
    const landings: Array<() => void> = [];
    let holdWrites = false;
    router = createMemoryRouter(
      [{
        path: '/glossary',
        element: <GlossaryRoute />,
        HydrateFallback: () => null,
        loader: () => (holdWrites ? new Promise<null>((resolve) => landings.push(() => resolve(null))) : null),
      }],
      { initialEntries: ['/glossary'] },
    );
    root = createRoot(container);
    await act(async () => root.render(<RouterProvider router={router} />));
    holdWrites = true;

    field().focus();
    await act(async () => {
      keyAtCaret('e');
      landings.shift()!();
      // Let the first write commit to the router before the next key.
      await new Promise((resolve) => setTimeout(resolve, 0));
      expect(router.state.location.search).toBe('?q=e');
      keyAtCaret('q');
    });
    expect(shownQuery(), 'the older write has landed').toBe('e');
    expect(field().value).toBe('eq');
    expect(field().selectionStart).toBe(2);

    await act(async () => {
      landings.shift()!();
      await new Promise((resolve) => setTimeout(resolve, 0));
    });
    expect(shownQuery()).toBe('eq');
    expect(field().value).toBe('eq');
  });

  it('a link that changes or drops ?q= from outside replaces the draft', async () => {
    type('equity');
    await navigate('/glossary?q=lien');
    expect(field().value).toBe('lien');
    await navigate('/glossary');
    expect(field().value).toBe('');
    expect(container.querySelector('.glossary-search__status')?.textContent).toBe('');
  });

  it('Clear search empties the field and the URL together', () => {
    type('zzqx');
    expect(container.querySelector('.glossary-search__status')?.textContent).toBe('No terms match "zzqx"');
    act(() => [...container.querySelectorAll('button')].find((button) => button.textContent === 'Clear search')!.click());
    expect(field().value).toBe('');
    expect(shownQuery()).toBeNull();
    expect(document.activeElement).toBe(field());
  });
});
