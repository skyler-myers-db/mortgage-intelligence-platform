#!/usr/bin/env node
/**
 * Perf ceilings from reference-runner calibration (audit runtime-09,
 * quality-08). The PERF_SPEC specs write calibration/<name>.json on every
 * run (frontend/tests/e2e/fixture/calibration.ts), and the e2e-fixture CI job
 * uploads them as `perf-calibration-<run id>-<attempt>`. Download three runs
 * of one sha and print the ceilings:
 *
 *   gh run download <id> -n perf-calibration-<id>-<attempt> -D /tmp/cal/<id>   # x3
 *   node tools/perf_ceilings.mjs /tmp/cal
 *
 * Per artifact name and metric: the median of the per-run medians x 1.2,
 * rounded UP to 10 ms (interaction-budget), 100 ms (LCP / TBT) or 0.01 (CLS).
 * It refuses fewer than three distinct (runId, runAttempt) runs, a local
 * input without a runId (unless --allow-local, for a dry run), and runs of
 * different shas (unless --allow-mixed-sha). It prints a TS literal per
 * artifact plus the runs it cites, and never writes a spec: the integrator
 * pastes the numbers and ratchets them down, never up.
 */
import { readdirSync, readFileSync, realpathSync, statSync } from 'node:fs';
import path from 'node:path';
import { pathToFileURL } from 'node:url';

export const HEADROOM = 1.2;
export const MIN_RUNS = 3;

/** The rounding step of one metric: 0.01 for CLS, 10 ms for an interaction, 100 ms for LCP / TBT. */
export function roundingStep(spec, metric) {
  if (metric === 'cls') return 0.01;
  return spec === 'interaction-budget' ? 10 : 100;
}

/** The middle value (the upper middle of an even count, as the specs take it). */
export function median(values) {
  if (values.length === 0) throw new Error('median of no values');
  const sorted = [...values].sort((a, b) => a - b);
  return sorted[Math.floor(sorted.length / 2)];
}

/** Round `value` up to a multiple of `step` (float-safe for 0.01). */
export function roundUp(value, step) {
  const units = Math.ceil(value / step - 1e-9);
  const decimals = step < 1 ? String(step).split('.')[1].length : 0;
  return Number((units * step).toFixed(decimals));
}

export function ceilingFor(runMedians, spec, metric) {
  return roundUp(median(runMedians) * HEADROOM, roundingStep(spec, metric));
}

function runKey(calibration) {
  return `${calibration.run?.runId ?? 'local'}/${calibration.run?.runAttempt ?? '1'}`;
}

/** Why a set of calibrations cannot become ceilings; empty when it can. */
export function refusals(calibrations, { allowLocal = false, allowMixedSha = false } = {}) {
  const problems = [];
  const byName = new Map();
  for (const [index, calibration] of calibrations.entries()) {
    const where = calibration.source ?? `input ${index + 1}`;
    const valid = calibration && typeof calibration.name === 'string' && typeof calibration.spec === 'string'
      && calibration.medians && typeof calibration.medians === 'object'
      && Object.values(calibration.medians).every((value) => Number.isFinite(value));
    if (!valid) {
      problems.push(`${where}: not a calibration record (name, spec, finite medians)`);
      continue;
    }
    if (!calibration.run?.runId && !allowLocal) problems.push(`${where}: a local input (no runId); pass --allow-local for a dry run`);
    const group = byName.get(calibration.name) ?? [];
    group.push(calibration);
    byName.set(calibration.name, group);
  }
  for (const [name, group] of byName) {
    const keys = group.map(runKey);
    const distinct = new Set(keys);
    if (distinct.size !== keys.length && !allowLocal) problems.push(`${name}: the same run (runId/attempt) is given twice`);
    if ((allowLocal ? keys.length : distinct.size) < MIN_RUNS) problems.push(`${name}: ${distinct.size} run(s); ${MIN_RUNS} distinct reference-runner runs are required`);
    const shas = new Set(group.map((calibration) => calibration.run?.sha ?? null));
    if (shas.size > 1 && !allowMixedSha) problems.push(`${name}: runs of ${shas.size} different shas; pass --allow-mixed-sha to accept`);
  }
  return problems;
}

/** Per artifact name: the ceiling of every metric and the runs it rests on. */
export function computeCeilings(calibrations) {
  const byName = new Map();
  for (const calibration of calibrations) {
    const group = byName.get(calibration.name) ?? [];
    group.push(calibration);
    byName.set(calibration.name, group);
  }
  return [...byName.entries()].sort(([a], [b]) => a.localeCompare(b)).map(([name, group]) => {
    const spec = group[0].spec;
    const metrics = [...new Set(group.flatMap((calibration) => Object.keys(calibration.medians)))].sort();
    const ceilings = Object.fromEntries(metrics.map((metric) => {
      const runMedians = group.map((calibration) => calibration.medians[metric]).filter((value) => Number.isFinite(value));
      return [metric, { runMedians, median: median(runMedians), ceiling: ceilingFor(runMedians, spec, metric) }];
    }));
    return { name, spec, ceilings, runs: group.map(runKey), shas: [...new Set(group.map((c) => c.run?.sha ?? 'local'))] };
  });
}

/** The literal the integrator pastes, with its provenance as comments. */
export function renderCeilings(results) {
  const lines = [];
  for (const result of results) {
    lines.push(`// ${result.name} (${result.spec}): runs ${result.runs.join(', ')}; sha ${result.shas.join(', ')}`);
    for (const [metric, { runMedians, median: middle }] of Object.entries(result.ceilings)) {
      lines.push(`//   ${metric}: run medians ${runMedians.join(' / ')} -> median ${middle} x ${HEADROOM}`);
    }
    const body = Object.entries(result.ceilings).map(([metric, { ceiling }]) => `${metric}: ${ceiling}`).join(', ');
    lines.push(`${JSON.stringify(result.name)}: { ${body} },`);
  }
  return `${lines.join('\n')}\n`;
}

function collect(inputs) {
  const files = [];
  for (const input of inputs) {
    if (statSync(input).isDirectory()) {
      for (const entry of readdirSync(input, { recursive: true })) {
        if (String(entry).endsWith('.json')) files.push(path.join(input, String(entry)));
      }
    } else {
      files.push(input);
    }
  }
  return files.sort();
}

export function main(argv = process.argv.slice(2)) {
  const allowLocal = argv.includes('--allow-local');
  const allowMixedSha = argv.includes('--allow-mixed-sha');
  const inputs = argv.filter((arg) => !arg.startsWith('--'));
  if (inputs.length === 0) {
    process.stderr.write('usage: node tools/perf_ceilings.mjs [--allow-local] [--allow-mixed-sha] <calibration.json | dir>...\n');
    return 2;
  }
  const calibrations = collect(inputs).map((file) => ({ ...JSON.parse(readFileSync(file, 'utf8')), source: file }));
  const problems = refusals(calibrations, { allowLocal, allowMixedSha });
  if (problems.length > 0) {
    process.stderr.write(`perf_ceilings: refused:\n  ${problems.join('\n  ')}\n`);
    return 1;
  }
  process.stdout.write(renderCeilings(computeCeilings(calibrations)));
  return 0;
}

// Symlink-safe entry guard: Node runs the realpath of the main module.
if (process.argv[1] && import.meta.url === pathToFileURL(realpathSync(process.argv[1])).href) {
  process.exitCode = main();
}
