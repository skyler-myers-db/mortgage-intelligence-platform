import type { PropsWithChildren, CSSProperties } from 'react';
import { useRevealOnScroll } from '../../lib/useRevealOnScroll';

/**
 * Reveal — wraps a below-fold section in a `.reveal-on-scroll` div. The hook
 * toggles `.is-visible` via IntersectionObserver on first viewport entry.
 * Respects `prefers-reduced-motion` (hook reveals immediately under that).
 *
 * Use for home future-modules, agent-activity panel, borrower-360 trigger
 * timeline, offer-orchestrator alternatives/thresholds row — anywhere the
 * presenter scrolls and a subtle fade-in adds perceived polish.
 */

interface RevealProps {
  as?: 'div' | 'section';
  className?: string;
  style?: CSSProperties;
}

export function Reveal({
  as = 'div',
  className = '',
  style,
  children,
}: PropsWithChildren<RevealProps>) {
  const ref = useRevealOnScroll<HTMLDivElement>();
  const cls = `reveal-on-scroll${className ? ` ${className}` : ''}`;
  if (as === 'section') {
    return (
      <section ref={ref} className={cls} style={style}>
        {children}
      </section>
    );
  }
  return (
    <div ref={ref} className={cls} style={style}>
      {children}
    </div>
  );
}
