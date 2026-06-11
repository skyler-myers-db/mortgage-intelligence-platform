// React Compiler memo caches add enough code here to breach the strict bundle budget.
// Keep this route explicit: chart-scale derivations use targeted useMemo below.
'use no memo';

import { useEffect, useId, useMemo, useRef, useState, type KeyboardEvent } from 'react';
import { Link } from 'react-router-dom';
import { Icon } from '../components/Icon';
import { GlossaryTerm } from '../components/GlossaryTerm';
import { KpiCard } from '../components/mortgage/KpiCard';
import type {
  EconomicsAnalyticsResponse,
  EvidenceBySignalRow,
  ExecutiveAnalyticsResponse,
  GeographyAnalyticsResponse,
  SegmentAnalyticsResponse,
  SegmentByStateRow,
  SegmentCode,
  SegmentMetricRow,
  SignalEvidenceExample,
  SignalAnalyticsResponse,
  StateAvmValueRow,
  StateOpportunityRow,
  TopBorrowerAnalyticsRow,
  TopSegmentByStateRow,
  TopZipOpportunityRow,
} from '../types';
import {
  analyticsHref,
  borrowerDisplay,
  buildDailyEvidenceTotals,
  fmt,
  fmtCurrency,
  leadQueueHref,
  segmentIntelligenceHref,
  signalLabel,
  toggleSelected,
  type LenderFilterParams,
  type MultiFilterOption,
} from './analytics.lib';
import {
  Bars,
  DailyEvidenceLineChart,
  DataTable,
  FunnelBars,
  LineChart,
  ScatterPlot,
  ScopeChip,
  SectionHeader,
} from './analytics.charts';

export function ExecutiveView({ data, leadParams }: { data: ExecutiveAnalyticsResponse; leadParams: LenderFilterParams }) {
  return (
    <>
      <div className="kpi-row">
        <KpiCard label="Addressable Borrowers" value={fmt(data.totals.addressable_borrowers)} delta={data.totals.snapshot_date ?? undefined} deltaDir="flat" />
        <KpiCard label="In the Money" value={fmt(data.totals.in_the_money_borrowers)} delta={`${fmt(data.totals.high_opportunity_borrowers)} high score`} deltaDir="up" />
        <KpiCard label="Offers Recommended" value={fmt(data.totals.offer_recommended_borrowers)} delta="Next-best-offer ready" deltaDir="up" />
        <KpiCard label="Approved Outreach" value={fmt(data.totals.approved_borrowers)} delta={`${fmt(data.totals.actioned_borrowers)} actioned`} deltaDir="flat" />
      </div>
      <div className="layoutA-grid analytics-grid">
        <section className="surface">
          <div className="surface__hdr surface__hdr--split">
            <h2 className="h-3">Opportunity Score Distribution</h2>
            <Link className="btn btn--sm" to={leadQueueHref(leadParams)}>Open queue</Link>
          </div>
          <div className="surface__body analytics-chart-panel">
            <LineChart
              rows={data.score_distribution}
              x={(row) => 'score_bucket' in row ? row.score_bucket : row.spread_bucket_bps}
              y={(row) => row.borrower_count}
              xLabel="Opportunity score"
              yLabel="Borrowers"
            />
          </div>
        </section>
        <section className="surface">
          <div className="surface__hdr surface__hdr--split">
            <h2 className="h-3">Pipeline Metrics</h2>
            <ScopeChip>Independent cuts</ScopeChip>
          </div>
          <div className="surface__body">
            <FunnelBars stages={data.stages} leadParams={leadParams} />
          </div>
        </section>
      </div>
    </>
  );
}

