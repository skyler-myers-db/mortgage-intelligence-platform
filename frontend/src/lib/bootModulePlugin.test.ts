import { describe, expect, it } from 'vitest';
import {
  BOOT_CHUNK_NAME,
  BOOT_ENTRY_SOURCE,
  HOME_SHELL_FILE,
  appEntryChunkOf,
  bootChunkOf,
  bootModulePlugin,
  homeRouteClosure,
  homeVariantHtml,
  insertBootScript,
  type BootBundle,
  type BootBundleChunk,
} from './bootModulePlugin';

/**
 * The boot module's build wiring (audit bundle-02) on synthetic bundles and
 * HTML shaped like Vite's output. The real build is exercised by
 * `npm run build`, tools/build_manifest.mjs and
 * tests/unit/test_frontend_build_artifacts.py.
 */

const ROOT = '/repo/frontend';

function chunk(fileName: string, facade: string | null, imports: string[] = [], isEntry = false): BootBundleChunk {
  return { type: 'chunk', fileName, isEntry, facadeModuleId: facade, imports };
}

function syntheticBundle(): BootBundle {
  return {
    'assets/index-A.js': chunk('assets/index-A.js', `${ROOT}/index.html`, ['assets/vendor-react-V.js', 'assets/apiPaths-P.js'], true),
    'assets/vendor-react-V.js': chunk('assets/vendor-react-V.js', null),
    'assets/apiPaths-P.js': chunk('assets/apiPaths-P.js', null),
    'assets/boot-B.js': chunk('assets/boot-B.js', `${ROOT}/${BOOT_ENTRY_SOURCE}`, [], true),
    'assets/home-H.js': chunk('assets/home-H.js', `${ROOT}/src/routes/home.tsx`, ['assets/index-A.js', 'assets/map-M.js']),
    'assets/map-M.js': chunk('assets/map-M.js', null, ['assets/vendor-react-V.js']),
    'index.html': { type: 'asset', fileName: 'index.html' },
  };
}

const BUILT_HTML = [
  '<!doctype html>',
  '<html lang="en">',
  '  <head>',
  '    <script src="/theme-boot.js?v=08c62c0a"></script>',
  '    <script type="module" crossorigin src="/assets/index-A.js"></script>',
  '    <link rel="modulepreload" crossorigin href="/assets/vendor-react-V.js">',
  '  </head>',
  '  <body>',
  '    <div id="root"></div>',
  '  </body>',
  '</html>',
].join('\n');

