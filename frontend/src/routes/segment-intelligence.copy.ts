/**
 * Segments route copy, in lender words (2026-09-21 audit flow-10).
 *
 * The page used to explain itself in set algebra ("de-duplicated OR cohort",
 * "AND intersection"). The exact set definitions now live in the glossary
 * entry `segment-match-mode`, reached through GlossaryTerm on "Any selected"
 * and "All selected"; the screen says what a lender needs to read the counts.
 * routes/routeCopy.lint.test.tsx lints every string here against the shared
 * plumbing-word list (src/test/routeCopyBans.ts).
 */
import type { SegmentFilterMode } from '../lib/apiTypes';

/**
 * The hero lede: ONE line at 1440x900 with the Console open. `.proto-hero
 * .lede` caps at 640px, and Linux Chromium draws Geist wider than macOS, so
 * each stays under 80 characters (routeCopy.lint.test pins it).
 */
export function segmentsLede(hasSelectedSegments: boolean): string {
  return hasSelectedSegments
    ? 'Cards, map and table all show the cohort you picked, each borrower once.'
    : 'Cards overlap: each counts its own borrowers. Pick cards to build one cohort.';
}

/** The two halves of the mode help around its GlossaryTerm links. */
export const SEGMENT_MODE_HELP = {
  any: ' counts a borrower who is in at least one picked segment, once. ',
  all: ' keeps only borrowers who are in every picked segment.',
} as const;

export function rankedScopeCopy(hasSelectedSegments: boolean): string {
  return hasSelectedSegments
    ? 'Showing the highest-ranked borrowers in the cohort you picked, with any map drill-down applied.'
    : 'No card is picked, so the table ranks the full lead queue after the filters above.';
}

export function segmentCountScopeCopy(hasSelectedSegments: boolean, mode: SegmentFilterMode): string {
  if (!hasSelectedSegments) {
    return 'Each card counts its own borrowers after filters. A borrower can be in several segments, so do not add the cards together.';
  }
  return mode === 'all'
    ? 'Card counts now show borrowers who are in every picked segment, so each picked card shows the same number.'
    : 'Card counts now show borrowers who are in at least one picked segment, each counted once.';
}

/** The hint under the secondary filters, after the card-count sentence. */
export function segmentFilterHint(mode: SegmentFilterMode): string {
  const join = mode === 'all'
    ? 'must all match the same borrower'
    : 'combine into one cohort, each borrower counted once,';
  return `Picked cards ${join} for the table, map and Lead Queue link. Listed for Sale comes from live Cotality MLS listings. HELOC Intent comes from Cotality HELOC propensity; filed building permits are a separate source that is still pending.`;
}

/** The ranked-table header's suffix when two or more cards are picked. */
export function rankedHeaderModeSuffix(mode: SegmentFilterMode): string {
  return mode === 'all' ? ' · in all selected segments' : ' · in any selected segment, counted once';
}
