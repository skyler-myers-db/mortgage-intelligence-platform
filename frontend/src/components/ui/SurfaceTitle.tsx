import type { ComponentPropsWithRef } from 'react';

/**
 * SurfaceTitle — the title of a `.surface`, as a real heading (audit
 * 2026-09-21 `a11y-03`, WCAG 1.3.1 / 2.4.6).
 *
 * The prototype draws surface titles as a `div` carrying `.h-4`
 * (design_files/Module 0 Prototype.html:1765, :2000, :2196, :2206), so every
 * product page exposed exactly one heading, its `<h1>`, and heading navigation
 * could not reach a single panel. This renders the same `.h-4` class on an
 * `<h2>` or `<h3>` instead. `.h-4` already sets font-size, font-weight and
 * `margin: 0` (01-app-shell.css), which are the only user-agent defaults a
 * heading adds over a `div`, so the swap is pixel-neutral.
 *
 * Levels: `2` (default) for a surface that sits directly under the page
 * `<h1>`; `3` for a sub-panel inside one (an expanded Lead Queue row, a
 * Console group, a Growth Agent card). When the surface is a labelled region,
 * pass `id` and point its `aria-labelledby` here rather than repeating the
 * text in an `aria-label`. `ref` and every other heading attribute pass
 * through (DecisionReceipt moves focus onto its title).
 *
 * Not for a title inside a `<button>` (a disclosure header): a heading is not
 * phrasing content and a button's children are presentational, so those keep
 * a `span` with `.h-4` and the button text names the control. The source gate
 * in SurfaceTitle.test.tsx fails on any new `div` title with `.h-4`.
 */
type SurfaceTitleProps = Omit<ComponentPropsWithRef<'h2'>, 'className'> & {
  /** Heading level: 2 under the page h1 (default), 3 inside a sub-panel. */
  level?: 2 | 3;
  /** Extra classes after `h-4` (e.g. `mt-1`). */
  className?: string;
};

export function SurfaceTitle({ level = 2, className, ...rest }: SurfaceTitleProps) {
  const Heading = level === 3 ? 'h3' : 'h2';
  return <Heading {...rest} className={className ? `h-4 ${className}` : 'h-4'} />;
}
