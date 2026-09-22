/**
 * Single responsibility: read `design-system/components.css` as one text blob
 * for the unit tests that assert on component CSS source.
 *
 * `components.css` is an entry file — six header lines plus one `@import` per
 * slice under `design-system/components/`. Vite inlines those imports in place,
 * so the shipped stylesheet is still one cascade in source order. Tests that
 * grep the CSS text need the same expansion, which is what this helper does:
 * every `@import "./components/NN-x.css";` line is replaced by that file's
 * contents, reproducing the pre-split file byte for byte.
 */
// @ts-expect-error Frontend app types intentionally exclude Node globals; this
// helper reads the design-system CSS text under Vitest only.
import { readFileSync } from 'node:fs';
// @ts-expect-error see node:fs note above.
import { join } from 'node:path';

declare const process: { cwd(): string };

const IMPORT_RE = /^@import\s+["'](.+?)["'];\s*$/;

// Resolved from the Vitest root (the `frontend` package) rather than
// `import.meta.url`, because callers that opt into the happy-dom environment
// get a non-`file:` module URL that `readFileSync` rejects.
const DESIGN_SYSTEM_DIR = ['src', 'design-system'];

/** The full component CSS text with every `@import` slice expanded in place. */
export const designCss = (): string => {
  const dir = join(process.cwd(), ...DESIGN_SYSTEM_DIR);
  return readFileSync(join(dir, 'components.css'), 'utf8')
    .split('\n')
    .map((line: string) => {
      const match = IMPORT_RE.exec(line);
      if (!match) return line;
      // Each slice file ends with a trailing newline; drop exactly one so the
      // join below re-adds it and the expansion stays byte-identical.
      return readFileSync(join(dir, match[1]), 'utf8').replace(/\n$/, '');
    })
    .join('\n');
};
