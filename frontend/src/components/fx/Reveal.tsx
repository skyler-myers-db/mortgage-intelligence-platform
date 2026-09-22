import type { PropsWithChildren, CSSProperties } from 'react';
import { useRevealOnScroll } from '../../lib/useRevealOnScroll';
import { useFirstAppearance } from '../../lib/useFirstAppearance';

/**
 * Reveal — wraps a below-fold section in a `.reveal-on-scroll` div. The hook
 * toggles `.is-visible` via IntersectionObserver on first viewport entry.
 * Respects `prefers-reduced-motion` (hook reveals immediately under that).
 *
 * One-time motion is one-time (audit motion-06): the fade plays the FIRST
 * time `revealKey` mounts this session (the `useFirstAppearance` pattern the
 * KPI entrance uses). Every later mount of the same key, including route
 * re-entry and the next borrower's dossier, renders `.is-visible` from the
 * first paint, so nothing fades again and nothing sits at opacity 0. Keys
 * are route-level (`borrower-360:trigger-timeline`), never per record.
 *
 * Use for the home brand signature, borrower-360 trigger timeline,
 * offer-orchestrator alternatives/thresholds row — anywhere the presenter
 * scrolls and a subtle fade-in adds perceived polish.
 */

interface RevealProps {
  /** Session-scoped reveal key; the fade plays once per key per page load. */
  revealKey: string;
  as?: 'div' | 'section';
  className?: string;
  style?: CSSProperties;
}

export function Reveal({
  revealKey,
  as = 'div',
  className = '',
  style,
  children,
}: PropsWithChildren<RevealProps>) {
  const firstAppearance = useFirstAppearance(`reveal:${revealKey}`);
  const initiallyVisible = !firstAppearance;
  const ref = useRevealOnScroll<HTMLDivElement>({ initiallyVisible });
  const cls = `reveal-on-scroll${initiallyVisible ? ' is-visible' : ''}${className ? ` ${className}` : ''}`;
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
