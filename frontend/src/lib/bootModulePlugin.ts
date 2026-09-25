/**
 * Build wiring for the boot module (src/boot/primeBoot.ts, audit bundle-02).
 * Imported by vite.config.ts; build only.
 *
 * 1. `buildStart` emits src/boot/primeBoot.ts as a second entry chunk named
 *    `boot` (`assets/boot-<hash>.js`). The app entry stays
 *    `assets/index-<hash>.js`.
 * 2. `transformIndexHtml` (post) puts `<script type="module" crossorigin
 *    src="/assets/boot-<hash>.js">` IMMEDIATELY BEFORE Vite's entry script
 *    tag, by string placement. Module scripts run in document order, so the
 *    boot reads start while the entry and its vendor chunks still download.
 *    No inline script (CSP `script-src 'self'`), no `preload as=fetch`
 *    (Firefox does not reuse a non-cacheable fetch preload, so each read
 *    would go twice), and no reliance on 103 Early Hints behind the proxy.
 * 3. `generateBundle` (post) writes `index.home.html`: the built index.html
 *    plus a `<link rel="modulepreload" crossorigin>` for every JS file of the
 *    Home route's closure that the initial closure does not already load, so a
 *    cold `/` fetches the Home chunks beside the entry instead of after it.
 *    No CSS preload. backend/services/spa_shell.py serves it for exactly `/`
 *    and plain index.html for every deep link (a deep link must not pay for
 *    Home's chunks).
 *
 * tools/build_manifest.mjs then requires exactly two manifest entries
 * (index.html and the boot module), fails when the boot chunk imports
 * anything or is imported, and counts it in the initial closure the budget
 * gate measures.
 */

export const BOOT_ENTRY_SOURCE = 'src/boot/primeBoot.ts';
export const BOOT_CHUNK_NAME = 'boot';
export const HOME_ROUTE_SOURCE = 'src/routes/home.tsx';
export const HOME_SHELL_FILE = 'index.home.html';

/** The fields of a Rollup/Rolldown output chunk this plugin reads. */
export interface BootBundleChunk {
  type: 'chunk';
  fileName: string;
  isEntry: boolean;
  facadeModuleId: string | null;
  imports: string[];
}

export interface BootBundleAsset {
  type: 'asset';
  fileName: string;
  source: string | Uint8Array;
}

export type BootBundle = Record<string, BootBundleChunk | BootBundleAsset | { type: string; fileName: string }>;

/**
 * The slice of Vite's plugin API this plugin uses, typed locally: importing
 * `vite` types into src would pull Node's global types into the app's
 * typecheck. A Vite `Plugin` accepts this shape structurally.
 */
interface EmitContext {
  emitFile(file: { type: 'chunk'; id: string; name: string } | { type: 'asset'; fileName: string; source: string }): string;
}

export interface BootModulePlugin {
  name: string;
  apply: 'build';
  configResolved(config: { root: string; base: string }): void;
  buildStart(this: EmitContext): void;
  transformIndexHtml: {
    order: 'post';
    handler(html: string, ctx: { bundle?: unknown; chunk?: { fileName: string } }): string;
  };
  generateBundle: {
    order: 'post';
    handler(this: EmitContext, options: unknown, bundle: unknown): void;
  };
}

const normalizeId = (id: string | null) => (id ?? '').replace(/^\0/, '').split('?')[0].replace(/\\/g, '/');

function chunks(bundle: BootBundle): BootBundleChunk[] {
  return Object.values(bundle).filter((output): output is BootBundleChunk => output.type === 'chunk');
}

function isBootChunk(chunk: BootBundleChunk): boolean {
  return chunk.isEntry && normalizeId(chunk.facadeModuleId).endsWith(`/${BOOT_ENTRY_SOURCE}`);
}

/** The emitted boot chunk; throws unless there is exactly one. */
export function bootChunkOf(bundle: BootBundle): BootBundleChunk {
  const found = chunks(bundle).filter(isBootChunk);
  if (found.length !== 1) throw new Error(`expected exactly one boot entry chunk (${BOOT_ENTRY_SOURCE}), found ${found.length}`);
  return found[0];
}

/** The app entry chunk (the index.html entry); throws unless there is exactly one. */
export function appEntryChunkOf(bundle: BootBundle): BootBundleChunk {
  const found = chunks(bundle).filter((chunk) => chunk.isEntry && !isBootChunk(chunk));
  if (found.length !== 1) throw new Error(`expected exactly one app entry chunk, found ${found.length}`);
  return found[0];
}

