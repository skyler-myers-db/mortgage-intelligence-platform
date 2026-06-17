import type {
  GenieAnswer as GenieAnswerShape,
  GenieVisualization,
} from '../../types';

export const MAX_TABLE_ROWS = 10;
export const MAX_TABLE_COLS = 4;

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

export interface GenieVisualizationPlan {
  kind: string;
  chart: InferredChart | null;
  viz: GenieVisualization | null;
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

const SEGMENT_LABELS: Record<string, string> = {
  itm: 'Prime Refi Candidates',
  equity: 'Home Equity Candidate',
  investor: 'Investor / Multi-Property',
  retention: 'Retention Risk',
  listed: 'Listed for Sale',
  permit: 'HELOC Intent',
};

const HUMANIZED_KEY_LABELS: Record<string, string> = {
  segment_code: 'Cohort',
  top_segment: 'Top Cohort',
  segment_codes: 'Cohorts',
};

export function isIdentifierColumn(column: string): boolean {
  return IDENTIFIER_COLUMN_PATTERNS.some((pattern) => pattern.test(column));
}

export function coerceNumber(value: unknown): number | null {
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

export function formatIdentifier(column: string, value: unknown): string {
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

function formatSegmentValue(value: unknown): string {
  if (Array.isArray(value)) {
    return value.map(formatSegmentValue).join(', ');
  }
  const raw = String(value ?? '').trim();
  if (!raw) return '—';
  return SEGMENT_LABELS[raw.toLowerCase()] ?? raw;
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
  // -- they're "missing" not "wrong type".
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

export function normalizeState(value: unknown): string | null {
  if (typeof value !== 'string') return null;
  const cleaned = value.trim();
  if (/^[A-Za-z]{2}$/.test(cleaned)) return cleaned.toUpperCase();
  return STATE_NAME_TO_CODE[cleaned.toLowerCase()] ?? null;
}

export function level(value: number, max: number): 1 | 2 | 3 | 4 {
  if (max <= 0 || value <= 0) return 1;
  const pct = value / max;
  if (pct >= 0.75) return 4;
  if (pct >= 0.45) return 3;
  if (pct >= 0.2) return 2;
  return 1;
}

export function pickPlan(
  payload: GenieAnswerShape,
  rows: Array<Record<string, unknown>>,
  columns: string[],
): GenieVisualizationPlan {
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

export function humanizeKey(k: string): string {
  const override = HUMANIZED_KEY_LABELS[k.toLowerCase()];
  if (override) return override;
  return k.replace(/_/g, ' ').replace(/\b\w/g, (c) => c.toUpperCase());
}

export function formatCell(column: string, v: unknown): string {
  if (v === null || v === undefined) return '—';
  if (/^(segment|segment_code|top_segment|segment_codes)$/i.test(column)) return formatSegmentValue(v);
  if (isIdentifierColumn(column)) return formatIdentifier(column, v);
  if (typeof v === 'number') return Number.isInteger(v) ? v.toLocaleString() : v.toFixed(2);
  return String(v);
}
