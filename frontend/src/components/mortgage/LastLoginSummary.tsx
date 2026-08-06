import { useMemo } from 'react';
import type { HomeSummary, HomeSummaryHighlight } from '../../types';
import { Icon } from '../Icon';
import { useApp } from '../AppContext';
import { loginSummaryDrawerSource } from '../../lib/loginSummaryDrawerSource';
import { formatTimestamp } from '../../lib/time';

/**
 * "Since your last login" — the S4 personalized home summary. Renders the
 * server-composed sentence with every number as a clickable evidence
 * affordance opening the EvidenceDrawer (baseline `mip_app.kpi_snapshots`
 * row + live `mip.semantics.portfolio_headline_metric_view` reading).
 *
 * Honesty contract mirrored from the backend:
 * - the deterministic template is the source of truth; a `genie` phrasing
 *   is a validated rephrasing of the exact same tokens and is labelled;
 * - first-visit / pre-backfill states render welcome copy with live
 *   numbers only — never fake deltas.
 */

type Segment = { text: string } | { highlight: HomeSummaryHighlight };

/** Split the sentence around each highlight's exact value token so the
 * numbers become interactive without re-deriving any of them client-side.
 * The backend guarantees each token appears exactly once (deterministic
 * template and validated Genie phrasing alike); `null` means a token
 * could not be located and the caller should fall back to the structured
 * per-highlight rendering. */
export function segmentHeadline(
  headline: string,
  highlights: HomeSummaryHighlight[],
): Segment[] | null {
  const matches: Array<{ start: number; end: number; highlight: HomeSummaryHighlight }> = [];
  for (const highlight of highlights) {
    let from = 0;
    let start = -1;
    for (;;) {
      start = headline.indexOf(highlight.value_token, from);
      if (start === -1) break;
      const end = start + highlight.value_token.length;
      if (!matches.some((m) => start < m.end && end > m.start)) break;
      from = start + 1;
    }
    if (start === -1) return null;
    matches.push({ start, end: start + highlight.value_token.length, highlight });
  }
  matches.sort((a, b) => a.start - b.start);
  const segments: Segment[] = [];
  let cursor = 0;
  for (const m of matches) {
    if (m.start > cursor) segments.push({ text: headline.slice(cursor, m.start) });
    segments.push({ highlight: m.highlight });
    cursor = m.end;
  }
  if (cursor < headline.length) segments.push({ text: headline.slice(cursor) });
  return segments;
}

function SummaryNumber({
  highlight,
  summary,
}: {
  highlight: HomeSummaryHighlight;
  summary: HomeSummary;
}) {
  const { setDrawer, showEvidence } = useApp();
  const source = loginSummaryDrawerSource(highlight, {
    previousVisitAt: summary.previous_visit_at,
  });
  if (!showEvidence) {
    return <strong className="login-summary__num-static">{highlight.display}</strong>;
  }
  return (
    <button
      type="button"
      className="login-summary__num"
      onClick={() => setDrawer(source)}
      aria-label={`${highlight.display} ${highlight.label} — view source evidence`}
    >
      {highlight.display}
      <Icon name="link" size={9} className="login-summary__num-ico" />
    </button>
  );
}

const TITLES: Record<HomeSummary['status'], string> = {
  delta: 'Since your last login',
  first_visit: 'Welcome to your book',
  no_baseline: 'Welcome back',
};

function subtitleFor(summary: HomeSummary): string {
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
  const segments = useMemo(() => {
    if (!summary?.headline || !Array.isArray(summary.highlights)) return null;
    return segmentHeadline(summary.headline, summary.highlights);
  }, [summary]);

  if (loading) {
    return (
      <section
        className="surface login-summary"
        aria-busy="true"
        aria-label="Last-login summary loading"
      >
        <div className="surface__hdr surface__hdr--split">
          <div className="surface__hdr-main">
            <div className="surface__icon"><Icon name="sparkle" size={14} /></div>
            <div className="h-4">Since your last login</div>
          </div>
        </div>
        <div className="surface__body">
          <span className="skeleton login-summary__skeleton" aria-hidden="true" />
        </div>
      </section>
    );
  }

  // Malformed / absent payloads render nothing: the KPI row above already
  // owns the degraded-state story for this data plane.
  if (!summary || !summary.status || !summary.headline) return null;

  return (
    <section className="surface login-summary" aria-label={TITLES[summary.status]}>
      <div className="surface__hdr surface__hdr--split">
        <div className="surface__hdr-main">
          <div className="surface__icon"><Icon name="sparkle" size={14} /></div>
          <div>
            <div className="h-4">{TITLES[summary.status]}</div>
            <div className="muted fs-12">{subtitleFor(summary)}</div>
          </div>
        </div>
        {summary.phrasing_source === 'genie' && (
          <span
            className="chip chip--neutral"
            title="Genie rephrased the sentence; every figure is the deterministic template's exact token."
          >
            Genie-phrased · deterministic numbers
          </span>
        )}
      </div>
      <div className="surface__body">
        {segments ? (
          <p className="login-summary__narrative">
            {segments.map((segment, idx) =>
              'text' in segment ? (
                <span key={idx}>{segment.text}</span>
              ) : (
                <SummaryNumber
                  key={segment.highlight.measure}
                  highlight={segment.highlight}
                  summary={summary}
                />
              ),
            )}
          </p>
        ) : (
          // Defensive fallback: a token could not be located in the
          // sentence (should be impossible given the backend validator).
          // Render the structured highlights so every number keeps its
          // evidence affordance instead of trusting unparseable prose.
          <p className="login-summary__narrative">
            {summary.highlights.map((highlight, idx) => (
              <span key={highlight.measure}>
                {idx > 0 && ', '}
                <SummaryNumber highlight={highlight} summary={summary} />
                {' '}
                {highlight.label}
              </span>
            ))}
          </p>
        )}
      </div>
    </section>
  );
}
