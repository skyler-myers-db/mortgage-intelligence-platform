import type { ReactNode } from 'react';
import type { GenieAnswer as GenieAnswerShape } from '../../types';

/**
 * GenieAnswer — renders the widened Genie payload: metric_value (big tabular
 * number), answer paragraph, table_rows (compact 3-4 col table, truncated to
 * 10), and follow_up_questions (clickable .filter chips). Used inside both
 * the floating GenieChat bubble and the Ask Genie deep-dive route, so the
 * presenter experience is identical regardless of entry point.
 *
 * 2026-05-04 polish (user feedback "Genie output is weird"):
 *   - The Genie space frequently restates the question as the first
 *     sentence ("You want to see which ZIPs..."). That's filler the
 *     reader already knows — `stripQuestionRestatement` drops it.
 *   - Bold (**x**), inline code (`x`), and bullet lists ("- item")
 *     are rendered as real markup so emphasized ZIP codes / segment
 *     names actually pop instead of reading as raw asterisks.
 *   - Charts: the Genie API does support attachment-based charts but
 *     the message endpoint returns them as separate attachment IDs
 *     that have to be fetched via a follow-up call. Not wired today;
 *     the follow-up question chips below let the user pivot to a
 *     more visual question (e.g. "show as a bar chart") and the
 *     table_rows path covers the most common "list me N things" ask.
 */

const MAX_TABLE_ROWS = 10;
const MAX_TABLE_COLS = 4;

/** Phrases Genie commonly uses to restate the question before answering.
 *  Matched at the very start of the answer; the WHOLE first sentence
 *  (everything up to the first ". " or ".\n") is dropped when one of
 *  these matches. We keep the strip narrow on purpose — only drop
 *  obvious restatements, never an actual answer that happens to start
 *  with "You". */