export function GeographyView({ data, leadParams }: { data: GeographyAnalyticsResponse; leadParams: LenderFilterParams }) {
  return (
    <>
      <SectionHeader title="Geography" action={<Link className="btn btn--sm" to={segmentIntelligenceHref(leadParams)}>Open map</Link>} />
      <div className="layoutA-grid analytics-grid">
        <section className="surface">
          <div className="surface__hdr"><h2 className="h-3">Opportunity by State</h2></div>
          <div className="surface__body">
            <Bars<StateOpportunityRow>
              rows={data.state_opportunities}
              value={(row) => row.in_the_money_borrowers}
              label={(row) => row.state}
              sublabel={(row) => `${fmt(row.borrower_count)} addressable · ${row.mean_opportunity_score} avg score`}
              href={(row) => leadQueueHref({ state: row.state, ...leadParams })}
            />
          </div>
        </section>
        <section className="surface">
          <div className="surface__hdr"><h2 className="h-3">AVM Value by State</h2></div>
          <div className="surface__body">
            <Bars<StateAvmValueRow>
              rows={data.state_avm_values}
              value={(row) => row.total_avm_value_usd}
              label={(row) => row.state}
              sublabel={(row) => `${fmtCurrency(row.total_equity_usd)} equity`}
              href={(row) => leadQueueHref({ state: row.state, ...leadParams })}
            />
          </div>
        </section>
      </div>
      <section className="surface analytics-section">
        <div className="surface__hdr"><h2 className="h-3">Top ZIPs in the Money</h2></div>
        <div className="surface__body">
          <DataTable<TopZipOpportunityRow>
            rows={data.top_zips}
            getKey={(row) => `${row.state}-${row.zip}`}
            columns={[
              { key: 'zip', label: 'ZIP', render: (row) => <Link to={leadQueueHref({ state: row.state, zip: row.zip, ...leadParams })}>{row.zip}</Link> },
              { key: 'place', label: 'Market', render: (row) => `${row.city ?? 'Unknown'}, ${row.state}` },
              { key: 'itm', label: 'In the Money', render: (row) => fmt(row.in_the_money_borrowers) },
              { key: 'score', label: 'ITM Avg Score', render: (row) => row.mean_opportunity_score },
              { key: 'spread', label: 'ITM Avg Spread', render: (row) => `${row.mean_rate_spread_bps} bps` },
            ]}
          />
        </div>
      </section>
    </>
  );
}

export function EconomicsView({ data }: { data: EconomicsAnalyticsResponse }) {
  return (
    <>
      <div className="layoutA-grid analytics-grid analytics-grid--wide-left">
        <section className="surface">
          <div className="surface__hdr"><h2 className="h-3">Rate Spread Distribution</h2></div>
          <div className="surface__body analytics-chart-panel">
            <LineChart
              rows={data.rate_spread_histogram}
              x={(row) => 'spread_bucket_bps' in row ? row.spread_bucket_bps : row.score_bucket}
              y={(row) => row.borrower_count}
              xLabel="Spread bps"
              yLabel="Borrowers"
            />
          </div>
        </section>
        <section className="surface">
          <div className="surface__hdr"><h2 className="h-3">Top Borrowers</h2></div>
          <div className="surface__body">
            <DataTable<TopBorrowerAnalyticsRow>
              rows={data.top_borrowers}
              getKey={(row) => row.borrower_id}
              columns={[
                { key: 'borrower', label: 'Borrower', render: (row) => <Link to={`/borrower-360/${row.borrower_id}`}>{borrowerDisplay(row)}</Link> },
                { key: 'score', label: 'Score', render: (row) => row.opportunity_score },
                { key: 'spread', label: 'Spread', render: (row) => `${row.rate_spread_bps} bps` },
                { key: 'offer', label: 'Offer', render: (row) => row.recommended_offer },
              ]}
            />
          </div>
        </section>
      </div>
      <section className="surface analytics-section">
        <div className="surface__hdr"><h2 className="h-3">Equity vs Rate Spread</h2></div>
        <div className="surface__body analytics-chart-panel analytics-chart-panel--scatter">
          <ScatterPlot rows={data.equity_vs_spread} />
        </div>
      </section>
    </>
  );
}

