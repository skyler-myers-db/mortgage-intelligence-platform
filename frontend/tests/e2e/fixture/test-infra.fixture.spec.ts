/**
 * Test-infra PR-1 (wave 4a; audit stack-10, a11y-05 item 5, runtime-09 step
 * 1), proven at the layer that runs the suite: the installed Playwright and
 * its own test collection. No page is opened.
 *
 *  - The installed @playwright/test (and its playwright / playwright-core)
 *    is the exact package.json pin, 1.63.0, the version whose
 *    v1.63.0-noble image the e2e-visual job runs in.
 *  - Nested `playwright test --list --reporter=json` runs (the
 *    runner.fixture.spec.ts spawn pattern; `--list` boots no web server)
 *    show what each CI step really collects: a PERF_SPEC spec only with
 *    MIP_PERF=1, visual.fixture.spec.ts never without MIP_VRT=1, and the
 *    perf step's exact filter `perf-budget interaction-budget` exits 0 and
 *    lists PERF_SPEC files only.
 *
 * W4b test-infra-pr2 EXTENDS this file (or names its aria-snapshot spec
 * differently).
 */
import { spawnSync } from 'node:child_process';
import fs from 'node:fs';
import path from 'node:path';
import { PERF_SPEC } from '../../../playwright.config';
import { expect, test } from './test';

interface ListReport {
  suites: Array<{ file: string }>;
  errors: Array<{ message?: string }>;
}

const PLAYWRIGHT_PIN = '1.63.0';
const PERF_STEP_FILTER = ['perf-budget', 'interaction-budget'];

function frontendDir(configFile: string | undefined): string {
  if (!configFile) throw new Error('test-infra needs frontend/playwright.config.ts');
  return path.dirname(configFile);
}

function readVersion(file: string): string {
  return (JSON.parse(fs.readFileSync(file, 'utf8')) as { version: string }).version;
}

/** Files a nested fixture-mode `--list` collects under `extraEnv`, relative to tests/e2e. */
function listCollected(dir: string, extraEnv: Record<string, string>, filters: string[] = []) {
  const env: NodeJS.ProcessEnv = { ...process.env, E2E_FIXTURE: '1', ...extraEnv };
  // The nested run is its own runner: no CI posture, no worker identity, and
  // no opt-in mode this test did not ask for.
  for (const name of ['CI', 'TEST_WORKER_INDEX', 'TEST_PARALLEL_INDEX', 'E2E_FIXTURE_NESTED', 'MIP_PERF', 'MIP_VRT']) {
    if (!(name in extraEnv)) delete env[name];
  }
  const run = spawnSync(
    process.execPath,
    [path.join(dir, 'node_modules', 'playwright', 'cli.js'), 'test', '--list', '--reporter=json', ...filters],
    { cwd: dir, env, encoding: 'utf8', timeout: 90_000, maxBuffer: 64 * 1024 * 1024 },
  );
  expect(run.error, 'the nested --list terminated on its own').toBeUndefined();
  const jsonStart = run.stdout.indexOf('{');
  expect(jsonStart, `the nested --list printed a JSON report\n--- stderr ---\n${run.stderr}`).toBeGreaterThanOrEqual(0);
  const report = JSON.parse(run.stdout.slice(jsonStart)) as ListReport;
  expect(report.errors, 'no collection or config error').toEqual([]);
  return { status: run.status, files: report.suites.map((suite) => suite.file.split(path.sep).join('/')) };
}

test('the installed @playwright/test is the package.json pin, 1.63.0', async ({}, testInfo) => {
  const dir = frontendDir(testInfo.config.configFile);
  const pkg = JSON.parse(fs.readFileSync(path.join(dir, 'package.json'), 'utf8')) as { devDependencies: Record<string, string> };

  expect(pkg.devDependencies['@playwright/test']).toBe(PLAYWRIGHT_PIN);
  for (const name of ['@playwright/test', 'playwright', 'playwright-core']) {
    expect(readVersion(path.join(dir, 'node_modules', name, 'package.json')), `installed ${name}`).toBe(PLAYWRIGHT_PIN);
  }
  expect(testInfo.config.version, 'the runner executing this test').toBe(PLAYWRIGHT_PIN);
});

test('a PERF_SPEC spec is collected only with MIP_PERF=1, and the VRT never without MIP_VRT=1', async ({}, testInfo) => {
  test.setTimeout(180_000);
  const dir = frontendDir(testInfo.config.configFile);

  const normal = listCollected(dir, {});
  expect(normal.status).toBe(0);
  expect(normal.files, 'the normal fixture run collects this spec and perf-motion').toEqual(
    expect.arrayContaining(['fixture/test-infra.fixture.spec.ts', 'fixture/perf-motion.fixture.spec.ts']),
  );
  expect(normal.files.filter((file) => PERF_SPEC.test(file)), 'no timing spec in the normal run').toEqual([]);
  expect(normal.files).not.toContain('fixture/visual.fixture.spec.ts');

  const perf = listCollected(dir, { MIP_PERF: '1' });
  expect(perf.files).toContain('fixture/perf-budget.fixture.spec.ts');
  expect(perf.files).not.toContain('fixture/visual.fixture.spec.ts');
});

test('the perf step filter `perf-budget interaction-budget` exits 0 and lists PERF_SPEC files only', async ({}, testInfo) => {
  test.setTimeout(120_000);
  const dir = frontendDir(testInfo.config.configFile);

  const step = listCollected(dir, { MIP_PERF: '1' }, PERF_STEP_FILTER);
  expect(step.status).toBe(0);
  expect(step.files).toContain('fixture/perf-budget.fixture.spec.ts');
  expect(step.files.filter((file) => !PERF_SPEC.test(file)), 'only timing specs run in the single-worker step').toEqual([]);
});
