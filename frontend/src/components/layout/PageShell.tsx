import type { PropsWithChildren, ReactNode } from 'react';

/**
 * PageShell — wraps a route's content in the prototype's `.main__inner` with
 * an optional hero block. The hero matches the prototype's `.proto-hero`
 * (eyebrow + big question + lede on the left, optional right-side action slot).
 */

interface PageShellProps {
  eyebrow?: string;
  title?: ReactNode;
  lede?: ReactNode;
  heroRight?: ReactNode;
}

export function PageShell({ eyebrow, title, lede, heroRight, children }: PropsWithChildren<PageShellProps>) {
  const hasHero = eyebrow || title || lede || heroRight;
  return (
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
    </div>
  );
}
