// Pure helpers, constants, and types extracted from analytics.tsx.
// No React: this module contains no hooks/components, so it needs no
// 'use no memo' React Compiler pragma (unlike analytics.sections.tsx).
import type { LeadFunnelStage } from '../lib/api';
import type {
  EquitySpreadBin,
  EquitySpreadOverview,
  EquitySpreadViewport,
  EvidenceDailyRow,
  FunnelStage,
  SegmentCode,
  TopBorrowerAnalyticsRow,
} from '../types';
import { HIGH_OPPORTUNITY_KPI_LABEL } from '../lib/opportunityScore';
import { fixedAttr } from '../lib/fixedPrecision';
import { formatCompact, formatCount, formatPercent } from '../lib/formatters';
import { formatDate } from '../lib/time';

export type AnalyticsTab = 'executive' | 'geography' | 'economics' | 'segments' | 'signals' | 'approval-funnel' | 'sales-ops';

export const TABS: Array<{ id: AnalyticsTab; label: string; icon: 'flow' | 'map' | 'money' | 'layers' | 'audit' | 'target' }> = [
  { id: 'executive', label: 'Executive', icon: 'flow' },
  { id: 'geography', label: 'Geography', icon: 'map' },
  { id: 'economics', label: 'Economics', icon: 'money' },
  { id: 'segments', label: 'Segments', icon: 'layers' },
  { id: 'signals', label: 'Signals', icon: 'audit' },
  { id: 'approval-funnel', label: 'Approval funnel', icon: 'flow' },
  { id: 'sales-ops', label: 'Sales ops', icon: 'target' },
];

/**
 * Resolve the `?view=` search param to a valid tab. Unknown/absent values fall
 * back to 'executive'. Type-safe against the AnalyticsTab union via TABS.
 */
export function parseAnalyticsTab(value: string | null | undefined): AnalyticsTab {
  return TABS.some((tab) => tab.id === value) ? (value as AnalyticsTab) : 'executive';
}

export const SEGMENT_FILTERS = [
  ['All segments', null],
  ['Prime Refi Candidates', 'itm'],
  ['Home Equity Candidate', 'equity'],
  ['Investor / Multi-Property', 'investor'],
  ['Retention Risk', 'retention'],
  ['Listed for Sale', 'listed'],
  ['HELOC Intent', 'permit'],
] as const satisfies ReadonlyArray<readonly [string, SegmentCode | null]>;
export const SEGMENT_CODE_TO_OPTION = Object.fromEntries(
  SEGMENT_FILTERS.filter(([, value]) => value !== null).map(([label, value]) => [value, label]),
) as Record<SegmentCode, string>;
export const SEGMENT_MULTI_OPTIONS = SEGMENT_FILTERS
  .flatMap(([label, value]) => (value ? [{ label, value }] : []));
export const SIGNAL_FILTERS = [
  ['All signals', null],
  ['Market trend', 'market_trend'],
  ['Equity', 'equity'],
  ['Competitor lien', 'competitor_lien'],
  ['Rate spread', 'rate_spread'],
  ['Multi-property', 'multi_property'],
  ['Corporate owner', 'corporate_owner'],
  ['Loan type fit', 'loan_type_fit'],
  ['Recent sale', 'recent_sale'],
  ['Absentee mailing', 'absentee_mailing'],
  ['Recent payoff', 'recent_payoff'],
  ['Recent refi', 'recent_refi'],
  ['Foreclosure stage', 'foreclosure_stage'],
  ['Listing', 'listing'],
  ['HELOC propensity', 'heloc_propensity'],
  ['Refi propensity', 'refi_propensity'],
] as const satisfies ReadonlyArray<readonly [string, string | null]>;
export const SIGNAL_TYPE_TO_OPTION = Object.fromEntries(
  SIGNAL_FILTERS.filter(([, value]) => value !== null).map(([label, value]) => [value, label]),
) as Record<string, string>;
export const SIGNAL_MULTI_OPTIONS = SIGNAL_FILTERS
  .flatMap(([label, value]) => (value ? [{ label, value }] : []));
