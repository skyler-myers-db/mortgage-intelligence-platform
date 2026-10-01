import { useEffect, useRef, useState, type RefObject } from 'react';
import { Link, useLocation } from 'react-router';
import { Chip } from '../Primitives';
import './AdminSectionNav.css';

/**
 * Administration's status summary row and section nav (2026-09-21 audit
 * critic-09). The page stacks ten panels in about 3,500px; operators hunted
 * by scrolling. Above the panels: a static row of status chips computed from
 * data the page already holds (no request of its own), then a one-line nav
 * of anchor links to every section, sticky under the route nav, with
 * scroll-spy marking the section being read (`aria-current="location"`).
 *
 * The links are router links (`{ search, hash }`), so the route's hash effect
 * scrolls the section to the top and focuses it, and Back returns to the
 * previous section. The prototype has no administration page; the nav is
 * built from its token vocabulary on the route nav's pattern
 * (design_files/index.html:926-935; deviation:admin-section-nav).
 */

export interface AdminSection {
  /** The section root's DOM id, which carries tabIndex={-1}. */
  id: string;
  label: string;
}

export interface AdminStatusItem {
  label: string;
  value: string;
  tone: 'success' | 'warning' | 'neutral';
}

/** Administration's sections, in page order. */
export const ADMIN_SECTIONS: readonly AdminSection[] = [
  { id: 'data-estate', label: 'Data estate' },
  { id: 'live-probes', label: 'Live probes' },
  { id: 'offer-rules', label: 'Offer rules' },
  { id: 'audit', label: 'Audit ledger' },
  { id: 'data-sources', label: 'Data sources' },
  { id: 'data-operations', label: 'Data operations' },
  { id: 'buyer-readiness', label: 'Deployment readiness' },
  { id: 'capability-readiness', label: 'Agentic capabilities' },
  { id: 'activation', label: 'Activation' },
  { id: 'appearance', label: 'Appearance' },
];

export const ADMIN_SECTION_IDS: ReadonlySet<string> = new Set(ADMIN_SECTIONS.map((section) => section.id));

/** Only the part of a section below this share of the scroller counts as read. */
const SPY_BOTTOM_MARGIN = '-60%';

/**
 * Past the landing line, so the previous section's last pixel above it (an
 * edge-adjacent intersection still counts) never marks that section.
 */
const SPY_LANDING_SLACK_PX = 1;

/**
 * How far from where its link landed it the followed section may sit and
 * still hold the marker: sub-pixel scroll rounding, never a user's scroll.
 */
const SPY_LANDED_TOLERANCE_PX = 4;

/**
 * How far below the scroller's top edge a hash or focus scroll lands a
 * section: the scroller's scroll-padding plus the section's scroll-margin,
 * which AdminSectionNav.css sets to --nav-clear (the route nav AND the focus
 * ring allowance) plus this nav's block size. Reading the resolved style
 * keeps the spy's band on the line the scroll uses; the two bars' heights
 * alone fell short by the focus ring, so the previous section's last pixels
 * stayed in the band and won (critic-09 fix round). Where computed style
 * resolves neither (a test DOM), the two bars' heights stand in.
 */
function landingInset(scroller: HTMLElement | null, sectionRoot: Element | undefined, fallback: () => number): number {
  const margin = sectionRoot ? parseFloat(getComputedStyle(sectionRoot).scrollMarginBlockStart) : Number.NaN;
  if (!Number.isFinite(margin)) return fallback();
  const padding = scroller ? parseFloat(getComputedStyle(scroller).scrollPaddingBlockStart) : Number.NaN;
  return margin + (Number.isFinite(padding) ? padding : 0);
}

/**
 * The section being read. The section a link (the location hash) went to
 * holds the marker while it sits where the link landed it: Offer rules, Audit
 * ledger and Data sources share one grid row, and the last sections cannot
 * scroll up to the landing line, so position alone would name a neighbour.
 * Any scroll of the user's own moves it off that landing and releases the
 * hold for good (fix round 2: holding while it was merely in view kept the
 * marker on it after the user scrolled away). From then on, and for a hash
 * the scroller was restored away from, the marker goes to the first section
 * (in page order) intersecting the band from the landing line to 40% down
 * the scroller. Null where IntersectionObserver is missing; the links still
 * work, without a marker.
 *
 * Each section's CURRENT root is observed: a panel that replaces its root
 * after mount (Data estate's skeleton for the loaded panel) is re-observed
 * through a MutationObserver on the page body, and an entry for a replaced
 * root is ignored. The landing line is re-read on every callback and both
 * observers are rebuilt when it has moved: the admin chunk's stylesheet
 * (this nav's scroll-margin) can apply after mount (seen on WebKit).
 */
