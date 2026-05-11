import { useEffect, useState, type ReactNode } from 'react';
import { createPortal } from 'react-dom';
import type {
  GenieActionSuggestion,
  GenieAnswer as GenieAnswerShape,
  GenieVisualization,
} from '../../types';
import { Icon } from '../Icon';
import { Chip, EvidenceChip } from '../Primitives';
import { drawerForAsset } from '../../lib/drawerSources';
import { useApp, type DrawerSource } from '../AppContext';

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

interface UsaSvgMapLocation { name: string; id: string; path: string }
interface UsaSvgMap { label: string; viewBox: string; locations: UsaSvgMapLocation[] }

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
          className="inline-code"
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
              className={`genie-md-p ${i === 0 ? 'genie-md-p--first' : ''}`}
            >
              {renderInlineMd(b.text)}
            </p>
          ) : (
            <ul
              key={i}
              className="genie-md-list"
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
 * Auto-detect a chartable payload from structured `table_rows` only.
 *
 * Genie prose is rendered as prose. Visualizations are allowed only when the
 * backend has returned query rows or an explicit visualization spec, so demo
 * charts remain tied to SQL-backed data instead of parsed answer wording.
 */
export type ChartRow = { label: string; value: number };

export interface InferredChart {
  rows: ChartRow[];
  labelCol: string;
  valueCol: string;
  source: 'table_rows';
}

const IDENTIFIER_COLUMN_PATTERNS = [
  /^zip(code)?$/i,
  /^zip_code$/i,
  /^postal(_code)?$/i,
  /^fips(_\d+)?$/i,
  /^county_fips(_5)?$/i,
  /^cbsa(_code)?$/i,
  /^msa_cbsa_code$/i,
  /^census_tract$/i,
  /^tract$/i,
  /(^|_)id$/i,
  /^id$/i,
  /^clip$/i,
];

const VALUE_COLUMN_PRIORITY = [
  'borrowers',
  'borrower_count',
  'count',
  'marketable_borrowers',
  'addressable_borrowers',
  'in_the_money_borrowers',
  'high_opportunity_borrowers',
  'opportunities',
  'opportunity_count',
  'avg_score',
  'average_score',
  'opportunity_score',
  'approval_rate',
  'conversion_rate',
  'rate_spread_bps',
  'equity_pct',
];

function isIdentifierColumn(column: string): boolean {
  return IDENTIFIER_COLUMN_PATTERNS.some((pattern) => pattern.test(column));
}

function coerceNumber(value: unknown): number | null {
  if (typeof value === 'number' && Number.isFinite(value)) return value;
  if (typeof value === 'string') {
    const cleaned = value.replace(/[$,%]/g, '').replace(/,/g, '').trim();
    if (!cleaned) return null;
    const n = Number(cleaned);
    return Number.isFinite(n) ? n : null;
  }
  return null;
}

function coerceMeasure(value: unknown, column: string): number | null {
  if (isIdentifierColumn(column)) return null;
  return coerceNumber(value);
}

function formatIdentifier(column: string, value: unknown): string {
  if (value === null || value === undefined) return '—';
  const raw = String(value).trim();
  if (/^zip(code)?$|^zip_code$|^postal(_code)?$/i.test(column)) {
    const digits = raw.replace(/\D/g, '');
    if (digits.length > 0 && digits.length <= 5) return digits.padStart(5, '0');
  }
  if (/^(fips|fips_5|county_fips|county_fips_5|cbsa_code|msa_cbsa_code)$/i.test(column)) {
    const digits = raw.replace(/\D/g, '');
    if (digits.length > 0 && digits.length <= 5) return digits.padStart(5, '0');
  }
  return raw;
}

function chooseValueColumn(columns: string[], types: Record<string, 'str' | 'num' | 'mixed'>): string | null {
  const candidates = columns.filter((col) => types[col] === 'num' && !isIdentifierColumn(col));
  if (candidates.length === 0) return null;
  for (const preferred of VALUE_COLUMN_PRIORITY) {
    const found = candidates.find((col) => col.toLowerCase() === preferred);
    if (found) return found;
  }
  return candidates[0];
}

function chartFromColumns(
  rows: Array<Record<string, unknown>>,
  labelCol: string,
  valueCol: string,
): InferredChart | null {
  const projected: ChartRow[] = [];
  for (const r of rows) {
    const vv = coerceMeasure(r[valueCol], valueCol);
    if (vv === null) continue;
    projected.push({ label: formatIdentifier(labelCol, r[labelCol]), value: vv });
  }
  if (projected.length < 2) return null;
  return { rows: projected, labelCol, valueCol, source: 'table_rows' };
}

function chartFromVisualization(
  rows: Array<Record<string, unknown>>,
  viz: GenieVisualization | null,
): InferredChart | null {
  if (!viz?.x || !viz.y) return null;
  const columns = new Set(Object.keys(rows[0] ?? {}));
  if (!columns.has(viz.x) || !columns.has(viz.y)) return null;
  return chartFromColumns(rows, viz.x, viz.y);
}

export function inferChartFromRows(
  rows: Array<Record<string, unknown>>,
  columns: string[],
): InferredChart | null {
  if (rows.length < 2) return null;
  if (columns.length < 2) return null;
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
      if (isIdentifierColumn(col)) {
        hasStr = true;
        continue;
      }
      if (coerceNumber(v) !== null) hasNum = true;
      else if (typeof v === 'string') hasStr = true;
      else {
        hasMixed = true;
        break;
      }
    }
    types[col] = hasMixed || (hasStr && hasNum) ? 'mixed' : hasStr ? 'str' : 'num';
  }
  const labelCol = columns.find((c) => types[c] === 'str');
  const valueCol = chooseValueColumn(columns, types);
  if (!labelCol || !valueCol) return null;
  return chartFromColumns(rows, labelCol, valueCol);
}

