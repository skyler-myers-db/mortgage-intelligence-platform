/**
 * SignalStack — where several Cotality signals fire on the same borrower
 * (audit wow-stage-5), on Segment Intelligence between the segment cards and
 * the Any / All control.
 *
 * deviation:signal-stack (product). The prototype's segment row
 * (design_files/Module 0 Prototype.html seg-grid ~L1546-1551) has no
 * overlap view. This surface leads with one sentence (borrowers firing three
 * or more core signals, and how many are contactable), then the largest
 * three-signal overlap with a plain link into the Lead Queue's
 * `segment_mode=all` intersection. "Show combinations" reveals an UpSet strip
 * (bars over a dot matrix) and its table alternative. Whole book, six core
 * segments, never narrowed by the page filters below it; built from the
 * prototype's `.surface` / `.tbl` vocabulary, tokens and `--seg-*` colours.
 *
 * Reads GET /api/segments/combinations only (audit-free, never prefetched).
 * Links are plain navigations: nothing here reads the Lead Queue.
 */
import { useId, useState } from 'react';
import { Link } from 'react-router';
import { segmentCombinationsApi } from '../../lib/apiClients/segmentCombinations';
import { formatCount } from '../../lib/formatters';
import { queryKeys } from '../../lib/queryKeys';
import { useWarmingUpRetry } from '../../lib/useWarmingUpRetry';
import type { SegmentCombination, SegmentCombinationResponse } from '../../types/segmentCombinations';
import { EvidenceChip, SurfaceTitle } from '../Primitives';
import { AsyncStatus } from '../ui/AsyncState';
import {
  SIGNAL_STACK_CORE,
  inclusive,
  largestTriple,
  leadQueueHref,
  signalNames,
  signalStackEvidenceSource,
  threePlus,
  upsetColumns,
} from './signalStack.logic';
import { safeSegmentName } from '../../lib/segmentMetadata';
import './SignalStack.css';

const FOOTNOTE =
  'Whole book, six core segments; not narrowed by the filters below. The Lead Queue lists only contact-eligible borrowers scoring 50 or more.';
const NOT_BUILT =
  'The signal stack is not built yet: the gold refresh job builds it (deploy or Admin > Data operations).';

const contactableText = (count: number | null) =>
  count === null ? 'contactable count unavailable' : `${formatCount(count)} contactable`;

export function SignalStack() {
  const query = useWarmingUpRetry<SegmentCombinationResponse>(segmentCombinationsApi.combinations, {
    queryKey: queryKeys.segments(['combinations']),
  });
  const titleId = useId();
  const data = query.data;
  const source = signalStackEvidenceSource(data?.provenance ?? null);
  const loading = data === null && query.warmingUp === null && query.error === null;
  return (
    <section className="surface signal-stack" aria-labelledby={titleId} aria-busy={loading}>
      <div className="surface__hdr">
        <SurfaceTitle id={titleId}>Signal stack</SurfaceTitle>
        <EvidenceChip source={source}>{source.short}</EvidenceChip>
      </div>
      <div className="signal-stack__body">
        {data === null ? (
          loading ? (
            <p className="signal-stack__status">Loading the signal stack…</p>
          ) : (
            <AsyncStatus query={query} subject="Signal stack" compact />
          )
        ) : !data.built ? (
          <p className="signal-stack__status" role="status">{NOT_BUILT}</p>
        ) : (
          <SignalStackBody rows={data.combinations} />
        )}
      </div>
    </section>
  );
}

function SignalStackBody({ rows }: { rows: SegmentCombination[] }) {
  const [open, setOpen] = useState(false);
  const [asTable, setAsTable] = useState(false);
  const panelId = useId();
  const stacked = threePlus(rows);
  const triple = largestTriple(rows);
  if (stacked.addressable === 0) {
    return <p className="signal-stack__headline">No borrower fires three or more signals in this refresh.</p>;
  }
  return (
    <>
      <p className="signal-stack__headline">
        <strong className="signal-stack__lead">
          {stacked.addressable === 1
            ? '1 borrower fires three or more signals at once.'
            : `${formatCount(stacked.addressable)} borrowers fire three or more signals at once.`}
        </strong>{' '}
        {stacked.contactable === null
          ? 'Contactable count unavailable.'
          : `${formatCount(stacked.contactable)} of them are contactable.`}
      </p>
      {triple && (
        <p className="signal-stack__overlap">
          Largest overlap: {signalNames(triple.codes)}: {formatCount(triple.addressable)} borrowers carry all three (
          {contactableText(triple.contactable)}).{' '}
          <Link className="signal-stack__link" to={leadQueueHref(triple.codes)}>
            Open in Lead Queue
          </Link>
        </p>
      )}
      <p className="signal-stack__footnote">{FOOTNOTE}</p>
      <div className="signal-stack__actions">
        <button
          type="button"
          className="btn btn--ghost btn--sm"
          aria-expanded={open}
          aria-controls={panelId}
          onClick={() => setOpen((value) => !value)}
        >
          {open ? 'Hide combinations' : 'Show combinations'}
        </button>
        {open && (
          <button type="button" className="btn btn--ghost btn--sm" onClick={() => setAsTable((value) => !value)}>
            {asTable ? 'View as chart' : 'View as table'}
          </button>
        )}
      </div>
      <div id={panelId} className="signal-stack__panel" hidden={!open}>
        {open && (asTable ? <SignalStackTable rows={rows} /> : <SignalStackUpset rows={rows} />)}
      </div>
    </>
  );
}

