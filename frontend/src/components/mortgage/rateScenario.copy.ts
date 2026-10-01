/**
 * Rate Lever sentences (audit wow-stage-1). Lazy-only: imported by
 * RateScenarioControl, which loads on demand, so none of this ships with the
 * map. Every number goes through lib/formatters; every rate is the server's
 * scenario par rate; the step is only ever turned into points for prose.
 */
import { formatCount, formatFixed, ratePct, signedCount } from '../../lib/formatters';

export interface ScenarioSentenceInput {
  /** Par move in basis points (positive = par rises). */
  step: number;
  /** The server's scenario par rate at `step`, in percent. */
  ratePct: number;
  inTheMoney: number;
  /** The same scope at step 0. */
  today: number;
  /** Contact-eligible subset at `step`; null drops the clause. */
  contactable: number | null;
  /** "Texas" when the map is drilled; null for the whole book. */
  scopeName: string | null;
}

function borrowers(input: ScenarioSentenceInput): string {
  const noun = input.inTheMoney === 1 ? 'borrower' : 'borrowers';
  return `${formatCount(input.inTheMoney)} ${noun}${input.scopeName ? ` in ${input.scopeName}` : ''}`;
}

function contactableClause(input: ScenarioSentenceInput): string {
  return input.contactable === null ? '.' : `; ${formatCount(input.contactable)} of them are contactable.`;
}

/** The plain-English headline beside the slider. */
export function scenarioHeadline(input: ScenarioSentenceInput): string {
  const rate = ratePct(input.ratePct);
  if (input.step === 0) {
    return `At today's 30-year par rate (${rate}), ${borrowers(input)} clear this refresh's refi screen${contactableClause(input)}`;
  }
  const points = formatFixed(Math.abs(input.step) / 100, 2);
  const direction = input.step < 0 ? 'lower' : 'higher';
  const delta = input.inTheMoney - input.today;
  const change = delta === 0
    ? 'the same as today'
    : `${formatCount(Math.abs(delta))} ${delta > 0 ? 'more' : 'fewer'} than today`;
  return `If the 30-year par rate were ${points} points ${direction} (${rate}), ${borrowers(input)} would clear this refresh's refi screen: ${change}${contactableClause(input)}`;
}

/** The slider's aria-valuetext: the rate, the move, and the count in one phrase. */
export function scenarioValueText(input: Pick<ScenarioSentenceInput, 'step' | 'ratePct' | 'inTheMoney'>): string {
  const move = input.step === 0
    ? "today's rate"
    : `${input.step < 0 ? 'down' : 'up'} ${formatCount(Math.abs(input.step))} basis points`;
  return `Par rate ${ratePct(input.ratePct)}, ${move}: ${formatCount(input.inTheMoney)} borrowers in the money.`;
}

/**
 * "Largest in-the-money gains: Texas +1,234 · Florida +980 · California +870"
 * (a par fall) or "... drops: ..." (a rise): the three states whose count
 * moves most at `step`, ties by name. Null at step 0, without a change map,
 * or when nothing moved. Whole book only (the control shows it unscoped).
 */
export function largestChanges(
  changeById: Readonly<Record<string, number>> | null,
  nameOf: (id: string) => string,
  step: number,
): string | null {
  if (step === 0 || !changeById) return null;
  const moved = Object.entries(changeById)
    .filter(([, change]) => change !== 0)
    .map(([id, change]) => ({ name: nameOf(id), change }))
    .sort((a, b) => Math.abs(b.change) - Math.abs(a.change) || a.name.localeCompare(b.name))
    .slice(0, 3);
  if (moved.length === 0) return null;
  const list = moved.map((entry) => `${entry.name} ${signedCount(entry.change)}`).join(' · ');
  return `Largest in-the-money ${step < 0 ? 'gains' : 'drops'}: ${list}`;
}

/**
 * The public spread bounds of GET /leads (routes/lead-queue.filters.ts
 * LEAD_BOUND_LIMITS.rate_spread_bps; parity pinned by the copy test). A
 * local copy so the control chunk never imports the Lead Queue's filters.
 */
const SPREAD_BOUND = { min: -1000, max: 5000 } as const;


/**
 * The map-side cohort handoff (wow-stage-1): the borrowers a par move would
 * bring onto (s < 0) or take off (s > 0) the refi screen, as TODAY's
 * integer spread band around the server's threshold T, in the Lead Queue.
 * It states no count: the queue applies contactability, and its rows are
 * today's par (RATE_COHORT_NOTE stays true). Null at step 0 or without T.
 */
export function scenarioCohortLink(
  step: number,
  minSpreadBps: number | null | undefined,
  scope: { id: string } | null,
): { href: string; label: string } | null {
  if (step === 0 || typeof minSpreadBps !== 'number' || !Number.isFinite(minSpreadBps)) return null;
  const threshold = Math.round(minSpreadBps);
  const gains = step < 0;
  // Clamped to the public range; a band wholly outside it opens nothing.
  const low = Math.max(SPREAD_BOUND.min, gains ? threshold + step : threshold);
  const high = Math.min(SPREAD_BOUND.max, gains ? threshold - 1 : threshold + step - 1);
  if (low > high) return null;
  const params = new URLSearchParams();
  if (!gains) params.set('segment', 'itm');
  params.set('min_rate_spread_bps', String(low));
  params.set('max_rate_spread_bps', String(high));
  if (scope) params.set('state', scope.id.toUpperCase());
  const move = formatCount(Math.abs(step));
  return {
    href: `/lead-queue?${params.toString()}`,
    label: gains
      ? `Open borrowers within ${move} bps of the refi screen in the Lead Queue`
      : `Open in-the-money borrowers within ${move} bps of dropping off the refi screen`,
  };
}
