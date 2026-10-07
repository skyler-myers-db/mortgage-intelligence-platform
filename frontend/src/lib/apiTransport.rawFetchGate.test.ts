/**
 * The raw-fetch gate (D-identity-review-a4 buildSpec 7). Every app request
 * carries the document owner's X-MIP-Actor-Key because it goes through
 * lib/apiTransport (lib/apiActorBinding). A raw `fetch` or `sendBeacon`
 * elsewhere would skip the binding, so only the reviewed, header-less call
 * sites below may make one, and each may name only its own routes.
 *
 * Over every non-test source file under frontend/src and frontend/public
 * (lib/apiTransport.ts and src/test/ excluded), parsed with the TypeScript
 * compiler so comments never count: a CallExpression whose callee is the bare
 * identifier `fetch`, `window.fetch`, `globalThis.fetch` or any `*.sendBeacon`
 * is flagged (`homeQueries.x.fetch()` and `query.refetch()` are not). A
 * flagged file must be in RAW_FETCH_ALLOWLIST, and in it every '/api…' string
 * literal and every apiPath('…') argument, query ignored, must name one of its
 * routes. Non-vacuity: injected sources fail.
 *
 * Adding a raw call site: add one entry below, with the routes it names, and
 * say in the commit why it cannot go through the transport.
 */
// @ts-expect-error Frontend app types intentionally exclude Node globals; this
// unit test reads the source tree under Vitest only.
import { readFileSync, readdirSync } from 'node:fs';
import ts from 'typescript';
import { describe, expect, it } from 'vitest';

const FRONTEND = decodeURIComponent(new URL('../../', import.meta.url).pathname);

/**
 * The reviewed raw call sites and the routes each may name (the path after
 * /api or /api/v1, query dropped). An empty list names no /api route.
 */
export const RAW_FETCH_ALLOWLIST: Readonly<Record<string, readonly string[]>> = Object.freeze({
  // The boot reads start before the transport exists (audit bundle-02).
  'src/boot/primeBoot.ts': ['/session', '/config/options', '/config/footprint', '/health'],
  // The footprint read skips the transport's retry loop on purpose.
  'src/components/FootprintProvider.tsx': ['/config/footprint'],
  // The degraded banner's own probe: health is exempt from the binding.
  'src/components/mortgage/DegradedBanner.tsx': ['/health'],
  // The session probe after an ambiguous response: health again.
  'src/lib/apiFailure.ts': ['/health'],
  // The RUM beacon carries no actor; telemetry is exempt server-side.
  'src/lib/rum.ts': ['/telemetry/rum'],
  // A static, hashed /assets topology URL: no /api route at all.
  'src/components/mortgage/zctaGeometry.ts': [],
});

const TRANSPORT = 'src/lib/apiTransport.ts';

interface Source {
  path: string;
  text: string;
}

function isTestPath(path: string): boolean {
  return /\.(test|spec|test-support|stories)\.[cm]?[jt]sx?$/.test(path) || path.endsWith('.d.ts') || path.startsWith('src/test/');
}

function readTree(root: 'src' | 'public'): Source[] {
  return (readdirSync(`${FRONTEND}${root}/`, { recursive: true }) as string[])
    .map((entry) => `${root}/${entry.split('\\').join('/')}`)
    .filter((path) => /\.(tsx?|[cm]?js)$/.test(path) && !isTestPath(path) && path !== TRANSPORT)
    .sort()
    .map((path) => ({ path, text: readFileSync(`${FRONTEND}${path}`, 'utf8') as string }));
}

const SOURCES: readonly Source[] = [...readTree('src'), ...readTree('public')];

function parse(source: Source): ts.SourceFile {
  const kind = source.path.endsWith('.tsx') ? ts.ScriptKind.TSX : source.path.endsWith('.ts') ? ts.ScriptKind.TS : ts.ScriptKind.JS;
  return ts.createSourceFile(source.path, source.text, ts.ScriptTarget.Latest, true, kind);
}

function walk(node: ts.Node, visit: (node: ts.Node) => void): void {
  visit(node);
  ts.forEachChild(node, (child) => walk(child, visit));
}

function isRawCall(node: ts.CallExpression): boolean {
  const callee = node.expression;
  if (ts.isIdentifier(callee)) return callee.text === 'fetch';
  if (!ts.isPropertyAccessExpression(callee)) return false;
  if (callee.name.text === 'sendBeacon') return true;
  return callee.name.text === 'fetch' && ts.isIdentifier(callee.expression) && ['window', 'globalThis'].includes(callee.expression.text);
}

