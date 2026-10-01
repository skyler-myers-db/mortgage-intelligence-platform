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
 * The section being read: the first section (in page order) intersecting the
 * band between the two sticky bars and 40% down the scroller. Null where
 * IntersectionObserver is missing; the links still work, without a marker.
 */
function useSectionInView(sections: readonly AdminSection[], navRef: RefObject<HTMLElement | null>): string | null {
  const [current, setCurrent] = useState<string | null>(null);

  useEffect(() => {
    const nav = navRef.current;
    if (typeof IntersectionObserver === 'undefined' || !nav) return undefined;
    const scroller = nav.closest<HTMLElement>('.main');
    const routeNav = scroller?.querySelector<HTMLElement>('.route-nav');
    const topClearance = (routeNav?.offsetHeight ?? 0) + nav.offsetHeight;
    const intersecting = new Set<string>();
    const observer = new IntersectionObserver(
      (entries) => {
        for (const entry of entries) {
          if (entry.isIntersecting) intersecting.add(entry.target.id);
          else intersecting.delete(entry.target.id);
        }
        const first = sections.find((section) => intersecting.has(section.id));
        if (first) setCurrent(first.id);
      },
      { root: scroller ?? null, rootMargin: `-${topClearance}px 0px ${SPY_BOTTOM_MARGIN} 0px` },
    );
    for (const section of sections) {
      const target = document.getElementById(section.id);
      if (target) observer.observe(target);
    }
    return () => observer.disconnect();
  }, [navRef, sections]);

  return current;
}

interface AdminSectionNavProps {
  sections: readonly AdminSection[];
  status: readonly AdminStatusItem[];
}

export function AdminSectionNav({ sections, status }: AdminSectionNavProps) {
  const { search } = useLocation();
  const navRef = useRef<HTMLElement | null>(null);
  const current = useSectionInView(sections, navRef);

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
