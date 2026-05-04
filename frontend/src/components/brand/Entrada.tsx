/**
 * Entrada brand marks.
 *
 * Both shapes derive from the official Entrada brand style guide
 * (Brand Style Guide.pptx + the high-res wordmark PNG shipped at
 * `frontend/public/brand/entrada-wordmark.png`):
 *
 *   - Short shorthand mark   → `<EntradaMark>` renders the 5-rect
 *     "E" composition inline so the bars can switch to currentColor
 *     on dark backgrounds (rail icons, monochrome surfaces).
 *   - Full wordmark          → `<EntradaWordmark>` renders the
 *     official PNG via <img> so it always reads exactly as the brand
 *     guide intended (Conquera Medium letterforms, brand cyan/navy
 *     palette baked in). Sized by `height` so the aspect ratio is
 *     preserved across the rail/topbar/footer surfaces.
 *
 * Mark geometry (22x22 viewBox, palette per brand guide):
 *   row 1 (y=0..4.5):    short CYAN  (#66C5FF) +  long  NAVY (#025080)
 *   row 2 (y=8.75..13.25): full-width NAVY
 *   row 3 (y=17.5..22):  long  NAVY              + short CYAN
 * The cyan accents sit diagonally opposite (top-left + bottom-right)
 * — this is what the prior 3-equal-solid-navy-bar placeholder was
 * missing. User feedback 2026-05-04: "the Entrada logos in the app
 * should match exactly. everywhere."
 */

const NAVY = '#025080';
const CYAN = '#66C5FF';

interface EntradaMarkProps {
  /** Rendered height of the mark in px. Aspect ratio is fixed (22:22 = 1:1). */
  size?: number;
  className?: string;
  /** `true` = tint the entire mark with currentColor (loses the cyan accent
   * — use only on monochrome surfaces where currentColor is the only
   * available stroke; the rail icon row is the canonical caller). */
  monochrome?: boolean;
}

export function EntradaMark({ size = 24, className, monochrome = false }: EntradaMarkProps) {
  const navy = monochrome ? 'currentColor' : NAVY;
  const cyan = monochrome ? 'currentColor' : CYAN;
  return (
    <svg
      viewBox="0 0 22 22"
      width={size}
      height={size}
      className={className}
      role="img"
      aria-label="Entrada"
      focusable="false"
      style={{ display: 'block' }}
    >
      <title>Entrada</title>
      <rect x="0"    y="0"    width="5.5"  height="4.5" fill={cyan} />
      <rect x="5.5"  y="0"    width="16.5" height="4.5" fill={navy} />
      <rect x="0"    y="8.75" width="22"   height="4.5" fill={navy} />
      <rect x="0"    y="17.5" width="16.5" height="4.5" fill={navy} />
      <rect x="16.5" y="17.5" width="5.5"  height="4.5" fill={cyan} />
    </svg>
  );
}

interface EntradaWordmarkProps {
  /** Rendered height of the wordmark in px. Width scales to preserve
   * the aspect ratio of the source PNG (~9.57:1 — 2048x214). */
  height?: number;
  /**
   * Back-compat alias for `height`. The pre-2026-05-04 wordmark
   * composed an EntradaMark + Geist text and was sized via fontSize;
   * since the wordmark is now a single PNG, we treat fontSize as the
   * target wordmark height so existing callers don't have to change
   * their prop name. New callers should use `height`.
   */
  fontSize?: number;
  className?: string;
}

export function EntradaWordmark({ height, fontSize, className }: EntradaWordmarkProps) {
  // Resolve final height: explicit `height` wins, then `fontSize`
  // (back-compat alias), then a sane default. Source PNG is 2048x214 —
  // display height drives width so the wordmark never stretches.
  const finalHeight = height ?? fontSize ?? 28;
  return (
    <img
      src="/brand/entrada-wordmark.png"
      alt="Entrada"
      height={finalHeight}
      className={className}
      style={{ display: 'block', height: finalHeight, width: 'auto' }}
    />
  );
}