export const EVIDENCE_WINDOWS = [['Last 7 days', 7], ['Last 30 days', 30], ['Last 90 days', 90]] as const;
export const EVIDENCE_WINDOW_OPTIONS = EVIDENCE_WINDOWS.map(([label]) => label);
export const EVIDENCE_WINDOW_TO_DAYS = Object.fromEntries(EVIDENCE_WINDOWS) as Record<string, number>;

// DOM budget for zoom-mode scatter dots. The server caps the honest payload
// at its own limit; this only bounds how many of those we absolutely-position
// at once, and the meta copy says so whenever it bites.
export const MAX_SCATTER_POINTS = 1_200;

export type LenderFilterParams = {
  lender_relationship?: string | null;
  target_lender_ref?: string | null;
};

export type MultiFilterOption<T extends string> = {
  label: string;
  value: T;
};

export type DailyEvidenceTotal = {
  event_date: string;
  event_count: number;
};

export function borrowerDisplay(row: Pick<TopBorrowerAnalyticsRow, 'display_name' | 'borrower_id'>): string {
  return `${row.display_name} · ${row.borrower_id.slice(-4)}`;
}

export function pct(n: number, total: number): number {
  if (!Number.isFinite(n) || !Number.isFinite(total) || total <= 0) return 0;
  return Math.max(0, Math.min(100, (n / total) * 100));
}

type RouteParamValue = string | number | null | undefined;

function routeHref(route: '/lead-queue' | '/segment-intelligence', params: Record<string, RouteParamValue>): string {
  const qs = new URLSearchParams();
  for (const [key, value] of Object.entries(params)) {
    if (value !== null && value !== undefined && value !== '') qs.set(key, String(value));
  }
  const encoded = qs.toString();
  return encoded ? `${route}?${encoded}` : route;
}

export function leadQueueHref(params: Record<string, RouteParamValue>): string {
  return routeHref('/lead-queue', params);
}

export function segmentIntelligenceHref(params: Record<string, RouteParamValue>): string {
  return routeHref('/segment-intelligence', params);
}

export function normalizeAnalyticsSegmentCodes(values: readonly string[]): SegmentCode[] {
  const out: SegmentCode[] = [];
  for (const value of values) {
    const code = value.toLowerCase();
    if (!SEGMENT_CODE_TO_OPTION[code as SegmentCode] || out.includes(code as SegmentCode)) continue;
    out.push(code as SegmentCode);
  }
  return out;
}

export function leadQueueHrefForFunnelStage(
  row: Pick<FunnelStage, 'stage' | 'stage_order'>,
  leadParams: LenderFilterParams = {},
): string {
  const byOrder: Record<number, LeadFunnelStage> = {
    1: 'addressable',
    2: 'in_the_money',
    3: 'high_opportunity',
    4: 'offer_recommended',
    5: 'approved',
    6: 'actioned',
  };
  const byLabel: Record<string, LeadFunnelStage> = {
    addressable: 'addressable',
    'in the money': 'in_the_money',
    'refi economics': 'in_the_money',
    'high opportunity': 'high_opportunity',
    [HIGH_OPPORTUNITY_KPI_LABEL.toLowerCase()]: 'high_opportunity',
    'top-tier score': 'high_opportunity',
    'offer recommended': 'offer_recommended',
    'primary offer selected': 'offer_recommended',
    approved: 'approved',
    actioned: 'actioned',
  };
  const stage = byOrder[row.stage_order] ?? byLabel[row.stage.trim().toLowerCase()];
  return leadQueueHref({ funnel_stage: stage ?? 'addressable', ...leadParams });
}

export function funnelStageDisplayLabel(row: Pick<FunnelStage, 'stage' | 'stage_order'>): string {
  const byOrder: Record<number, string> = {
    1: 'Addressable',
    2: 'Refi economics',
    3: HIGH_OPPORTUNITY_KPI_LABEL,
    4: 'Primary offer selected',
    5: 'Approved',
    6: 'Actioned',
  };
  return byOrder[row.stage_order] ?? row.stage;
}

export function isOfferRecommendedStage(row: Pick<FunnelStage, 'stage' | 'stage_order'>): boolean {
  return row.stage_order === 4 || row.stage.trim().toLowerCase() === 'offer recommended';
}

