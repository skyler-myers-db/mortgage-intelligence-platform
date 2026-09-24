import { afterEach, describe, expect, it } from 'vitest';
// @ts-expect-error Frontend app types intentionally exclude Node globals; this
// unit test builds a scratch dist tree under Vitest only.
import { existsSync, mkdirSync, mkdtempSync, readdirSync, rmSync, writeFileSync } from 'node:fs';
// @ts-expect-error Node built-in, Vitest only (see above).
import { tmpdir } from 'node:os';
// @ts-expect-error Node built-in, Vitest only (see above).
import path from 'node:path';
// @ts-expect-error The budget gate (tools/), a Node ESM script with no types.
import * as budgetTool from '../../../tools/check_frontend_budgets.mjs';
// @ts-expect-error The postbuild step (tools/), a Node ESM script with no types.
import * as postbuildTool from '../../../tools/postbuild_artifacts.mjs';

/**
 * Audit bundle-08: the build tooling's own maths, on synthetic manifests.
 * The budget gate (tools/check_frontend_budgets.mjs) measures the initial
 * payload, each route's closure and the lazy chunks through Vite's build
 * manifest; tools/postbuild_artifacts.mjs moves that manifest out of dist
 * before anything can serve it. The real build is exercised by `npm run
 * build && npm run budget` and tests/unit/test_frontend_build_artifacts.py.
 */

interface ManifestChunk {
  file: string;
  name?: string;
  src?: string;
  isEntry?: boolean;
  isDynamicEntry?: boolean;
  imports?: string[];
  dynamicImports?: string[];
  css?: string[];
}
type Manifest = Record<string, ManifestChunk>;
interface Closure { entry: string; keys: string[]; js: string[]; css: string[] }
interface RouteClosure { js: string[]; css: string[] }
interface ScratchDirs { distDir: string; metaDir: string; mapsDir: string }
interface ChunkModules { chunks: Record<string, string[]>; entryStaticModules: string[] }

const tools = {
  initialClosure: budgetTool.initialClosure as (manifest: Manifest) => Closure,
  routeClosures: budgetTool.routeClosures as (manifest: Manifest, initial?: Closure) => Record<string, RouteClosure>,
  staleManifestProblems: budgetTool.staleManifestProblems as (manifest: Manifest, dist: string[]) => string[],
  fontCountProblem: budgetTool.fontCountProblem as (actual: number, expected: number) => string | null,
  routeBudgetProblems: budgetTool.routeBudgetProblems as (keys: string[], budgets: Record<string, number>) => string[],
  vendorChunkProblems: budgetTool.vendorChunkProblems as (
    manifest: Manifest,
    initial: Closure,
    chunkModules?: ChunkModules | null,
  ) => string[],
  relocate: postbuildTool.relocateBuildArtifacts as (dirs: ScratchDirs) => { manifest: string; maps: string[] },
  postbuildInitialClosure: postbuildTool.initialClosure as (manifest: Manifest) => Closure,
};

/**
 * index.html statically imports a shared vendor-ish chunk (which has its own
 * CSS) and lazily imports two routes. `lead` statically imports a table
 * chunk and the shared chunk; `home` imports only the shared chunk.
 */
function syntheticManifest(): Manifest {
  return {
    'index.html': {
      file: 'assets/index-A.js',
      src: 'index.html',
      isEntry: true,
      imports: ['_shared-B.js'],
      dynamicImports: ['src/routes/lead.tsx', 'src/routes/home.tsx', 'src/lib/rum.ts'],
      css: ['assets/index-A.css'],
    },
    '_shared-B.js': { file: 'assets/shared-B.js', css: ['assets/shared-B.css'] },
    'src/routes/lead.tsx': {
      file: 'assets/lead-C.js',
      src: 'src/routes/lead.tsx',
      isDynamicEntry: true,
      imports: ['_shared-B.js', 'index.html', '_table-D.js'],
      css: ['assets/lead-C.css'],
    },
    '_table-D.js': { file: 'assets/table-D.js', imports: ['_shared-B.js'], css: ['assets/table-D.css'] },
    'src/routes/home.tsx': {
      file: 'assets/home-E.js',
      src: 'src/routes/home.tsx',
      isDynamicEntry: true,
      imports: ['index.html', '_shared-B.js'],
    },
    'src/lib/rum.ts': { file: 'assets/rum-F.js', src: 'src/lib/rum.ts', isDynamicEntry: true, imports: ['index.html'] },
  };
}