const STATE_NAME_TO_CODE: Record<string, string> = {
  alabama: 'AL', alaska: 'AK', arizona: 'AZ', arkansas: 'AR',
  california: 'CA', colorado: 'CO', connecticut: 'CT', delaware: 'DE',
  florida: 'FL', georgia: 'GA', hawaii: 'HI', idaho: 'ID',
  illinois: 'IL', indiana: 'IN', iowa: 'IA', kansas: 'KS',
  kentucky: 'KY', louisiana: 'LA', maine: 'ME', maryland: 'MD',
  massachusetts: 'MA', michigan: 'MI', minnesota: 'MN', mississippi: 'MS',
  missouri: 'MO', montana: 'MT', nebraska: 'NE', nevada: 'NV',
  'new hampshire': 'NH', 'new jersey': 'NJ', 'new mexico': 'NM',
  'new york': 'NY', 'north carolina': 'NC', 'north dakota': 'ND',
  ohio: 'OH', oklahoma: 'OK', oregon: 'OR', pennsylvania: 'PA',
  'rhode island': 'RI', 'south carolina': 'SC', 'south dakota': 'SD',
  tennessee: 'TN', texas: 'TX', utah: 'UT', vermont: 'VT',
  virginia: 'VA', washington: 'WA', 'west virginia': 'WV',
  wisconsin: 'WI', wyoming: 'WY',
};

function normalizeState(value: unknown): string | null {
  if (typeof value !== 'string') return null;
  const cleaned = value.trim();
  if (/^[A-Za-z]{2}$/.test(cleaned)) return cleaned.toUpperCase();
  return STATE_NAME_TO_CODE[cleaned.toLowerCase()] ?? null;
}

function level(value: number, max: number): 1 | 2 | 3 | 4 {
  if (max <= 0 || value <= 0) return 1;
  const pct = value / max;
  if (pct >= 0.75) return 4;
  if (pct >= 0.45) return 3;
  if (pct >= 0.2) return 2;
  return 1;
}

