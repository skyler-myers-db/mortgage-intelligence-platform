// Pure helpers, constants, and types extracted from analytics.tsx.
// No React: this module contains no hooks/components, so it needs no
// 'use no memo' React Compiler pragma (unlike analytics.sections.tsx).
import type { LeadFunnelStage } from '../lib/api';
import type {
  EquitySpreadPoint,
  EvidenceDailyRow,
  FunnelStage,
  SegmentCode,
  TopBorrowerAnalyticsRow,
} from '../types';

export type AnalyticsTab = 'executive' | 'geography' | 'economics' | 'segments' | 'signals';

export const TABS: Array<{ id: AnalyticsTab; label: string; icon: 'flow' | 'map' | 'money' | 'layers' | 'audit' }> = [
  { id: 'executive', label: 'Executive', icon: 'flow' },
  { id: 'geography', label: 'Geography', icon: 'map' },
  { id: 'economics', label: 'Economics', icon: 'money' },
  { id: 'segments', label: 'Segments', icon: 'layers' },
  { id: 'signals', label: 'Signals', icon: 'audit' },
];

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

export const COMPACT_FORMAT = new Intl.NumberFormat('en-US', {
  notation: 'compact',
  maximumFractionDigits: 2,
});
export const MAX_SCATTER_POINTS = 1_200;
export const SCATTER_SPREAD_BUCKET_BPS = 25;

export type PreparedScatterPoint = {
  row: EquitySpreadPoint;
  xPct: number;
  yPct: number;
};

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

export function fmt(n: number | null | undefined): string {
  if (n === null || n === undefined) return '—';
  return COMPACT_FORMAT.format(n);
}

export function fmtCurrency(n: number | null | undefined): string {
  if (n === null || n === undefined) return '—';
  return `$${COMPACT_FORMAT.format(n)}`;
}

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
    'opportunity score 75+': 'high_opportunity',
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
    3: 'Opportunity score 75+',
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
  return compact ? fmt(rounded) : rounded.toLocaleString();
}