export function activationFunnelStages(stages: ReadonlyArray<FunnelStage>): FunnelStage[] {
  return stages
    .filter((stage) => !isOfferRecommendedStage(stage))
    .sort((a, b) => a.stage_order - b.stage_order);
}

export function makeTicks(min: number, max: number, count = 5): number[] {
  if (!Number.isFinite(min) || !Number.isFinite(max)) return [];
  if (max === min) return [min];
  const ticks: number[] = [];
  const steps = Math.max(1, count - 1);
  for (let i = 0; i <= steps; i += 1) {
    ticks.push(min + ((max - min) * i) / steps);
  }
  return ticks;
}

export function formatAxisTick(value: number, compact = false): string {
  if (!Number.isFinite(value)) return '';
  const rounded = Math.round(value);
  return compact ? formatCompact(rounded) : formatCount(rounded);
}

/** "Jul 14" for a YYYY-MM-DD axis label: lib/time, never shifted by the viewer zone. */
export function formatShortDate(value: string): string {
  return formatDate(value, { withYear: false });
}

function dateToUtc(value: string): number | null {
  const [year, month, day] = value.split('-').map((part) => Number(part));
  if (!year || !month || !day) return null;
  return Date.UTC(year, month - 1, day);
}

function utcToIsoDate(value: number): string {
  return new Date(value).toISOString().slice(0, 10);
}

export function signalLabel(value: string): string {
  const known = SIGNAL_TYPE_TO_OPTION[value];
  if (known) return known;
  // Sentence-case unknown signal types (e.g. "product_type" -> "Product type")
  // so they match the casing of the curated labels above.
  const words = value.replace(/_/g, ' ').trim();
  return words ? words.charAt(0).toUpperCase() + words.slice(1) : value;
}

export function parseCsvParam(value: string | null): string[] {
  if (!value) return [];
  const out: string[] = [];
  value.split(',').forEach((part) => {
    const trimmed = part.trim();
    if (trimmed && !out.includes(trimmed)) out.push(trimmed);
  });
  return out;
}

export function toggleSelected<T extends string>(selected: readonly T[], value: T): T[] {
  return selected.includes(value)
    ? selected.filter((item) => item !== value)
    : [...selected, value];
}

export function buildDailyEvidenceTotals(rows: EvidenceDailyRow[]): DailyEvidenceTotal[] {
  const byDate = new Map<string, number>();
  for (const row of rows) {
    byDate.set(row.event_date, (byDate.get(row.event_date) ?? 0) + row.event_count);
  }
  const observed = [...byDate.entries()]
    .map(([event_date, event_count]) => ({ event_date, event_count }))
    .sort((a, b) => a.event_date.localeCompare(b.event_date));
  if (observed.length <= 1) return observed;
  const start = dateToUtc(observed[0].event_date);
  const end = dateToUtc(observed[observed.length - 1].event_date);
  if (start === null || end === null || end <= start) return observed;
  const dayMs = 24 * 60 * 60 * 1000;
  const filled: DailyEvidenceTotal[] = [];
  for (let cursor = start; cursor <= end; cursor += dayMs) {
    const event_date = utcToIsoDate(cursor);
    filled.push({ event_date, event_count: byDate.get(event_date) ?? 0 });
  }
  return filled;
}

export function categoricalTickIndexes(length: number, maxTicks = 6): number[] {
  if (length <= 0) return [];
  if (length <= maxTicks) return Array.from({ length }, (_, idx) => idx);
  const step = Math.ceil((length - 1) / (maxTicks - 1));
  const indexes = new Set<number>([0, length - 1]);
  for (let idx = step; idx < length - 1; idx += step) indexes.add(idx);
  return [...indexes].sort((a, b) => a - b);
}

// ---------------------------------------------------------------------------
// S7 economics scatter geometry. Pure, deterministic layout math over the
// server's density-bin overview + zoomed real-point payloads, so the SVG/DOM
// components stay thin renderers and the math is unit-pinnable.
// ---------------------------------------------------------------------------

export interface ScatterLayout {
  xMin: number;
  xRange: number;
  yMin: number;
  yRange: number;
}