/** `start` plus every chunk file it reaches through static imports. */
export function staticChunkClosure(bundle: BootBundle, start: string): Set<string> {
  const byFile = new Map(chunks(bundle).map((chunk) => [chunk.fileName, chunk]));
  const seen = new Set<string>();
  const queue = [start];
  while (queue.length > 0) {
    const file = queue.shift() as string;
    if (seen.has(file)) continue;
    const chunk = byFile.get(file);
    if (!chunk) throw new Error(`bundle has no chunk ${file}`);
    seen.add(file);
    queue.push(...chunk.imports);
  }
  return seen;
}

/**
 * The Home route's JS beyond the initial closure: the chunk whose facade is
 * src/routes/home.tsx plus its transitive static imports, minus the entry's
 * static closure. Sorted.
 */
export function homeRouteClosure(bundle: BootBundle): string[] {
  const home = chunks(bundle).filter((chunk) => normalizeId(chunk.facadeModuleId).endsWith(`/${HOME_ROUTE_SOURCE}`));
  if (home.length !== 1) throw new Error(`expected exactly one ${HOME_ROUTE_SOURCE} chunk, found ${home.length}`);
  const initial = staticChunkClosure(bundle, appEntryChunkOf(bundle).fileName);
  return [...staticChunkClosure(bundle, home[0].fileName)]
    .filter((file) => !initial.has(file) && file.endsWith('.js'))
    .sort();
}

const entryScriptTag = (base: string, file: string) => `<script type="module" crossorigin src="${base}${file}"></script>`;

/** Puts the boot module's script tag immediately before the entry's; throws unless the entry tag occurs once. */
export function insertBootScript(html: string, { base, entryFile, bootFile }: { base: string; entryFile: string; bootFile: string }): string {
  const entryTag = entryScriptTag(base, entryFile);
  const at = html.indexOf(entryTag);
  if (at === -1 || html.indexOf(entryTag, at + 1) !== -1) {
    throw new Error(`expected the entry script tag exactly once in index.html: ${entryTag}`);
  }
  return `${html.slice(0, at)}${entryScriptTag(base, bootFile)}\n    ${html.slice(at)}`;
}

/** index.html plus one modulepreload link per file, before `</head>`. */
export function homeVariantHtml(html: string, { base, files }: { base: string; files: string[] }): string {
  const at = html.indexOf('</head>');
  if (at === -1) throw new Error('index.html has no </head>');
  const lineStart = html.lastIndexOf('\n', at) + 1;
  const links = files.map((file) => `    <link rel="modulepreload" crossorigin href="${base}${file}">\n`).join('');
  return `${html.slice(0, lineStart)}${links}${html.slice(lineStart)}`;
}

export function bootModulePlugin(): BootModulePlugin {
  let root = '';
  let base = '/';
  return {
    name: 'mip:boot-module',
    apply: 'build',
    configResolved(config) {
      root = config.root.replace(/\\/g, '/').replace(/\/$/, '');
      base = config.base;
    },
    buildStart() {
      this.emitFile({ type: 'chunk', id: `${root}/${BOOT_ENTRY_SOURCE}`, name: BOOT_CHUNK_NAME });
    },
    transformIndexHtml: {
      order: 'post',
      handler(html, ctx) {
        if (!ctx.bundle || !ctx.chunk) throw new Error('mip:boot-module: transformIndexHtml ran without a bundle');
        return insertBootScript(html, {
          base,
          entryFile: ctx.chunk.fileName,
          bootFile: bootChunkOf(ctx.bundle as BootBundle).fileName,
        });
      },
    },
    generateBundle: {
      order: 'post',
      handler(_options, bundle) {
        const view = bundle as BootBundle;
        const index = view['index.html'];
        if (!index || index.type !== 'asset') throw new Error('mip:boot-module: the bundle has no index.html asset');
        const { source } = index as BootBundleAsset;
        const html = typeof source === 'string' ? source : new TextDecoder().decode(source);
        this.emitFile({
          type: 'asset',
          fileName: HOME_SHELL_FILE,
          source: homeVariantHtml(html, { base, files: homeRouteClosure(view) }),
        });
      },
    },
  };
}
