/**
 * @vitest-environment happy-dom
 */
import { act } from 'react';
import { createRoot, type Root } from 'react-dom/client';
import { MemoryRouter } from 'react-router';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { ADMIN_SECTIONS, AdminSectionNav, type AdminStatusItem } from './AdminSectionNav';

(globalThis as unknown as { IS_REACT_ACT_ENVIRONMENT: boolean }).IS_REACT_ACT_ENVIRONMENT = true;

/**
 * Administration's status row and section nav (audit critic-09): a static
 * row of status chips, router anchor links to every section in page order,
 * and scroll-spy marking the section being read with aria-current="location"
 * (none where IntersectionObserver is missing; the links still work).
 */

type ObserverCallback = (entries: Array<Pick<IntersectionObserverEntry, 'isIntersecting' | 'target'>>) => void;

const STATUS: readonly AdminStatusItem[] = [
  { label: 'Offer rules', value: 'active', tone: 'success' },
  { label: 'Data sources', value: '3 of 5 live', tone: 'neutral' },
  { label: 'Presenter mode', value: 'Off', tone: 'neutral' },
];

let root: Root;
let observed: Element[];
let observerCallback: ObserverCallback | null;
let observerOptions: IntersectionObserverInit | undefined;
const originalObserver = globalThis.IntersectionObserver;

class FakeIntersectionObserver {
  constructor(callback: ObserverCallback, options?: IntersectionObserverInit) {
    observerCallback = callback;
    observerOptions = options;
  }

  observe(target: Element) {
    observed.push(target);
  }

  disconnect() {
    observed = [];
  }
}

function render(entry = '/admin-config?tab=x') {
  act(() => {
    root.render(
      <MemoryRouter initialEntries={[entry]}>
        <main className="main">
          <nav className="route-nav" />
          <AdminSectionNav sections={ADMIN_SECTIONS} status={STATUS} />
          {ADMIN_SECTIONS.map((section) => (
            <section key={section.id} id={section.id} tabIndex={-1}>
              {section.label}
            </section>
          ))}
        </main>
      </MemoryRouter>,
    );
  });
}

const links = () => [...document.querySelectorAll<HTMLAnchorElement>('nav[aria-label="Administration sections"] a')];
const current = () => links().filter((link) => link.getAttribute('aria-current') === 'location');

beforeEach(() => {
  observed = [];
  observerCallback = null;
  observerOptions = undefined;
  vi.stubGlobal('IntersectionObserver', FakeIntersectionObserver);
  document.body.innerHTML = '<div id="root"></div>';
  root = createRoot(document.getElementById('root') as HTMLElement);
});

afterEach(() => {
  act(() => root.unmount());
  vi.unstubAllGlobals();
  globalThis.IntersectionObserver = originalObserver;
  document.body.innerHTML = '';
});

describe('AdminSectionNav', () => {
  it('links every section in page order, keeping the search and adding the hash', () => {
    render();

    expect(links().map((link) => link.textContent)).toEqual([
      'Data estate',
      'Live probes',
      'Offer rules',
      'Audit ledger',
      'Data sources',
      'Data operations',
      'Deployment readiness',
      'Agentic capabilities',
      'Activation',
      'Appearance',
    ]);
    expect(links().map((link) => link.getAttribute('href'))).toEqual(
      ADMIN_SECTIONS.map((section) => `/admin-config?tab=x#${section.id}`),
    );
    expect(links().every((link) => link.className === 'admin-section-nav__link')).toBe(true);
  });

  it('renders the static status row from what the page already holds', () => {
    render();

    const row = document.querySelector('ul[aria-label="Administration status"]');
    expect([...(row?.querySelectorAll('li .chip') ?? [])].map((chip) => chip.textContent)).toEqual([
      'Offer rules: active',
      'Data sources: 3 of 5 live',
      'Presenter mode: Off',
    ]);
    expect(row?.querySelector('a, button')).toBeNull();
  });

  it('marks the first section in view with aria-current="location", below both sticky bars', () => {
    render();

    expect(observed.map((target) => target.id)).toEqual(ADMIN_SECTIONS.map((section) => section.id));
    expect(observerOptions?.root).toBe(document.querySelector('main.main'));
    expect(observerOptions?.rootMargin).toMatch(/^-\d+px 0px -60% 0px$/);
    expect(current()).toEqual([]);

    const byId = (id: string) => document.getElementById(id) as Element;
    act(() => observerCallback?.([
      { isIntersecting: true, target: byId('data-operations') },
      { isIntersecting: true, target: byId('buyer-readiness') },
    ]));
    expect(current().map((link) => link.textContent)).toEqual(['Data operations']);

    act(() => observerCallback?.([{ isIntersecting: false, target: byId('data-operations') }]));
    expect(current().map((link) => link.textContent)).toEqual(['Deployment readiness']);
  });

  it('still links every section, with no marker, where IntersectionObserver is missing', () => {
    vi.stubGlobal('IntersectionObserver', undefined);

    render();

    expect(links()).toHaveLength(ADMIN_SECTIONS.length);
    expect(current()).toEqual([]);
  });
});