/**
 * Overview layout. The plot range extends one bin width past the domain max
 * on each axis so the boundary bins (equity 100, spread 400 — real bins,
 * since the domain bounds are inclusive) render as full-size cells instead
 * of zero-width slivers. Ticks/dots use the same layout, so positioning
 * stays mutually consistent.
 */
export function overviewScatterLayout(overview: EquitySpreadOverview): ScatterLayout {
  return {
    xMin: overview.equity_domain_min,
    xRange: overview.equity_domain_max + overview.equity_bin_pct - overview.equity_domain_min,
    yMin: overview.spread_domain_min,
    yRange: overview.spread_domain_max + overview.spread_bin_bps - overview.spread_domain_min,
  };
}

/** Zoom layout over an integer-inclusive viewport (hence the +1 ranges). */
export function zoomScatterLayout(viewport: EquitySpreadViewport): ScatterLayout {
  return {
    xMin: viewport.equity_min,
    xRange: Math.max(1, viewport.equity_max - viewport.equity_min + 1),
    yMin: viewport.spread_min,
    yRange: Math.max(1, viewport.spread_max - viewport.spread_min + 1),
  };
}

/** Map a (equity, spread) pair onto CSS percentages (yPct is top-based). */
export function scatterPosition(
  equityPct: number,
  spreadBps: number,
  layout: ScatterLayout,
): { xPct: number; yPct: number } {
  return {
    xPct: pct(equityPct - layout.xMin, layout.xRange),
    yPct: 100 - pct(spreadBps - layout.yMin, layout.yRange),
  };
}

/** CSS rect for one density cell (top-left corner + size, in percent). */
export function binCellRect(
  bin: EquitySpreadBin,
  overview: EquitySpreadOverview,
): { xPct: number; yPct: number; wPct: number; hPct: number } {
  const layout = overviewScatterLayout(overview);
  const corner = scatterPosition(bin.equity_bin_pct, bin.spread_bin_bps + overview.spread_bin_bps, layout);
  return {
    xPct: corner.xPct,
    yPct: corner.yPct,
    wPct: pct(overview.equity_bin_pct, layout.xRange),
    hPct: pct(overview.spread_bin_bps, layout.yRange),
  };
}

/**
 * Zoom window for a clicked bin. Values are integers, so the inclusive
 * upper bound is `bin + width - 1` — bins stay disjoint and the zoomed
 * total_matching equals the bin's borrower_count under identical filters.
 */
export function binZoomViewport(
  bin: EquitySpreadBin,
  overview: EquitySpreadOverview,
): EquitySpreadViewport {
  return {
    equity_min: bin.equity_bin_pct,
    equity_max: Math.min(bin.equity_bin_pct + overview.equity_bin_pct - 1, overview.equity_domain_max),
    spread_min: bin.spread_bin_bps,
    spread_max: Math.min(bin.spread_bin_bps + overview.spread_bin_bps - 1, overview.spread_domain_max),
  };
}

/**
 * Density opacity for a cell: sqrt scaling keeps the long tail visible
 * while the hottest cell saturates. Clamped to [0.18, 1].
 */
export function binDensityAlpha(count: number, maxCount: number): number {
  if (!Number.isFinite(count) || !Number.isFinite(maxCount) || maxCount <= 0 || count <= 0) return 0.18;
  return Math.min(1, 0.18 + 0.82 * Math.sqrt(Math.min(1, count / maxCount)));
}

export function analyticsHref(params: Record<string, RouteParamValue>): string {
  const qs = new URLSearchParams();
  for (const [key, value] of Object.entries(params)) {
    if (value !== null && value !== undefined && value !== '') qs.set(key, String(value));
  }
  const encoded = qs.toString();
  return `/analytics${encoded ? `?${encoded}` : ''}`;
}

// ---------------------------------------------------------------------------
// Funnel Sankey geometry (re-audit Buyer-Wow #5). Pure, deterministic model
// over the SAME FunnelStage[] the Pipeline Metrics bars already use — no new
// data. Produces viewBox-space nodes + connecting ribbon paths so the SVG
// component stays a thin renderer and the math is unit-pinnable.
// ---------------------------------------------------------------------------
//
// What the stages ARE (2026-09-21 audit, dataviz-v1). Addressable is COUNT(*)
// over the population; every other stage is an independent SUM(CASE ...) over
// those same rows (sql/transformations/gold_funnel_snapshot_daily.sql, and
// DatabricksAnalyticsRepository._LIVE_FUNNEL_SQL for the filtered path). A
// high-opportunity borrower need not pass the refi-economics screen and an
// approved borrower need not be high-opportunity, so "stage / previous stage"
// is a ratio of unrelated counts, not a conversion. Addressable is the one
// guaranteed superset, so every stage is labelled with its share of THAT.
export const ADDRESSABLE_STAGE_ORDER = 1;

