/**
 * @vitest-environment happy-dom
 *
 * LeadTableStatusChips' provenance rows:
 *  - bundle-09 item 1: intent on 'Open bound offer' (pointer enter, focus)
 *    warms the Offer route CHUNK for that exact href; never a query or
 *    prefetch (opening an offer writes audit rows), nothing under saveData;
 *  - critic-08: the sliced campaign and Growth Agent ids carry the full value
 *    on the design-system tooltip, never a native title.
 */
import { act, type ReactNode } from 'react';
import { createRoot, type Root } from 'react-dom/client';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';

const preloads = vi.hoisted(() => [] as string[]);
const saveData = vi.hoisted(() => ({ on: false }));
vi.mock('../../lib/routePreloaders', () => ({
  preloadRouteForPath: (path: string) => preloads.push(path),
}));
vi.mock('../../lib/prefetch', async (importOriginal) => ({
  ...(await importOriginal<typeof import('../../lib/prefetch')>()),
  saveDataRequested: () => saveData.on,
}));
vi.mock('react-router', () => ({
  Link: ({ children, to, ...props }: { children: ReactNode; to: string }) => <a href={to} {...props}>{children}</a>,
}));

import { LeadTableStatusChips } from './LeadTableStatusChips';

const BINDING = { campaign_id: 'campaign-0123456789abcdef', variant_name: 'Variant A' };
const BORROWER = 'B-0000000000BO1';
const HREF = `/offer-orchestrator/${BORROWER}?campaign_id=${BINDING.campaign_id}&variant_name=Variant+A`;

let root: Root;
let container: HTMLDivElement;

function render(props: { approverGate?: string | null } = {}) {
  act(() => {
    root.render(
      <LeadTableStatusChips
        growthAgentVerification={null}
        campaignBindingState="verified"
        requestedCampaignBinding={BINDING}
        campaignBinding={BINDING}
        expandedBorrowerId={BORROWER}
        approverGate={props.approverGate ?? null}
      />,
    );
  });
}

function tooltipText(element: Element | null): string | null {
  const id = element?.getAttribute('data-tooltip');
  return id ? document.getElementById(id)?.querySelector('[data-tooltip-text]')?.textContent ?? null : null;
}

describe('LeadTableStatusChips', () => {
  beforeEach(() => {
    preloads.length = 0;
    saveData.on = false;
    container = document.createElement('div');
    document.body.appendChild(container);
    root = createRoot(container);
  });

  afterEach(() => {
    act(() => root.unmount());
    container.remove();
  });

  it('warms the Offer chunk for the bound offer href on pointer intent and on focus, and only that', () => {
    const fetchSpy = vi.fn();
    vi.stubGlobal('fetch', fetchSpy);
    render();
    const link = container.querySelector('a.btn--primary')!;
    expect(link.getAttribute('href')).toBe(HREF);

    act(() => {
      link.dispatchEvent(new PointerEvent('pointerover', { bubbles: true }));
      link.dispatchEvent(new PointerEvent('pointerenter', { bubbles: false }));
    });
    act(() => (link as HTMLAnchorElement).focus());

    expect(preloads.length).toBeGreaterThan(0);
    expect(new Set(preloads)).toEqual(new Set([HREF]));
    expect(fetchSpy).not.toHaveBeenCalled();
    vi.unstubAllGlobals();
  });

  it('preloads nothing while the browser asks to save data', () => {
    saveData.on = true;
    render();
    const link = container.querySelector('a.btn--primary') as HTMLAnchorElement;
    act(() => link.focus());
    expect(preloads).toEqual([]);
  });

  it('puts the sliced campaign id on a tooltip, not a title', () => {
    render();
    const id = [...container.querySelectorAll('span.mono')].find((span) => span.textContent?.startsWith('campaign '));
    expect(id?.hasAttribute('title')).toBe(false);
    expect(tooltipText(id ?? null)).toBe(BINDING.campaign_id);
  });
});