describe('bootModulePlugin', () => {
  it('finds the boot chunk and the app entry chunk in the bundle', () => {
    expect(bootChunkOf(syntheticBundle()).fileName).toBe('assets/boot-B.js');
    expect(appEntryChunkOf(syntheticBundle()).fileName).toBe('assets/index-A.js');
  });

  it('refuses a bundle without exactly one boot chunk', () => {
    const none = syntheticBundle();
    delete none['assets/boot-B.js'];
    expect(() => bootChunkOf(none)).toThrow(/exactly one boot entry chunk/);
  });

  it('places the boot script immediately before the entry script, with no inline script and no fetch preload', () => {
    const html = insertBootScript(BUILT_HTML, { base: '/', entryFile: 'assets/index-A.js', bootFile: 'assets/boot-B.js' });
    const boot = html.indexOf('<script type="module" crossorigin src="/assets/boot-B.js"></script>');
    const entry = html.indexOf('<script type="module" crossorigin src="/assets/index-A.js"></script>');

    expect(boot).toBeGreaterThan(-1);
    expect(entry).toBeGreaterThan(boot);
    expect(html.slice(boot, entry).replace(/\s+/g, ''), 'nothing between the two tags').toBe(
      '<scripttype="module"crossoriginsrc="/assets/boot-B.js"></script>',
    );
    expect(html).not.toMatch(/<script(?![^>]*\bsrc=)[^>]*>/);
    expect(html).not.toContain('as="fetch"');
    expect(html.replace(/\s*<script type="module" crossorigin src="\/assets\/boot-B.js"><\/script>/, '')).toBe(BUILT_HTML);
  });

  it('fails the build when the entry script tag is missing or repeated', () => {
    expect(() => insertBootScript(BUILT_HTML, { base: '/', entryFile: 'assets/index-Z.js', bootFile: 'assets/boot-B.js' }))
      .toThrow(/exactly once/);
    const twice = BUILT_HTML.replace('</head>', '<script type="module" crossorigin src="/assets/index-A.js"></script></head>');
    expect(() => insertBootScript(twice, { base: '/', entryFile: 'assets/index-A.js', bootFile: 'assets/boot-B.js' }))
      .toThrow(/exactly once/);
  });

  it("computes the Home closure: home.tsx plus its static imports, minus the entry's static closure", () => {
    expect(homeRouteClosure(syntheticBundle())).toEqual(['assets/home-H.js', 'assets/map-M.js']);
  });

  it('never follows a dynamic import into the Home closure', () => {
    const bundle = syntheticBundle();
    bundle['assets/geometry-G.js'] = chunk('assets/geometry-G.js', null);
    // As Rolldown reports it: the lazily loaded geometry is a dynamic import of Home.
    const home = { ...chunk('assets/home-H.js', `${ROOT}/src/routes/home.tsx`, ['assets/index-A.js', 'assets/map-M.js']) };
    bundle['assets/home-H.js'] = Object.assign(home, { dynamicImports: ['assets/geometry-G.js'] });
    expect(homeRouteClosure(bundle)).toEqual(['assets/home-H.js', 'assets/map-M.js']);
  });

  it('writes the variant HTML: the built HTML plus one modulepreload per Home file, and no CSS preload', () => {
    const withBoot = insertBootScript(BUILT_HTML, { base: '/', entryFile: 'assets/index-A.js', bootFile: 'assets/boot-B.js' });
    const variant = homeVariantHtml(withBoot, { base: '/', files: homeRouteClosure(syntheticBundle()) });

    const preloads = [...variant.matchAll(/<link rel="modulepreload" crossorigin href="\/([^"]+)">/g)].map((m) => m[1]);
    expect(preloads).toEqual(['assets/vendor-react-V.js', 'assets/home-H.js', 'assets/map-M.js']);
    expect(variant).not.toMatch(/rel="preload"[^>]*as="style"/);
    expect(variant.indexOf('assets/boot-B.js')).toBeLessThan(variant.indexOf('src="/assets/index-A.js"'));
    expect(variant.replace(/ {4}<link rel="modulepreload" crossorigin href="\/assets\/(?:home-H|map-M)\.js">\n/g, '')).toBe(withBoot);
  });

  it('emits index.home.html from the bundle in generateBundle', () => {
    const plugin = bootModulePlugin();
    plugin.configResolved({ root: ROOT, base: '/' });
    const bundle: BootBundle = { ...syntheticBundle(), 'index.html': { type: 'asset', fileName: 'index.html', source: BUILT_HTML } };
    const emitted: Array<{ type: string; fileName?: string; source?: string }> = [];
    plugin.generateBundle.handler.call({ emitFile: (file) => {
      emitted.push(file);
      return 'ref';
    } }, {}, bundle);

    expect(emitted.map((file) => file.fileName)).toEqual([HOME_SHELL_FILE]);
    expect(emitted[0].source).toContain('<link rel="modulepreload" crossorigin href="/assets/home-H.js">');
  });

  it('emits src/boot/primeBoot.ts as the `boot` chunk, build only', () => {
    const plugin = bootModulePlugin();
    expect(plugin.apply).toBe('build');
    const emitted: unknown[] = [];
    plugin.configResolved({ root: ROOT, base: '/' });
    plugin.buildStart.call({ emitFile: (file) => {
      emitted.push(file);
      return 'ref';
    } });
    expect(emitted).toEqual([{ type: 'chunk', id: `${ROOT}/${BOOT_ENTRY_SOURCE}`, name: BOOT_CHUNK_NAME }]);
  });
});