/**
 * Stage pairs (parent -> child, by stage_order) where SQL guarantees
 * child ⊆ parent, so child / parent IS a conversion. There is exactly one:
 * Actioned is `approval_status = 'approved' AND outreach_status = 'actioned'`
 * in all three statements that produce it (the gold snapshot CTAS,
 * _LIVE_FUNNEL_SQL and _LIVE_WORKFLOW_COUNTS_SQL). Add a pair here only with
 * the SQL that proves the nesting.
 */
export const NESTED_FUNNEL_STAGE_PAIRS: ReadonlyArray<readonly [parent: number, child: number]> = [[5, 6]];

export interface SankeyNode {
  stage: string;
  stageOrder: number;
  count: number;
  /**
   * Share of the Addressable stage (0..1). Null for Addressable itself, and
   * when Addressable is absent from the stages or empty (undefined, not 0%).
   */
  shareOfAddressable: number | null;
  /**
   * Conversion from `nestedIn` (0..1) — ONLY for a pair in
   * NESTED_FUNNEL_STAGE_PAIRS whose parent stage is present and non-empty.
   * Null everywhere else: adjacency on the chart is not nesting in the data.
   */
  conversion: number | null;
  /** The SQL-guaranteed parent stage `conversion` is measured against. */
  nestedIn: Pick<FunnelStage, 'stage' | 'stage_order'> | null;
  /** True when the node is drawn at the minimum thickness, not in proportion. */
  clamped: boolean;
  xCenter: number;
  yTop: number;
  yBottom: number;
  height: number;
}

export interface SankeyRibbon {
  /** SVG path (filled) connecting node i to node i+1. */
  path: string;
  fromOrder: number;
  toOrder: number;
}

export interface SankeyModel {
  viewWidth: number;
  viewHeight: number;
  nodes: SankeyNode[];
  ribbons: SankeyRibbon[];
  /**
   * True when at least one non-empty stage is drawn at the minimum thickness.
   * The renderer must then say so: thickness stops encoding magnitude for
   * those stages, and a chart that silently stops being proportional is worse
   * than one that says "not to scale".
   */
  notToScale: boolean;
}

// `minNodeHeight` (2026-09-21 audit, dataviz-03): at real magnitudes (5.16M,
// 117K, 3.9K, 35, 3) a linear scale with a 3-unit floor drew stages two to
// five as 3-4px hairlines. The scale stays LINEAR — a log or sqrt scale would
// misstate proportions in a product sold on reconcilable numbers — and a
// non-empty stage simply never draws thinner than this, with `notToScale`
// disclosing it. An EMPTY stage keeps the thin `emptyNodeHeight` sliver: a
// visible ribbon into a stage with zero borrowers would imply a flow.
export const SANKEY_VIEW = {
  width: 1000,
  height: 240,
  padX: 70,
  padY: 44,
  nodeWidth: 16,
  minNodeHeight: 12,
  emptyNodeHeight: 3,
} as const;