function pickPlan(
  payload: GenieAnswerShape,
  rows: Array<Record<string, unknown>>,
  columns: string[],
): { kind: string; chart: InferredChart | null; viz: GenieVisualization | null } {
  const viz = payload.visualization ?? null;
  const chart = chartFromVisualization(rows, viz) ?? inferChart(rows, columns);
  if (viz?.kind) return { kind: viz.kind, chart, viz };
  if (rows.some((r) => typeof r.borrower_id === 'string')) return { kind: 'borrower_list', chart, viz };
  return { kind: chart ? 'bar' : rows.length > 0 ? 'table' : 'none', chart, viz };
}

/**
 * Combined chart inference. Future: if the backend starts shipping a
 * `chart_spec` field on GenieMessageResponse (Vega-Lite from a Genie
 * attachment), prefer that as the highest-fidelity path because it
 * lets Genie pick the chart type, not us.
 */
function inferChart(
  rows: Array<Record<string, unknown>>,
  columns: string[],
): InferredChart | null {
  return inferChartFromRows(rows, columns);
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
    <div className="genie-chart">
      <div className="eyebrow genie-chart__title">
        {humanizeKey(valueCol)} by {humanizeKey(labelCol)}
      </div>
      <svg
        width="100%"
        viewBox={`0 0 ${totalW} ${totalH}`}
        role="img"
        aria-label={`Bar chart: ${humanizeKey(valueCol)} by ${humanizeKey(labelCol)}`}
        className="genie-chart__svg"
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
        <div className="genie-chart__more">
          chart shows top {MAX_BARS}; full {data.length} rows in the table below
        </div>
      )}
    </div>
  );
}

function GenieLineChart({ data, labelCol, valueCol }: { data: ChartRow[]; labelCol: string; valueCol: string }) {
  const points = data.slice(0, 24);
  const maxV = Math.max(1, ...points.map((p) => p.value));
  const minV = Math.min(0, ...points.map((p) => p.value));
  const width = 520;
  const height = 180;
  const span = Math.max(1, maxV - minV);
  const path = points
    .map((p, i) => {
      const x = points.length === 1 ? width / 2 : (i / (points.length - 1)) * width;
      const y = height - ((p.value - minV) / span) * height;
      return `${i === 0 ? 'M' : 'L'}${x.toFixed(1)},${y.toFixed(1)}`;
    })
    .join(' ');
  return (
    <div className="genie-chart">
      <div className="eyebrow genie-chart__title">
        {humanizeKey(valueCol)} over {humanizeKey(labelCol)}
      </div>
      <svg className="genie-chart__svg" viewBox={`0 0 ${width} ${height + 36}`} role="img" aria-label={`Line chart: ${humanizeKey(valueCol)} over ${humanizeKey(labelCol)}`}>
        <path d={path} className="genie-line__path" />
        {points.map((p, i) => {
          const x = points.length === 1 ? width / 2 : (i / (points.length - 1)) * width;
          const y = height - ((p.value - minV) / span) * height;
          return <circle key={`${p.label}-${i}`} cx={x} cy={y} r="3" className="genie-line__dot" />;
        })}
        {points[0] && <text x="0" y={height + 24} className="genie-line__axis">{points[0].label}</text>}
        {points[points.length - 1] && <text x={width} y={height + 24} textAnchor="end" className="genie-line__axis">{points[points.length - 1].label}</text>}
      </svg>
    </div>
  );
}

