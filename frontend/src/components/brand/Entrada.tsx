/**
 * Entrada brand marks.
 *
 * Shorthand mark is a static SVG (`public/brand/entrada-mark.svg`) — three
 * equal-length horizontal bars on a 22×22 viewBox (1:1 aspect). Prior
 * 32×22 viewBox produced a wide "E" that visually read as a logo block
 * next to the "NTRADA" wordmark; user feedback 2026-04-23 asked for a
 * narrower, square "E" that matches the Geist letter geometry.
 *
 * The full wordmark composes the mark with "NTRADA" in Geist Black. To keep
 * the mark visually the same height as the surrounding letters, the mark is
 * sized to match the font's cap-height (≈0.72× font-size for Geist), not the
 * full line-height. With a 1:1 mark aspect, the rendered mark width also
 * equals capHeight — producing an "E" that reads as one letter.
 */

interface EntradaMarkProps {
  /** Rendered cap-height of the mark in px. Aspect ratio is fixed (22:22 = 1:1). */
  size?: number;
  className?: string;
  /** `true` = tint the mark with currentColor instead of the brand two-tone. */
  monochrome?: boolean;
}

export function EntradaMark({ size = 24, className, monochrome = false }: EntradaMarkProps) {
  const width = size; // 1:1 aspect ratio
  if (monochrome) {
    return (
      <svg
        viewBox="0 0 22 22"
        width={width}
        height={size}
        className={className}
        aria-hidden="true"
        focusable="false"
        style={{ display: 'block' }}
      >
        <rect x="0"  y="0"    width="6"  height="4.5" fill="currentColor" opacity="0.55"/>
        <rect x="7"  y="0"    width="15" height="4.5" fill="currentColor"/>
        <rect x="0"  y="8.75" width="22" height="4.5" fill="currentColor"/>
        <rect x="0"  y="17.5" width="15" height="4.5" fill="currentColor"/>
        <rect x="16" y="17.5" width="6"  height="4.5" fill="currentColor" opacity="0.55"/>
      </svg>
    );
  }
  return (
    <img
      src="/brand/entrada-mark.svg"
      alt="Entrada"
      width={width}
      height={size}
      className={className}
      style={{ display: 'block' }}
    />
  );
}

interface EntradaWordmarkProps {
  /** Font-size for "NTRADA"; mark is sized to match cap-height. */
  fontSize?: number;
  /** `true` renders the whole wordmark in currentColor. */
  monochrome?: boolean;
  className?: string;
}

export function EntradaWordmark({ fontSize = 24, monochrome = false, className }: EntradaWordmarkProps) {
  // Geist cap-height ≈ 0.72 of font-size. Make mark that tall so the E
  // matches the rest of the letters visually.
  const capHeight = Math.round(fontSize * 0.72);
  const gap = Math.round(fontSize * 0.04);
  return (
    <span
      role="img"
      aria-label="Entrada"
      className={className}
      style={{
        display: 'inline-flex',
        alignItems: 'baseline',
        gap,
        lineHeight: 1,
        color: monochrome ? 'currentColor' : 'var(--entrada-navy)',
      }}
    >
      <EntradaMark size={capHeight} monochrome={monochrome} />
      <span
        style={{
          fontFamily: 'Geist, ui-sans-serif, system-ui, sans-serif',
          fontWeight: 900,
          fontSize,
          letterSpacing: '-0.02em',
          textTransform: 'uppercase',
          lineHeight: 1,
        }}
      >
        NTRADA
      </span>
    </span>
  );
}