export function buildFunnelSankeyModel(
  stages: ReadonlyArray<FunnelStage>,
  view = SANKEY_VIEW,
): SankeyModel {
  const ordered = [...stages].sort((a, b) => a.stage_order - b.stage_order);
  const { width, height, padX, padY, nodeWidth, minNodeHeight, emptyNodeHeight } = view;
  const empty: SankeyModel = { viewWidth: width, viewHeight: height, nodes: [], ribbons: [], notToScale: false };
  if (ordered.length === 0) return empty;

  const maxCount = Math.max(...ordered.map((s) => Math.max(0, s.borrower_count)), 1);
  const maxBarH = height - padY * 2;
  const usableW = width - padX * 2;
  const stepX = ordered.length > 1 ? usableW / (ordered.length - 1) : 0;
  const midY = height / 2;
  const countOf = (stageOrder: number): number | null => {
    const found = ordered.find((s) => s.stage_order === stageOrder);
    return found ? Math.max(0, found.borrower_count) : null;
  };
  const addressable = countOf(ADDRESSABLE_STAGE_ORDER);

  const nodes: SankeyNode[] = ordered.map((s, i) => {
    const count = Math.max(0, s.borrower_count);
    const proportional = (count / maxCount) * maxBarH;
    const clamped = count > 0 && proportional < minNodeHeight;
    const h = count === 0 ? emptyNodeHeight : Math.max(minNodeHeight, proportional);
    // A ratio is published only against a population SQL guarantees contains
    // this stage, and only when that population is non-empty: dividing by an
    // empty parent is undefined, not a fake 0%.
    const shareOfAddressable =
      s.stage_order !== ADDRESSABLE_STAGE_ORDER && addressable !== null && addressable > 0
        ? count / addressable
        : null;
    const pair = NESTED_FUNNEL_STAGE_PAIRS.find(([, child]) => child === s.stage_order);
    const parent = pair ? ordered.find((p) => p.stage_order === pair[0]) : undefined;
    const parentCount = parent ? Math.max(0, parent.borrower_count) : 0;
    const nested = parent !== undefined && parentCount > 0;
    return {
      stage: s.stage,
      stageOrder: s.stage_order,
      count,
      shareOfAddressable,
      conversion: nested ? count / parentCount : null,
      nestedIn: nested ? { stage: parent.stage, stage_order: parent.stage_order } : null,
      clamped,
      xCenter: padX + i * stepX,
      yTop: midY - h / 2,
      yBottom: midY + h / 2,
      height: h,
    };
  });

  const ribbons: SankeyRibbon[] = [];
  for (let i = 0; i < nodes.length - 1; i += 1) {
    const a = nodes[i];
    const b = nodes[i + 1];
    const x0 = a.xCenter + nodeWidth / 2;
    const x1 = b.xCenter - nodeWidth / 2;
    const cx = (x0 + x1) / 2; // control-point x for the smooth S-curve
    // Top edge x0→x1, then down b's left edge, bottom edge x1→x0, close.
    const path =
      `M ${fixedAttr(x0)} ${fixedAttr(a.yTop)} ` +
      `C ${fixedAttr(cx)} ${fixedAttr(a.yTop)}, ${fixedAttr(cx)} ${fixedAttr(b.yTop)}, ${fixedAttr(x1)} ${fixedAttr(b.yTop)} ` +
      `L ${fixedAttr(x1)} ${fixedAttr(b.yBottom)} ` +
      `C ${fixedAttr(cx)} ${fixedAttr(b.yBottom)}, ${fixedAttr(cx)} ${fixedAttr(a.yBottom)}, ${fixedAttr(x0)} ${fixedAttr(a.yBottom)} Z`;
    ribbons.push({ path, fromOrder: a.stageOrder, toOrder: b.stageOrder });
  }

  return {
    viewWidth: width,
    viewHeight: height,
    nodes,
    ribbons,
    notToScale: nodes.some((node) => node.clamped),
  };
}

/**
 * Compact percent for a share or conversion ratio (0.0427 → "4.3%"). Returns
 * null — i.e. "show no label" — for an undefined ratio (null) or one above
 * 100%, which a subset can never produce: a label there would be reporting a
 * data problem as a percentage. A ratio of exactly 100% still shows.
 */
export function formatConversionPct(conversion: number | null): string | null {
  if (conversion === null || !Number.isFinite(conversion) || conversion > 1.0001) return null;
  const pct = conversion * 100;
  // A genuinely tiny-but-nonzero narrowing (e.g. 8 / 61,500 = 0.013%) would
  // round to "0.0%" via toFixed(1) and read as "nothing converted" when in
  // fact a small number did. Floor it to "<0.1%" so a real (shrunk) stage is
  // never mislabelled as a flatline. A true zero stage still shows "0.0%".
  if (pct > 0 && pct < 0.05) return '<0.1%';
  return pct >= 10 ? `${Math.round(pct)}%` : formatPercent(conversion, 1);
}
