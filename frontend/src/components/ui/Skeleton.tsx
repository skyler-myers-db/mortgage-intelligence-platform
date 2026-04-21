import type { CSSProperties } from 'react';

/**
 * Skeleton — token-driven placeholder rect. Shimmer uses the .skeleton CSS
 * class defined in components.css (linear-gradient slide keyed off
 * --dur-slow). prefers-reduced-motion switches to a static muted rect via
 * the CSS media query — no JS branching needed.
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
