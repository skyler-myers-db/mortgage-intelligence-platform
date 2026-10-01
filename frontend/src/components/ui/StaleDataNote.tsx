import { Timestamp, useMinuteClock } from './Timestamp';
import './StaleDataNote.css';

/**
 * StaleDataNote — the visible marker for counts the server RETAINED after a
 * failed refresh (audit delivery-06, D-platform-process-e1 items 7-9, client
 * half). A read whose 2xx response carried `X-Data-Last-Good-At`
 * (apiClients/headers) passes that instant here; anything else passes null
 * and nothing renders, so the note clears on the first response without the
 * header. Home also passes the oldest age of a hero read whose manual refresh
 * failed over the data on screen (delivery-05, the W5a ruling).
 *
 * It never says "Refresh to try again": the server retries on its own, and
 * a Refresh beside it (FetchedAt) is a separate, explicit control.
 *
 * deviation:stale-data-note: a declared extension, the prototype's
 * `.chip--warning` (design_files/index.html:413) as an inline status line
 * (`.stale-note` only lets it wrap). Inline by design, no card; beside a
 * header it is the one-line `compact` form, so nothing below it moves.
 */

const DAY_MS = 24 * 60 * 60 * 1000;

interface StaleDataNoteProps {
  /** The last successful read behind the counts on screen (ISO or epoch ms), or null when they are current. */
  lastGoodAt: string | number | null;
  /**
   * One line beside a header (Segment Intelligence's ranked table, next to
   * FetchedAt): the age and the failure stay visible, the sentence about the
   * next refresh is read to assistive technology only, so the header row
   * keeps its height and nothing under it moves (stale-data-note.fixture.spec.ts).
   */
  compact?: boolean;
}

const NEXT_REFRESH = 'update on a later refresh once the warehouse responds.';

export function StaleDataNote({ lastGoodAt, compact = false }: StaleDataNoteProps) {
  const now = useMinuteClock();
  if (lastGoodAt === null) return null;
  const at = typeof lastGoodAt === 'number' ? lastGoodAt : Date.parse(lastGoodAt);
  if (!Number.isFinite(at)) return null;
  const age = <Timestamp value={lastGoodAt} format={now - at > DAY_MS ? 'datetime' : 'relative'} now={now} />;
  if (compact) {
    return (
      <p role="status" className="chip chip--warning stale-note stale-note--compact" data-testid="stale-data-note">
        <span className="chip__label">Refresh failed; counts from {age}.</span>
        <span className="sr-only"> The counts {NEXT_REFRESH}</span>
      </p>
    );
  }
  return (
    <p role="status" className="chip chip--warning stale-note" data-testid="stale-data-note">
      <span>
        Showing counts last read {age}. The latest refresh failed; the counts {NEXT_REFRESH}
      </span>
    </p>
  );
}
