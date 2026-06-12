import { type ChangeEvent, useMemo, useState } from 'react';
import { Icon } from '../components/Icon';
import type { FootprintState, RoiAssumptionInputs } from './portfolio-builder.logic';
import {
  DEFAULT_ROI_ASSUMPTIONS,
  formatUsdCompact,
  projectRoi,
  stateLabel,
} from './portfolio-builder.logic';

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

/**
 * Campaign ROI projector (re-audit #4 Buyer-Wow #7) — turns the build's
 * high-intent lead count into a board-room revenue headline with fully
 * visible, operator-tunable assumptions. Pure client-side; recomputes
 * instantly; never calls the network. The headline answers the CFO
 * question ("what is this build worth?") without hiding the math.
 */
export function RoiProjector({ leads }: { leads: number }) {
  const [assumptions, setAssumptions] = useState<Omit<RoiAssumptionInputs, 'leads'>>(
    DEFAULT_ROI_ASSUMPTIONS,
  );
  const projection = useMemo(
    () => projectRoi({ leads, ...assumptions }),
    [leads, assumptions],
  );
  const set = (key: keyof Omit<RoiAssumptionInputs, 'leads'>) =>
    (e: ChangeEvent<HTMLInputElement>) =>
      setAssumptions((a) => ({ ...a, [key]: e.target.value }));

  return (
    <div className="surface mt-4 roi-projector" data-testid="roi-projector">
      <div className="surface__hdr surface__hdr--split">
        <div className="surface__hdr-main">
          <div className="surface__icon">
            <Icon name="money" size={14} />
          </div>
          <div>
            <div className="h-4">Projected economics</div>
            <div className="muted fs-12">
              What this build is worth at the funnel top — tune the assumptions, the math updates live.
            </div>
          </div>
        </div>
        <span className="chip chip--neutral">illustrative · operator-set</span>
      </div>
      <div className="surface__body">
        <div className="roi-projector__headline">
          <div className="roi-projector__headline-figure num" data-testid="roi-gross">
            {projection.valid ? formatUsdCompact(projection.grossRevenueUsd) : '—'}
          </div>
          <div className="roi-projector__headline-label">
            projected origination revenue
            <span className="muted">
              {' '}from {Math.round(leads).toLocaleString()} high-intent leads
            </span>
          </div>
        </div>

        <div className="roi-projector__assumptions">
          <label className="roi-projector__field">
            <span>Response rate %</span>
            <input
              className="form-input"
              inputMode="decimal"
              value={assumptions.responseRatePct}
              onChange={set('responseRatePct')}
              data-testid="roi-response-rate"
              aria-label="Response rate percent"
            />
          </label>
          <label className="roi-projector__field">
            <span>Avg balance $</span>
            <input
              className="form-input"
              inputMode="decimal"
              value={assumptions.avgBalanceUsd}
              onChange={set('avgBalanceUsd')}
              data-testid="roi-avg-balance"
              aria-label="Average origination balance in dollars"
            />
          </label>
          <label className="roi-projector__field">
            <span>Revenue rate %</span>
            <input
              className="form-input"
              inputMode="decimal"
              value={assumptions.revenueRatePct}
              onChange={set('revenueRatePct')}
              data-testid="roi-revenue-rate"
              aria-label="Revenue per origination percent"
            />
          </label>
          <label className="roi-projector__field">
            <span>Cost / lead $</span>
            <input
              className="form-input"
              inputMode="decimal"
              value={assumptions.costPerLeadUsd}
              onChange={set('costPerLeadUsd')}
              data-testid="roi-cost-per-lead"
              aria-label="Blended outreach cost per lead in dollars"
            />
          </label>
        </div>

        {projection.valid ? (
          <div className="roi-projector__derived">
            <RoiStat label="Projected fundings" value={Math.round(projection.fundings).toLocaleString()} />
            <RoiStat label="Origination volume" value={formatUsdCompact(projection.originationVolumeUsd)} />
            <RoiStat label="Outreach cost" value={formatUsdCompact(projection.outreachCostUsd)} />
            <RoiStat label="Net revenue" value={formatUsdCompact(projection.netRevenueUsd)} emphasis />
          </div>
        ) : (
          <div className="roi-projector__invalid muted fs-12" role="status">
            Enter non-negative numbers (rates 0–100) to project revenue.
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
