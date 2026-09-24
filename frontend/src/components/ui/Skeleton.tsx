import type { CSSProperties } from 'react';

/**
 * Skeleton — token-driven placeholder rect. Shimmer uses the .skeleton CSS
 * class (design-system/components/11-skeleton-menu-and-genie-answer.css): a
 * highlight band swept across by a transform on `::after`, so the compositor
 * animates it without repainting (audit 2026-09-21 states-10).
 * prefers-reduced-motion, and a browser that is offline (a paused query is
 * waiting, not loading), switch to a static muted rect in CSS — no JS
 * branching needed.
 *
 * Prefer a placeholder shaped like the content it stands in for (the Lead
 * Queue and Analytics skeletons reuse the loaded view's own table / grid
 * classes) over a stack of generic bars.
 *
 * Sized by width/height props; rounded corners default to --r-sm but can be
 * overridden for circular or square elements.
 */

interface SkeletonProps {
  width?: number | string;
  height?: number | string;
  rounded?: 'sm' | 'md' | 'lg' | 'pill' | 'none';
  style?: CSSProperties;
  className?: string;
}

const ROUNDED_TOKEN: Record<NonNullable<SkeletonProps['rounded']>, string> = {
  sm: 'var(--r-sm)',
  md: 'var(--r-md)',
  lg: 'var(--r-lg)',
  pill: 'var(--r-pill)',
  none: '0',
};

export function Skeleton({
  width = '100%',
  height = 14,
  rounded = 'sm',
  style,
  className,
}: SkeletonProps) {
  return (
    <div
      className={`skeleton${className ? ` ${className}` : ''}`}
      aria-hidden="true"
      style={{
        width,
        height,
        borderRadius: ROUNDED_TOKEN[rounded],
        ...style,
      }}
    />
  );
}
