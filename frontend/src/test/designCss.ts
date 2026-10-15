/**
 * Single responsibility: read `design-system/components.css` as one text blob
 * for the unit tests that assert on component CSS source.
 *
 * `components.css` is an entry file — six header lines plus one `@import` per
 * slice under `design-system/components/`. Vite inlines those imports in place,
 * so the shipped stylesheet is still one cascade in source order. Tests that
 * grep the CSS text need the same expansion, which is what this helper does:
 * every `@import "./components/NN-x.css";` line, with or without a cascade
 * layer (`@import "./components/NN-x.css" layer(components);`, css-05), is
 * replaced by that file's contents, reproducing the pre-split file byte for
 * byte.
 *
 * A partial that components.css no longer imports (it loads with its lazy
 * chunk instead; css-05 step 2, today 14-genie-markdown-and-proof.css) is
 * appended after the expansion, in file-name order, so the token, BEM and
 * contrast scans that read this blob keep covering it.
 */
// @ts-expect-error Frontend app types intentionally exclude Node globals; this
// helper reads the design-system CSS text under Vitest only.
import { readdirSync, readFileSync } from 'node:fs';
// @ts-expect-error see node:fs note above.
import { join } from 'node:path';

declare const process: { cwd(): string };

const IMPORT_RE = /^@import\s+["'](.+?)["'](?:\s+layer\(\s*[A-Za-z][\w-]*\s*\))?;\s*$/;

// Resolved from the Vitest root (the `frontend` package) rather than
// `import.meta.url`, because callers that opt into the happy-dom environment
// get a non-`file:` module URL that `readFileSync` rejects.
const DESIGN_SYSTEM_DIR = ['src', 'design-system'];

/** `components/NN-x.css` paths components.css imports, in import order. */
export const importedPartials = (): string[] => {
  const dir = join(process.cwd(), ...DESIGN_SYSTEM_DIR);
  return (readFileSync(join(dir, 'components.css'), 'utf8') as string)
    .split('\n')
    .map((line: string) => IMPORT_RE.exec(line)?.[1])
    .filter((path: string | undefined): path is string => path !== undefined);
};

/** `components/NN-x.css` partials that load with a lazy chunk (not imported by components.css). */
export const lazyPartials = (): string[] => {
  const dir = join(process.cwd(), ...DESIGN_SYSTEM_DIR);
  const imported = new Set(importedPartials().map((path) => path.replace(/^\.\//, '')));
  return (readdirSync(join(dir, 'components')) as string[])
    .filter((name) => name.endsWith('.css'))
    .map((name) => `components/${name}`)
    .filter((path) => !imported.has(path))
    .sort();
};

/** The full component CSS text with every `@import` slice expanded in place, then the lazy partials. */
export const designCss = (): string => {
  const dir = join(process.cwd(), ...DESIGN_SYSTEM_DIR);
  const expanded = (readFileSync(join(dir, 'components.css'), 'utf8') as string)
    .split('\n')
    .map((line: string) => {
      const match = IMPORT_RE.exec(line);
      if (!match) return line;
      // Each slice file ends with a trailing newline; drop exactly one so the
      // join below re-adds it and the expansion stays byte-identical.
      return (readFileSync(join(dir, match[1]), 'utf8') as string).replace(/\n$/, '');
    })
    .join('\n');
  const lazy = lazyPartials().map((path) => readFileSync(join(dir, path), 'utf8') as string);
  return [expanded, ...lazy].join('\n');
};
