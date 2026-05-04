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
 * Mark geometry (32x22 viewBox = 16:11 ≈ 1.45:1 width:height, palette
 * per brand guide):
 *   row 1 (y=0..4.5):    short CYAN  (#66C5FF) +  long  NAVY (#025080)
 *   row 2 (y=8.75..13.25): full-width NAVY
 *   row 3 (y=17.5..22):  long  NAVY              + short CYAN
 * The cyan accents sit diagonally opposite (top-left + bottom-right)
 * — this is what the prior 3-equal-solid-navy-bar placeholder was
 * missing. User feedback 2026-05-04: "the Entrada logos in the app
 * should match exactly. everywhere."
 *
 * 2026-05-04 #3 (round 2) fix: the previous 26x22 viewBox was closer
 * but still didn't match the wordmark E. Pixel-measured the brand
 * wordmark PNG (frontend/public/brand/entrada-wordmark.png) directly:
 *   - E painted region:    218w × 151h  →  aspect 1.444 : 1
 *   - Bar thickness:       30px → 19.9% of height  →  4.37 in 22-unit space
 *   - Cyan tip:            84px / 218px = 38.5% of bar width
 *                          (top-bar cyan); 89/218 = 40.8% (bot-bar cyan)
 * So the wordmark E is significantly WIDER than tall (1.45:1) and the
 * cyan accent is ~40% of the bar — not the 25%/square the earlier
 * commits guessed at. Geometry below tracks the measured brand asset:
 *   - viewBox 32×22  (32 / 22 ≈ 1.45)
 *   - bar height 4.5 (≈ 20% of 22, matches measured 30/151)
 *   - cyan tip 13   (≈ 40% of 32, matches measured 38–41%)
 *   - long bar 19   (32 − 13)
 */

const NAVY = '#025080';
const CYAN = '#66C5FF';

// Source viewBox dimensions — kept separate from the rect coordinates
// so any future aspect-ratio tweak is a one-line change. The displayed
// width is computed as `size * (VB_W / VB_H)` so callers continue to
// pass a single `size` prop that represents target HEIGHT.
const VB_W = 32;
const VB_H = 22;

interface EntradaMarkProps {
  /** Rendered HEIGHT of the mark in px. Width derives from the brand
   * aspect ratio (32:22 ≈ 1.45:1 — pixel-measured from the wordmark). */
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
  // Width derives from height so the brand aspect ratio is preserved no
  // matter what the caller passes. Round to avoid sub-pixel blur on the
  // 1px navy bars.
  const renderedW = Math.round(size * (VB_W / VB_H));
  return (
    <svg
      viewBox={`0 0 ${VB_W} ${VB_H}`}
      width={renderedW}
      height={size}
      className={className}
      role="img"
      aria-label="Entrada"
      focusable="false"
      style={{ display: 'block' }}
    >
      <title>Entrada</title>
      {/* Bar geometry pixel-measured from the brand wordmark PNG.
          Cyan tip = 13 of 32 (≈ 40%). Long bar = 19 of 32. The cyan
          accents sit diagonally opposite (top-left + bot-right) just
          like the wordmark E. */}
      <rect x="0"  y="0"    width="13" height="4.5" fill={cyan} />
      <rect x="13" y="0"    width="19" height="4.5" fill={navy} />
      <rect x="0"  y="8.75" width="32" height="4.5" fill={navy} />
      <rect x="0"  y="17.5" width="19" height="4.5" fill={navy} />
      <rect x="19" y="17.5" width="13" height="4.5" fill={cyan} />
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
