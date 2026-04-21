import type { GenieAnswer as GenieAnswerShape } from '../../types';

/**
 * GenieAnswer — renders the widened Genie payload: metric_value (big tabular
 * number), answer paragraph, table_rows (compact 3-4 col table, truncated to
 * 10), and follow_up_questions (clickable .filter chips). Used inside both
 * the floating GenieChat bubble and the Ask Genie deep-dive route, so the
 * presenter experience is identical regardless of entry point.
 */

const MAX_TABLE_ROWS = 10;
const MAX_TABLE_COLS = 4;

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
      {answer && (
        <p style={{ margin: 0, fontSize: 'var(--fs-13)', color: 'var(--text-1)', lineHeight: 1.5 }}>
          {answer}
        </p>
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
