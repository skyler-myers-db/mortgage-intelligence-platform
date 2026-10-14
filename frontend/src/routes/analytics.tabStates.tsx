import { EmptyState } from '../components/ui/EmptyState';
import { isAbortError } from '../lib/apiTransport';
import type {
  ExecutiveAnalyticsResponse,
  GeographyAnalyticsResponse,
  SegmentAnalyticsResponse,
  SignalAnalyticsResponse,
} from '../types';

/**
 * The Analytics tab's shared states (2026-09-21 audit states-09, states-v2,
 * delivery-05; deviation:analytics-fetched-at): the tab's fetch age and its
 * stale marker from the reads behind it, and the empty state for a settled,
 * measured zero. Pure over the reads' public fields; no read, no audit row.
 */

/** The fields of a tab read (useWarmingUpRetry) that these states read. */
export interface TabRead {
  data: unknown;
  error: Error | null;
  isFetching: boolean;
  dataUpdatedAt: number | null;
  manualRetry: () => void;
}

/** The oldest of the known instants (epoch ms), or null. */
export function oldestInstant(instants: readonly (number | null)[]): number | null {
  const known = instants.filter((at): at is number => typeof at === 'number' && Number.isFinite(at));
  return known.length > 0 ? Math.min(...known) : null;
}

export interface TabFreshness {
  /** The OLDEST fetch behind the tab: a restored snapshot shows its true age. */
  fetchedAt: number | null;
  isFetching: boolean;
  /** The age of data still on screen whose refresh failed, or null. */
  staleAt: number | null;
}

export function tabFreshness(reads: readonly TabRead[]): TabFreshness {
  return {
    fetchedAt: oldestInstant(reads.map((read) => read.dataUpdatedAt)),
    isFetching: reads.some((read) => read.isFetching),
    staleAt: oldestInstant(
      reads
        .filter((read) => read.data !== null && read.error !== null && !isAbortError(read.error))
        .map((read) => read.dataUpdatedAt),
    ),
  };
}

/** One Refresh for the tab: exactly one manualRetry per read behind it. */
export function refreshTab(reads: readonly TabRead[]): void {
  for (const read of reads) read.manualRetry();
}

/** A measured zero on each data tab (states-v2): the whole tab, never one panel. */
export const isEmptyExecutive = (data: ExecutiveAnalyticsResponse): boolean => data.totals.addressable_borrowers === 0;
export const isEmptyGeography = (data: GeographyAnalyticsResponse): boolean =>
  data.state_opportunities.length === 0 && data.state_avm_values.length === 0 && data.top_zips.length === 0;
export const isEmptySegments = (data: SegmentAnalyticsResponse): boolean =>
  data.overview.length === 0
  && data.counts.length === 0
  && data.average_scores.length === 0
  && data.top_segments_by_state.length === 0;
export const isEmptySignals = (data: SignalAnalyticsResponse): boolean =>
  data.evidence_daily.length === 0 && data.evidence_by_signal.length === 0 && data.evidence_examples.length === 0;

/**
 * The empty tab: the filters matched nobody (with the one recovery, Clear
 * filters), or the book itself is empty because the first refresh has not
 * landed.
 */
export function AnalyticsEmpty({ filtersActive, onClearFilters }: { filtersActive: boolean; onClearFilters: () => void }) {
  return filtersActive ? (
    <EmptyState
      cause="filtered"
      title="No borrowers match these filters."
      actions={[
        <button key="clear" type="button" className="btn btn--sm" onClick={onClearFilters}>
          Clear filters
        </button>,
      ]}
    />
  ) : (
    <EmptyState cause="day-zero" title="No borrowers yet: the first data refresh has not landed." />
  );
}
