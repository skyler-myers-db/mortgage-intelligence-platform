#!/usr/bin/env node
/**
 * Exports every response body the e2e fixture harness can serve, as JSON, so
 * tests/unit/test_e2e_fixture_contract.py can validate each one against the
 * real FastAPI response model (audit 2026-09-21 quality-09, step 3).
 *
 *   node tools/export_e2e_fixtures.mjs --out /tmp/e2e-fixtures.json
 *
 * Collected, in this order:
 *   1. every entry of defaultFixtures() (frontend/tests/e2e/fixture/registry.ts),
 *      invoked with PARAM_SAMPLES for its `:params` and BODY_SAMPLES for its
 *      request body, plus one extra invocation per QUERY_SAMPLES query string;
 *   2. contractSamples() from frontend/tests/e2e/fixture/contractSamples.ts
 *      (curated scenario bodies no default route serves);
 *   3. contractSamples() from any frontend/tests/e2e/fixture/data/*.ts module
 *      that exports one, so a lane validates its per-spec payloads without
 *      editing contractSamples.ts.
 *
 * Output: a deterministic JSON array of
 *   {source, method, pattern, path, query, status, body}
 * where `path` is the concrete path the app would call and `query` has no
 * leading '?'. A handler that throws, a sample with an unknown `:param`, or a
 * malformed sample exits non-zero with the source named.
 *
 * Runtime: Node >= 22.18 (type stripping on by default). The fixture modules
 * are TypeScript with extensionless relative imports, which Node cannot
 * resolve on its own, so a synchronous `module.registerHooks` resolve hook
 * maps `./x` to `./x.ts`. Type-only imports are erased, so nothing here needs
 * node_modules (the fixture ESLint block forbids a runtime import of a bare
 * package, node: builtins aside). No tsx, no new dependency.
 */
import { existsSync, mkdirSync, readdirSync, realpathSync, statSync, writeFileSync } from 'node:fs';
import { registerHooks } from 'node:module';
import path from 'node:path';
import { fileURLToPath, pathToFileURL } from 'node:url';

const ROOT = path.resolve(path.dirname(fileURLToPath(import.meta.url)), '..');
export const FIXTURE_DIR = path.join(ROOT, 'frontend', 'tests', 'e2e', 'fixture');
const HTTP_METHODS = new Set(['GET', 'POST', 'PUT', 'PATCH', 'DELETE']);

