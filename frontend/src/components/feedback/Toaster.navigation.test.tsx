/**
 * @vitest-environment happy-dom
 *
 * A toast's "View audit event" link under the app's data router and the
 * unsaved-changes guard (audit states-05 x states-07): the toast goes only
 * once the explorer shows its row. Dismissing it in the click lost the toast
 * and its link whenever the guard held the navigation and the operator chose
 * Stay.
 */
import { act, useState } from 'react';
import { createRoot, type Root } from 'react-dom/client';
import { Route, RouterProvider, Routes, createMemoryRouter, useLocation } from 'react-router';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { useUnsavedGuard } from '../../hooks/useUnsavedGuard';
import { clearToasts, getToasts, toast } from '../../lib/toast';
import { Toaster } from './Toaster';
import { UnsavedChangesGuard } from './UnsavedChangesGuard';

(globalThis as unknown as { IS_REACT_ACT_ENVIRONMENT: boolean }).IS_REACT_ACT_ENVIRONMENT = true;

vi.mock('../AppContext', () => ({ useApp: () => ({ canAccessAdmin: true }) }));

const AUDIT_ID = 'evt-0001';

function NotePage() {
  const [note, setNote] = useState('');
  useUnsavedGuard(note.length > 0, 'Your note has not been saved.');
  return <input aria-label="Note" value={note} onChange={(event) => setNote(event.target.value)} />;
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
        <Route path="/admin-config" element={<h1>Audit explorer</h1>} />
      </Routes>
      <Where />
      <Toaster />
      <UnsavedChangesGuard />
    </>
  );
}

describe('Toaster audit link under the unsaved guard', () => {
  let root: Root;

  beforeEach(() => {
    document.body.innerHTML = '<div id="root"></div>';
    root = createRoot(document.getElementById('root') as HTMLElement);
    const router = createMemoryRouter([{ path: '*', element: <Shell /> }], { initialEntries: ['/notes'] });
    act(() => root.render(<RouterProvider router={router} />));
  });

  afterEach(() => {
    act(() => root.unmount());
    act(() => clearToasts());
    document.body.innerHTML = '';
  });

  const where = () => document.querySelector('[data-testid="where"]')?.textContent;
  const auditLink = () => document.querySelector<HTMLAnchorElement>('a.toast__link');

  function dialogButton(name: string): HTMLButtonElement {
    const found = [...document.querySelectorAll<HTMLButtonElement>('dialog button')].find((b) => b.textContent === name);
    if (!found) throw new Error(`no dialog button "${name}"`);
    return found;
  }

  async function click(element: HTMLElement): Promise<void> {
    act(() => element.click());
    await act(async () => {
      await new Promise((resolve) => setTimeout(resolve, 0));
    });
  }

  it('keeps the toast through Stay, and dismisses it once the explorer shows the row', async () => {
    act(() => {
      toast.success('Build saved', { auditEventId: AUDIT_ID });
    });
    const note = document.querySelector<HTMLInputElement>('input[aria-label="Note"]');
    if (!note) throw new Error('note input not rendered');
    act(() => {
      Object.getOwnPropertyDescriptor(HTMLInputElement.prototype, 'value')!.set!.call(note, 'Call after the rate drop');
      note.dispatchEvent(new Event('input', { bubbles: true }));
    });

    const link = auditLink();
    if (!link) throw new Error('no audit link');
    await click(link);
    await click(dialogButton('Stay'));
    expect(where()).toBe('/notes');
    expect(getToasts().map((item) => item.auditEventId)).toEqual([AUDIT_ID]);
    expect(auditLink()?.textContent).toBe('View audit event');

    await click(auditLink() as HTMLAnchorElement);
    await click(dialogButton('Leave'));
    expect(where()).toBe(`/admin-config?audit_event_id=${AUDIT_ID}`);
    expect(getToasts()).toEqual([]);
  });
});