export function SegmentsView({ data, leadParams }: { data: SegmentAnalyticsResponse; leadParams: LenderFilterParams }) {
  const topStates = data.top_segments_by_state.filter((row) => row.state_rank === 1);
  const scopeLabel = data.scope.label;
  const scopeDescription = data.scope.description;
  return (
    <>
      <section className="surface">
        <div className="surface__hdr surface__hdr--split">
          <h2 className="h-3">Segment Overview</h2>
          <ScopeChip title={scopeDescription}>{scopeLabel}</ScopeChip>
        </div>
        <div className="surface__body">
          <DataTable
            rows={data.overview}
            getKey={(row) => row.segment_code}
            columns={[
              { key: 'segment', label: 'Segment', render: (row) => <Link to={leadQueueHref({ segment_codes: row.segment_code, segment_mode: 'all', ...leadParams })}>{row.name}</Link> },
              { key: 'borrowers', label: 'Borrowers', render: (row) => fmt(row.borrower_count) },
              { key: 'score', label: 'Avg Score', render: (row) => row.mean_opportunity_score },
              { key: 'itm', label: 'In the Money', render: (row) => fmt(row.in_the_money_borrowers) },
              { key: 'approval', label: 'Approval', render: (row) => row.approval_rate === null || row.approval_rate === undefined ? '—' : `${row.approval_rate.toFixed(1)}%` },
            ]}
          />
        </div>
      </section>
      <div className="layoutA-grid analytics-grid">
        <section className="surface">
          <div className="surface__hdr surface__hdr--split">
            <h2 className="h-3">Segment Size</h2>
            <ScopeChip title={scopeDescription}>{scopeLabel}</ScopeChip>
          </div>
          <div className="surface__body">
            <Bars<SegmentMetricRow>
              rows={data.counts}
              value={(row) => row.value}
              label={(row) => row.segment_name}
              href={(row) => leadQueueHref({ segment_codes: row.segment_code, segment_mode: 'all', ...leadParams })}
            />
          </div>
        </section>
        <section className="surface">
          <div className="surface__hdr surface__hdr--split">
            <h2 className="h-3">Mean Opportunity Score</h2>
            <ScopeChip title={scopeDescription}>{scopeLabel}</ScopeChip>
          </div>
          <div className="surface__body">
            <Bars<SegmentMetricRow>
              rows={data.average_scores}
              value={(row) => row.value}
              label={(row) => row.segment_name}
              href={(row) => leadQueueHref({ segment_codes: row.segment_code, segment_mode: 'all', ...leadParams })}
            />
          </div>
        </section>
      </div>
      <section className="surface analytics-section">
        <div className="surface__hdr surface__hdr--split">
          <h2 className="h-3">Leading Segment by State</h2>
          <ScopeChip title={scopeDescription}>{scopeLabel}</ScopeChip>
        </div>
        <div className="surface__body">
          <Bars<TopSegmentByStateRow | SegmentByStateRow>
            rows={topStates}
            value={(row) => row.borrower_count}
            label={(row) => `${row.state} · ${row.segment_name}`}
            href={(row) => leadQueueHref({ state: row.state, segment_codes: row.segment_code, segment_mode: 'all', ...leadParams })}
          />
        </div>
      </section>
    </>
  );
}

function EvidenceExamplesTable({ rows }: { rows: SignalEvidenceExample[] }) {
  return (
    <DataTable<SignalEvidenceExample>
      rows={rows}
      getKey={(row, idx) => `${row.borrower_id}-${row.signal_type}-${row.timestamp}-${idx}`}
      columns={[
        { key: 'borrower', label: 'Borrower', render: (row) => <Link to={`/borrower-360/${row.borrower_id}`}>{borrowerDisplay(row)}</Link> },
        { key: 'signal', label: 'Signal', render: (row) => signalLabel(row.signal_type) },
        { key: 'value', label: 'Value', render: (row) => row.signal_value },
        { key: 'confidence', label: 'Evidence confidence', render: (row) => row.confidence.toFixed(3) },
        { key: 'source', label: 'Source', render: (row) => row.source_product },
      ]}
    />
  );
}

