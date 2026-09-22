/**
 * Pins the harness's runner contract at the layer where it broke: a failing
 * fixture test fails with ITS OWN error, within its timeout, the run
 * terminates, and the failure trace is attached.
 *
 * Before the `failureTrace` fixture existed, every failing test in fixture
 * mode stalled for the whole tracing slot (= the test timeout) and gained a
 * second error, "Test timeout of Nms exceeded", because Playwright 1.59's
 * trace merge never finishes on Node 26 once a trace holds an entry over
 * 64 KiB. Only a real runner run exercises that finalization, so this test
 * spawns a nested Playwright run of nested/failing.nested.ts (which fails on
 * purpose, with a 100 KB attachment) and reads its JSON report.
 */
import { spawnSync } from 'node:child_process';
import fs from 'node:fs';
import path from 'node:path';
import { LARGE_ATTACHMENT_BYTES, NESTED_FAILURE_MESSAGE, NESTED_TEST_TITLE } from './nested/contract';
import { expect, test } from './test';

interface JsonAttachment {
  name: string;
  contentType: string;
  path?: string;
  /** Base64, for attachments given as a body rather than a file. */
  body?: string;
}

interface JsonResult {
  status: string;
  duration: number;
  errors: Array<{ message?: string }>;
  attachments: JsonAttachment[];
}

interface JsonSuite {
  title: string;
  specs: Array<{ title: string; tests: Array<{ results: JsonResult[] }> }>;
  suites?: JsonSuite[];
}

interface JsonReport {
  suites: JsonSuite[];
  errors: Array<{ message?: string }>;
  stats: { expected: number; unexpected: number; flaky: number; skipped: number; duration: number };
}

const NESTED_TEST_TIMEOUT_MS = 20_000;

function collectResults(suites: JsonSuite[], into: Array<{ title: string; result: JsonResult }> = []) {
  for (const suite of suites) {
    for (const spec of suite.specs) {
      for (const candidate of spec.tests) {
        for (const result of candidate.results) into.push({ title: spec.title, result });
      }
    }
    if (suite.suites) collectResults(suite.suites, into);
  }
  return into;
}

test('a failing test fails with its own error, the run terminates, and its trace is attached', async ({}, testInfo) => {
  test.setTimeout(180_000);
  const configFile = testInfo.config.configFile;
  if (!configFile) throw new Error('runner self-test needs frontend/playwright.config.ts');
  const frontendDir = path.dirname(configFile);
  const outputDir = testInfo.outputPath('nested');

  const env: NodeJS.ProcessEnv = { ...process.env, E2E_FIXTURE: '1', E2E_FIXTURE_NESTED: '1', E2E_FIXTURE_WORKERS: '1' };
  // The nested run is its own runner: no CI posture (retries, forbidOnly) and
  // no worker identity inherited from this test.
  delete env.CI;
  delete env.TEST_WORKER_INDEX;
  delete env.TEST_PARALLEL_INDEX;

  const startedAt = Date.now();
  const run = spawnSync(
    process.execPath,
    [
      path.join(frontendDir, 'node_modules', 'playwright', 'cli.js'),
      'test',
      '--reporter=json',
      `--timeout=${NESTED_TEST_TIMEOUT_MS}`,
      `--output=${outputDir}`,
    ],
    { cwd: frontendDir, env, encoding: 'utf8', timeout: 150_000, maxBuffer: 64 * 1024 * 1024 },
  );
  const elapsedMs = Date.now() - startedAt;
  testInfo.annotations.push({ type: 'nested-run-ms', description: String(elapsedMs) });

  expect(run.error, 'the nested run must terminate on its own (spawnSync did not have to kill it)').toBeUndefined();
  expect(run.status, 'one failing test exits 1').toBe(1);

  const jsonStart = run.stdout.indexOf('{');
  expect(jsonStart, `the nested run printed a JSON report\n--- stderr ---\n${run.stderr}`).toBeGreaterThanOrEqual(0);
  const report = JSON.parse(run.stdout.slice(jsonStart)) as JsonReport;

  expect(report.errors, 'no runner-level error (config, collection, web server)').toEqual([]);
  expect(report.stats).toMatchObject({ expected: 0, unexpected: 1, flaky: 0, skipped: 0 });

  const results = collectResults(report.suites);
  expect(results.map((entry) => entry.title), 'only the nested probe is collected').toEqual([NESTED_TEST_TITLE]);
  const { result } = results[0];

  expect(result.status, 'the probe failed on its assertion, it did not time out').toBe('failed');
  const messages = result.errors.map((error) => error.message ?? '');
  expect(messages, 'exactly one error: the probe’s own assertion, no spurious timeout').toHaveLength(1);
  expect(messages[0]).toContain(NESTED_FAILURE_MESSAGE);
  expect(messages.join('\n')).not.toMatch(/Test timeout of \d+ms exceeded/);
  expect(result.duration, 'the probe itself failed well inside its timeout').toBeLessThan(NESTED_TEST_TIMEOUT_MS);

  const attachmentNames = result.attachments.map((attachment) => attachment.name);
  expect(attachmentNames, 'failure screenshot, the probe’s own attachment and the harness trace').toEqual(
    expect.arrayContaining(['screenshot', 'large-attachment', 'trace']),
  );
  const trace = result.attachments.find((attachment) => attachment.name === 'trace');
  expect(trace?.contentType).toBe('application/zip');
  // testInfo.attach copies the file into the result's attachments directory.
  expect(trace?.path, 'the trace is a zip under the nested output dir').toMatch(/[\\/]attachments[\\/]trace-[0-9a-f]+\.zip$/);
  expect(trace?.path?.startsWith(outputDir), 'the nested run wrote inside its own --output dir').toBe(true);
  const traceBytes = fs.readFileSync(trace?.path ?? '');
  expect(traceBytes.length, 'the trace zip is not empty').toBeGreaterThan(0);
  expect(traceBytes.subarray(0, 2).toString('latin1'), 'a zip file starts with the PK signature').toBe('PK');

  // A `body` attachment is reported inline (base64) by the JSON reporter.
  const large = result.attachments.find((attachment) => attachment.name === 'large-attachment');
  const largeBytes = large?.path ? fs.statSync(large.path).size : Buffer.from(large?.body ?? '', 'base64').length;
  expect(largeBytes, 'the probe really attached the over-64-KiB file').toBe(LARGE_ATTACHMENT_BYTES);
});
