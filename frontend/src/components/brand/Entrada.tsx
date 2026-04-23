/**
 * Entrada brand marks.
 *
 * The shorthand mark is a static SVG in `public/brand/entrada-mark.svg`
 * (a 3-row bar pattern that also doubles as the "E" in the full wordmark).
 *
 * The full wordmark composes the mark with "NTRADA" typeset in Geist Black.
 * Rendering the wordmark with live text rather than a single flat SVG means
 * the color adapts to light / dark theme via `--entrada-navy` / CSS currentColor.
 */

interface EntradaMarkProps {
  size?: number;
  className?: string;
}

export function EntradaMark({ size = 24, className }: EntradaMarkProps) {
  return (
    <img
      src="/brand/entrada-mark.svg"
      alt="Entrada"
      width={size}
      height={size}
      className={className}
      style={{ display: 'block' }}
    />
  );
}

interface EntradaWordmarkProps {
  /** Full wordmark height in px. Mark + text scale from this single value. */
  height?: number;
  /** Optional monochrome override — `true` renders the whole wordmark in currentColor. */
  monochrome?: boolean;
  className?: string;
}

export function EntradaWordmark({ height = 28, monochrome = false, className }: EntradaWordmarkProps) {
  // The mark is square; the text is ~0.85× the height. Letter-spacing pulled
  // tight to match the wordmark's density in the reference art.
  const markSize = height;
  const fontSize = Math.round(height * 0.82);
  const gap = Math.round(height * 0.12);
  return (
    <span
      role="img"
      aria-label="Entrada"
      className={className}
      style={{
        display: 'inline-flex',
        alignItems: 'center',
        gap,
        lineHeight: 1,
        height,
        color: monochrome ? 'currentColor' : 'var(--entrada-navy)',
      }}
    >
      {monochrome ? (
        // Monochrome variant: render the bar pattern inline so we can tint
        // it with currentColor instead of the static two-tone SVG.
        <svg
          viewBox="0 0 32 32"
          width={markSize}
          height={markSize}
          fill="currentColor"
          aria-hidden="true"
          focusable="false"
        >
          <rect x="0"  y="5"     width="9"  height="4.5" opacity="0.55"/>
          <rect x="11" y="5"     width="21" height="4.5"/>
          <rect x="0"  y="13.75" width="28" height="4.5"/>
          <rect x="0"  y="22.5"  width="21" height="4.5"/>
          <rect x="23" y="22.5"  width="9"  height="4.5" opacity="0.55"/>
        </svg>
      ) : (
        <img
          src="/brand/entrada-mark.svg"
          alt=""
          aria-hidden="true"
          width={markSize}
          height={markSize}
          style={{ display: 'block' }}
        />
      )}
      <span
        style={{
          fontFamily: 'Geist, ui-sans-serif, system-ui, sans-serif',
          fontWeight: 900,
          fontSize,
          letterSpacing: '-0.02em',
          textTransform: 'uppercase',
        }}
      >
        NTRADA
      </span>
    </span>
  );
}