function useSectionInView(
  sections: readonly AdminSection[],
  navRef: RefObject<HTMLElement | null>,
  hash: string,
): string | null {
  const [current, setCurrent] = useState<string | null>(null);

  useEffect(() => {
    const nav = navRef.current;
    if (typeof IntersectionObserver === 'undefined' || !nav) return undefined;
    const scroller = nav.closest<HTMLElement>('.main');
    const routeNav = scroller?.querySelector<HTMLElement>('.route-nav');
    const bars = () => (routeNav?.offsetHeight ?? 0) + nav.offsetHeight;
    const observedRoots = new Map<string, Element>();
    const inBand = new Set<string>();
    const inView = new Set<string>();
    let observers: IntersectionObserver[] = [];
    let builtTop = 0;
    let held = sections.find((section) => `#${section.id}` === hash)?.id ?? null;
    let heldSeen = false;

    function topMargin(): number {
      const root = sections.map((section) => document.getElementById(section.id)).find((node) => node !== null);
      return Math.ceil(landingInset(scroller, root ?? undefined, bars)) + SPY_LANDING_SLACK_PX;
    }

    /**
     * Whether the held section still sits where following its link put it:
     * its top on the landing line or, where the page ends before it can reach
     * the line, the scroller at its end with that top below the line. Read
     * from the live geometry, so scroll anchoring (the section kept in place
     * while a panel above it loads) holds and a wheel, key or scrollbar
     * scroll does not.
     */
    function holdLanded(id: string): boolean {
      const root = observedRoots.get(id);
      if (!scroller || !root) return true;
      const line = landingInset(scroller, root, bars);
      const top = root.getBoundingClientRect().top - scroller.getBoundingClientRect().top - scroller.clientTop;
      if (Math.abs(top - line) <= SPY_LANDED_TOLERANCE_PX) return true;
      const atEnd = scroller.scrollTop + scroller.clientHeight >= scroller.scrollHeight - SPY_LANDED_TOLERANCE_PX;
      return atEnd && top > line;
    }

    function update(): void {
      if (held && inView.has(held) && holdLanded(held)) {
        heldSeen = true;
        setCurrent(held);
        return;
      }
      if (held && (heldSeen || inView.has(held))) held = null;
      const first = sections.find((section) => inBand.has(section.id));
      if (first) setCurrent(first.id);
    }

    // A scroll that crosses no band edge delivers no observer entry; the
    // hold is re-checked on the scroll itself.
    function onScroll(): void {
      if (held) update();
    }

    function record(ids: Set<string>): IntersectionObserverCallback {
      return (entries) => {
        if (topMargin() !== builtTop) {
          connect();
          return;
        }
        for (const entry of entries) {
          const { id } = entry.target;
          if (observedRoots.get(id) !== entry.target) continue;
          if (entry.isIntersecting) ids.add(id);
          else ids.delete(id);
        }
        update();
      };
    }

    function observeCurrentRoots(): void {
      for (const section of sections) {
        const root = document.getElementById(section.id);
        const observedRoot = observedRoots.get(section.id);
        if (root === observedRoot) continue;
        if (observedRoot) {
          for (const observer of observers) observer.unobserve(observedRoot);
          observedRoots.delete(section.id);
          inBand.delete(section.id);
          inView.delete(section.id);
        }
        if (root) {
          observedRoots.set(section.id, root);
          for (const observer of observers) observer.observe(root);
        }
      }
    }

    function connect(): void {
      for (const observer of observers) observer.disconnect();
      observedRoots.clear();
      inBand.clear();
      inView.clear();
      builtTop = topMargin();
      const band = (bottomMargin: string): IntersectionObserverInit => ({
        root: scroller ?? null,
        rootMargin: `-${builtTop}px 0px ${bottomMargin} 0px`,
      });
      observers = [
        new IntersectionObserver(record(inBand), band(SPY_BOTTOM_MARGIN)),
        new IntersectionObserver(record(inView), band('0px')),
      ];
      observeCurrentRoots();
    }

    connect();
    scroller?.addEventListener('scroll', onScroll, { passive: true });
    const body = nav.parentElement;
    let replacements: MutationObserver | null = null;
    if (typeof MutationObserver !== 'undefined' && body) {
      replacements = new MutationObserver(observeCurrentRoots);
      replacements.observe(body, { childList: true, subtree: true });
    }
    return () => {
      scroller?.removeEventListener('scroll', onScroll);
      replacements?.disconnect();
      for (const observer of observers) observer.disconnect();
    };
  }, [navRef, sections, hash]);

  return current;
}

interface AdminSectionNavProps {
  sections: readonly AdminSection[];
  status: readonly AdminStatusItem[];
}

export function AdminSectionNav({ sections, status }: AdminSectionNavProps) {
  const { search, hash } = useLocation();
  const navRef = useRef<HTMLElement | null>(null);
  const current = useSectionInView(sections, navRef, hash);

  return (
    <>
      <ul className="chip-row admin-status" aria-label="Administration status">
        {status.map((item) => (
          <li key={item.label} className="admin-status__item">
            <Chip variant={item.tone}>
              {item.label}: {item.value}
            </Chip>
          </li>
        ))}
      </ul>
      <nav ref={navRef} aria-label="Administration sections" className="admin-section-nav">
        {sections.map((section) => (
          <Link
            key={section.id}
            to={{ search, hash: `#${section.id}` }}
            className="admin-section-nav__link"
            aria-current={current === section.id ? 'location' : undefined}
          >
            {section.label}
          </Link>
        ))}
      </nav>
    </>
  );
}
