import { useId, useState } from 'react';
import { Link, useInRouterContext } from 'react-router';
import { ApiError } from '../../lib/api';
import {
  refusalReportsApi,
  type RefusalReportItem,
  type RefusalReportQuestionResponse,
} from '../../lib/apiClients/refusalReports';
import { auditEventHref } from '../../lib/auditLinks';
import { formatTimestamp } from '../../lib/time';
import { Chip } from '../Primitives';
import { GENIE_REFUSAL_FAMILIES } from '../mortgage/genieRefusal';

/**
 * One refusal report row of RefusalReportsPanel (D-audit-reads-d).
 *
 * "Show question" is the ONLY path to a consented question: it calls the
 * audited read inside its click handler (the server writes a fail-closed
 * VIEW_REFUSAL_REPORT_TEXT row before it answers), never on hover, focus or
 * prefetch and never through the query cache. The text lives in this row's
 * state until "Hide question" or unmount; it is shown in a `.source-card`
 * with a Redacted chip, its expiry and Copy. deviation:refusal-reports-panel
 */

type QuestionState =
  | { status: 'hidden' }
  | { status: 'reading' }
  | { status: 'shown'; question: RefusalReportQuestionResponse; copied: boolean | null }
  | { status: 'failed'; message: string };

export const QUESTION_EXPIRED = 'This question has expired or was purged.';
export const QUESTION_NOT_RECORDED = 'The read could not be recorded, so the question is not shown.';
const QUESTION_UNREADABLE = 'The question could not be read. Please try again.';

/** The audited read. Never rejects: a failure becomes its fixed line. */
function readQuestion(reportId: string): Promise<QuestionState> {
  return refusalReportsApi.question(reportId).then(
    (question): QuestionState => ({ status: 'shown', question, copied: null }),
    (err: unknown): QuestionState => {
      const status = err instanceof ApiError ? err.status : null;
      if (status === 404) return { status: 'failed', message: QUESTION_EXPIRED };
      if (status === 503) return { status: 'failed', message: QUESTION_NOT_RECORDED };
      return { status: 'failed', message: QUESTION_UNREADABLE };
    },
  );
}

function copyText(text: string): Promise<boolean> {
  if (typeof navigator === 'undefined' || !navigator.clipboard) return Promise.resolve(false);
  return navigator.clipboard.writeText(text).then(() => true, () => false);
}

function AuditEventLink({ id }: { id: string }) {
  const inRouter = useInRouterContext();
  return inRouter ? (
    <Link className="mono" to={auditEventHref(id)}>{id}</Link>
  ) : (
    <a className="mono" href={auditEventHref(id)}>{id}</a>
  );
}

export function RefusalReportRow({ item }: { item: RefusalReportItem }) {
  const [state, setState] = useState<QuestionState>({ status: 'hidden' });
  const detailId = useId();
  const shown = state.status === 'shown';
  const detail = shown || state.status === 'failed';

  const showQuestion = () => {
    if (state.status === 'reading') return;
    setState({ status: 'reading' });
    void readQuestion(item.report_id).then(setState);
  };
  const hideQuestion = () => setState({ status: 'hidden' });
  const copyQuestion = (question: RefusalReportQuestionResponse) => {
    void copyText(question.question_text).then((copied) => {
      setState((current) => (current.status === 'shown' ? { ...current, copied } : current));
    });
  };

  return (
    <>
      <tr data-testid="refusal-report-row">
        <td className="mono fs-12">{formatTimestamp(item.reported_at, { withYear: 'auto' })}</td>
        <td>
          <Chip variant="neutral">{GENIE_REFUSAL_FAMILIES[item.refusal_reason].title}</Chip>
        </td>
        <td className="fs-12">{item.reporter}</td>
        <td className="mono fs-11">
          {item.conversation_id ?? '—'} · {item.message_id ?? '—'}
        </td>
        <td className="fs-11">{item.audit_event_id ? <AuditEventLink id={item.audit_event_id} /> : '—'}</td>
        <td>
          {item.has_text ? (
            <button
              type="button"
              className="btn btn--ghost btn--sm"
              onClick={shown ? hideQuestion : showQuestion}
              aria-expanded={shown}
              aria-controls={detail ? detailId : undefined}
              aria-busy={state.status === 'reading' || undefined}
              data-testid="refusal-report-show-question"
            >
              {shown ? 'Hide question' : 'Show question'}
            </button>
          ) : (
            <span className="muted fs-12">Hash only</span>
          )}
        </td>
      </tr>
      {detail && (
        <tr>
          <td colSpan={6} id={detailId}>
            {state.status === 'shown' ? (
              <div className="source-card" data-testid="refusal-report-question">
                <div className="chip-row">
                  {state.question.redacted && <Chip variant="warning">Redacted</Chip>}
                  <span className="muted fs-11">
                    Kept until {formatTimestamp(state.question.expires_at, { withYear: 'auto' })}
                  </span>
                </div>
                <p className="mono fs-12">{state.question.question_text}</p>
                <div className="chip-row">
                  <button type="button" className="btn btn--ghost btn--sm" onClick={() => copyQuestion(state.question)}>
                    {state.copied === true ? 'Copied' : 'Copy'}
                  </button>
                  <button type="button" className="btn btn--ghost btn--sm" onClick={hideQuestion}>
                    Hide question
                  </button>
                  {state.copied === false && (
                    <span className="muted fs-11" role="status">The browser blocked the copy.</span>
                  )}
                </div>
              </div>
            ) : (
              <p className="fs-12" role="alert">{state.message}</p>
            )}
          </td>
        </tr>
      )}
    </>
  );
}
