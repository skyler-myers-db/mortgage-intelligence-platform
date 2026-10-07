/**
 * The report half of the frontend budget gate (audit quality-08): absolute
 * budget TARGETS with dated waivers, a per-chunk JSON report, and the
 * markdown step summary with base-vs-head delta columns. Pure functions;
 * tools/check_frontend_budgets.mjs owns the measuring and the gates (their
 * numbers live in frontend_budget_gates.mjs), and imports this module from
 * main() only.
 *
 * The ratchet gates in check_frontend_budgets.mjs are a CHANGE DETECTOR
 * (measured + ~5%); a target in tools/frontend_budget_targets.json is the
 * DESTINATION the budget owner sets. `targetKiB: null` means no target yet.
 * A dimension over its target fails unless it carries an unexpired waiver
 * ({until, finding, owner, reason}); an expired waiver fails, and a waiver
 * on a dimension already back under its target (or with no target) fails as
 * stale, so waivers only shrink.
 */
import { readFileSync } from 'node:fs';

const KiB = 1024;
export const TOP_DIMENSIONS = ['initialJsBr', 'initialCssBr', 'totalJsBr'];
const WAIVER_KEYS = ['until', 'finding', 'owner', 'reason'];

/** Parse the flags check_frontend_budgets.mjs accepts; paths default to today's constants. */
export function parseBudgetFlags(argv, defaults) {
  const options = { json: null, base: null, reportOnly: false, distDir: defaults.distDir, buildMetaDir: defaults.buildMetaDir };
  const takes = { '--json': 'json', '--base': 'base', '--dist': 'distDir', '--build-meta': 'buildMetaDir' };
  for (let index = 0; index < argv.length; index += 1) {
    const flag = argv[index];
    if (flag === '--report-only') options.reportOnly = true;
    else if (flag in takes) {
      const value = argv[index + 1];
      if (!value || value.startsWith('--')) throw new Error(`${flag} needs a value`);
      options[takes[flag]] = value;
      index += 1;
    } else throw new Error(`unknown flag ${flag}`);
  }
  return options;
}

export function loadTargets(file) {
  return JSON.parse(readFileSync(file, 'utf8'));
}

/** Every targeted dimension as [key, entry]: the three top ones and routes.<key>. */
function targetEntries(targets) {
  const top = TOP_DIMENSIONS.filter((key) => key in (targets.targets ?? {})).map((key) => [key, targets.targets[key]]);
  const routes = Object.entries(targets.targets?.routes ?? {}).map(([key, entry]) => [`routes.${key}`, entry]);
  return [...top, ...routes];
}

/** The actuals, in bytes, keyed like the targets. */
export function actualDimensions(actuals) {
  return {
    initialJsBr: actuals.initialJsBr,
    initialCssBr: actuals.initialCssBr,
    totalJsBr: actuals.totalJsBr,
    ...Object.fromEntries(Object.entries(actuals.routes).map(([key, value]) => [`routes.${key}`, value])),
  };
}

/** Why the actuals break the targets file; `today` is YYYY-MM-DD. */
export function evaluateTargets(actuals, targets, today) {
  const problems = [];
  const measured = actualDimensions(actuals);
  const entries = targetEntries(targets);
  const targeted = new Set(entries.map(([key]) => key));
  for (const key of TOP_DIMENSIONS) if (!targeted.has(key)) problems.push(`${key}: no entry in the targets file`);
  for (const key of Object.keys(measured)) {
    if (key.startsWith('routes.') && !targeted.has(key)) problems.push(`${key}: a measured route with no entry in the targets file`);
  }
  for (const [key, entry] of entries) {
    if (!(key in measured)) {
      problems.push(`${key}: unknown key (no such budget dimension)`);
      continue;
    }
    if (!entry || !('targetKiB' in entry) || typeof entry.finding !== 'string' || !('waiver' in entry)) {
      problems.push(`${key}: an entry needs targetKiB, finding and waiver`);
      continue;
    }
    const actualKiB = measured[key] / KiB;
    const over = entry.targetKiB !== null && actualKiB > entry.targetKiB;
    const waiver = entry.waiver;
    if (waiver !== null && !WAIVER_KEYS.every((field) => typeof waiver[field] === 'string' && waiver[field])) {
      problems.push(`${key}: a waiver needs ${WAIVER_KEYS.join(', ')}`);
      continue;
    }
    if (waiver && waiver.until < today) problems.push(`${key}: waiver expired ${waiver.until} (${waiver.owner}, ${waiver.finding})`);
    else if (waiver && !over) problems.push(`${key}: stale waiver: ${actualKiB.toFixed(2)} KiB is within ${entry.targetKiB ?? 'no'} target; remove it`);
    else if (over && !waiver) problems.push(`${key}: ${actualKiB.toFixed(2)} KiB is over its ${entry.targetKiB} KiB target (${entry.finding})`);
  }
  return problems;
}

/** The per-chunk report: every dist chunk with its sizes, initial flag and the routes whose closure holds it. */
export function chunkReport(chunks, initialFiles, routeClosureFiles) {
  const initial = new Set(initialFiles);
  return [...chunks]
    .sort((a, b) => a.file.localeCompare(b.file))
    .map((chunk) => ({
      file: chunk.file,
      raw: chunk.bytes,
      gzip: chunk.gzipBytes,
      br: chunk.brBytes,
      initial: initial.has(chunk.file),
      routeKeys: Object.entries(routeClosureFiles)
        .filter(([, files]) => files.includes(chunk.file))
        .map(([key]) => key)
        .sort(),
    }));
}

const kib = (value) => (value === null || value === undefined ? '-' : `${(value / KiB).toFixed(2)}`);
const signed = (value) => `${value >= 0 ? '+' : ''}${(value / KiB).toFixed(2)}`;

/**
 * The step-summary table: dimension | actual | gate | target | base | delta
 * (KiB br). The base and delta columns appear only when a base report is given.
 */
export function summaryTable(report, gates, targets, baseReport = null) {
  const head = baseReport ? ['dimension', 'actual', 'gate', 'target', 'base', 'delta'] : ['dimension', 'actual', 'gate', 'target'];
  const measured = actualDimensions(report.actuals);
  const baseMeasured = baseReport ? actualDimensions(baseReport.actuals) : null;
  const targetOf = Object.fromEntries(targetEntries(targets).map(([key, entry]) => [key, entry?.targetKiB ?? null]));
  const rows = Object.keys(measured).sort((a, b) => {
    const rank = (key) => (key.startsWith('routes.') ? 1 : 0);
    return rank(a) - rank(b) || (TOP_DIMENSIONS.indexOf(a) - TOP_DIMENSIONS.indexOf(b)) || a.localeCompare(b);
  }).map((key) => {
    const cells = [
      `\`${key}\``,
      kib(measured[key]),
      kib(gates[key] ?? null),
      targetOf[key] === null || targetOf[key] === undefined ? '-' : targetOf[key].toFixed(2),
    ];
    if (baseMeasured) {
      const base = baseMeasured[key];
      cells.push(kib(base ?? null), base === undefined ? 'new' : signed(measured[key] - base));
    }
    return `| ${cells.join(' | ')} |`;
  });
  return [
    '### Frontend budget (KiB, brotli q11)',
    '',
    `| ${head.join(' | ')} |`,
    `| ${head.map(() => '---').join(' | ')} |`,
    ...rows,
    '',
  ].join('\n');
}