function GenieMapChart({
  rows,
  x,
  y,
}: {
  rows: Array<Record<string, unknown>>;
  x?: string | null;
  y?: string | null;
}) {
  const [usaMap, setUsaMap] = useState<UsaSvgMap | null>(null);
  useEffect(() => {
    let live = true;
    import('@svg-maps/usa').then((mod) => {
      if (live) setUsaMap(mod.default as UsaSvgMap);
    });
    return () => {
      live = false;
    };
  }, []);
  if (!x || !y) return null;
  const values = new Map<string, number>();
  for (const row of rows) {
    const state = normalizeState(row[x]);
    const value = coerceNumber(row[y]);
    if (!state || value === null) continue;
    values.set(state, value);
  }
  if (values.size === 0) return null;
  const maxV = Math.max(...values.values(), 1);
  if (!usaMap) {
    return (
      <div className="genie-map">
        <div className="eyebrow genie-chart__title">
          {humanizeKey(y)} by {humanizeKey(x)}
        </div>
      </div>
    );
  }
  return (
    <div className="genie-map">
      <div className="eyebrow genie-chart__title">
        {humanizeKey(y)} by {humanizeKey(x)}
      </div>
      <svg viewBox={usaMap.viewBox} className="genie-map__svg" role="img" aria-label={`Map: ${humanizeKey(y)} by state`}>
        {usaMap.locations.map((location) => {
          const code = location.id.toUpperCase();
          const value = values.get(code) ?? 0;
          return (
            <path
              key={location.id}
              d={location.path}
              className={`genie-map__region lvl-${level(value, maxV)} ${value > 0 ? 'has-data' : ''}`}
              aria-label={`${location.name}: ${value.toLocaleString()}`}
            />
          );
        })}
      </svg>
      <div className="genie-map__legend">
        {Array.from(values.entries())
          .sort((a, b) => b[1] - a[1])
          .slice(0, 6)
          .map(([state, value]) => (
            <span key={state} className="genie-map__legend-item">
              <span className={`genie-map__dot lvl-${level(value, maxV)}`} />
              {state} {value.toLocaleString()}
            </span>
          ))}
      </div>
    </div>
  );
}

function GenieBorrowerList({ rows }: { rows: Array<Record<string, unknown>> }) {
  const borrowers = rows
    .filter((r) => typeof r.borrower_id === 'string')
    .slice(0, 10);
  if (borrowers.length === 0) return null;
  return (
    <div className="genie-board">
      <div className="eyebrow genie-chart__title">Borrower drill-down</div>
      <div className="genie-board__grid">
        {borrowers.map((row) => {
          const id = String(row.borrower_id);
          const score = coerceNumber(row.opportunity_score ?? row.score);
          return (
            <a key={id} className="genie-board__card" href={`/borrower-360/${encodeURIComponent(id)}`}>
              <div className="genie-board__title">{id}</div>
              <div className="genie-board__meta">
                {[row.city, row.state, row.zip].filter(Boolean).join(', ') || 'Open borrower evidence'}
              </div>
              {score !== null && <div className="genie-board__value">{score.toLocaleString()}</div>}
            </a>
          );
        })}
      </div>
    </div>
  );
}

function GenieStrategyBoard({
  rows,
  x,
  y,
}: {
  rows: Array<Record<string, unknown>>;
  x?: string | null;
  y?: string | null;
}) {
  const label = x ?? Object.keys(rows[0] ?? {}).find((c) => typeof rows[0]?.[c] === 'string');
  const value = y ?? Object.keys(rows[0] ?? {}).find((c) => coerceNumber(rows[0]?.[c]) !== null);
  if (!label || !value || rows.length === 0) return null;
  return (
    <div className="genie-board">
      <div className="eyebrow genie-chart__title">Strategy board</div>
      <div className="genie-board__grid">
        {rows.slice(0, 6).map((row, i) => {
          const title = formatCell(label, row[label]);
          const metric = coerceNumber(row[value]);
          const offer = row.recommended_offer ?? row.offer_code ?? row.product_label;
          const segment = row.segment ?? row.segment_code ?? row.top_segment;
          return (
            <div key={`${title}-${i}`} className="genie-board__card">
              <div className="genie-board__title">{title}</div>
              <div className="genie-board__meta">
                {[segment, offer].filter(Boolean).map(String).join(' · ') || humanizeKey(value)}
              </div>
              {metric !== null && <div className="genie-board__value">{metric.toLocaleString()}</div>}
            </div>
          );
        })}
      </div>
    </div>
  );
}

