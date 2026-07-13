import { type ChangeEvent, useMemo, useState } from 'react';
import { Icon } from '../components/Icon';
import type { PortfolioPreview, SalesConversionResponse, SalesOutcomeSummaryResponse } from '../types';
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

/** Cohort economics use gold-layer facts and observed sales outcomes. */
export function RoiProjector({
  preview,
  conversion,
  outcomes,
  performanceStatus = 'available',
}: {
  preview: PortfolioPreview;
  conversion?: SalesConversionResponse;
  outcomes?: SalesOutcomeSummaryResponse;
  performanceStatus?: 'loading' | 'available' | 'unavailable';
}) {
  const [unitEconomics, setUnitEconomics] = useState({ revenueRatePct: '', costPerLeadUsd: '' });
  const observed = useMemo(() => {
    const hasObservedData = performanceStatus === 'available' && conversion !== undefined && outcomes !== undefined;
    const calls = hasObservedData
      ? conversion.rows.reduce((sum, row) => sum + row.calls_attempted, 0)
      : null;
    const contacted = hasObservedData && conversion.rows.every((row) => row.unique_contacts_reached != null)
      ? conversion.rows.reduce((sum, row) => sum + (row.unique_contacts_reached ?? 0), 0)
      : null;
    const applications = hasObservedData && conversion.rows.every((row) => row.unique_application_starts != null)
      ? conversion.rows.reduce((sum, row) => sum + (row.unique_application_starts ?? 0), 0)
      : null;
    const submitted = hasObservedData ? outcomes.unique_applications_submitted ?? null : null;
    const funded = hasObservedData ? outcomes.unique_closed_funded ?? null : null;
    const coherent = contacted !== null
      && applications !== null
      && submitted !== null
      && funded !== null
      && contacted >= applications
      && applications >= submitted
      && submitted >= funded;
    const qualified = coherent && contacted >= 30 && applications >= 10 && submitted >= 10;
    const applicationRate = qualified ? applications / contacted : null;
    const submissionRate = qualified ? submitted / applications : null;
    const fundingRate = qualified ? funded / submitted : null;
    const leads = preview.high_intent_leads;
    const projectedApplications = applicationRate === null ? null : leads * applicationRate;
    const projectedSubmissions = projectedApplications === null || submissionRate === null
      ? null
      : projectedApplications * submissionRate;
    const projectedFundings = projectedSubmissions === null || fundingRate === null
      ? null
      : projectedSubmissions * fundingRate;
    const projectedVolume = projectedFundings === null || preview.avg_high_intent_lien_balance_usd == null
      ? null
      : projectedFundings * preview.avg_high_intent_lien_balance_usd;
    return {
      calls,
      contacted,
      applications,
      submitted,
      funded,
      applicationRate,
      submissionRate,
      fundingRate,
      coherent,
      qualified,
      projectedFundings,
      projectedVolume,
    };
  }, [conversion, outcomes, performanceStatus, preview]);
  const revenueRate = Number.parseFloat(unitEconomics.revenueRatePct);
  const costPerLead = Number.parseFloat(unitEconomics.costPerLeadUsd);
  const hasUnitEconomics = Number.isFinite(revenueRate) && revenueRate >= 0 && revenueRate <= 100
    && Number.isFinite(costPerLead) && costPerLead >= 0;
  const netRevenue = hasUnitEconomics && observed.projectedVolume !== null
    ? observed.projectedVolume * (revenueRate / 100) - preview.high_intent_leads * costPerLead
    : null;
  const set = (key: keyof typeof unitEconomics) => (e: ChangeEvent<HTMLInputElement>) =>
    setUnitEconomics((current) => ({ ...current, [key]: e.target.value }));

  return (
    <div className="surface mt-4 roi-projector" data-testid="roi-projector">
      <div className="surface__hdr surface__hdr--split">
        <div className="surface__hdr-main">
          <div className="surface__icon">
            <Icon name="money" size={14} />
          </div>
          <div>
            <div className="h-4">Observed-rate economics scenario</div>
            <div className="muted fs-12">
              Current refinance-economics cohort with your team&apos;s distinct-borrower 90-day funnel.
            </div>
          </div>
        </div>
        <span className={`chip ${performanceStatus === 'available' ? 'chip--success' : 'chip--neutral'}`}>
          {performanceStatus === 'loading'
            ? 'cohort exact · loading performance'
            : performanceStatus === 'unavailable'
              ? 'cohort exact · performance unavailable'
              : observed.qualified
                ? 'cohort exact · qualified 90d funnel'
                : 'cohort exact · sample not qualified'}
        </span>
      </div>
      <div className="surface__body">
        <div className="roi-projector__headline">
          <div className="roi-projector__headline-figure num" data-testid="roi-gross">
            {observed.projectedFundings === null ? '—' : Math.round(observed.projectedFundings).toLocaleString()}
          </div>
          <div className="roi-projector__headline-label">
            scenario fundings
            <span className="muted">
              {' '}from {preview.high_intent_leads.toLocaleString()} refinance-economics leads
            </span>
          </div>
        </div>

        <div className="roi-projector__assumptions">
          <label className="roi-projector__field">
            <span>Revenue rate % (tenant)</span>
            <input
              className="form-input"
              inputMode="decimal"
              value={unitEconomics.revenueRatePct}
              onChange={set('revenueRatePct')}
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
              onChange={set('costPerLeadUsd')}
              data-testid="roi-cost-per-lead"
              aria-label="Blended outreach cost per lead in dollars"
            />
          </label>
        </div>

        <div className="roi-projector__derived">
          <RoiStat label="Average refi-economics balance" value={preview.avg_high_intent_lien_balance_usd == null ? '—' : formatUsdCompact(preview.avg_high_intent_lien_balance_usd)} />
          <RoiStat label="Average modeled equity" value={preview.avg_equity_pct == null ? '—' : `${preview.avg_equity_pct.toFixed(1)}%`} />
          <RoiStat label="Average rate spread" value={preview.avg_rate_spread_bps == null ? '—' : `${preview.avg_rate_spread_bps.toFixed(1)} bps`} />
          <RoiStat label="Reached → application start" value={observed.applicationRate == null ? 'Not qualified' : `${(observed.applicationRate * 100).toFixed(1)}%`} />
          <RoiStat label="Application start → submitted" value={observed.submissionRate == null ? 'Not qualified' : `${(observed.submissionRate * 100).toFixed(1)}%`} />
          <RoiStat label="Submitted → funded" value={observed.fundingRate == null ? 'Not qualified' : `${(observed.fundingRate * 100).toFixed(1)}%`} />
          <RoiStat
            label="Observed distinct-borrower sample"
            value={observed.contacted == null || observed.submitted == null
              ? 'Unavailable'
              : `${observed.contacted.toLocaleString()} reached / ${observed.submitted.toLocaleString()} submitted`}
          />
          <RoiStat label="Expected origination volume" value={observed.projectedVolume == null ? '—' : formatUsdCompact(observed.projectedVolume)} />
          <RoiStat label="Net revenue" value={netRevenue == null ? 'Add tenant economics' : formatUsdCompact(netRevenue)} emphasis />
        </div>
        {performanceStatus === 'loading' && (
          <div className="roi-projector__invalid muted fs-12" role="status">
            Loading the team&apos;s 90-day outcome funnel. Cohort facts remain visible while rates load.
          </div>
        )}
        {performanceStatus === 'unavailable' && (
          <div className="roi-projector__invalid muted fs-12" role="status">
            Team outcome history is unavailable for this session. No zero or benchmark rate is substituted.
          </div>
        )}
        {performanceStatus === 'available' && !observed.qualified && (
          <div className="roi-projector__invalid muted fs-12" role="status">
            The 90-day funnel needs at least 30 reached borrowers, 10 application starts, and 10 submitted applications with monotonic stage counts. No benchmark rate is substituted.
          </div>
        )}
        {observed.qualified && (
          <div className="muted fs-12 mt-2">
            Scenario applies tenant-wide observed rates to the current refinance-economics cohort; it is not a commitment or a borrower-level prediction.
          </div>
        )}
      </div>
    </div>
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