/** `./x` or `../x` with no extension, imported from a .ts file: resolve to `x.ts`. */
export function tsSpecifierFor(specifier, parentURL) {
  if (!parentURL || !parentURL.startsWith('file:') || !parentURL.endsWith('.ts')) return null;
  if (!/^\.\.?\//.test(specifier) || path.extname(specifier) !== '') return null;
  const candidate = new URL(`${specifier}.ts`, parentURL);
  return existsSync(fileURLToPath(candidate)) ? candidate.href : null;
}

let hooksInstalled = false;

function installTsResolver() {
  if (hooksInstalled) return;
  hooksInstalled = true;
  registerHooks({
    resolve(specifier, context, nextResolve) {
      const mapped = tsSpecifierFor(specifier, context.parentURL);
      return nextResolve(mapped ?? specifier, context);
    },
  });
}

/** Version-normalize like mockApi.normalizeApiPath: `/api/v1/x` -> `/api/x`. */
export function normalizeApiPath(pathname) {
  return pathname.replace(/^\/api\/v\d+(?=\/|$)/, '/api');
}

/** The concrete path for a registry pattern; an unknown `:param` throws, naming the pattern. */
export function substitutePattern(pattern, paramSamples) {
  const params = {};
  const concrete = pattern
    .split('/')
    .map((segment) => {
      if (!segment.startsWith(':')) return segment;
      const name = segment.slice(1);
      if (!Object.hasOwn(paramSamples, name)) {
        throw new Error(`no PARAM_SAMPLES entry for :${name} in "${pattern}" (add one to contractSamples.ts)`);
      }
      params[name] = paramSamples[name];
      return encodeURIComponent(paramSamples[name]);
    })
    .join('/');
  return { path: concrete, params };
}

async function importFixtureModule(file) {
  installTsResolver();
  return import(pathToFileURL(path.join(FIXTURE_DIR, file)).href);
}

async function invoke(entry, { path: concretePath, params, query, body }) {
  const url = new URL(`http://fixture.invalid${concretePath}${query ? `?${query}` : ''}`);
  const reply = await entry.handler({
    method: entry.method,
    path: normalizeApiPath(url.pathname),
    url,
    query: url.searchParams,
    params,
    body: body ?? null,
  });
  return { status: reply.status ?? 200, body: reply.body };
}

/** One record from a contractSamples() entry, with its defaults filled in. */
export function normalizeSample(sample, sourcePrefix, paramSamples) {
  const where = `${sourcePrefix}:${sample?.source ?? '<no source>'}`;
  if (!sample || typeof sample.source !== 'string' || sample.source === '') {
    throw new Error(`${where}: every contract sample needs a non-empty source`);
  }
  if (!HTTP_METHODS.has(sample.method)) throw new Error(`${where}: unknown method ${String(sample.method)}`);
  if (typeof sample.pattern !== 'string' || !sample.pattern.startsWith('/api/')) {
    throw new Error(`${where}: pattern must start with /api/ (got ${String(sample.pattern)})`);
  }
  if (!Object.hasOwn(sample, 'body')) throw new Error(`${where}: missing body`);
  const concretePath = sample.path ?? substitutePattern(sample.pattern, paramSamples).path;
  return {
    source: where,
    method: sample.method,
    pattern: sample.pattern,
    path: concretePath,
    query: (sample.query ?? '').replace(/^\?/, ''),
    status: sample.status ?? 200,
    body: sample.body,
  };
}

async function registrySamples(contract) {
  const { defaultFixtures } = await importFixtureModule('registry.ts');
  const records = [];
  for (const entry of defaultFixtures()) {
    const key = `${entry.method} ${entry.pattern}`;
    const { path: concretePath, params } = substitutePattern(entry.pattern, contract.PARAM_SAMPLES);
    const body = contract.BODY_SAMPLES[key] ?? null;
    for (const query of ['', ...(contract.QUERY_SAMPLES[key] ?? [])]) {
      const source = `registry:${key}${query ? `?${query}` : ''}`;
      let reply;
      try {
        reply = await invoke(entry, { path: concretePath, params, query, body });
      } catch (error) {
        throw new Error(`${source}: handler threw: ${error instanceof Error ? error.message : String(error)}`);
      }
      records.push({ source, method: entry.method, pattern: entry.pattern, path: concretePath, query, status: reply.status, body: reply.body });
    }
  }
  return records;
}

async function moduleSamples(file, module, paramSamples) {
  if (typeof module.contractSamples !== 'function') return [];
  let samples;
  try {
    samples = await module.contractSamples();
  } catch (error) {
    throw new Error(`${file}: contractSamples() threw: ${error instanceof Error ? error.message : String(error)}`);
  }
  if (!Array.isArray(samples)) throw new Error(`${file}: contractSamples() must return an array`);
  return samples.map((sample) => normalizeSample(sample, file, paramSamples));
}

/** Every sample, in the documented order. */
export async function collectFixtureSamples() {
  const contract = await importFixtureModule('contractSamples.ts');
  const records = await registrySamples(contract);
  records.push(...(await moduleSamples('contractSamples.ts', contract, contract.PARAM_SAMPLES)));
  const dataDir = path.join(FIXTURE_DIR, 'data');
  const dataFiles = readdirSync(dataDir)
    .filter((name) => name.endsWith('.ts') && statSync(path.join(dataDir, name)).isFile())
    .sort();
  for (const name of dataFiles) {
    const file = `data/${name}`;
    records.push(...(await moduleSamples(file, await importFixtureModule(file), contract.PARAM_SAMPLES)));
  }
  return records;
}

function parseOut(argv) {
  const index = argv.indexOf('--out');
  if (index === -1 || !argv[index + 1]) throw new Error('usage: node tools/export_e2e_fixtures.mjs --out <file.json>');
  return path.resolve(argv[index + 1]);
}

async function main(argv) {
  const out = parseOut(argv);
  const records = await collectFixtureSamples();
  mkdirSync(path.dirname(out), { recursive: true });
  writeFileSync(out, `${JSON.stringify(records, null, 1)}\n`, 'utf8');
  console.log(`exported ${records.length} fixture samples -> ${out}`);
}

const invokedDirectly = (() => {
  try {
    return Boolean(process.argv[1]) && realpathSync(process.argv[1]) === fileURLToPath(import.meta.url);
  } catch {
    return false;
  }
})();

if (invokedDirectly) {
  main(process.argv.slice(2)).catch((error) => {
    console.error(`export_e2e_fixtures: ${error instanceof Error ? error.message : String(error)}`);
    process.exitCode = 1;
  });
}