const RESTATEMENT_LEADERS = [
  /^you want to (see|know|find out|understand)\b/i,
  /^you're (asking|looking for|interested in|curious about)\b/i,
  /^you would like to\b/i,
  /^you'd like to\b/i,
  /^you wanted to\b/i,
  /^to answer your question\b/i,
  /^based on (your question|the data|the available data|what you're asking)\b/i,
  /^let me (answer|address)\b/i,
];

export function stripQuestionRestatement(answer: string): string {
  if (!answer) return answer;
  const trimmed = answer.replace(/^\s+/, '');
  if (!RESTATEMENT_LEADERS.some((re) => re.test(trimmed))) return trimmed;
  // The leader-clause can be terminated EITHER by sentence punctuation
  // (".  You want to see X. The data shows Y.") OR by a comma
  // ("Based on your question, the data shows Y."). Try comma first,
  // then sentence-ending punctuation. If both candidates exist, take
  // whichever cuts off MORE of the leader (i.e. higher index — same
  // visible sentence, not the one in the middle of the answer body).
  const commaEnd = trimmed.search(/,\s/);
  const sentenceEnd = trimmed.search(/[.!?](\s|$)/);
  let cut = -1;
  if (commaEnd !== -1 && sentenceEnd !== -1) {
    cut = Math.min(commaEnd, sentenceEnd);
  } else if (commaEnd !== -1) {
    cut = commaEnd;
  } else if (sentenceEnd !== -1) {
    cut = sentenceEnd;
  }
  if (cut === -1) return trimmed;
  const remainder = trimmed.slice(cut + 1).replace(/^\s+/, '');
  // Capitalize the first letter of the remainder so the cleaned answer
  // reads as a proper sentence (e.g. ", the top segment is X" becomes
  // "The top segment is X").
  if (remainder.length > 0) {
    return remainder[0].toUpperCase() + remainder.slice(1);
  }
  return trimmed;
}

/** Tiny markdown renderer for the small subset Genie answers actually
 *  use: bold (**x**), inline code (`x`), bullet lists ("- " / "* "),
 *  and paragraph breaks (blank line or single newline between bullets).
 *  We do NOT pull a full markdown lib because (a) the dependency budget
 *  for one paragraph isn't justified and (b) the Genie space's output
 *  is constrained by its own instructions, so the render surface stays
 *  predictable. */
function renderInlineMd(text: string): ReactNode[] {
  const out: ReactNode[] = [];
  // Combined regex: **bold** OR `code`. We walk the matches and slice
  // text between them so the React tree doesn't lose key stability on
  // re-render.
  const re = /(\*\*([^*]+?)\*\*|`([^`]+?)`)/g;
  let last = 0;
  let match: RegExpExecArray | null;
  let key = 0;
  while ((match = re.exec(text)) !== null) {
    if (match.index > last) {
      out.push(<span key={key++}>{text.slice(last, match.index)}</span>);
    }
    if (match[2] != null) {
      out.push(<strong key={key++}>{match[2]}</strong>);
    } else if (match[3] != null) {
      out.push(
        <code
          key={key++}
          style={{
            fontFamily: 'var(--font-mono)',
            fontSize: '0.92em',
            background: 'var(--bg-3)',
            padding: '1px 4px',
            borderRadius: 3,
          }}
        >
          {match[3]}
        </code>,
      );
    }
    last = match.index + match[0].length;
  }
  if (last < text.length) {
    out.push(<span key={key++}>{text.slice(last)}</span>);
  }
  return out;
}

function MarkdownAnswer({ text }: { text: string }) {
  // Split into paragraphs / lists. A "block" is either a run of bullet
  // lines or a paragraph. Bullet lines start with "- " or "* " or "• ".
  const lines = text.split(/\r?\n/);
  type Block = { type: 'p'; text: string } | { type: 'ul'; items: string[] };
  const blocks: Block[] = [];
  for (const raw of lines) {
    const line = raw.trimEnd();
    const bullet = /^\s*([-*•])\s+(.*)$/.exec(line);
    if (bullet) {
      const item = bullet[2];
      const last = blocks[blocks.length - 1];
      if (last && last.type === 'ul') {
        last.items.push(item);
      } else {
        blocks.push({ type: 'ul', items: [item] });
      }
    } else if (line.trim() === '') {
      // blank line — close any open paragraph
      const last = blocks[blocks.length - 1];
      if (last && last.type === 'p') blocks.push({ type: 'p', text: '' });
    } else {
      const last = blocks[blocks.length - 1];
      if (last && last.type === 'p' && last.text === '') {
        last.text = line;
      } else if (last && last.type === 'p') {
        last.text = `${last.text} ${line.trim()}`;
      } else {
        blocks.push({ type: 'p', text: line });
      }
    }
  }
  return (
    <>
      {blocks
        .filter((b) => (b.type === 'p' ? b.text.length > 0 : b.items.length > 0))
        .map((b, i) =>
          b.type === 'p' ? (
            <p
              key={i}
              style={{
                margin: i === 0 ? 0 : '8px 0 0',
                fontSize: 'var(--fs-13)',
                color: 'var(--text-1)',
                lineHeight: 1.5,
              }}
            >
              {renderInlineMd(b.text)}
            </p>
          ) : (
            <ul
              key={i}
              style={{
                margin: '8px 0 0',
                paddingLeft: 18,
                fontSize: 'var(--fs-13)',
                color: 'var(--text-1)',
                lineHeight: 1.5,
              }}
            >
              {b.items.map((it, j) => (
                <li key={j}>{renderInlineMd(it)}</li>
              ))}
            </ul>
          ),
        )}
    </>
  );
}

interface GenieAnswerProps {
  payload: GenieAnswerShape;
  onFollowUp?: (q: string) => void;
  /** Compact mode (used inside the floating chat bubble). */
  dense?: boolean;
}

export function GenieAnswer({ payload, onFollowUp, dense = false }: GenieAnswerProps) {
  const { answer, metric_value, table_rows, follow_up_questions } = payload;
  const rows = Array.isArray(table_rows) ? table_rows : [];
  const visibleRows = rows.slice(0, MAX_TABLE_ROWS);
  const hiddenRows = Math.max(0, rows.length - MAX_TABLE_ROWS);
  const columns = visibleRows[0] ? Object.keys(visibleRows[0]).slice(0, MAX_TABLE_COLS) : [];
  const cleanedAnswer = answer ? stripQuestionRestatement(answer) : '';

  return (
    <div>
      {metric_value && (
        <div
          className="genie-answer__metric"
          style={dense ? { fontSize: 'var(--fs-22)' } : undefined}
        >
          {metric_value}
        </div>
      )}
      {cleanedAnswer && <MarkdownAnswer text={cleanedAnswer} />}
      {visibleRows.length > 0 && columns.length > 0 && (
        <>
          <table className="genie-answer__table">
            <thead>
              <tr>
                {columns.map((c) => (
                  <th key={c}>{humanizeKey(c)}</th>
                ))}
              </tr>
            </thead>
            <tbody>
              {visibleRows.map((row, i) => (
                <tr key={i}>
                  {columns.map((c) => {
                    const v = row[c];
                    const isNum = typeof v === 'number';
                    return (
                      <td key={c} className={isNum ? 'num' : undefined}>
                        {formatCell(v)}
                      </td>
                    );
                  })}
                </tr>
              ))}
            </tbody>
          </table>
          {hiddenRows > 0 && (
            <div className="genie-answer__more">+{hiddenRows} more row{hiddenRows === 1 ? '' : 's'}</div>
          )}
        </>
      )}
      {follow_up_questions && follow_up_questions.length > 0 && onFollowUp && (
        <div className="genie-answer__followups">
          {follow_up_questions.slice(0, 3).map((q) => (
            <button
              key={q}
              type="button"
              className="filter"
              onClick={() => onFollowUp(q)}
              style={{ textAlign: 'left' }}
            >
              <span className="filter__label">Ask</span>
              <span className="filter__value" style={{ fontFamily: 'var(--font-sans)', color: 'var(--text-1)' }}>{q}</span>
            </button>
          ))}
        </div>
      )}
    </div>
  );
}

function humanizeKey(k: string): string {
  return k.replace(/_/g, ' ').replace(/\b\w/g, (c) => c.toUpperCase());
}

function formatCell(v: unknown): string {
  if (v === null || v === undefined) return '—';
  if (typeof v === 'number') return Number.isInteger(v) ? v.toLocaleString() : v.toFixed(2);
  return String(v);
}
