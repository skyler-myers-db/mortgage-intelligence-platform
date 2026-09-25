/**
 * RateScenarioControl — the Rate Lever's scrubber (audit wow-stage-1), loaded
 * on demand (rateScenario.lazy, useLazyModule) the first time the user picks
 * "Rate scenario", and rendered in the map legend. Its sentences and styles
 * ship in the same lazy chunk.
 *
 * DECLARED EXTENSION beyond design_files: the prototype has no slider
 * (Module 0 Prototype.html's map legend, ~L1860-1871, is a static class bar).
 * The control is a native `input[type=range]` in basis points of par move, so
 * the keyboard is the platform's own (arrows +/-1 step, PageUp / PageDown,
 * Home / End) and `aria-valuenow` is a meaningful number; `aria-valuetext`
 * speaks the rate, the move and the count. Block `.rate-lever`
 * (RateScenarioControl.css) is built from prototype tokens only.
 *
 * The thumb, the <output> and the value text follow `step` (the user's
 * input, never deferred); the fill, the table and the tooltip follow the
 * map's deferred copy of it. Every rate shown is the server's scenario par
 * rate; the client does no rate arithmetic.
 *
 * While the grid is not usable it draws no slider and no number, only why:
 * warming (one line in the WarmingUpBlock's words, whose live region steps
 * aside when the DegradedBanner tells the story), failed (Retry), or not
 * built. The map keeps the borrower fill meanwhile.
 */
import { useId } from 'react';
import type { RateLeverInputs } from './USChoroplethMapLegend';
import { useWarmingBlockDefers } from './USChoroplethMap.warming';
import { DRAWER_SOURCES } from '../../lib/drawerSources';
import { bpsLabel, formatCount, ratePct } from '../../lib/formatters';
import { formatDate } from '../../lib/time';
import { EvidenceChip } from '../Primitives';
import type { RateScenarioIndex } from './rateScenario.logic';
import { nearestStep, recountAt } from './rateScenario.recount';
import { scenarioHeadline, scenarioValueText } from './rateScenario.copy';
import './RateScenarioControl.css';

export default function RateScenarioControl({ rate }: { rate: RateLeverInputs }) {
  const { read, index } = rate;
  const warming = read.warmingUp;
  const defers = useWarmingBlockDefers(warming);
  const status = warming
    ? `Rate scenarios: ${warming.label}. Retrying automatically (attempt ${warming.attempt} of ${warming.maxAttempts}).`
    : read.error
      ? 'Rate scenarios could not load.'
      : read.data && !index
        ? 'Rate scenarios are not built yet: the gold refresh job builds them (deploy or Admin > Data operations).'
        : null;
  if (status) {
    return (
      <div className="map-legend__caption map-legend__caption--degraded" role={warming && !defers ? 'status' : undefined}>
        {status} Showing borrower counts.{' '}
        {read.error && (
          <button type="button" className="btn btn--ghost btn--sm" onClick={read.retry}>
            Retry
          </button>
        )}
      </div>
    );
  }
  return index ? <RateLever index={index} step={rate.step} onStepChange={rate.onStepChange} scope={rate.scope} /> : null;
}

interface RateLeverProps {
  index: RateScenarioIndex;
  /** The thumb's step in bps (the undeferred user input). */
  step: number;
  onStepChange: (step: number) => void;
  /** The drilled state, when the legend recounts one state; null for the whole book. */
  scope: { id: string; name: string } | null;
}

function RateLever({ index, step, onStepChange, scope }: RateLeverProps) {
  const inputId = useId();
  const { steps, response } = index;
  const stride = steps.length > 1 ? steps[1] - steps[0] : 1;
  const rate = response.scenario_market_rate_pct[steps.indexOf(step)] ?? null;
  const recount = recountAt(index, step, scope?.id ?? null);
  const { min_spread_bps: minSpread, min_equity_pct: minEquity } = response.thresholds;
  const sentence = rate !== null && recount
    ? { step, ratePct: rate, ...recount, scopeName: scope?.name ?? null }
    : null;
  return (
    <div className="rate-lever" data-step={step}>
      <div className="rate-lever__row">
        <label className="rate-lever__label" htmlFor={inputId}>
          Par rate move
        </label>
        <input
          id={inputId}
          className="rate-lever__range"
          type="range"
          min={steps[0]}
          max={steps[steps.length - 1]}
          step={stride}
          value={step}
          aria-valuetext={sentence ? scenarioValueText(sentence) : undefined}
          onChange={(event) => onStepChange(nearestStep(steps, Number(event.currentTarget.value)))}
        />
        <output className="rate-lever__output" htmlFor={inputId}>
          {rate !== null ? ratePct(rate) : '—'}
        </output>
        <button type="button" className="btn btn--ghost btn--sm rate-lever__reset" onClick={() => onStepChange(0)}>
          Reset to today
        </button>
      </div>
      <p className="rate-lever__headline">
        <span className="rate-lever__sentence">{sentence ? scenarioHeadline(sentence) : '—'}</span>{' '}
        <EvidenceChip source={DRAWER_SOURCES.rateSensitivity}>{DRAWER_SOURCES.rateSensitivity.short}</EvidenceChip>
      </p>
      <div className="rate-lever__meta">
        Refi screen {typeof minSpread === 'number' ? bpsLabel(minSpread) : '—'} spread,{' '}
        {typeof minEquity === 'number' ? `${formatCount(minEquity)}%` : '—'} equity · today&apos;s par{' '}
        {typeof response.base_market_rate_pct === 'number' ? ratePct(response.base_market_rate_pct) : '—'} · book as
        of {formatDate(response.provenance.book_as_of ?? null)}
      </div>
    </div>
  );
}
