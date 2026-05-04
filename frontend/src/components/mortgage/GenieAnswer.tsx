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

/**
 * Auto-detect "this table_rows payload is one categorical column +
 * one numeric column" — the simplest shape that's worth charting.
 * Returns {label, value} per row when chartable, else null.
 *
 * 2026-05-04 (FIX Δ3): the user wants charts on the /ask-genie deep-
 * dive view when the data calls for it (a top-N by category, a
 * per-state breakdown, etc.) without forcing a chart on every text
 * answer. Detection rules:
 *   - 2 to MAX_TABLE_COLS columns total (else the table is too wide
 *     to summarize with one bar series)
 *   - one column has ALL string values (the label axis)
 *   - the other has ALL numeric values (the bar height)
 *   - >= 2 rows (a single bar isn't a chart)
 *
 * The Genie space's `chart_spec` attachment path (Vega-Lite JSON
 * via a follow-up GET) is documented but not wired today; this
 * table-rows-shaped detector covers the common cases without that
 * extra round-trip. When chart_spec lands we can switch to it as
 * the primary path.
 */
type ChartRow = { label: string; value: number };

function inferChartFromRows(
  rows: Array<Record<string, unknown>>,
  columns: string[],
): { rows: ChartRow[]; labelCol: string; valueCol: string } | null {
  if (rows.length < 2) return null;
  if (columns.length < 2 || columns.length > MAX_TABLE_COLS) return null;
  // Walk the columns once and tag each as "all string" / "all
  // numeric" / "mixed". Skip rows where the value is null/undefined
  // — they're "missing" not "wrong type".
  const types: Record<string, 'str' | 'num' | 'mixed'> = {};
  for (const col of columns) {
    let hasStr = false;
    let hasNum = false;
    let hasMixed = false;
    for (const r of rows) {
      const v = r[col];
      if (v === null || v === undefined) continue;
      if (typeof v === 'string') hasStr = true;
      else if (typeof v === 'number' && Number.isFinite(v)) hasNum = true;
      else {
        hasMixed = true;
        break;
      }
    }
    types[col] = hasMixed || (hasStr && hasNum) ? 'mixed' : hasStr ? 'str' : 'num';
  }
  const labelCol = columns.find((c) => types[c] === 'str');
  const valueCol = columns.find((c) => types[c] === 'num');
  if (!labelCol || !valueCol) return null;
  const projected: ChartRow[] = [];
  for (const r of rows) {
    const lv = r[labelCol];
    const vv = r[valueCol];
    if (typeof lv !== 'string') continue;
    if (typeof vv !== 'number' || !Number.isFinite(vv)) continue;
    projected.push({ label: lv, value: vv });
  }
  if (projected.length < 2) return null;
  return { rows: projected, labelCol, valueCol };
}

/**
 * Inline horizontal bar chart. Dependency-free SVG so we don't pull
 * a 100KB+ chart lib. Each bar is sized relative to the max value;
 * negative values are clamped to 0 (real Genie data is counts /
 * scores / dollars — all >= 0). Truncates to 12 bars to stay
 * readable in the Ask Genie surface; the underlying table still
 * renders below for the full data.
 */
function GenieBarChart({
  data,
  labelCol,
  valueCol,
}: {
  data: ChartRow[];
  labelCol: string;
  valueCol: string;
}) {
  const MAX_BARS = 12;
  const bars = data.slice(0, MAX_BARS);
  const maxV = Math.max(1, ...bars.map((b) => b.value));
  const rowH = 22;
  const labelW = 140;
  const trackW = 240;
  const valueW = 70;
  const totalW = labelW + trackW + 12 + valueW;
  const totalH = bars.length * rowH + 28;
  return (
    <div style={{ marginTop: 12 }}>
      <div
        className="eyebrow"
        style={{ marginBottom: 6, color: 'var(--text-3)' }}
      >
        {humanizeKey(valueCol)} by {humanizeKey(labelCol)}
      </div>
      <svg
        width="100%"
        viewBox={`0 0 ${totalW} ${totalH}`}
        role="img"
        aria-label={`Bar chart: ${humanizeKey(valueCol)} by ${humanizeKey(labelCol)}`}
        style={{ maxWidth: 540, display: 'block' }}
      >
        {bars.map((b, i) => {
          const y = i * rowH + 10;
          const w = (b.value / maxV) * trackW;
          return (
            <g key={`${b.label}-${i}`}>
              <text
                x={labelW - 8}
                y={y + rowH / 2}
                textAnchor="end"
                dominantBaseline="middle"
                fontSize={11}
                fill="var(--text-2)"
                fontFamily="var(--font-sans)"
              >
                {b.label.length > 22 ? `${b.label.slice(0, 21)}…` : b.label}
              </text>
              <rect
                x={labelW}
                y={y + 4}
                width={trackW}
                height={rowH - 8}
                fill="var(--bg-3)"
                rx={3}
              />
              <rect
                x={labelW}
                y={y + 4}
                width={Math.max(2, w)}
                height={rowH - 8}
                fill="var(--accent)"
                rx={3}
              />
              <text
                x={labelW + trackW + 8}
                y={y + rowH / 2}
                dominantBaseline="middle"
                fontSize={11}
                fill="var(--text-1)"
                fontFamily="var(--font-mono)"
                fontVariant="tabular-nums"
              >
                {Number.isInteger(b.value)
                  ? b.value.toLocaleString()
                  : b.value.toFixed(2)}
              </text>
            </g>
          );
        })}
      </svg>
      {data.length > MAX_BARS && (
        <div
          style={{
            fontSize: 11,
            color: 'var(--text-3)',
            marginTop: 4,
            fontStyle: 'italic',
          }}
        >
          chart shows top {MAX_BARS}; full {data.length} rows in the table below
        </div>
      )}
    </div>
  );
}

interface GenieAnswerProps {
  payload: GenieAnswerShape;
  onFollowUp?: (q: string) => void;
  /** Compact mode (used inside the floating chat bubble). */
  dense?: boolean;
  /** Render an inline chart when the table_rows shape is chartable.
   *  Off by default so the floating GenieChat bubble stays compact;
   *  the Ask Genie deep-dive route opts in. (FIX Δ3, 2026-05-04). */
  withChart?: boolean;
}

export function GenieAnswer({
  payload,
  onFollowUp,
  dense = false,
  withChart = false,
}: GenieAnswerProps) {
  const { answer, metric_value, table_rows, follow_up_questions } = payload;
  const rows = Array.isArray(table_rows) ? table_rows : [];
  const visibleRows = rows.slice(0, MAX_TABLE_ROWS);
  const hiddenRows = Math.max(0, rows.length - MAX_TABLE_ROWS);
  const columns = visibleRows[0] ? Object.keys(visibleRows[0]).slice(0, MAX_TABLE_COLS) : [];
  const cleanedAnswer = answer ? stripQuestionRestatement(answer) : '';
  // Chart is optional: only computed when the caller opts in AND the
  // table_rows shape is chartable. Computed lazily so the floating
  // bubble (which never opts in) doesn't pay the inference cost.
  const chart = withChart ? inferChartFromRows(rows, columns) : null;

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
      {/* FIX Δ3: chart renders BEFORE the underlying table so the user
          sees the visual summary first; the table stays as the
          authoritative data source below. Only renders when withChart
          is true (Ask Genie deep-dive route only) AND the data shape
          is chartable (1 categorical + 1 numeric column). */}
      {chart && (
        <GenieBarChart
          data={chart.rows}
          labelCol={chart.labelCol}
          valueCol={chart.valueCol}
        />
      )}
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