const DIST_FILES = [
  'assets/index-A.js', 'assets/index-A.css', 'assets/shared-B.js', 'assets/shared-B.css',
  'assets/lead-C.js', 'assets/lead-C.css', 'assets/table-D.js', 'assets/table-D.css',
  'assets/home-E.js', 'assets/rum-F.js',
];

describe('initial closure (manifest maths)', () => {
  it('is the entry plus its static imports and never follows dynamicImports', () => {
    const closure = tools.initialClosure(syntheticManifest());
    expect(closure.entry).toBe('index.html');
    expect(closure.js.sort()).toEqual(['assets/index-A.js', 'assets/shared-B.js']);
    expect(closure.js).not.toContain('assets/lead-C.js');
    expect(closure.js).not.toContain('assets/rum-F.js');
  });

  it('unions the CSS of every chunk in the closure, not just the entry', () => {
    expect(tools.initialClosure(syntheticManifest()).css.sort()).toEqual(['assets/index-A.css', 'assets/shared-B.css']);
  });

  it('is the same helper the postbuild step exports for other gates', () => {
    expect(tools.postbuildInitialClosure(syntheticManifest())).toEqual(tools.initialClosure(syntheticManifest()));
  });

  it('refuses a manifest without exactly one entry', () => {
    const manifest = syntheticManifest();
    manifest['src/routes/home.tsx'].isEntry = true;
    expect(() => tools.initialClosure(manifest)).toThrow(/exactly one entry/);
  });
});

describe('route closures', () => {
  it('are each route chunk plus its static imports, minus the initial closure, JS and CSS', () => {
    const routes = tools.routeClosures(syntheticManifest());
    expect(Object.keys(routes).sort()).toEqual(['src/routes/home.tsx', 'src/routes/lead.tsx']);
    expect(routes['src/routes/lead.tsx'].js.sort()).toEqual(['assets/lead-C.js', 'assets/table-D.js']);
    expect(routes['src/routes/lead.tsx'].css.sort()).toEqual(['assets/lead-C.css', 'assets/table-D.css']);
    expect(routes['src/routes/home.tsx']).toEqual({ js: ['assets/home-E.js'], css: [] });
  });

  it('need a budget entry each, and every budget entry needs a route module', () => {
    const keys = ['src/routes/home.tsx', 'src/routes/lead.tsx'];
    expect(tools.routeBudgetProblems(keys, { 'src/routes/home.tsx': 1, 'src/routes/lead.tsx': 1 })).toEqual([]);
    expect(tools.routeBudgetProblems(keys, { 'src/routes/home.tsx': 1 })).toEqual([
      'route module src/routes/lead.tsx has no entry in budgets.routes',
    ]);
    expect(tools.routeBudgetProblems(keys, { 'src/routes/home.tsx': 1, 'src/routes/lead.tsx': 1, 'src/routes/gone.tsx': 1 }))
      .toEqual(['budgets.routes names src/routes/gone.tsx, which is not a route module in the build manifest']);
  });
});

/**
 * The shape vite.config.ts's codeSplitting groups produce: the entry
 * statically imports the runtime helper and both vendor chunks; vendor-data
 * imports vendor-react; a route imports the entry and the vendor chunks.
 */
function vendorManifest(): Manifest {
  return {
    'index.html': {
      file: 'assets/index-A.js',
      src: 'index.html',
      isEntry: true,
      imports: ['_rolldown-runtime-R.js', '_vendor-react-V.js', '_vendor-data-W.js'],
      dynamicImports: ['src/routes/lead.tsx'],
      css: ['assets/index-A.css'],
    },
    '_rolldown-runtime-R.js': { file: 'assets/rolldown-runtime-R.js', name: 'rolldown-runtime' },
    '_vendor-react-V.js': { file: 'assets/vendor-react-V.js', name: 'vendor-react', imports: ['_rolldown-runtime-R.js'] },
    '_vendor-data-W.js': {
      file: 'assets/vendor-data-W.js',
      name: 'vendor-data',
      imports: ['_rolldown-runtime-R.js', '_vendor-react-V.js'],
    },
    'src/routes/lead.tsx': {
      file: 'assets/lead-C.js',
      src: 'src/routes/lead.tsx',
      isDynamicEntry: true,
      imports: ['index.html', '_vendor-react-V.js', '_table-D.js'],
    },
    '_table-D.js': { file: 'assets/table-D.js', name: 'table' },
  };
}