/** '/api/v1/x?y' and '/api/x' and apiPath('/x') all name '/x'. */
function routeOf(text: string): string {
  const path = text.split(/[?#]/, 1)[0] ?? '';
  if (path.startsWith('/api/v1/')) return path.slice('/api/v1'.length);
  if (path.startsWith('/api/')) return path.slice('/api'.length);
  return path.startsWith('/') ? path : `/${path}`;
}

interface Scan {
  rawCalls: number[];
  routes: string[];
}

function scan(source: Source): Scan {
  const file = parse(source);
  const rawCalls: number[] = [];
  const routes: string[] = [];
  walk(file, (node) => {
    if (ts.isCallExpression(node)) {
      if (isRawCall(node)) rawCalls.push(file.getLineAndCharacterOfPosition(node.getStart(file)).line + 1);
      const callee = node.expression;
      const [first] = node.arguments;
      if (ts.isIdentifier(callee) && callee.text === 'apiPath' && first && ts.isStringLiteralLike(first)) {
        routes.push(routeOf(first.text));
      }
    }
    if (ts.isStringLiteralLike(node) && /^\/api(\/|$)/.test(node.text)) routes.push(routeOf(node.text));
    if (ts.isTemplateExpression(node) && /^\/api(\/|$)/.test(node.head.text)) routes.push(routeOf(node.head.text));
  });
  return { rawCalls, routes };
}

/** Every violation over `sources`: an unlisted raw call, or an unlisted route in a listed file. */
function violations(sources: readonly Source[]): string[] {
  const found: string[] = [];
  for (const source of sources) {
    const { rawCalls, routes } = scan(source);
    const allowed = RAW_FETCH_ALLOWLIST[source.path];
    if (allowed === undefined) {
      for (const line of rawCalls) found.push(`${source.path}:${line} raw fetch/sendBeacon outside the allowlist`);
      continue;
    }
    for (const route of routes) {
      if (!allowed.includes(route)) found.push(`${source.path} names ${route}, not in its allowlist row`);
    }
  }
  return found;
}

describe('raw fetch and sendBeacon stay on the reviewed, header-less call sites', () => {
  it('no other source makes a raw request, and each listed file names only its routes', () => {
    expect(violations(SOURCES)).toEqual([]);
  });

  it('every allowlist row is live: the file exists and still makes a raw call', () => {
    const byPath = new Map(SOURCES.map((source) => [source.path, source]));
    for (const path of Object.keys(RAW_FETCH_ALLOWLIST)) {
      const source = byPath.get(path);
      expect(source, `${path} exists`).toBeDefined();
      expect(scan(source as Source).rawCalls.length, `${path} still makes a raw call`).toBeGreaterThan(0);
    }
  });

  it('the scanner sees the known call sites (primeBoot, rum sendBeacon and fetch, the zcta asset)', () => {
    const scanned = (path: string) => scan(SOURCES.find((source) => source.path === path) as Source);
    expect(scanned('src/boot/primeBoot.ts').routes.sort()).toEqual(['/config/footprint', '/config/options', '/health', '/session']);
    expect(scanned('src/lib/rum.ts').rawCalls).toHaveLength(2);
    expect(scanned('src/components/mortgage/zctaGeometry.ts').routes).toEqual([]);
  });
});

describe('non-vacuity: injected sources fail', () => {
  it('a fetch of an app route in an unlisted file', () => {
    const injected = { path: 'src/routes/injected.ts', text: "export const go = () => fetch('/api/v1/leads');" };
    expect(violations([injected])).toEqual(['src/routes/injected.ts:1 raw fetch/sendBeacon outside the allowlist']);
  });

  it('window.fetch, globalThis.fetch and a sendBeacon on any receiver in an unlisted file', () => {
    const injected = {
      path: 'src/lib/injected.ts',
      text: [
        "window.fetch('/api/v1/leads');",
        "globalThis.fetch('/api/v1/leads');",
        "nav.sendBeacon('/api/v1/telemetry/rum', blob);",
      ].join('\n'),
    };
    expect(violations([injected])).toHaveLength(3);
  });

  it('a listed file naming a route outside its row', () => {
    const injected = { path: 'src/components/FootprintProvider.tsx', text: "fetch(apiPath('/leads'));" };
    expect(violations([injected])).toEqual(['src/components/FootprintProvider.tsx names /leads, not in its allowlist row']);
  });

  it('a comment, a refetch() and a client .fetch() are not raw calls', () => {
    const injected = {
      path: 'src/routes/injected.ts',
      text: "// fetch('/api/v1/leads')\nvoid query.refetch();\nvoid homeQueries.homeSummary.fetch(signal);",
    };
    expect(violations([injected])).toEqual([]);
  });
});