function GenieProofPanel({
  payload,
  onOpenSource,
}: {
  payload: GenieAnswerShape;
  onOpenSource: (source: DrawerSource) => void;
}) {
  const proof = payload.proof;
  if (!proof) return null;
  const assets = proof.source_assets ?? payload.trusted_assets ?? [];
  return (
    <div className="genie-proof" role="region" aria-label="Genie proof">
      <div className="genie-proof__grid">
        <div>
          <div className="eyebrow">Trust</div>
          <div className={proof.trusted ? 'chip chip--success' : 'chip chip--warning'}>
            {proof.trusted ? 'Trusted SELECT on curated assets' : 'Review required'}
          </div>
        </div>
        <div>
          <div className="eyebrow">Rows</div>
          <div className="genie-proof__value">{proof.row_count ?? payload.row_count ?? 0}</div>
        </div>
        <div>
          <div className="eyebrow">Latency</div>
          <div className="genie-proof__value">{proof.elapsed_ms ? `${proof.elapsed_ms} ms` : '—'}</div>
        </div>
      </div>
      {assets.length > 0 && (
        <div className="genie-proof__section">
          <div className="eyebrow">Source UC assets</div>
          <div className="chip-row">
            {assets.map((asset) => {
              const drawer = drawerForAsset(asset);
              return drawer ? (
                <EvidenceChip key={asset} source={drawer} onClick={() => onOpenSource(drawer)}>
                  {asset}
                </EvidenceChip>
              ) : (
                <Chip key={asset} variant="neutral" title={`Source: ${asset}`}>
                  {asset}
                </Chip>
              );
            })}
          </div>
        </div>
      )}
      {proof.data_freshness && proof.data_freshness.length > 0 && (
        <div className="genie-proof__section">
          <div className="eyebrow">Data freshness</div>
          {proof.data_freshness.map((f) => (
            <div key={`${f.asset}-${f.refreshed_at ?? f.status}`} className="genie-proof__line">
              <span>{f.asset}</span>
              <span>{f.refreshed_at ?? f.status}</span>
            </div>
          ))}
        </div>
      )}
      {proof.filters && proof.filters.length > 0 && (
        <div className="genie-proof__section">
          <div className="eyebrow">Filters applied</div>
          {proof.filters.map((filter) => <code key={filter} className="genie-proof__sql">{filter}</code>)}
        </div>
      )}
      {proof.known_data_gaps && proof.known_data_gaps.length > 0 && (
        <div className="genie-proof__section">
          <div className="eyebrow">Known data gaps</div>
          {proof.known_data_gaps.map((gap) => <div key={gap} className="genie-proof__gap">{gap}</div>)}
        </div>
      )}
      {proof.reasoning_trace && proof.reasoning_trace.length > 0 && (
        <div className="genie-proof__section">
          <div className="eyebrow">Genie query trace</div>
          <div className="genie-proof__trace">
            {proof.reasoning_trace.slice(0, 4).map((step, i) => (
              <div key={`${step.kind}-${i}`} className="genie-proof__trace-step">
                <div className="genie-proof__trace-kind">
                  {humanizeKey(step.kind.replace(/^THOUGHT_TYPE_/, '').toLowerCase())}
                </div>
                <div className="genie-proof__trace-content">{step.content}</div>
              </div>
            ))}
          </div>
        </div>
      )}
      {proof.sql_query && (
        <div className="genie-proof__section">
          <div className="eyebrow">Generated SQL</div>
          <pre className="genie-proof__sql">{proof.sql_query}</pre>
        </div>
      )}
    </div>
  );
}

interface GenieAnswerProps {
  payload: GenieAnswerShape;
  onFollowUp?: (q: string) => void;
  onAction?: (action: GenieActionSuggestion) => void | Promise<void>;
  /** Compact mode (used inside the floating chat bubble). */
  dense?: boolean;
  /** Render an inline chart when the table_rows shape is chartable.
   *  Off by default so the floating GenieChat bubble stays compact;
   *  the Ask Genie deep-dive route opts in. (FIX Δ3, 2026-05-04). */
  withChart?: boolean;
}