const VENDOR_MODULES: ChunkModules = {
  chunks: {
    'assets/vendor-react-V.js': ['node_modules/react/index.js', 'node_modules/react-dom/client.js'],
    'assets/vendor-data-W.js': ['node_modules/@tanstack/query-core/build/modern/queryClient.js'],
  },
  entryStaticModules: [
    'index.html',
    'node_modules/@tanstack/query-core/build/modern/queryClient.js',
    'node_modules/react-dom/client.js',
    'node_modules/react/index.js',
  ],
};

describe('vendor chunks (bundle-03)', () => {
  it('pass when both groups sit in the initial closure and import only vendor or runtime chunks', () => {
    const manifest = vendorManifest();
    expect(tools.vendorChunkProblems(manifest, tools.initialClosure(manifest), VENDOR_MODULES)).toEqual([]);
  });

  it('fail when a vendor group escaped the initial closure', () => {
    const manifest = vendorManifest();
    manifest['index.html'].imports = ['_rolldown-runtime-R.js', '_vendor-react-V.js'];
    manifest['src/routes/lead.tsx'].imports?.push('_vendor-data-W.js');
    expect(tools.vendorChunkProblems(manifest, tools.initialClosure(manifest))).toEqual([
      'vendor group escaped the closure: assets/vendor-data-W.js is not in the initial closure',
    ]);
  });

  it('fail when a vendor chunk imports an app chunk', () => {
    const manifest = vendorManifest();
    manifest['_vendor-react-V.js'].imports = ['_rolldown-runtime-R.js', '_table-D.js'];
    expect(tools.vendorChunkProblems(manifest, tools.initialClosure(manifest))).toEqual([
      'vendor chunk assets/vendor-react-V.js imports app chunk assets/table-D.js',
    ]);
  });

  it('fail when a vendor chunk holds a module the entry does not import statically', () => {
    const manifest = vendorManifest();
    const lazyOnly = 'node_modules/@tanstack/query-core/build/modern/infiniteQueryObserver.js';
    const modules: ChunkModules = {
      ...VENDOR_MODULES,
      chunks: { ...VENDOR_MODULES.chunks, 'assets/vendor-data-W.js': [...VENDOR_MODULES.chunks['assets/vendor-data-W.js'], lazyOnly] },
    };
    expect(tools.vendorChunkProblems(manifest, tools.initialClosure(manifest), modules)).toEqual([
      `vendor chunk assets/vendor-data-W.js holds ${lazyOnly}, which the entry does not import statically`,
    ]);
  });

  it('fail when an expected group is missing or an unexpected one appears', () => {
    const manifest = vendorManifest();
    manifest['_vendor-data-W.js'].name = 'vendor-virtual';
    expect(tools.vendorChunkProblems(manifest, tools.initialClosure(manifest))).toEqual([
      'vendor chunk vendor-data is missing (vite.config.ts codeSplitting groups)',
      'unexpected vendor chunk assets/vendor-data-W.js',
    ]);
  });
});

describe('stale-manifest detection', () => {
  it('passes a manifest that names exactly the dist chunks, ignoring precompressed siblings', () => {
    const withSiblings = [...DIST_FILES, 'assets/index-A.js.br', 'assets/index-A.js.gz', 'assets/geist.woff2'];
    expect(tools.staleManifestProblems(syntheticManifest(), withSiblings)).toEqual([]);
  });

  it('fails when the manifest names a file dist does not have', () => {
    const dist = DIST_FILES.filter((file) => file !== 'assets/table-D.js');
    expect(tools.staleManifestProblems(syntheticManifest(), dist)).toEqual([
      'stale build manifest: it names assets/table-D.js, which is not in dist',
    ]);
  });

  it('fails when dist has a chunk the manifest does not name', () => {
    expect(tools.staleManifestProblems(syntheticManifest(), [...DIST_FILES, 'assets/index-Z.js'])).toEqual([
      'stale build manifest: dist has assets/index-Z.js, which it does not name',
    ]);
  });
});

