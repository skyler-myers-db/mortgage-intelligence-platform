/**
 * Single responsibility: read every stylesheet under `src/` that is NOT part
 * of `design-system/` (today the route-scoped sheets in `src/routes/`), so
 * unit tests can hold feature CSS to the same token contracts as the
 * component partials that `designCss()` expands.
 */
// @ts-expect-error Frontend app types intentionally exclude Node globals; this
// helper reads stylesheet text under Vitest only.
import { readFileSync, readdirSync } from 'node:fs';
// @ts-expect-error see node:fs note above.
import { join } from 'node:path';

declare const process: { cwd(): string };

export interface FeatureStylesheet {
  /** Path relative to the frontend package, e.g. `src/routes/glossary.css`. */
  file: string;
  css: string;
}

/** Every `*.css` under `src/` outside `src/design-system/`, sorted by path. */
export function featureStylesheets(): FeatureStylesheet[] {
  const root = process.cwd();
  const entries: string[] = readdirSync(join(root, 'src'), { recursive: true });
  return entries
    .map((entry) => entry.split('\\').join('/'))
    .filter((entry) => entry.endsWith('.css') && !entry.startsWith('design-system/'))
    .sort()
    .map((entry) => ({ file: `src/${entry}`, css: readFileSync(join(root, 'src', entry), 'utf8') as string }));
}
