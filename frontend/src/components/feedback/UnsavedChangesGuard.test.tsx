/**
 * @vitest-environment happy-dom
 *
 * The navigation half of the unsaved-changes guard (audit states-05): under
 * a data router a dirty page's in-app navigation stops at "Leave without
 * saving?"; Stay keeps the page and its typed text, Leave navigates. Under a
 * declarative router (every route unit test's MemoryRouter) nothing throws
 * and navigation is not blocked. The rendered-layer proof in the production
 * build is tests/e2e/fixture/feedback-guard.fixture.spec.ts.
 */
import { act, useState } from 'react';
import { createRoot, type Root } from 'react-dom/client';
import {
  Link,
  MemoryRouter,
  Route,
  RouterProvider,
  Routes,
  createMemoryRouter,
  useLocation,
} from 'react-router';
import { afterEach, beforeEach, describe, expect, it } from 'vitest';
import { useUnsavedGuard } from '../../hooks/useUnsavedGuard';
import { UNSAVED_DIALOG_TITLE } from './UnsavedChangesDialog';
import { UnsavedChangesGuard } from './UnsavedChangesGuard';

function NotePage() {
  const [note, setNote] = useState('');
  useUnsavedGuard(note.length > 0, 'Your note has not been saved.');
  return (
    <>
      <input aria-label="Note" value={note} onChange={(event) => setNote(event.target.value)} />
      <Link to="/other">Other page</Link>
      <Link to="/notes?tab=history">Same page, other tab</Link>
    </>
  );
}

function Where() {
  const { pathname, search } = useLocation();
  return <output data-testid="where">{`${pathname}${search}`}</output>;
}

function Shell() {
  return (
    <>
      <Routes>
        <Route path="/notes" element={<NotePage />} />
        <Route path="/other" element={<h1>Other page</h1>} />
      </Routes>
      <Where />
      <UnsavedChangesGuard />
    </>
  );
}

describe('UnsavedChangesGuard', () => {
  let root: Root;

  beforeEach(() => {
    document.body.innerHTML = '<div id="root"></div>';
    root = createRoot(document.getElementById('root') as HTMLElement);
  });

  afterEach(() => {
    act(() => root.unmount());
    document.body.innerHTML = '';
  });

  const where = () => document.querySelector('[data-testid="where"]')?.textContent;
  const dialog = () => document.querySelector<HTMLDialogElement>('dialog.unsaved-dialog');
  const note = () => document.querySelector<HTMLInputElement>('input[aria-label="Note"]');

  function link(name: string): HTMLAnchorElement {
    const found = [...document.querySelectorAll<HTMLAnchorElement>('a')].find((a) => a.textContent === name);
    if (!found) throw new Error(`no link "${name}"`);
    return found;
  }

  function button(name: string): HTMLButtonElement {
    const found = [...document.querySelectorAll<HTMLButtonElement>('dialog button')].find((b) => b.textContent === name);
    if (!found) throw new Error(`no dialog button "${name}"`);
    return found;
  }

  async function settle(): Promise<void> {
    await act(async () => {
      await new Promise((resolve) => setTimeout(resolve, 0));
    });
  }

  function type(text: string): void {
    const input = note();
    if (!input) throw new Error('note input not rendered');
    act(() => {
      Object.getOwnPropertyDescriptor(HTMLInputElement.prototype, 'value')!.set!.call(input, text);
      input.dispatchEvent(new Event('input', { bubbles: true }));
    });
  }

  async function click(element: HTMLElement): Promise<void> {
    act(() => element.click());
    await settle();
  }

  function renderDataRouter() {
    const router = createMemoryRouter([{ path: '*', element: <Shell /> }], { initialEntries: ['/notes'] });
    act(() => root.render(<RouterProvider router={router} />));
    return router;
  }

  it('stops a dirty page at "Leave without saving?"; Stay keeps the page and the text', async () => {
    renderDataRouter();
    type('Call after the rate drop');
    await click(link('Other page'));

    const open = dialog();
    expect(open?.open).toBe(true);
    expect(open?.textContent).toContain(UNSAVED_DIALOG_TITLE);
    expect(open?.textContent).toContain('Your note has not been saved.');
    expect(document.activeElement?.textContent).toBe('Stay');
    expect(where()).toBe('/notes');

    await click(button('Stay'));
    expect(dialog()).toBeNull();
    expect(where()).toBe('/notes');
    expect(note()?.value).toBe('Call after the rate drop');
  });

  it('Leave completes the navigation the guard held', async () => {
    renderDataRouter();
    type('Call after the rate drop');
    await click(link('Other page'));
    await click(button('Leave'));
    expect(dialog()).toBeNull();
    expect(where()).toBe('/other');
    expect(document.querySelector('h1')?.textContent).toBe('Other page');
  });

  it('never asks for a clean page, or for a search change on the same page', async () => {
    renderDataRouter();
    type('Call after the rate drop');
    await click(link('Same page, other tab'));
    expect(dialog()).toBeNull();
    expect(where()).toBe('/notes?tab=history');

    type('');
    await click(link('Other page'));
    expect(dialog()).toBeNull();
    expect(where()).toBe('/other');
  });

  it('degrades to beforeunload alone under a declarative router, without throwing', async () => {
    act(() => {
      root.render(
        <MemoryRouter initialEntries={['/notes']}>
          <Shell />
        </MemoryRouter>,
      );
    });
    type('Call after the rate drop');
    const unload = new Event('beforeunload', { cancelable: true });
    window.dispatchEvent(unload);
    expect(unload.defaultPrevented).toBe(true);

    await click(link('Other page'));
    expect(dialog()).toBeNull();
    expect(where()).toBe('/other');
  });
});
