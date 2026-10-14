/**
 * @vitest-environment happy-dom
 *
 * The Lead Queue Status sort menu's exit (motion-01 item 2e,
 * deviation:menu-exit-motion). The menu is portalled to <body>, so its
 * closing contract is what keeps lib/keymap hasOpenOverlay from holding the
 * queue's A / R keys off while it fades: a closed menu stays mounted only as
 * `is-closing`, aria-hidden and inert, with no id, until its own
 * transitionend; and a reopen during the fade keeps that one element.
 * The test DOM runs no CSS, so src/test/runningExit stands in for the fade.
 */

import { act } from 'react';
import { createRoot, type Root } from 'react-dom/client';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { hasOpenOverlay } from '../../lib/keymap';
import { installFakeCssTransition, runExitOn } from '../../test/runningExit';
import { LeadTableStatusSortMenu } from './LeadTableStatusSortMenu';

(globalThis as unknown as { IS_REACT_ACT_ENVIRONMENT: boolean }).IS_REACT_ACT_ENVIRONMENT = true;

describe('LeadTableStatusSortMenu exit (motion-01 item 2e)', () => {
  let container: HTMLDivElement;
  let root: Root;
  let uninstall: () => void = () => undefined;

  beforeEach(() => {
    uninstall = installFakeCssTransition();
    container = document.createElement('div');
    document.body.appendChild(container);
    root = createRoot(container);
    act(() => {
      root.render(
        <table>
          <thead>
            <tr>
              <LeadTableStatusSortMenu sortKey="score" sortDir="desc" onSort={vi.fn()} />
            </tr>
          </thead>
        </table>,
      );
    });
  });

  afterEach(() => {
    act(() => root.unmount());
    container.remove();
    uninstall();
  });

  const trigger = () => container.querySelector<HTMLButtonElement>('[data-testid="lead-status-sort"]')!;
  // Portalled: the menu is a child of <body>, not of the table.
  const menus = () => Array.from(document.querySelectorAll<HTMLElement>('.lead-table__status-menu'));
  const menu = () => menus()[0] ?? null;
  const open = () => act(() => trigger().click());
  const escape = () =>
    act(() => {
      document.activeElement!.dispatchEvent(new KeyboardEvent('keydown', { key: 'Escape', bubbles: true, cancelable: true }));
    });

  it('keeps the closing menu mounted, is-closing, aria-hidden, inert and id-less until its own transitionend', () => {
    open();
    const opened = menu();
    expect(opened).not.toBeNull();
    expect(opened!.id).not.toBe('');
    expect(trigger().getAttribute('aria-controls')).toBe(opened!.id);
    expect(hasOpenOverlay(document)).toBe(true);

    const exit = runExitOn(opened!);
    escape();

    expect(menu(), 'the same element fades out').toBe(opened);
    expect(opened!.classList.contains('is-closing')).toBe(true);
    expect(opened!.getAttribute('aria-hidden')).toBe('true');
    expect(opened!.hasAttribute('inert')).toBe(true);
    expect(opened!.hasAttribute('id'), 'a closing menu owns no id').toBe(false);
    expect(trigger().getAttribute('aria-expanded')).toBe('false');
    expect(trigger().hasAttribute('aria-controls')).toBe(false);
    expect(document.activeElement, 'focus returns when the exit starts').toBe(trigger());
    expect(hasOpenOverlay(document), 'a closing menu never holds the queue keys off').toBe(false);

    exit.finish();
    expect(menus()).toHaveLength(0);
  });

  it('a reopen during the exit keeps the one element, open again with one id', () => {
    open();
    const first = menu();
    const exit = runExitOn(first!);
    escape();
    expect(first!.classList.contains('is-closing')).toBe(true);

    open();

    expect(menus(), 'one menu, never a duplicate').toHaveLength(1);
    expect(menu()).toBe(first);
    expect(first!.classList.contains('is-closing')).toBe(false);
    expect(first!.hasAttribute('aria-hidden')).toBe(false);
    expect(first!.hasAttribute('inert')).toBe(false);
    expect(first!.id).not.toBe('');
    expect(document.querySelectorAll(`[id="${first!.id}"]`)).toHaveLength(1);
    expect(trigger().getAttribute('aria-expanded')).toBe('true');
    expect(trigger().getAttribute('aria-controls')).toBe(first!.id);
    expect(hasOpenOverlay(document)).toBe(true);

    // The cancelled fade's late transitionend does not drop the reopened menu.
    exit.finish();
    expect(menu()).toBe(first);
    expect(first!.classList.contains('is-closing')).toBe(false);
  });
});
