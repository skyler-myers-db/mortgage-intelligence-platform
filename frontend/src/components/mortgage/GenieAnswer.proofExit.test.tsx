/**
 * @vitest-environment happy-dom
 *
 * The Genie proof drawer's exit (audit 2026-09-21 motion-01 remainder): it
 * used to unmount the moment it closed, cutting its own .drawer slide-out.
 * It now stays rendered, closed and inert, until the drawer's transition
 * ends; the focus trap still releases and focus still returns at once,
 * because both follow the live open state.
 */
import { act } from 'react';
import { createRoot, type Root } from 'react-dom/client';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import type { GenieAnswer as GenieAnswerShape } from '../../types';

vi.mock('../AppContext', () => ({ useApp: () => ({ setDrawer: vi.fn() }) }));
vi.mock('../HealthProvider', () => ({ useWorkspaceHost: () => null }));

import { GenieAnswer } from './GenieAnswer';

(globalThis as unknown as { IS_REACT_ACT_ENVIRONMENT: boolean }).IS_REACT_ACT_ENVIRONMENT = true;

function payload(): GenieAnswerShape {
  return {
    answer: 'There are 124,946 borrowers.',
    source: 'trusted_sql',
    trusted_assets: ['mip.gold.borrower_360'],
    question_hash: 'h1',
    metric_value: '124,946',
    table_rows: null,
    follow_up_questions: [],
    proof: {
      trusted: true,
      source_assets: ['mip.gold.borrower_360'],
      row_count: 1,
      sql_query: 'SELECT COUNT(*) FROM mip.gold.borrower_360',
    },
  } as unknown as GenieAnswerShape;
}

describe('Genie proof drawer exit (motion-01 remainder)', () => {
  let container: HTMLDivElement;
  let root: Root;
  const realGetComputedStyle = window.getComputedStyle.bind(window);

  beforeEach(() => {
    // The .drawer exit transition, as the stylesheet declares it; happy-dom
    // has no cascade for the component CSS.
    vi.spyOn(window, 'getComputedStyle').mockImplementation((element: Element, pseudo?: string | null) => {
      const style = realGetComputedStyle(element, pseudo);
      if (!(element as HTMLElement).classList?.contains('genie-proof-drawer')) return style;
      return { ...style, transitionDuration: '0.2s', transitionDelay: '0s' } as CSSStyleDeclaration;
    });
    container = document.createElement('div');
    document.body.appendChild(container);
    root = createRoot(container);
  });

  afterEach(() => {
    act(() => root.unmount());
    container.remove();
    vi.restoreAllMocks();
  });

  const drawer = () => document.body.querySelector<HTMLElement>('aside.genie-proof-drawer');

  it('keeps the closing drawer rendered, closed and inert until its transition ends', async () => {
    act(() => root.render(<GenieAnswer payload={payload()} question="How many borrowers?" />));
    const toggle = Array.from(container.querySelectorAll<HTMLButtonElement>('button')).find((b) =>
      b.textContent?.includes('Show proof'),
    )!;
    toggle.focus();
    await act(async () => {
      toggle.click();
      await Promise.resolve();
    });
    expect(drawer()?.classList.contains('is-open')).toBe(true);
    expect(document.activeElement?.getAttribute('aria-label')).toBe('Close Genie proof');

    await act(async () => {
      window.dispatchEvent(new KeyboardEvent('keydown', { key: 'Escape' }));
      await Promise.resolve();
    });

    // Still there, sliding out: no longer open, out of the tab order.
    const closing = drawer();
    expect(closing).not.toBeNull();
    expect(closing!.classList.contains('is-open')).toBe(false);
    expect(closing!.hasAttribute('inert')).toBe(true);
    expect(document.body.querySelector('.drawer-scrim.is-open')).toBeNull();
    // Focus returned at once, when the exit started.
    expect(document.activeElement).toBe(toggle);

    act(() => {
      closing!.dispatchEvent(new Event('transitionend', { bubbles: true }));
    });
    expect(drawer()).toBeNull();
  });
});
