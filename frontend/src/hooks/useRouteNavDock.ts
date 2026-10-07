import { useLayoutEffect, type RefObject } from 'react';
import { ROUTE_NAV_BLOCK_PROPERTY, ROUTE_NAV_DOCKED_ATTRIBUTE, routeNavDocks } from '../lib/routeNavDock';

/** `.main`'s content-box block size: the client height less its block padding (the Console sheet's gutter). */
function contentBlockSize(main: HTMLElement): number {
  const style = getComputedStyle(main);
  const padding = (Number.parseFloat(style.paddingTop) || 0) + (Number.parseFloat(style.paddingBottom) || 0);
  return main.clientHeight - padding;
}

/**
 * Dock the route nav while it is at most one sixth of `.main`'s content box
 * (lib/routeNavDock, report 12.4 #5). Measures the nav's border box and
 * `.main`'s content box (which the Console bottom sheet's padding shrinks),
 * toggles `data-docked` on the nav and writes `--route-nav-block` on `.main`,
 * each only when it changes. One ResizeObserver re-measures both; without
 * one, a window resize does. No React state, no scroll listener, no frame
 * loop. A nav outside a `.main` (a test that renders RouteNav alone) is left
 * untouched, in flow.
 */
export function useRouteNavDock(navRef: RefObject<HTMLElement | null>): void {
  useLayoutEffect(() => {
    const nav = navRef.current;
    const main = nav?.closest<HTMLElement>('.main');
    if (!nav || !main) return undefined;
    let navBlock = nav.offsetHeight;
    let scrollport = contentBlockSize(main);
    let docked = false;
    let written = '';
    const apply = () => {
      const next = routeNavDocks(navBlock, scrollport);
      if (next !== docked) {
        docked = next;
        nav.toggleAttribute(ROUTE_NAV_DOCKED_ATTRIBUTE, next);
      }
      const value = `${navBlock}px`;
      if (value !== written) {
        written = value;
        main.style.setProperty(ROUTE_NAV_BLOCK_PROPERTY, value);
      }
    };
    apply();
    const remeasure = () => {
      navBlock = nav.offsetHeight;
      scrollport = contentBlockSize(main);
      apply();
    };
    const observer = typeof ResizeObserver === 'function'
      ? new ResizeObserver((entries) => {
          for (const entry of entries) {
            if (entry.target === main) scrollport = entry.contentBoxSize?.[0]?.blockSize ?? entry.contentRect.height;
            else navBlock = entry.borderBoxSize?.[0]?.blockSize ?? nav.offsetHeight;
          }
          apply();
        })
      : null;
    if (observer) {
      observer.observe(nav, { box: 'border-box' });
      observer.observe(main, { box: 'content-box' });
    } else {
      window.addEventListener('resize', remeasure);
    }
    return () => {
      observer?.disconnect();
      window.removeEventListener('resize', remeasure);
      nav.removeAttribute(ROUTE_NAV_DOCKED_ATTRIBUTE);
      main.style.removeProperty(ROUTE_NAV_BLOCK_PROPERTY);
    };
  }, [navRef]);
}
