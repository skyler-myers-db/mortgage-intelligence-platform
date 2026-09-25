/**
 * Rate Lever sentences (audit wow-stage-1). Lazy-only: imported by
 * RateScenarioControl, which loads on demand, so none of this ships with the
 * map. Every number goes through lib/formatters; every rate is the server's
 * scenario par rate; the step is only ever turned into points for prose.
 */
import { formatCount, formatFixed, ratePct } from '../../lib/formatters';

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