function actionPreview(action: GenieActionSuggestion): string[] {
  const criteria = action.criteria ?? {};
  const filters = criteria.result_filters && typeof criteria.result_filters === 'object'
    ? criteria.result_filters as Record<string, unknown>
    : {};
  const preview: string[] = [];
  const zips = Array.isArray(filters.zips) ? filters.zips : [];
  if (zips.length > 0) preview.push(`${zips.length} ZIP${zips.length === 1 ? '' : 's'}: ${zips.slice(0, 5).join(', ')}${zips.length > 5 ? '…' : ''}`);
  const states = Array.isArray(filters.states) ? filters.states : [];
  if (states.length > 0) preview.push(`States: ${states.join(', ')}`);
  const segments = Array.isArray(filters.segment_codes) ? filters.segment_codes : [];
  if (segments.length > 0) preview.push(`Segments: ${segments.join(', ')} (${filters.segment_mode === 'all' ? 'all' : 'any'})`);
  if (typeof filters.target_lender_ref === 'string' && filters.target_lender_ref.length > 0) {
    preview.push(`Target lien holder: ${filters.target_lender_ref}`);
  }
  if (action.borrower_ids && action.borrower_ids.length > 0) {
    preview.push(`${action.borrower_ids.length} borrower${action.borrower_ids.length === 1 ? '' : 's'} bound by ID`);
  }
  if (typeof criteria.row_count === 'number') preview.push(`${criteria.row_count.toLocaleString()} result row${criteria.row_count === 1 ? '' : 's'}`);
  return preview;
}

