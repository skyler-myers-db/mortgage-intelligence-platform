import type { PropsWithChildren, ReactNode } from 'react';
import { EntradaWordmark } from '../brand/Entrada';

/**
 * PageShell — wraps a route's content in the prototype's `.main__inner` with
 * an optional hero block. The hero matches the prototype's `.proto-hero`
 * (eyebrow + big question + lede on the left, optional right-side action slot).
 *
 * Responsive note: the output is wrapped in `.main__content` (centered,
 * capped at 1920px) so page content stays readable on ultra-wide monitors.
 * Routes with a hero viz that should break past the cap (Home's
 * USChoropleth map) pass `wideMap` to add `.main__content--wide-map`,
 * which widens the cap to 2400px. See components.css "ULTRA-WIDE" block.
 */

interface PageShellProps {
  eyebrow?: string;
  title?: ReactNode;
  lede?: ReactNode;
  heroRight?: ReactNode;
  /**
   * When true, widens the content-cap from 1920px to 2400px so the hero
   * viz (today: the USChoropleth map on Home) can breathe on ultra-wide
   * monitors without forcing the rest of the page to stretch.
   */
  wideMap?: boolean;
}

export function PageShell({
  eyebrow,
  title,
  lede,
  heroRight,
  wideMap,
  children,
}: PropsWithChildren<PageShellProps>) {
  const hasHero = eyebrow || title || lede || heroRight;
  const contentClass = wideMap ? 'main__content main__content--wide-map' : 'main__content';
  return (
    <div className={contentClass}>
      <div className="main__inner">
        {hasHero && (
          <div className="proto-hero">
            <div>
              {eyebrow && <div className="eyebrow">{eyebrow}</div>}
              {title && <h1>{title}</h1>}
              {lede && <p className="lede">{lede}</p>}
            </div>
            {heroRight && (
              <div style={{ display: 'flex', gap: 8, alignItems: 'center', flexShrink: 0 }}>{heroRight}</div>
            )}
          </div>
        )}
        {children}
        <footer className="page-footer" aria-label="Product footer">
          <EntradaWordmark height={18} monochrome />
          <span className="page-footer__sep" aria-hidden="true">·</span>
          <span className="page-footer__name">Mortgage Intelligence Platform</span>
        </footer>
      </div>
    </div>
  );
}