/** The column's accessible name: its signals, both counts and the contactable subset of "at least". */
function columnLabel(rows: readonly SegmentCombination[], row: SegmentCombination): string {
  const atLeast = inclusive(rows, row.segment_codes);
  return `${signalNames(row.segment_codes)}: ${formatCount(row.addressable)} borrowers carry exactly these signals, `
    + `${formatCount(atLeast.addressable)} carry at least these, ${contactableText(atLeast.contactable)}. Open in Lead Queue`;
}

function SignalStackUpset({ rows }: { rows: SegmentCombination[] }) {
  const columns = upsetColumns(rows);
  const max = Math.max(1, ...columns.map((row) => row.addressable));
  return (
    <div className="signal-stack__upset">
      <ul className="signal-stack__legend" aria-hidden="true">
        {SIGNAL_STACK_CORE.map((code) => (
          <li key={code} className="signal-stack__legend-row">{safeSegmentName(code) ?? code}</li>
        ))}
      </ul>
      <ol className="signal-stack__columns" aria-label="Combinations of two or more signals, largest first">
        {columns.map((row) => (
          <li key={row.segment_codes.join('+')} className="signal-stack__column">
            <Link className="signal-stack__column-link" to={leadQueueHref(row.segment_codes)} aria-label={columnLabel(rows, row)}>
              <span className="signal-stack__count" aria-hidden="true">{formatCount(row.addressable)}</span>
              <span className="signal-stack__bar-track" aria-hidden="true">
                <span className="signal-stack__bar" style={{ blockSize: `${(row.addressable / max) * 100}%` }} />
              </span>
              <span className="signal-stack__dots" aria-hidden="true">
                {SIGNAL_STACK_CORE.map((code) => (
                  <span
                    key={code}
                    className={`signal-stack__dot signal-stack__dot--${code}${row.segment_codes.includes(code) ? ' is-on' : ''}`}
                  />
                ))}
              </span>
            </Link>
          </li>
        ))}
      </ol>
    </div>
  );
}

function SignalStackTable({ rows }: { rows: SegmentCombination[] }) {
  const exactTotal = rows.reduce((total, row) => total + row.addressable, 0);
  return (
    <div className="signal-stack__table-wrap">
      <table className="tbl signal-stack__table">
        <caption className="signal-stack__caption">Every exact combination of the six core signals, whole book</caption>
        <thead>
          <tr>
            <th scope="col">Signals</th>
            <th scope="col" className="tbl-cell--right">Exactly these</th>
            <th scope="col" className="tbl-cell--right">At least these</th>
            <th scope="col" className="tbl-cell--right">Contactable (at least)</th>
            <th scope="col">Lead Queue</th>
          </tr>
        </thead>
        <tbody>
          {rows.map((row) => {
            const atLeast = inclusive(rows, row.segment_codes);
            const names = signalNames(row.segment_codes);
            return (
              <tr key={row.segment_codes.join('+')}>
                <th scope="row">{names}</th>
                <td className="num tbl-cell--right">{formatCount(row.addressable)}</td>
                <td className="num tbl-cell--right">{formatCount(atLeast.addressable)}</td>
                <td className="num tbl-cell--right">{formatCount(atLeast.contactable)}</td>
                <td>
                  <Link className="signal-stack__link" to={leadQueueHref(row.segment_codes)} aria-label={`Open ${names} in Lead Queue`}>
                    Open
                  </Link>
                </td>
              </tr>
            );
          })}
        </tbody>
        <tfoot>
          <tr>
            <th scope="row">All combinations ({formatCount(rows.length)})</th>
            <td className="num tbl-cell--right" data-testid="signal-stack-total">{formatCount(exactTotal)}</td>
            <td colSpan={3} className="signal-stack__note">Each borrower counts once, in the row of its exact signals.</td>
          </tr>
        </tfoot>
      </table>
    </div>
  );
}
