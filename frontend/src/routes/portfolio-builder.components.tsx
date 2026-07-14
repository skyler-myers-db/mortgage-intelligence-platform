import { type ChangeEvent, useMemo, useState } from 'react';
import { Icon } from '../components/Icon';
import type { CampaignPerformanceFunnelResponse, PortfolioPreview } from '../types';
import type { FootprintState } from './portfolio-builder.logic';
import { formatUsdCompact, stateLabel } from './portfolio-builder.logic';

export function StateMultiSelect({
  label,
  allLabel,
  states,
  value,
  onChange,
}: {
  label: string;
  allLabel: string;
  states: ReadonlyArray<FootprintState>;
  value: string[];
  onChange: (next: string[]) => void;
}) {
  const [open, setOpen] = useState(false);
  const active = value.length > 0;
  const display = !active
    ? allLabel
    : value.length === 1
      ? stateLabel(value[0], states)
      : `${value.length} states`;
  const toggleState = (code: string) => {
    const next = value.includes(code)
      ? value.filter((state) => state !== code)
      : [...value, code];
    onChange(next.length === states.length ? [] : next);
  };

  return (
    <div className="filter-root">
      <button
        type="button"
        className={`filter ${active ? 'is-active' : ''}`}
        aria-haspopup="listbox"
        aria-expanded={open}
        aria-label={`${label}: ${display}`}
        onClick={() => setOpen((next) => !next)}
      >
        <span className="filter__label">{label}</span>
        <span className="filter__value">{display}</span>
        <Icon name="chevdown" size={11} />
      </button>
      {open && (
        <ul className="filter-menu" role="listbox" aria-label={label}>
          <li
            role="option"
            aria-selected={!active}
            className={`filter-menu__item${!active ? ' is-selected' : ''}`}
            onClick={() => {
              onChange([]);
              setOpen(false);
            }}
          >
            {allLabel}
            {!active && <Icon name="check" size={11} />}
          </li>
          {states.map((state) => {
            const selected = value.includes(state.state_code);
            return (
              <li
                key={state.state_code}
                role="option"
                aria-selected={selected}
                className={`filter-menu__item${selected ? ' is-selected' : ''}`}
                onClick={() => toggleState(state.state_code)}
              >
                {state.state_name}
                {selected && <Icon name="check" size={11} />}
              </li>
            );
          })}
        </ul>
      )}
    </div>
  );
}

type FunnelRates = {
  reachRate: number;
  applicationRate: number;
  submissionRate: number;
  fundingRate: number;
};

function percentOverride(value: string): number | null {
  const parsed = Number.parseFloat(value);
  return Number.isFinite(parsed) && parsed >= 0 && parsed <= 100 ? parsed / 100 : null;
}

function wilson95(successes: number, total: number): [number, number] | null {
  if (!Number.isInteger(successes) || !Number.isInteger(total) || total <= 0 || successes < 0 || successes > total) {
    return null;
  }
  const z = 1.959963984540054;
  const proportion = successes / total;
  const zSquaredOverN = (z * z) / total;
  const center = (proportion + zSquaredOverN / 2) / (1 + zSquaredOverN);
  const margin = (z / (1 + zSquaredOverN))
    * Math.sqrt((proportion * (1 - proportion) / total) + ((z * z) / (4 * total * total)));
  return [Math.max(0, center - margin), Math.min(1, center + margin)];
}

