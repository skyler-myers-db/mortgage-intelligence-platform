/**
 * Calibration output of the PERF_SPEC specs (audit runtime-09, quality-08):
 * each writes `<project outputDir>/calibration/<name>.json` (under
 * test-results/perf/ in the perf step), which the e2e-fixture CI job uploads
 * as `perf-calibration-<run id>-<attempt>`. tools/perf_ceilings.mjs turns
 * three such artifacts from distinct reference-runner runs of one sha into
 * ceilings; only the integrator sets a ceiling in a spec.
 *
 * Numbers and run metadata only: never a borrower id, a URL or any text a
 * page rendered.
 */
import type { TestInfo } from '@playwright/test';
import { mkdirSync, writeFileSync } from 'node:fs';
import { join } from 'node:path';

export interface CalibrationRun {
  sha: string | null;
  runId: string | null;
  runAttempt: string | null;
  runnerOs: string | null;
  imageOs: string | null;
}

export interface Calibration {
  /** The artifact's name: the spec, or `<route>-lab-vitals` for perf-budget. */
  name: string;
  spec: string;
  /** Per metric, the median of this run's samples (ms, or unitless for CLS). */
  medians: Record<string, number>;
  /** Per sample, per metric; null where a sample had no reading. */
  samples: Array<Record<string, number | null>>;
  run: CalibrationRun;
}

/** The CI run this process belongs to; all null on a developer host. */
export function calibrationRun(env: NodeJS.ProcessEnv = process.env): CalibrationRun {
  const read = (name: string) => env[name] || null;
  return {
    sha: read('GITHUB_SHA'),
    runId: read('GITHUB_RUN_ID'),
    runAttempt: read('GITHUB_RUN_ATTEMPT'),
    runnerOs: read('RUNNER_OS'),
    imageOs: read('ImageOS'),
  };
}

/** Write `<outputDir>/calibration/<name>.json`; returns the path. */
export function writeCalibration(testInfo: TestInfo, name: string, calibration: Omit<Calibration, 'run' | 'name'>): string {
  const directory = join(testInfo.project.outputDir, 'calibration');
  mkdirSync(directory, { recursive: true });
  const file = join(directory, `${name}.json`);
  const body: Calibration = { name, ...calibration, run: calibrationRun() };
  writeFileSync(file, `${JSON.stringify(body, null, 2)}\n`);
  return file;
}