function MultiFilterSelect<T extends string>({
  label,
  allLabel,
  selected,
  options,
  onChange,
}: {
  label: string;
  allLabel: string;
  selected: readonly T[];
  options: ReadonlyArray<MultiFilterOption<T>>;
  onChange: (next: T[]) => void;
}) {
  const menuId = useId();
  const [open, setOpen] = useState(false);
  const [focusIdx, setFocusIdx] = useState(0);
  const rootRef = useRef<HTMLDivElement>(null);
  const btnRef = useRef<HTMLButtonElement>(null);
  const optionRefs = useRef<Array<HTMLButtonElement | null>>([]);
  const selectedSet = useMemo(() => new Set(selected), [selected]);
  const active = selected.length > 0;
  const display = selected.length === 0
    ? allLabel
    : selected.length === 1
      ? options.find((option) => option.value === selected[0])?.label ?? selected[0]
      : `${selected.length} selected`;

  useEffect(() => {
    if (!open) return;
    const onDown = (event: MouseEvent) => {
      if (!rootRef.current?.contains(event.target as Node)) setOpen(false);
    };
    const onKey = (event: globalThis.KeyboardEvent) => {
      if (event.key === 'Escape') {
        setOpen(false);
        btnRef.current?.focus();
      }
    };
    window.addEventListener('mousedown', onDown);
    window.addEventListener('keydown', onKey);
    return () => {
      window.removeEventListener('mousedown', onDown);
      window.removeEventListener('keydown', onKey);
    };
  }, [open]);

  useEffect(() => {
    if (!open) return;
    const selectedIdx = options.findIndex((option) => selectedSet.has(option.value));
    setFocusIdx(selectedIdx >= 0 ? selectedIdx + 1 : 0);
  }, [open, options, selectedSet]);

  useEffect(() => {
    if (!open) return;
    optionRefs.current[focusIdx]?.focus();
  }, [focusIdx, open]);

  const itemCount = options.length + 1;
  const activeOptionId = `${menuId}-option-${focusIdx}`;

  const pickIndex = (idx: number) => {
    if (idx === 0) {
      onChange([]);
      return;
    }
    const option = options[idx - 1];
    if (option) onChange(toggleSelected(selected, option.value));
  };

  const onTriggerKeyDown = (event: KeyboardEvent<HTMLButtonElement>) => {
    if (event.key === 'ArrowDown' || event.key === 'ArrowUp') {
      event.preventDefault();
      if (!open) {
        setOpen(true);
        return;
      }
      setFocusIdx((idx) => (
        event.key === 'ArrowDown'
          ? (idx + 1) % itemCount
          : (idx - 1 + itemCount) % itemCount
      ));
    } else if (event.key === 'Home' && open) {
      event.preventDefault();
      setFocusIdx(0);
    } else if (event.key === 'End' && open) {
      event.preventDefault();
      setFocusIdx(itemCount - 1);
    } else if (event.key === 'Enter' || event.key === ' ') {
      event.preventDefault();
      if (!open) {
        setOpen(true);
        return;
      }
      pickIndex(focusIdx);
    } else if (event.key === 'Escape' && open) {
      event.preventDefault();
      setOpen(false);
    }
  };

  const onOptionKeyDown = (event: KeyboardEvent<HTMLButtonElement>) => {
    if (event.key === 'ArrowDown' || event.key === 'ArrowUp') {
      event.preventDefault();
      setFocusIdx((idx) => (
        event.key === 'ArrowDown'
          ? (idx + 1) % itemCount
          : (idx - 1 + itemCount) % itemCount
      ));
    } else if (event.key === 'Home') {
      event.preventDefault();
      setFocusIdx(0);
    } else if (event.key === 'End') {
      event.preventDefault();
      setFocusIdx(itemCount - 1);
    } else if (event.key === 'Enter' || event.key === ' ') {
      event.preventDefault();
      pickIndex(focusIdx);
    } else if (event.key === 'Escape') {
      event.preventDefault();
      setOpen(false);
      btnRef.current?.focus();
    }
  };

  return (
    <div ref={rootRef} className="filter-root">
      <button
        ref={btnRef}
        type="button"
        className={`filter ${active ? 'is-active' : ''}`}
        onClick={() => setOpen((value) => !value)}
        onKeyDown={onTriggerKeyDown}
        aria-haspopup="listbox"
        aria-expanded={open}
        aria-controls={open ? menuId : undefined}
        aria-activedescendant={open ? activeOptionId : undefined}
        aria-label={`${label}: ${display}`}
      >
        <span className="filter__label">{label}</span>
        <span className="filter__value">{display}</span>
        <Icon name="chevdown" size={11} />
      </button>
      {open && (
        <ul id={menuId} className="filter-menu filter-menu--multi" role="listbox" aria-label={label} aria-multiselectable="true">
          <li role="presentation">
            <button
              ref={(node) => {
                optionRefs.current[0] = node;
              }}
              id={`${menuId}-option-0`}
              type="button"
              role="option"
              aria-selected={!active}
              tabIndex={focusIdx === 0 ? 0 : -1}
              className={`filter-menu__item${!active ? ' is-selected' : ''}${focusIdx === 0 ? ' is-focused' : ''}`}
              onMouseEnter={() => setFocusIdx(0)}
              onKeyDown={onOptionKeyDown}
              onClick={() => onChange([])}
            >
              {allLabel}
              {!active && <Icon name="check" size={11} />}
            </button>
          </li>
          {options.map((option, idx) => {
            const selectedOption = selectedSet.has(option.value);
            return (
              <li key={option.value} role="presentation">
                <button
                  ref={(node) => {
                    optionRefs.current[idx + 1] = node;
                  }}
                  id={`${menuId}-option-${idx + 1}`}
                  type="button"
                  role="option"
                  aria-selected={selectedOption}
                  tabIndex={focusIdx === idx + 1 ? 0 : -1}
                  className={`filter-menu__item${selectedOption ? ' is-selected' : ''}${focusIdx === idx + 1 ? ' is-focused' : ''}`}
                  onMouseEnter={() => setFocusIdx(idx + 1)}
                  onKeyDown={onOptionKeyDown}
                  onClick={() => onChange(toggleSelected(selected, option.value))}
                >
                  {option.label}
                  {selectedOption && <Icon name="check" size={11} />}
                </button>
              </li>
            );
          })}
        </ul>
      )}
    </div>
  );
}