/** Cohort economics use gold-layer facts and observed sales outcomes. */
export function RoiProjector({
  preview,
  performance,
  performanceStatus = 'available',
}: {
  preview: PortfolioPreview;
  performance?: CampaignPerformanceFunnelResponse;
  performanceStatus?: 'loading' | 'available' | 'unavailable';
}) {
  const [scenarioMode, setScenarioMode] = useState<'baseline' | 'manual'>('baseline');
  const [manualRates, setManualRates] = useState({
    reachRatePct: '',
    applicationRatePct: '',
    submissionRatePct: '',
    fundingRatePct: '',
  });
  const [unitEconomics, setUnitEconomics] = useState({ revenueRatePct: '', costPerLeadUsd: '' });
  const observed = useMemo(() => {
    const hasObservedData = performanceStatus === 'available' && performance !== undefined;
    const attempted = hasObservedData ? performance.unique_leads_attempted : null;
    const contacted = hasObservedData ? performance.unique_contacts_reached : null;
    const applications = hasObservedData ? performance.unique_application_starts : null;
    const submitted = hasObservedData ? performance.unique_applications_submitted : null;
    const funded = hasObservedData ? performance.unique_closed_funded : null;
    const coherent = attempted !== null
      && contacted !== null
      && applications !== null
      && submitted !== null
      && funded !== null
      && attempted >= contacted
      && contacted >= applications
      && applications >= submitted
      && submitted >= funded;
    const qualified = coherent
      && attempted >= 30
      && contacted >= 30
      && applications >= 10
      && submitted >= 10;
    const rates: FunnelRates | null = qualified
      ? {
        reachRate: contacted! / attempted!,
        applicationRate: applications! / contacted!,
        submissionRate: submitted! / applications!,
        fundingRate: funded! / submitted!,
      }
      : null;
    const fundingInterval = qualified ? wilson95(funded!, attempted!) : null;
    return { attempted, contacted, applications, submitted, funded, coherent, qualified, rates, fundingInterval };
  }, [performance, performanceStatus]);
  const manual = useMemo(() => {
    const reachRate = percentOverride(manualRates.reachRatePct);
    const applicationRate = percentOverride(manualRates.applicationRatePct);
    const submissionRate = percentOverride(manualRates.submissionRatePct);
    const fundingRate = percentOverride(manualRates.fundingRatePct);
    const rates = reachRate !== null
      && applicationRate !== null
      && submissionRate !== null
      && fundingRate !== null
      ? { reachRate, applicationRate, submissionRate, fundingRate }
      : null;
    return { qualified: rates !== null, rates };
  }, [manualRates]);
  const projection = useMemo(() => {
    const rates = scenarioMode === 'baseline' ? observed.rates : manual.rates;
    if (rates === null) {
      return { rates: null, projectedFundings: null, projectedVolume: null, fundingRange: null };
    }
    const projectedReached = preview.high_intent_leads * rates.reachRate;
    const projectedApplications = projectedReached * rates.applicationRate;
    const projectedSubmissions = projectedApplications * rates.submissionRate;
    const projectedFundings = projectedSubmissions * rates.fundingRate;
    const projectedVolume = preview.avg_high_intent_lien_balance_usd == null
      ? null
      : projectedFundings * preview.avg_high_intent_lien_balance_usd;
    const fundingRange = scenarioMode === 'baseline' && observed.fundingInterval !== null
      ? observed.fundingInterval.map((bound) => preview.high_intent_leads * bound) as [number, number]
      : null;
    return { rates, projectedFundings, projectedVolume, fundingRange };
  }, [manual.rates, observed.fundingInterval, observed.rates, preview, scenarioMode]);
  const revenueRate = Number.parseFloat(unitEconomics.revenueRatePct);
  const costPerLead = Number.parseFloat(unitEconomics.costPerLeadUsd);
  const hasUnitEconomics = Number.isFinite(revenueRate) && revenueRate >= 0 && revenueRate <= 100
    && Number.isFinite(costPerLead) && costPerLead >= 0;
  const netRevenue = hasUnitEconomics && projection.projectedVolume !== null
    ? projection.projectedVolume * (revenueRate / 100) - preview.high_intent_leads * costPerLead
    : null;
  const setEconomics = (key: keyof typeof unitEconomics) => (e: ChangeEvent<HTMLInputElement>) =>
    setUnitEconomics((current) => ({ ...current, [key]: e.target.value }));
  const setManualRate = (key: keyof typeof manualRates) => (e: ChangeEvent<HTMLInputElement>) =>
    setManualRates((current) => ({ ...current, [key]: e.target.value }));
  const selectedQualification = scenarioMode === 'baseline' ? observed.qualified : manual.qualified;

  return (
    <div className="surface mt-4 roi-projector" data-testid="roi-projector">
      <div className="surface__hdr surface__hdr--split">
        <div className="surface__hdr-main">
          <div className="surface__icon">
            <Icon name="money" size={14} />
          </div>
          <div>
            <div className="h-4">Campaign economics projection</div>
            <div className="muted fs-12">
              Selected cohort facts projected through the team&apos;s qualified 90-day funnel, or explicit manual overrides.
            </div>
          </div>
        </div>
        <span className={`chip ${selectedQualification ? 'chip--success' : 'chip--neutral'}`}>
          {scenarioMode === 'manual'
            ? manual.qualified ? 'manual · explicit overrides' : 'manual · overrides required'
            : performanceStatus === 'loading'
            ? 'selected cohort · loading team benchmark'
            : performanceStatus === 'unavailable'
              ? 'selected cohort · team benchmark unavailable'
              : observed.qualified
                ? 'selected cohort · qualified team benchmark'
                : 'selected cohort · team sample not qualified'}
        </span>
      </div>
      <div className="surface__body">
        <div className="segment-mode-control">
          <div>
            <div className="eyebrow">Projection mode</div>
            <div className="segment-mode-control__total">
              <span className="num">{scenarioMode === 'baseline' ? 'Observed team benchmark' : 'Manual scenario'}</span>
            </div>
          </div>
          <div className="segmented" role="group" aria-label="Projection mode">
            <button
              type="button"
              className={scenarioMode === 'baseline' ? 'is-active' : ''}
              onClick={() => setScenarioMode('baseline')}
              data-testid="roi-mode-baseline"
              aria-pressed={scenarioMode === 'baseline'}
            >
              Team benchmark
            </button>
            <button
              type="button"
              className={scenarioMode === 'manual' ? 'is-active' : ''}
              onClick={() => setScenarioMode('manual')}
              data-testid="roi-mode-manual"
              aria-pressed={scenarioMode === 'manual'}
            >
              Manual scenario
            </button>
          </div>
        </div>
        <div className="roi-projector__headline mt-4">
          <div className="roi-projector__headline-figure num" data-testid="roi-gross">
            {projection.projectedFundings === null ? '—' : Math.round(projection.projectedFundings).toLocaleString()}
          </div>
          <div className="roi-projector__headline-label">
            {scenarioMode === 'baseline' ? 'benchmark fundings' : 'manual scenario fundings'}
            <span className="muted">
              {' '}from {preview.high_intent_leads.toLocaleString()} refinance-economics leads
            </span>
          </div>
        </div>

        {scenarioMode === 'manual' && (
          <div className="roi-projector__assumptions">
            <RateOverrideField label="Lead → reached %" testId="roi-manual-reach" value={manualRates.reachRatePct} onChange={setManualRate('reachRatePct')} />
            <RateOverrideField label="Reached → app %" testId="roi-manual-application" value={manualRates.applicationRatePct} onChange={setManualRate('applicationRatePct')} />
            <RateOverrideField label="App → submitted %" testId="roi-manual-submission" value={manualRates.submissionRatePct} onChange={setManualRate('submissionRatePct')} />
            <RateOverrideField label="Submitted → funded %" testId="roi-manual-funding" value={manualRates.fundingRatePct} onChange={setManualRate('fundingRatePct')} />
          </div>
        )}
        <div className="roi-projector__assumptions">
          <label className="roi-projector__field">
            <span>Revenue rate % (tenant)</span>
            <input
              className="form-input"
              inputMode="decimal"
              value={unitEconomics.revenueRatePct}
              onChange={setEconomics('revenueRatePct')}
              data-testid="roi-revenue-rate"
              aria-label="Revenue per origination percent"
            />
          </label>
          <label className="roi-projector__field">
            <span>Cost / lead $ (tenant)</span>
            <input
              className="form-input"
              inputMode="decimal"
              value={unitEconomics.costPerLeadUsd}
              onChange={setEconomics('costPerLeadUsd')}
              data-testid="roi-cost-per-lead"
              aria-label="Blended outreach cost per lead in dollars"
            />
          </label>
        </div>

        <div className="roi-projector__derived">
          <RoiStat label="Average refi-economics balance" value={preview.avg_high_intent_lien_balance_usd == null ? '—' : formatUsdCompact(preview.avg_high_intent_lien_balance_usd)} />
          <RoiStat label="Average modeled equity" value={preview.avg_equity_pct == null ? '—' : `${preview.avg_equity_pct.toFixed(1)}%`} />
          <RoiStat label="Average rate spread" value={preview.avg_rate_spread_bps == null ? '—' : `${preview.avg_rate_spread_bps.toFixed(1)} bps`} />
          <RoiStat label="Lead → reached" value={projection.rates == null ? 'Not qualified' : `${(projection.rates.reachRate * 100).toFixed(1)}%`} />
          <RoiStat label="Reached → application start" value={projection.rates == null ? 'Not qualified' : `${(projection.rates.applicationRate * 100).toFixed(1)}%`} />
          <RoiStat label="Application start → submitted" value={projection.rates == null ? 'Not qualified' : `${(projection.rates.submissionRate * 100).toFixed(1)}%`} />
          <RoiStat label="Submitted → funded" value={projection.rates == null ? 'Not qualified' : `${(projection.rates.fundingRate * 100).toFixed(1)}%`} />
          <RoiStat
            label="Observed distinct-borrower sample"
            value={observed.attempted == null || observed.contacted == null || observed.funded == null
              ? 'Unavailable'
              : `${observed.attempted.toLocaleString()} attempted / ${observed.contacted.toLocaleString()} reached / ${observed.funded.toLocaleString()} funded`}
          />
          <RoiStat
            label="Funding range (Wilson 95%)"
            value={scenarioMode === 'manual'
              ? 'Team benchmark only'
              : projection.fundingRange === null
                ? 'Not qualified'
                : `${Math.round(projection.fundingRange[0]).toLocaleString()}–${Math.round(projection.fundingRange[1]).toLocaleString()}`}
          />
          <RoiStat
            label="Projection qualification"
            value={scenarioMode === 'manual'
              ? manual.qualified ? 'Explicit manual override' : 'Overrides required'
              : observed.qualified ? 'Qualified team benchmark' : observed.coherent ? 'Insufficient denominators' : 'Non-monotonic counts'}
          />
          <RoiStat label="Activity source" value="Call dispositions" />
          <RoiStat label="Outcome source" value="Lead outcomes" />
          <RoiStat label="Expected origination volume" value={projection.projectedVolume == null ? '—' : formatUsdCompact(projection.projectedVolume)} />
          <RoiStat label="Net revenue" value={netRevenue == null ? 'Add tenant economics' : formatUsdCompact(netRevenue)} emphasis />
        </div>
        {scenarioMode === 'baseline' && performanceStatus === 'loading' && (
          <div className="roi-projector__invalid muted fs-12" role="status">
            Loading the team&apos;s 90-day outcome funnel. Cohort facts remain visible while rates load.
          </div>
        )}
        {scenarioMode === 'baseline' && performanceStatus === 'unavailable' && (
          <div className="roi-projector__invalid muted fs-12" role="status">
            Team outcome history is unavailable for this session. No zero or benchmark rate is substituted.
          </div>
        )}
        {scenarioMode === 'baseline' && performanceStatus === 'available' && !observed.qualified && (
          <div className="roi-projector__invalid muted fs-12" role="status">
            The team benchmark needs at least 30 attempted borrowers, 30 reached borrowers, 10 application starts, and 10 submitted applications with monotonic stage counts. No fallback rate is substituted.
          </div>
        )}
        {scenarioMode === 'baseline' && observed.qualified && (
          <div className="muted fs-12 mt-2">
            Cohort size, average balance, equity, and rate spread come from the selected population. Conversion rates are lender-team-wide over the last 90 days, not measured performance of this cohort. The funding range is a Wilson 95% interval over unique funded / attempted borrowers. Sources: mip_app.call_dispositions and mip_app.lead_outcomes.
          </div>
        )}
        {scenarioMode === 'manual' && manual.qualified && (
          <div className="muted fs-12 mt-2">
            Manual scenario uses only the four operator-entered stage-rate overrides. It does not inherit the team benchmark or its Wilson interval.
          </div>
        )}
      </div>
    </div>
  );
}

function RateOverrideField({
  label,
  testId,
  value,
  onChange,
}: {
  label: string;
  testId: string;
  value: string;
  onChange: (event: ChangeEvent<HTMLInputElement>) => void;
}) {
  return (
    <label className="roi-projector__field">
      <span>{label}</span>
      <input
        className="form-input"
        inputMode="decimal"
        value={value}
        onChange={onChange}
        data-testid={testId}
      />
    </label>
  );
}

function RoiStat({ label, value, emphasis }: { label: string; value: string; emphasis?: boolean }) {
  return (
    <div className={`roi-projector__stat${emphasis ? ' roi-projector__stat--emphasis' : ''}`}>
      <div className="roi-projector__stat-label">{label}</div>
      <div className="roi-projector__stat-value num">{value}</div>
    </div>
  );
}
