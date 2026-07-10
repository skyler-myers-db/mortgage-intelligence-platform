// Plain-English labels for known Unity Catalog assets. Business surfaces show
// the friendly label (with the fully-qualified id kept in a `title` attribute);
// the /admin-config and /data-estate/assets detail routes still show the raw FQ
// string. Single source of truth so labels are not hand-edited in N places.

const FRIENDLY_BY_LEAF: Record<string, string> = {
  // gold
  borrower_360: 'Borrower 360',
  lead_population: 'Lead population',
  segment_population: 'Segment rollups',
  lead_scores: 'Opportunity scores',
  borrower_dossier: 'Borrower dossier',
  evidence_events: 'Evidence events',
  source_readiness: 'Source readiness',
  lockin_cohort: 'Lock-in cohort',
  household_rollup: 'Household rollup',
  // semantic views
  lead_generation_metric_view: 'Lead-generation metric view',
  segment_performance_metric_view: 'Segment performance view',
  borrower_opportunity_metric_view: 'Borrower opportunity view',
  // silver source tables (Signals evidence rows)
  market_rates_weekly: 'Weekly market rates',
  lien_current: 'Current liens',
  property_master: 'Property master',
};

/**
 * Resolve any asset id (bare table name, `schema.table`, or fully-qualified
 * `mip.schema.table[.column]`) to a plain-English label. Unknown assets fall
 * back to a humanized leaf (snake_case -> Sentence case).
 */
export function friendlyAssetLabel(asset: string): string {
  const cleaned = asset.trim().replace(/`/g, '');
  const segments = cleaned.split('.').filter(Boolean);
  // Try the leaf, then the second-to-last (handles a trailing `.column`).
  for (const candidate of [segments[segments.length - 1], segments[segments.length - 2]]) {
    const key = candidate?.toLowerCase();
    if (key && FRIENDLY_BY_LEAF[key]) return FRIENDLY_BY_LEAF[key];
  }
  const leaf = segments[segments.length - 1] ?? cleaned;
  const words = leaf.replace(/_/g, ' ').trim();
  return words ? words.charAt(0).toUpperCase() + words.slice(1) : cleaned;
}

const KNOWN_TABLE_RE = new RegExp(
  '\\b(?:mip\\.)?(?:gold|silver|semantics|ref|first_party)?\\.?(' +
    Object.keys(FRIENDLY_BY_LEAF).join('|') +
    ')(?:\\.[a-z0-9_]+)?',
  'gi',
);

/**
 * Replace raw known-table mentions inside free-text captions with their plain
 * label (dropping any trailing `.column`). No-op on text without known tables.
 */
export function humanizeAssetMentions(text: string): string {
  return text.replace(KNOWN_TABLE_RE, (_full, table: string) => friendlyAssetLabel(table));
}
