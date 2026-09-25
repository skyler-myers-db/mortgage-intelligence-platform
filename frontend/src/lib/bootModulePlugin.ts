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
 *
 * tools/build_manifest.mjs then requires exactly two manifest entries
 * (index.html and the boot module), fails when the boot chunk imports
 * anything or is imported, and counts it in the initial closure the budget
 * gate measures.
 */

export const BOOT_ENTRY_SOURCE = 'src/boot/primeBoot.ts';
export const BOOT_CHUNK_NAME = 'boot';

/** The fields of a Rollup/Rolldown output chunk this plugin reads. */
export interface BootBundleChunk {
  type: 'chunk';
  fileName: string;
  isEntry: boolean;
  facadeModuleId: string | null;
  imports: string[];
}

export type BootBundle = Record<string, BootBundleChunk | { type: string; fileName: string }>;

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
  };
}