describe('font count is exact, both ways', () => {
  it('passes only the expected count', () => {
    expect(tools.fontCountProblem(2, 2)).toBeNull();
  });

  it('fails a build that emits no fonts', () => {
    expect(tools.fontCountProblem(0, 2)).toBe('font asset count is 0, expected exactly 2');
  });

  it('fails a build that emits an extra font', () => {
    expect(tools.fontCountProblem(3, 2)).toBe('font asset count is 3, expected exactly 2');
  });
});

describe('relocateBuildArtifacts', () => {
  let scratch: string | null = null;
  afterEach(() => {
    if (scratch) rmSync(scratch, { recursive: true, force: true });
    scratch = null;
  });

  function scratchTree(): ScratchDirs {
    scratch = mkdtempSync(path.join(tmpdir(), 'mip-postbuild-'));
    const distDir = path.join(scratch, 'dist');
    const metaDir = path.join(scratch, 'build-meta');
    const mapsDir = path.join(scratch, 'sourcemaps');
    mkdirSync(path.join(distDir, 'assets'), { recursive: true });
    writeFileSync(path.join(distDir, 'index.html'), '<!doctype html>');
    // A string that merely mentions the comment mid-line is not a source-map
    // comment; the check is line-anchored.
    writeFileSync(path.join(distDir, 'assets', 'index-A.js'), 'const s="//# sourceMappingURL=x";export{s};\n');
    writeFileSync(path.join(distDir, 'assets', 'index-A.js.map'), '{"version":3}');
    writeFileSync(path.join(distDir, 'theme-boot.js.map'), '{"version":3}');
    writeFileSync(path.join(distDir, 'build-manifest.json'), JSON.stringify(syntheticManifest()));
    writeFileSync(path.join(distDir, 'build-modules.json'), JSON.stringify(VENDOR_MODULES));
    return { distDir, metaDir, mapsDir };
  }

  it('moves the manifest and the chunk-modules map out of dist into a freshly emptied build-meta', () => {
    const dirs = scratchTree();
    mkdirSync(dirs.metaDir, { recursive: true });
    writeFileSync(path.join(dirs.metaDir, 'stale-from-last-build.json'), '{}');

    const result = tools.relocate(dirs);

    expect(result.manifest).toBe(path.join(dirs.metaDir, 'build-manifest.json'));
    expect(readdirSync(dirs.metaDir).sort()).toEqual(['build-manifest.json', 'build-modules.json']);
    expect(existsSync(path.join(dirs.distDir, 'build-manifest.json'))).toBe(false);
    expect(existsSync(path.join(dirs.distDir, 'build-modules.json'))).toBe(false);
    expect(readdirSync(dirs.distDir).sort()).toEqual(['assets', 'index.html']);
  });

  it('moves every source map into a freshly emptied sourcemaps dir, keeping its dist path', () => {
    const dirs = scratchTree();
    mkdirSync(path.join(dirs.mapsDir, 'assets'), { recursive: true });
    writeFileSync(path.join(dirs.mapsDir, 'assets', 'old-Z.js.map'), '{}');

    const result = tools.relocate(dirs);

    expect(result.maps.sort()).toEqual(['assets/index-A.js.map', 'theme-boot.js.map']);
    expect(readdirSync(dirs.mapsDir).sort()).toEqual(['assets', 'theme-boot.js.map']);
    expect(readdirSync(path.join(dirs.mapsDir, 'assets'))).toEqual(['index-A.js.map']);
    expect(readdirSync(path.join(dirs.distDir, 'assets'))).toEqual(['index-A.js']);
  });

  it('fails when a dist JS file carries a sourceMappingURL comment', () => {
    const dirs = scratchTree();
    writeFileSync(path.join(dirs.distDir, 'assets', 'lead-C.js'), 'export {};\n//# sourceMappingURL=lead-C.js.map\n');
    expect(() => tools.relocate(dirs)).toThrow(/sourceMappingURL comment: assets\/lead-C\.js/);
  });

  it('fails when the build emitted no manifest', () => {
    const dirs = scratchTree();
    rmSync(path.join(dirs.distDir, 'build-manifest.json'));
    expect(() => tools.relocate(dirs)).toThrow(/build-manifest\.json missing from/);
  });

  it('fails when a .vite/ metadata directory is left in dist', () => {
    const dirs = scratchTree();
    mkdirSync(path.join(dirs.distDir, '.vite'));
    writeFileSync(path.join(dirs.distDir, '.vite', 'manifest.json'), '{}');
    expect(() => tools.relocate(dirs)).toThrow(/\.vite\/ metadata directory/);
  });
});
