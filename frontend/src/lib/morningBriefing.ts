import type { PortfolioPreview } from '../types';

/**
 * Morning briefing (re-audit Buyer-Wow #6). The "since yesterday" standing
 * artifact a Head of Growth opens each morning — the retention hook that
 * turns the demo into a daily habit. Deterministic and PURE: it reads the
 * day-over-day deltas the Home portfolio preview ALREADY carries
 * (preview.trends — each metric's delta_pct/direction/comparison_label, plus
 * a governance `note` the backend sets when a move is a rules/step change
 * rather than market movement). No new data, no new endpoint.
 *
 * When only one snapshot exists yet (a fresh workspace), trends aren't
 * "live" — the briefing shows an honest "movement begins after the next
 * refresh" state rather than fabricating "+0%".
 */

export interface BriefingMovement {
  key: string;
  label: string;
  value: number | null;
  deltaPct: number | null;
  direction: 'up' | 'down' | 'flat';
  /** Backend caveat — e.g. a rules/step-change flag — surfaced verbatim. */
  note: string | null;
}

export interface Briefing {
  /** False → render the "first snapshot, deltas pending" state. */
  available: boolean;
  comparisonLabel: string | null;
  headline: string;
  movements: BriefingMovement[];
  moversCount: number;
}

const BRIEFING_METRICS: ReadonlyArray<{ key: string; label: string; pick: (p: PortfolioPreview) => number | null }> = [
  { key: 'marketable_population', label: 'Marketable population', pick: (p) => p.marketable_population ?? null },
  { key: 'high_intent_leads', label: 'High-intent leads', pick: (p) => p.high_intent_leads ?? null },
  { key: 'top_tier_opportunities', label: 'Top-tier opportunities', pick: (p) => p.top_tier_opportunities ?? null },
  { key: 'offers_recommended', label: 'Offers recommended', pick: (p) => p.offers_recommended ?? null },
  { key: 'approved_count', label: 'Approved outreach', pick: (p) => p.approved_count ?? null },
  { key: 'in_outreach_count', label: 'In outreach', pick: (p) => p.in_outreach_count ?? null },
];

const PENDING: Briefing = {
  available: false,
  comparisonLabel: null,
  headline: 'First snapshot recorded — daily movement appears after the next refresh.',
  movements: [],
  moversCount: 0,
};

export function buildBriefing(preview: PortfolioPreview | null | undefined): Briefing {
  if (!preview || preview.day_zero === true || preview.trend_status !== 'live') {
    return PENDING;
  }
  const trends = preview.trends ?? {};
  const movements: BriefingMovement[] = BRIEFING_METRICS.map((m) => {
    const t = trends[m.key];
    return {
      key: m.key,
      label: m.label,
      value: m.pick(preview),
      deltaPct: t?.delta_pct ?? null,
      direction: t?.direction ?? 'flat',
      note: t?.note ?? null,
    };
  });

  const movers = movements.filter((mv) => mv.direction !== 'flat');
  const comparisonLabel =
    trends.marketable_population?.comparison_label ??
    movements.map((mv) => trends[mv.key]?.comparison_label).find(Boolean) ??
    null;

  let headline: string;
  if (movers.length === 0) {
    headline = `Portfolio steady${comparisonLabel ? ` ${comparisonLabel}` : ''} — no material movement.`;
  } else {
    const biggest = [...movers].sort(
      (a, b) => Math.abs(b.deltaPct ?? 0) - Math.abs(a.deltaPct ?? 0),
    )[0];
    const dir = biggest.direction === 'up' ? 'up' : 'down';
    const mag = Math.abs(biggest.deltaPct ?? 0).toFixed(1);
    headline = `${movers.length} metric${movers.length > 1 ? 's' : ''} moved${
      comparisonLabel ? ` ${comparisonLabel}` : ''
    } — ${biggest.label} ${dir} ${mag}%.`;
  }

  return { available: true, comparisonLabel, headline, movements, moversCount: movers.length };
}

/** Signed compact percent for a delta chip ("+0.4%", "−74.2%", "0.0%"). */
export function formatBriefingDelta(deltaPct: number | null): string {
  if (deltaPct === null || !Number.isFinite(deltaPct)) return '—';
  const sign = deltaPct > 0 ? '+' : deltaPct < 0 ? '−' : '';
  return `${sign}${Math.abs(deltaPct).toFixed(1)}%`;
}