export function GenieAnswer({
  payload,
  onFollowUp,
  onAction,
  dense = false,
  withChart = false,
}: GenieAnswerProps) {
  const { answer, metric_value, table_rows, follow_up_questions, actions } = payload;
  const { setDrawer } = useApp();
  const [showProof, setShowProof] = useState(false);
  const [confirmActionId, setConfirmActionId] = useState<string | null>(null);
  const [pendingActionId, setPendingActionId] = useState<string | null>(null);
  const rows = Array.isArray(table_rows) ? table_rows : [];
  const visibleRows = rows.slice(0, MAX_TABLE_ROWS);
  const hiddenRows = Math.max(0, rows.length - MAX_TABLE_ROWS);
  const columns = visibleRows[0] ? Object.keys(visibleRows[0]).slice(0, MAX_TABLE_COLS) : [];
  const chartColumns = rows[0] ? Object.keys(rows[0]) : [];
  const cleanedAnswer = answer ? stripQuestionRestatement(answer) : '';
  // Chart is optional and only computed from structured table_rows.
  // Answer prose is never parsed into visualization data.
  const plan = withChart ? pickPlan(payload, rows, chartColumns) : { kind: 'none', chart: null, viz: null };
  const chart = plan.chart;

  return (
    <div>
      {metric_value !== null && metric_value !== undefined && metric_value !== '' && (
        <div
          className={`genie-answer__metric ${dense ? 'genie-answer__metric--dense' : ''}`}
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
      {plan.kind === 'strategy_board' && (
        <GenieStrategyBoard rows={rows} x={plan.viz?.x} y={plan.viz?.y} />
      )}
      {plan.kind === 'borrower_list' && <GenieBorrowerList rows={rows} />}
      {plan.kind === 'map' && (
        <GenieMapChart rows={rows} x={plan.viz?.x ?? chart?.labelCol} y={plan.viz?.y ?? chart?.valueCol} />
      )}
      {plan.kind === 'line' && chart && (
        <GenieLineChart data={chart.rows} labelCol={chart.labelCol} valueCol={chart.valueCol} />
      )}
      {(plan.kind === 'bar' || plan.kind === 'funnel' || (!['strategy_board', 'borrower_list', 'map', 'line'].includes(plan.kind) && chart)) && chart && (
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
                    const isNum = typeof v === 'number' && !isIdentifierColumn(c);
                    return (
                      <td key={c} className={isNum ? 'num' : undefined}>
                        {formatCell(c, v)}
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
      {payload.proof && (
        <div className="genie-proof-toggle">
          <button type="button" className="btn btn--ghost btn--sm" onClick={() => setShowProof((cur) => !cur)}>
            <Icon name="audit" size={12} />
            {showProof ? 'Hide proof' : 'Show proof'}
          </button>
          {payload.proof.trusted !== undefined && (
            <span className={payload.proof.trusted ? 'chip chip--success' : 'chip chip--warning'}>
              {payload.proof.trusted ? 'trusted' : 'review'}
            </span>
          )}
        </div>
      )}
      {payload.proof && showProof && typeof document !== 'undefined' && createPortal(
        <>
          <div
            className="drawer-scrim is-open"
            onClick={() => setShowProof(false)}
            aria-hidden="true"
          />
          <aside
            className="drawer genie-proof-drawer is-open"
            role="dialog"
            aria-modal="true"
            aria-label="Genie answer proof"
          >
            <div className="drawer__hdr">
              <div className="drawer__source-icon">
                <Icon name="audit" size={16} />
              </div>
              <div>
                <div className="drawer__title">Answer proof</div>
                <div className="drawer__subtitle">{payload.question_hash ?? payload.message_id ?? 'Genie result'}</div>
              </div>
              <button
                className="drawer__close"
                onClick={() => setShowProof(false)}
                aria-label="Close Genie proof"
                type="button"
              >
                <Icon name="close" size={14} />
              </button>
            </div>
            <div className="drawer__body">
              <GenieProofPanel
                payload={payload}
                onOpenSource={(source) => {
                  setShowProof(false);
                  setDrawer(source);
                }}
              />
            </div>
          </aside>
        </>,
        document.body,
      )}
      {follow_up_questions && follow_up_questions.length > 0 && onFollowUp && (
        <div className="genie-answer__followups">
          {follow_up_questions.slice(0, 3).map((q) => (
            <button
              key={q}
              type="button"
              className="filter filter--question"
              onClick={() => onFollowUp(q)}
            >
              <span className="filter__label">Ask</span>
              <span className="filter__value filter__value--question">{q}</span>
            </button>
          ))}
        </div>
      )}
      {actions && actions.length > 0 && onAction && (
        <div className="genie-actions">
          <div className="eyebrow">Governed actions</div>
          {actions.slice(0, 5).map((action) => {
            const confirming = confirmActionId === action.id;
            const pending = pendingActionId === action.id;
            const preview = actionPreview(action);
            return (
              <div key={action.id} className="genie-action">
                <div className="genie-action__body">
                  <div className="genie-action__label">{action.label}</div>
                  <div className="genie-action__desc">{action.description}</div>
                  {preview.length > 0 && (
                    <div className="genie-action__preview">
                      {preview.slice(0, 4).map((item) => (
                        <span key={item} className="chip chip--neutral">{item}</span>
                      ))}
                    </div>
                  )}
                </div>
                {confirming ? (
                  <div className="genie-action__confirm">
                    <button
                      type="button"
                      className="btn btn--primary btn--sm"
                      disabled={Boolean(pendingActionId)}
                      onClick={async () => {
                        setPendingActionId(action.id);
                        setConfirmActionId(null);
                        try {
                          await onAction(action);
                        } finally {
                          setPendingActionId(null);
                        }
                      }}
                    >
                      {pending ? 'Recording…' : 'Confirm'}
                    </button>
                    <button type="button" className="btn btn--ghost btn--sm" disabled={Boolean(pendingActionId)} onClick={() => setConfirmActionId(null)}>
                      Cancel
                    </button>
                  </div>
                ) : (
                  <button type="button" className="btn btn--ghost btn--sm" disabled={Boolean(pendingActionId)} onClick={() => setConfirmActionId(action.id)}>
                    <Icon name="play" size={12} />
                    {pending ? 'Recording…' : 'Run'}
                  </button>
                )}
              </div>
            );
          })}
        </div>
      )}
    </div>
  );
}

function humanizeKey(k: string): string {
  return k.replace(/_/g, ' ').replace(/\b\w/g, (c) => c.toUpperCase());
}

function formatCell(column: string, v: unknown): string {
  if (v === null || v === undefined) return '—';
  if (isIdentifierColumn(column)) return formatIdentifier(column, v);
  if (typeof v === 'number') return Number.isInteger(v) ? v.toLocaleString() : v.toFixed(2);
  return String(v);
}
