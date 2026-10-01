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
 *
 * Two observers share the section roots: the BAND (landing line to 40% down
 * the scroller, bottom margin -60%) and the VIEW (landing line to the
 * scroller's bottom). The section a link went to holds the marker while it is
 * in view; otherwise the first section in the band has it.
 */

type ObserverCallback = (entries: Array<Pick<IntersectionObserverEntry, 'isIntersecting' | 'target'>>) => void;

const STATUS: readonly AdminStatusItem[] = [
  { label: 'Offer rules', value: 'active', tone: 'success' },
  { label: 'Data sources', value: '3 of 5 live', tone: 'neutral' },
  { label: 'Presenter mode', value: 'Off', tone: 'neutral' },
];

let root: Root;
let observers: FakeIntersectionObserver[];
const originalObserver = globalThis.IntersectionObserver;

class FakeIntersectionObserver {
  observed: Element[] = [];

  constructor(
    readonly callback: ObserverCallback,
    readonly options?: IntersectionObserverInit,
  ) {
    observers.push(this);
  }

  observe(target: Element) {
    this.observed.push(target);
  }

  unobserve(target: Element) {
    this.observed = this.observed.filter((node) => node !== target);
  }

  disconnect() {
    this.observed = [];
    observers = observers.filter((observer) => observer !== this);
  }
}

const band = () => observers.find((observer) => observer.options?.rootMargin?.endsWith(' -60% 0px')) as FakeIntersectionObserver;
const view = () => observers.find((observer) => observer.options?.rootMargin?.endsWith(' 0px 0px 0px')) as FakeIntersectionObserver;
const byId = (id: string) => document.getElementById(id) as Element;
const fire = (observer: FakeIntersectionObserver, entries: Array<[string | Element, boolean]>) =>
  act(() =>
    observer.callback(
      entries.map(([target, isIntersecting]) => ({ target: typeof target === 'string' ? byId(target) : target, isIntersecting })),
    ),
  );

/** The page's sections; `estateKey` re-keys Data estate's root, as its skeleton -> panel swap does. */
function page(estateKey = 'skeleton') {
  return (
    <main className="main">
      <nav className="route-nav" />
      <div className="main__inner">
        <AdminSectionNav sections={ADMIN_SECTIONS} status={STATUS} />
        <div className="admin-config-sections">
          {ADMIN_SECTIONS.map((section) => (
            <section key={section.id === 'data-estate' ? estateKey : section.id} id={section.id} tabIndex={-1}>
              {section.label}
            </section>
          ))}
        </div>
      </div>
    </main>
  );
}

function render(entry = '/admin-config?tab=x') {
  act(() => {
    root.render(<MemoryRouter initialEntries={[entry]}>{page()}</MemoryRouter>);
  });
}

const links = () => [...document.querySelectorAll<HTMLAnchorElement>('nav[aria-label="Administration sections"] a')];
const current = () => links().filter((link) => link.getAttribute('aria-current') === 'location').map((link) => link.textContent);

beforeEach(() => {
  observers = [];
  vi.stubGlobal('IntersectionObserver', FakeIntersectionObserver);
  document.body.innerHTML = '<div id="root"></div>';
  root = createRoot(document.getElementById('root') as HTMLElement);
});

afterEach(() => {
  act(() => root.unmount());
  vi.restoreAllMocks();
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

  it('marks the first section in the band with aria-current="location", below both sticky bars', () => {
    render();

    expect(observers).toHaveLength(2);
    for (const observer of [band(), view()]) {
      expect(observer.observed.map((target) => target.id)).toEqual(ADMIN_SECTIONS.map((section) => section.id));
      expect(observer.options?.root).toBe(document.querySelector('main.main'));
    }
    expect(band().options?.rootMargin).toMatch(/^-\d+px 0px -60% 0px$/);
    expect(view().options?.rootMargin).toMatch(/^-\d+px 0px 0px 0px$/);
    expect(current()).toEqual([]);

    fire(band(), [['data-operations', true], ['buyer-readiness', true]]);
    expect(current()).toEqual(['Data operations']);

    fire(band(), [['data-operations', false]]);
    expect(current()).toEqual(['Deployment readiness']);
  });

  it('starts both observers one pixel past where a hash scroll lands: scroll-margin plus scroll-padding', () => {
    // The section's scroll-margin is --nav-clear (route nav + focus ring) plus
    // the section nav; the bars' heights alone left the previous section's
    // last pixels in the band (critic-09 fix round).
    const computed = window.getComputedStyle;
    vi.spyOn(window, 'getComputedStyle').mockImplementation((element: Element) => {
      if (element.id === 'data-estate') return { scrollMarginBlockStart: '101.5px' } as CSSStyleDeclaration;
      if (element.classList.contains('main')) return { scrollPaddingBlockStart: '8px' } as CSSStyleDeclaration;
      return computed(element);
    });

    render();

    expect(band().options?.rootMargin).toBe('-111px 0px -60% 0px');
    expect(view().options?.rootMargin).toBe('-111px 0px 0px 0px');
  });

  it('rebuilds both observers on the moved line when the stylesheet lands after mount', () => {
    // At mount the admin chunk's stylesheet may not apply yet: only the shell's
    // --nav-clear (61px) is the scroll-margin. Once it applies (102px), the next
    // callback must not trust the stale band.
    let margin = '61px';
    const computed = window.getComputedStyle;
    vi.spyOn(window, 'getComputedStyle').mockImplementation((element: Element) =>
      element.id === 'data-estate' ? ({ scrollMarginBlockStart: margin } as CSSStyleDeclaration) : computed(element),
    );

    render();
    const stale = band();
    expect(stale.options?.rootMargin).toBe('-62px 0px -60% 0px');

    margin = '102px';
    fire(stale, [['live-probes', true]]);

    expect(observers).toHaveLength(2);
    expect(band()).not.toBe(stale);
    expect(band().options?.rootMargin).toBe('-103px 0px -60% 0px');
    expect(view().options?.rootMargin).toBe('-103px 0px 0px 0px');
    expect(band().observed.map((target) => target.id)).toEqual(ADMIN_SECTIONS.map((section) => section.id));
    expect(current(), 'the stale band\'s entries are dropped, not applied').toEqual([]);

    fire(band(), [['offer-rules', true], ['audit', true]]);
    expect(current()).toEqual(['Offer rules']);
  });

  it('gives the marker to the section a link went to while it is in view, not to a grid neighbour', () => {
    // Offer rules, Audit ledger and Data sources share one grid row: all three
    // land in the band together, and page order alone would always say Offer rules.
    render('/admin-config?tab=x#audit');

    fire(band(), [['offer-rules', true], ['audit', true], ['data-sources', true]]);
    fire(view(), [['offer-rules', true], ['audit', true], ['data-sources', true], ['data-operations', true]]);
    expect(current()).toEqual(['Audit ledger']);

    // Scrolled away: once the linked section has left the view, the band decides.
    fire(view(), [['audit', false]]);
    fire(band(), [['offer-rules', false], ['audit', false], ['data-sources', false], ['data-operations', true]]);
    expect(current()).toEqual(['Data operations']);

    // ...and it does not take the marker back by re-entering the view.
    fire(view(), [['audit', true]]);
    expect(current()).toEqual(['Data operations']);
  });

  it('marks a linked section that cannot scroll up to the landing line (the page end)', () => {
    render('/admin-config?tab=x#appearance');

    fire(band(), [['buyer-readiness', true]]);
    fire(view(), [['buyer-readiness', true], ['capability-readiness', true], ['activation', true], ['appearance', true]]);

    expect(current()).toEqual(['Appearance']);
  });

  it('re-observes a section whose root is replaced after mount and ignores the replaced root', async () => {
    render('/admin-config?tab=x#data-estate');
    const skeleton = byId('data-estate');
    expect(band().observed).toContain(skeleton);

    // Data estate's skeleton is swapped for the loaded panel: a new node.
    await act(async () => {
      root.render(<MemoryRouter initialEntries={['/admin-config?tab=x#data-estate']}>{page('panel')}</MemoryRouter>);
    });
    const panel = byId('data-estate');
    expect(panel).not.toBe(skeleton);
    for (const observer of [band(), view()]) {
      expect(observer.observed).toContain(panel);
      expect(observer.observed).not.toContain(skeleton);
    }

    fire(band(), [[skeleton, true]]);
    fire(view(), [[skeleton, true]]);
    expect(current(), 'an entry for the replaced root marks nothing').toEqual([]);

    fire(view(), [[panel, true]]);
    expect(current()).toEqual(['Data estate']);
  });

  it('still links every section, with no marker, where IntersectionObserver is missing', () => {
    vi.stubGlobal('IntersectionObserver', undefined);

    render();

    expect(links()).toHaveLength(ADMIN_SECTIONS.length);
    expect(current()).toEqual([]);
  });
});