export function formatShortDate(value: string): string {
  const [year, month, day] = value.split('-').map((part) => Number(part));
  if (!year || !month || !day) return value;
  return new Intl.DateTimeFormat('en-US', {
    month: 'short',
    day: 'numeric',
    timeZone: 'UTC',
  }).format(new Date(Date.UTC(year, month - 1, day)));
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
  return SIGNAL_TYPE_TO_OPTION[value] ?? value.replace(/_/g, ' ');
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

export function segmentClass(value: string): string {
  const text = value.toLowerCase();
  if (text.includes('equity')) return 'analytics-segment--equity';
  if (text.includes('money') || text === 'itm') return 'analytics-segment--itm';
  if (text.includes('investor')) return 'analytics-segment--investor';
  if (text.includes('listed')) return 'analytics-segment--listed';
  if (text.includes('permit') || text.includes('heloc') || text.includes('propensity')) {
    return 'analytics-segment--permit';
  }
  if (text.includes('retention')) return 'analytics-segment--retention';
  return 'analytics-segment--none';
}

export function compactScatterRows(rows: EquitySpreadPoint[], limit = MAX_SCATTER_POINTS): EquitySpreadPoint[] {
  const byBucket = new Map<string, EquitySpreadPoint>();
  const ordered = [...rows].sort((a, b) => (
    b.opportunity_score - a.opportunity_score || a.borrower_id.localeCompare(b.borrower_id)
  ));
  for (const row of ordered) {
    const key = [
      Math.round(row.equity_pct),
      Math.round(row.rate_spread_bps / SCATTER_SPREAD_BUCKET_BPS) * SCATTER_SPREAD_BUCKET_BPS,
      row.segment,
    ].join(':');
    if (!byBucket.has(key)) byBucket.set(key, row);
    if (byBucket.size >= limit) break;
  }
  return [...byBucket.values()].sort((a, b) => a.equity_pct - b.equity_pct || a.rate_spread_bps - b.rate_spread_bps);
}

export function prepareScatterPoints(rows: EquitySpreadPoint[], minSpread: number, maxSpread: number): PreparedScatterPoint[] {
  const points = compactScatterRows(rows).map((row) => ({
    row,
    xPct: pct(row.equity_pct, 100),
    yPct: 100 - pct(row.rate_spread_bps - minSpread, maxSpread - minSpread),
  }));
  const groups = new Map<string, PreparedScatterPoint[]>();
  for (const point of points) {
    const key = `${point.xPct.toFixed(2)}:${point.yPct.toFixed(2)}`;
    groups.set(key, [...(groups.get(key) ?? []), point]);
  }
  for (const group of groups.values()) {
    if (group.length <= 1) continue;
    const radiusX = Math.min(1.2, 0.35 + group.length * 0.1);
    const radiusY = Math.min(2.8, 0.8 + group.length * 0.22);
    group.forEach((point, idx) => {
      const angle = (Math.PI * 2 * idx) / group.length;
      point.xPct = Math.max(0, Math.min(100, point.xPct + Math.cos(angle) * radiusX));
      point.yPct = Math.max(0, Math.min(100, point.yPct + Math.sin(angle) * radiusY));
    });
  }
  return points;
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
export interface SankeyNode {
  stage: string;
  stageOrder: number;
  count: number;
  /** Conversion from the previous stage (0..1); null for the first stage. */
  conversion: number | null;
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
}

export const SANKEY_VIEW = { width: 1000, height: 240, padX: 70, padY: 44, nodeWidth: 16 } as const;

export function buildFunnelSankeyModel(
  stages: ReadonlyArray<FunnelStage>,
  view = SANKEY_VIEW,
): SankeyModel {
  const ordered = [...stages].sort((a, b) => a.stage_order - b.stage_order);
  const { width, height, padX, padY, nodeWidth } = view;
  const empty: SankeyModel = { viewWidth: width, viewHeight: height, nodes: [], ribbons: [] };
  if (ordered.length === 0) return empty;

  const maxCount = Math.max(...ordered.map((s) => Math.max(0, s.borrower_count)), 1);
  const maxBarH = height - padY * 2;
  const minBarH = 3; // a zero/near-zero stage stays visible as a sliver
  const usableW = width - padX * 2;
  const stepX = ordered.length > 1 ? usableW / (ordered.length - 1) : 0;
  const midY = height / 2;

  const nodes: SankeyNode[] = ordered.map((s, i) => {
    const count = Math.max(0, s.borrower_count);
    const h = Math.max(minBarH, (count / maxCount) * maxBarH);
    const prev = i > 0 ? Math.max(0, ordered[i - 1].borrower_count) : null;
    // Conversion is defined only as a narrowing from a non-empty prior
    // stage. First stage -> null; prev=0 -> null (undefined, not a fake 0%).
    // A stage that grew keeps its raw ratio here; the formatter suppresses
    // the >100% label. The Executive view filters non-narrowing NBO coverage
    // out of the main activation funnel, while this generic model remains
    // honest for any stage array passed to it.
    const conversion = i === 0 ? null : prev && prev > 0 ? count / prev : null;
    return {
      stage: s.stage,
      stageOrder: s.stage_order,
      count,
      conversion,
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
      `M ${x0.toFixed(2)} ${a.yTop.toFixed(2)} ` +
      `C ${cx.toFixed(2)} ${a.yTop.toFixed(2)}, ${cx.toFixed(2)} ${b.yTop.toFixed(2)}, ${x1.toFixed(2)} ${b.yTop.toFixed(2)} ` +
      `L ${x1.toFixed(2)} ${b.yBottom.toFixed(2)} ` +
      `C ${cx.toFixed(2)} ${b.yBottom.toFixed(2)}, ${cx.toFixed(2)} ${a.yBottom.toFixed(2)}, ${x0.toFixed(2)} ${a.yBottom.toFixed(2)} Z`;
    ribbons.push({ path, fromOrder: a.stageOrder, toOrder: b.stageOrder });
  }

  return { viewWidth: width, viewHeight: height, nodes, ribbons };
}

/**
 * Compact percent for a conversion ratio (0.0427 → "4.3%"). Returns null —
 * i.e. "show no conversion label" — for an undefined conversion (null) or a
 * stage that GREW vs its predecessor (ratio > 1), where a "conversion"
 * percentage is meaningless (the real funnel is non-monotonic at the offer
 * stage). A stage that exactly held (100%) still shows.
 */
export function formatConversionPct(conversion: number | null): string | null {
  if (conversion === null || !Number.isFinite(conversion) || conversion > 1.0001) return null;
  const pct = conversion * 100;
  // A genuinely tiny-but-nonzero narrowing (e.g. 8 / 61,500 = 0.013%) would
  // round to "0.0%" via toFixed(1) and read as "nothing converted" when in
  // fact a small number did. Floor it to "<0.1%" so a real (shrunk) stage is
  // never mislabelled as a flatline. A true zero stage still shows "0.0%".
  if (pct > 0 && pct < 0.05) return '<0.1%';
  return pct >= 10 ? `${Math.round(pct)}%` : `${pct.toFixed(1)}%`;
}
