import { Link } from 'react-router';
import type { HomeSummary } from '../../types';
import { EvidenceChip } from '../Primitives';
import { useApp } from '../AppContext';
import { whyNowTriggers } from '../../lib/homeAnswer';
import { loginSummaryDrawerSource } from '../../lib/loginSummaryDrawerSource';
import { formatTimestamp } from '../../lib/time';

/**
 * WHY NOW — the S4 "since your last login" summary as the answer band's
 * middle column (2026-09-21 audit flow-05 / visual-06). Each server
 * highlight becomes one trigger: its exact server-minted token as an
 * EvidenceChip (the drawer cites the `mip_app.kpi_snapshots` baseline row and
 * the live `mip.semantics.portfolio_headline_metric_view` reading), then a
 * plain-language population that deep-links to the Lead Queue filter with the
 * same predicate.
 *
 * Honesty contract mirrored from the backend:
 * - the deterministic highlights are the source of truth; tokens render
 *   verbatim and nothing is re-derived client-side. The optional Genie
 *   phrasing of the sentence is no longer shown: the column lists the same
 *   tokens as structured items instead of a rephrased sentence;
 * - first-visit / pre-backfill states render welcome copy with live
 *   numbers only — never fake deltas.
 */

const TITLES: Record<HomeSummary['status'], string> = {
  delta: 'Since your last login',
  first_visit: 'Welcome to your book',
  no_baseline: 'Welcome back',
};

export function loginSummarySubtitle(summary: HomeSummary): string {
  if (summary.status === 'delta') {
    const visit = summary.previous_visit_at
      ? formatTimestamp(summary.previous_visit_at, { withYear: false })
      : null;
    const snapshot = summary.baseline_snapshot_at
      ? formatTimestamp(summary.baseline_snapshot_at, { withYear: false })
      : null;
    return `Live headline KPIs vs the ${snapshot ?? 'daily'} snapshot nearest your previous visit${
      visit ? ` (${visit})` : ''
    }.`;
  }
  if (summary.status === 'first_visit') {
    return 'First visit on record — last-login deltas start with your next login.';
  }
  return 'Deltas arrive after the next daily KPI snapshot lands.';
}

export function LastLoginSummary({
  summary,
  loading = false,
}: {
  summary: HomeSummary | null;
  loading?: boolean;
}) {
  const { showEvidence } = useApp();
  // Malformed / absent payloads list nothing: the KPI row owns the
  // degraded-state story for this data plane, so the column says what it
  // cannot show instead of stacking a second error callout.
  const valid = Boolean(summary?.status && TITLES[summary.status] && Array.isArray(summary.highlights));
  const triggers = valid ? whyNowTriggers(summary) : [];

  return (
    <section
      className="home-answer__col home-answer__col--why login-summary"
      aria-labelledby="home-answer-why"
      aria-busy={loading || undefined}
    >
      <div className="home-answer__col-hdr">
        <h3 className="h-4" id="home-answer-why">Why now</h3>
        {valid && summary && <span className="home-answer__col-sub">{TITLES[summary.status]}</span>}
      </div>
      {loading ? (
        <ul className="home-answer__triggers" aria-hidden="true">
          {[0, 1, 2].map((index) => (
            <li key={index}><span className="skeleton home-answer__row-skeleton" /></li>
          ))}
        </ul>
      ) : triggers.length === 0 ? (
        <p className="home-answer__empty" role="status">
          Changes since your last login are not available right now.
        </p>
      ) : (
        <ul className="home-answer__triggers">
          {triggers.map((trigger) => (
            <li key={trigger.highlight.measure} className="home-answer__trigger">
              {showEvidence ? (
                <EvidenceChip
                  source={loginSummaryDrawerSource(trigger.highlight, {
                    previousVisitAt: summary?.previous_visit_at ?? null,
                  })}
                >
                  {trigger.display}
                </EvidenceChip>
              ) : (
                <strong className="login-summary__num-static">{trigger.display}</strong>
              )}{' '}
              <span className="home-answer__trigger-text">
                {trigger.joiner.trim() && `${trigger.joiner.trim()} `}
                {trigger.href ? <Link to={trigger.href}>{trigger.noun}</Link> : trigger.noun}
              </span>
            </li>
          ))}
        </ul>
      )}
      {valid && summary && !loading && (
        <p className="home-answer__note">{loginSummarySubtitle(summary)}</p>
      )}
    </section>
  );
}
