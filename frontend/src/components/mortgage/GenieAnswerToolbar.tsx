import { useEffect, useState } from 'react';
import type { GenieAnswer as GenieAnswerShape } from '../../types';
import { Icon } from '../Icon';
import { formatCell, humanizeKey } from './GenieAnswer.logic';

/* ------------------------------------------------------------------------
 * Answer toolbar: Copy SQL / Copy answer (audit 2026-09-21 `genie-06`).
 *
 * `.genie-answer__toolbar` is a documented BEM extension of `.genie-answer`
 * (the prototype has no toolbar under an answer). The clipboard is written
 * with `navigator.clipboard.writeText`; when the browser denies it (no
 * permission, insecure context, an older engine) the text is shown selected
 * in a read-only field so it can still be copied by hand. Nothing here
 * downloads a file: the audited CSV export is a later wave.
 * ---------------------------------------------------------------------- */

/** Markdown the answer prose carries, flattened for a plain-text copy. */
function plainText(text: string): string {
  return text
    .replace(/\*\*(.+?)\*\*/g, '$1')
    .replace(/`([^`]+)`/g, '$1')
    .replace(/^\s*[-*]\s+/gm, '- ')
    .replace(/[ \t]+\n/g, '\n')
    .trim();
}

function rowsAsText(rows: Array<Record<string, unknown>>): string {
  if (rows.length === 0) return '';
  const columns = Object.keys(rows[0]);
  const header = columns.map(humanizeKey).join('\t');
  const body = rows.map((row) => columns.map((column) => formatCell(column, row[column])).join('\t'));
  return [header, ...body].join('\n');
}

/**
 * The whole answer as plain text: headline metric, the narrative (or the
 * summary plus every section of a deep-research sweep), and EVERY row --
 * not the capped table -- so what the user copies is the answer's data.
 */
export function answerPlainText(payload: GenieAnswerShape): string {
  const parts: string[] = [];
  const metric = payload.metric_value;
  if (metric !== null && metric !== undefined && String(metric).trim() !== '') parts.push(String(metric).trim());
  const sections = Array.isArray(payload.sections) ? payload.sections : [];
  const summary = (payload.summary ?? '').trim();
  if (sections.length > 0) {
    if (summary) parts.push(plainText(summary));
    for (const section of sections) {
      const title = (section.title || section.question || '').trim();
      const body = plainText(section.answer ?? '');
      const rows = Array.isArray(section.table_rows) ? rowsAsText(section.table_rows) : '';
      parts.push([title, body, rows].filter(Boolean).join('\n'));
    }
  } else if ((payload.answer ?? '').trim()) {
    parts.push(plainText(payload.answer));
  }
  if (sections.length === 0 && Array.isArray(payload.table_rows) && payload.table_rows.length > 0) {
    parts.push(rowsAsText(payload.table_rows));
  }
  return parts.filter(Boolean).join('\n\n');
}

/** Governed SQL of the answer, when the proof carries one. */
export function answerSql(payload: GenieAnswerShape): string | null {
  const sql = (payload.proof?.sql_query ?? payload.sql_query ?? '').trim();
  return sql.length > 0 ? sql : null;
}

/** Write `text` to the clipboard; false when the browser refused. */
export async function copyTextToClipboard(text: string): Promise<boolean> {
  try {
    const clipboard = typeof navigator === 'undefined' ? undefined : navigator.clipboard;
    if (!clipboard || typeof clipboard.writeText !== 'function') return false;
    await clipboard.writeText(text);
    return true;
  } catch {
    return false;
  }
}

type CopyTarget = 'SQL' | 'answer';

type CopyStatus =
  | { kind: 'idle' }
  | { kind: 'copied'; target: CopyTarget }
  | { kind: 'blocked'; target: CopyTarget; text: string };

const COPIED_STATUS_MS = 2_500;

export function GenieAnswerToolbar({ payload }: { payload: GenieAnswerShape }) {
  const [status, setStatus] = useState<CopyStatus>({ kind: 'idle' });
  const sql = answerSql(payload);

  useEffect(() => {
    if (status.kind !== 'copied') return undefined;
    const timer = window.setTimeout(() => setStatus({ kind: 'idle' }), COPIED_STATUS_MS);
    return () => window.clearTimeout(timer);
  }, [status]);

  const copy = async (target: CopyTarget, text: string) => {
    const ok = await copyTextToClipboard(text);
    setStatus(ok ? { kind: 'copied', target } : { kind: 'blocked', target, text });
  };

  return (
    <div className="genie-answer__toolbar" role="group" aria-label="Answer actions">
      {sql && (
        <button
          type="button"
          className="btn btn--ghost btn--sm"
          onClick={() => void copy('SQL', sql)}
          title="Copy the governed SQL behind this answer"
        >
          <Icon name="doc" size={12} />
          Copy SQL
        </button>
      )}
      <button
        type="button"
        className="btn btn--ghost btn--sm"
        onClick={() => void copy('answer', answerPlainText(payload))}
        title="Copy the answer as plain text, every row included"
      >
        <Icon name="doc" size={12} />
        Copy answer
      </button>
      {/* Visible confirmation only, deliberately NOT a live region: the
          floating panel owns exactly one persistent announcer outside its
          dialog (audit `a11y-06`), and an answer must not mount a second. */}
      <span className="genie-answer__toolbar-status" data-copy-status={status.kind}>
        {status.kind === 'copied' ? `${status.target === 'SQL' ? 'SQL' : 'Answer'} copied` : ''}
        {status.kind === 'blocked' ? 'Clipboard blocked' : ''}
      </span>
      {status.kind === 'blocked' && (
        <div className="genie-answer__copy-fallback">
          <textarea
            readOnly
            aria-label={`${status.target === 'SQL' ? 'SQL' : 'Answer'} to copy`}
            value={status.text}
            onFocus={(event) => event.currentTarget.select()}
            ref={(element) => element?.select()}
          />
          <p className="genie-answer__copy-fallback-hint">
            The browser blocked clipboard access. The text is selected above: copy it with your keyboard.
          </p>
        </div>
      )}
    </div>
  );
}