export { MultiFilterSelect };

export function SignalsView({
  data,
  filterParams,
}: {
  data: SignalAnalyticsResponse;
  filterParams: { states: string[]; segmentCodes: SegmentCode[]; days: number } & LenderFilterParams;
}) {
  const dailyTotals = useMemo(() => buildDailyEvidenceTotals(data.evidence_daily), [data.evidence_daily]);

  return (
    <>
      <section className="surface">
        <div className="surface__hdr surface__hdr--split">
          <div>
            <h2 className="h-3">Evidence Events Per Day</h2>
            <p className="analytics-panel-note">
              Source-event dates from <span className="mono">mip.gold.evidence_events.timestamp</span>; blank days are shown as zero.
            </p>
          </div>
          <ScopeChip>{data.evidence_daily.length} signal/date buckets</ScopeChip>
        </div>
        <div className="surface__body analytics-chart-panel">
          <DailyEvidenceLineChart rows={dailyTotals} />
        </div>
      </section>
      <section className="surface analytics-section">
        <div className="surface__hdr surface__hdr--split">
          <div>
            <h2 className="h-3">Evidence by Signal Type</h2>
            <p className="analytics-panel-note">
              Rows count governed evidence events by signal and Cotality source. <GlossaryTerm term="evidenceConfidence" />
              {' '}is the mean of <span className="mono">confidence</span> in the same gold table.
            </p>
          </div>
          <ScopeChip title="Grouped from mip.gold.evidence_events">mip.gold.evidence_events</ScopeChip>
        </div>
        <div className="surface__body">
          <Bars<EvidenceBySignalRow>
            rows={data.evidence_by_signal}
            value={(row) => row.event_count}
            label={(row) => signalLabel(row.signal_type)}
            sublabel={(row) => `${row.source_product} · ${row.source_table} · ${row.mean_confidence === null || row.mean_confidence === undefined ? '—' : row.mean_confidence.toFixed(3)} mean evidence confidence`}
            href={(row) => analyticsHref({
              states: filterParams.states,
              segment_codes: filterParams.segmentCodes,
              segment_mode: filterParams.segmentCodes.length ? 'any' : null,
              lender_relationship: filterParams.lender_relationship,
              target_lender_ref: filterParams.target_lender_ref,
              days: filterParams.days === 30 ? null : filterParams.days,
              signal_types: row.signal_type,
            })}
          />
        </div>
      </section>
      <section className="surface analytics-section">
        <div className="surface__hdr surface__hdr--split">
          <div>
            <h2 className="h-3">Evidence Drilldown</h2>
            <p className="analytics-panel-note">
              Highest evidence-confidence matching rows with borrower links. Each row is one evidence event attached to a borrower dossier.
            </p>
          </div>
          <ScopeChip>Top 25</ScopeChip>
        </div>
        <div className="surface__body">
          <EvidenceExamplesTable rows={data.evidence_examples} />
        </div>
      </section>
    </>
  );
}
