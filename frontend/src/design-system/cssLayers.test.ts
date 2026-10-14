/**
 * Cascade layers, source guard (2026-09-21 audit css-05 / stack-v3 /
 * bundle-06; docs/testing.md "Cascade layers (css-05)"). The served-CSSOM
 * twin is tests/e2e/fixture/css-layers.cross-engine.fixture.spec.ts.
 *
 *  - design-system/layers.css is exactly the order statement, and it is
 *    main.tsx's first import (the first @layer name in document order fixes
 *    its rank, and app.tsx's sheets load before tokens.css);
 *  - print.css is layered only through print.layer.css, into components
 *    (its base position; print.layer.css says why);
 *  - components.css imports every partial into `components`, 33 into
 *    `modes`, and no imported partial wraps itself;
 *  - tokens.css: fonts.css is its one import, every rule sits in a tokens
 *    block (custom-property carriers) or the base block (element styles);
 *  - every other sheet keeps nothing outside top-level @layer blocks but
 *    @property, in the layer cssLayerMap.ts assigns, with its print and
 *    contrast-mode blocks in `print` / `modes` blocks, and an in-place
 *    `components` block only where COMPONENTS_BLOCKS names the sheet;
 *  - no nested @layer, no `&` nesting, and unique @keyframes / @property
 *    names, so layering never decides between two definitions;
 *  - PENDING_INTEGRATOR_WRAP lists only sheets that still exist unwrapped.
 */
// @ts-expect-error Frontend app types intentionally exclude Node globals; this
// test reads the stylesheet sources under Vitest only.
import { existsSync, readdirSync, readFileSync } from 'node:fs';
import { describe, expect, it } from 'vitest';
import { cssStatements, stripCssComments, walkCss, type CssStatement } from '../test/cssBlocks';
import { importedPartials, lazyPartials } from '../test/designCss';
import {
  COMPONENTS_BLOCKS,
  INITIAL_COMPONENT_SHEETS,
  LAYER_MANIFEST,
  PENDING_INTEGRATOR_WRAP,
  ROUTES_EXCEPTIONS,
  mainLayer,
} from '../test/cssLayerMap';

declare const process: { cwd(): string };

const texts = new Map<string, string>();
const read = (path: string): string => {
  if (!texts.has(path)) texts.set(path, readFileSync(`${process.cwd()}/${path}`, 'utf8') as string);
  return texts.get(path) as string;
};
const stripped = new Map<string, string>();
const code = (path: string): string => {
  if (!stripped.has(path)) stripped.set(path, stripCssComments(read(path)));
  return stripped.get(path) as string;
};

const MANIFEST_STATEMENT = `@layer ${LAYER_MANIFEST.join(', ')};`;
const MODE_MEDIA = /^@media\b.*\b(?:forced-colors|prefers-contrast|prefers-reduced-transparency)\b/;
const PRINT_MEDIA = /^@media\s+print\b/;
const LAYER_BLOCK = /^@layer\s+([A-Za-z][\w-]*)$/;

/** design-system sheets with their own contract (everything else is a colocated sheet). */
const DESIGN_SYSTEM_ENTRIES = [
  'src/design-system/layers.css',
  'src/design-system/print.layer.css',
  'src/design-system/print.css',
  'src/design-system/tokens.css',
  'src/design-system/fonts.css',
  'src/design-system/components.css',
];

const allSheets = (): string[] =>
  (readdirSync(`${process.cwd()}/src`, { recursive: true }) as string[])
    .map((path) => `src/${String(path).split('\\').join('/')}`)
    .filter((path) => path.endsWith('.css'))
    .sort();

const partialPath = (path: string): string => `src/design-system/${path.replace(/^\.\//, '')}`;

/** Colocated sheets and lazy partials: every sheet with a layer to declare in-file. */
const wrappedSheets = (): string[] => {
  const imported = new Set(importedPartials().map(partialPath));
  return allSheets().filter(
    (path) => !DESIGN_SYSTEM_ENTRIES.includes(path) && !imported.has(path) && !PENDING_INTEGRATOR_WRAP.includes(path),
  );
};

/** The layer of each top-level block of a wrapped sheet (throws on anything else but @property). */
function topLevelLayers(path: string): Array<{ layer: string; body: string; statements: CssStatement[] }> {
  const blocks: Array<{ layer: string; body: string; statements: CssStatement[] }> = [];
  for (const statement of cssStatements(code(path))) {
    if (statement.prelude.startsWith('@property') && statement.body !== undefined) continue;
    const match = LAYER_BLOCK.exec(statement.prelude);
    if (!match || statement.body === undefined) {
      throw new Error(`${path}: \`${statement.prelude.slice(0, 80)}\` sits outside any @layer block`);
    }
    blocks.push({ layer: match[1], body: statement.body, statements: cssStatements(statement.body) });
  }
  return blocks;
}

describe('the layer manifest', () => {
  it('layers.css holds exactly the order statement', () => {
    expect(code('src/design-system/layers.css').trim()).toBe(MANIFEST_STATEMENT);
  });

  it("is main.tsx's first import, above ./appRouter", () => {
    const imports = [...read('src/main.tsx').matchAll(/^import\s+(?:[^'"]*from\s+)?["']([^"']+)["'];/gm)].map((match) => match[1]);
    expect(imports[0]).toBe('./design-system/layers.css');
    expect(imports.indexOf('./appRouter')).toBeGreaterThan(0);
  });

  it('declares the order once: no other sheet carries a layer statement', () => {
    for (const path of allSheets().filter((sheet) => sheet !== 'src/design-system/layers.css')) {
      expect(code(path), path).not.toMatch(/@layer\s+[\w-]+\s*(?:,\s*[\w-]+\s*)*;/);
    }
  });
});

describe('print.css', () => {
  it('is layered through print.layer.css, which main.tsx imports instead of it', () => {
    expect(code('src/design-system/print.layer.css').trim()).toBe('@import "./print.css" layer(components);');
    expect(read('src/main.tsx')).toMatch(/^import "\.\/design-system\/print\.layer\.css";$/m);
    expect(read('src/main.tsx')).not.toMatch(/print\.css["']/);
    expect(code('src/design-system/print.css')).not.toMatch(/@layer/);
  });
});

describe('components.css', () => {
  const imports = [...code('src/design-system/components.css').matchAll(/@import\s+"([^"]+)"\s*([^;]*);/g)];

  it('imports every partial into components, and 33-contrast-modes into modes', () => {
    expect(imports.length).toBeGreaterThan(20);
    for (const [, path, layer] of imports) {
      expect(layer.trim(), path).toBe(path.includes('/33-contrast-modes.css') ? 'layer(modes)' : 'layer(components)');
    }
    expect(code('src/design-system/components.css').replace(/@import\s+"[^"]+"\s*[^;]*;/g, '').trim()).toBe('');
  });

  it('no imported partial declares a layer itself', () => {
    for (const path of importedPartials().map(partialPath)) {
      expect(code(path), path).not.toMatch(/@layer\b/);
    }
  });

  it('leaves the Genie markdown/proof partial to the Genie surfaces (css-05 step 2)', () => {
    expect(imports.map(([, path]) => path)).not.toContain('./components/14-genie-markdown-and-proof.css');
    expect(lazyPartials()).toContain('components/14-genie-markdown-and-proof.css');
    expect(read('src/components/mortgage/GenieChat.tsx')).toMatch(/^import '\.\.\/\.\.\/design-system\/components\/14-genie-markdown-and-proof\.css';$/m);
    expect(read('src/routes/ask-genie.tsx')).toMatch(/^import '\.\.\/design-system\/components\/14-genie-markdown-and-proof\.css';$/m);
  });
});

describe('tokens.css and fonts.css', () => {
  const statements = cssStatements(code('src/design-system/tokens.css'));
  const CARRIER = /^(?::root)?(?:\[data-(?:theme|accent|density)(?:="[a-z]+")?\])*$/;
  const carrier = (selector: string) => selector.split(',').every((part) => part.trim() !== '' && CARRIER.test(part.trim()));

  it('imports only fonts.css, unlayered, then holds only tokens and base blocks', () => {
    const [first, ...rest] = statements;
    expect(first).toEqual({ prelude: '@import "./fonts.css"' });
    expect(rest.map((statement) => statement.prelude)).toEqual(['@layer tokens', '@layer base', '@layer tokens']);
  });

  it('tokens blocks hold only custom-property carriers; the base block only element styles', () => {
    for (const block of statements.slice(1)) {
      for (const rule of walkCss(block.body ?? '').filter((statement) => statement.body !== undefined && !statement.prelude.startsWith('@'))) {
        const declaresToken = /(?:^|;)\s*--[\w-]+\s*:/.test(rule.body ?? '');
        if (block.prelude === '@layer tokens') {
          expect(carrier(rule.prelude), `tokens: ${rule.prelude}`).toBe(true);
        } else {
          expect(carrier(rule.prelude), `base: ${rule.prelude}`).toBe(false);
          expect(declaresToken, `base: ${rule.prelude} declares a token`).toBe(false);
        }
      }
    }
  });

  it('fonts.css holds only @font-face rules', () => {
    const faces = cssStatements(code('src/design-system/fonts.css'));
    expect(faces.length).toBeGreaterThan(0);
    expect(faces.every((statement) => statement.prelude === '@font-face' && statement.body !== undefined)).toBe(true);
  });
});

describe('colocated sheets and lazy partials', () => {
  it('cover every colocated sheet and lazy partial, the initial ones included', () => {
    const wrapped = wrappedSheets();
    for (const path of [...INITIAL_COMPONENT_SHEETS, ...lazyPartials().map(partialPath)]) expect(wrapped).toContain(path);
    expect(wrapped.length).toBeGreaterThan(50);
  });

  it.each(wrappedSheets())('%s keeps every rule in a manifest layer, in the layer the map assigns', (path) => {
    const blocks = topLevelLayers(path);
    expect(blocks.length, `${path} has a layer block`).toBeGreaterThan(0);
    const expected = mainLayer(path);
    const layers = blocks.map((block) => block.layer);
    for (const { layer, statements } of blocks) {
      expect(LAYER_MANIFEST as readonly string[], `${path}: @layer ${layer}`).toContain(layer);
      if (layer === 'print' || layer === 'modes') {
        const media = layer === 'print' ? PRINT_MEDIA : MODE_MEDIA;
        for (const statement of statements) {
          expect(statement.prelude, `${path}: a ${layer} block holds only its media`).toMatch(media);
        }
      } else if (layer === 'components' && expected === 'routes') {
        expect(Object.keys(COMPONENTS_BLOCKS), `${path}: an in-place components block needs a COMPONENTS_BLOCKS reason`).toContain(path);
      } else {
        expect(layer, `${path}: main layer`).toBe(expected);
      }
    }
    if (path in COMPONENTS_BLOCKS) {
      expect(layers, `${path}: COMPONENTS_BLOCKS names it, so it keeps a components block`).toContain('components');
    }
  });

  it('COMPONENTS_BLOCKS names routes sheets, each with a reason', () => {
    for (const [path, reason] of Object.entries(COMPONENTS_BLOCKS)) {
      expect(mainLayer(path), path).toBe('routes');
      expect(reason.length, path).toBeGreaterThan(20);
    }
  });

  it.each(wrappedSheets().filter((path) => !path.startsWith('src/design-system/')))(
    '%s keeps print and contrast-mode media in print / modes blocks only',
    (path) => {
      for (const { layer, body } of topLevelLayers(path)) {
        for (const statement of walkCss(body)) {
          if (PRINT_MEDIA.test(statement.prelude)) expect(layer, `${path}: ${statement.prelude}`).toBe('print');
          if (MODE_MEDIA.test(statement.prelude)) expect(layer, `${path}: ${statement.prelude}`).toBe('modes');
        }
      }
    },
  );
});

describe('every stylesheet', () => {
  it('nests no @layer and uses no & nesting', () => {
    for (const path of allSheets()) {
      const text = code(path);
      const nested = walkCss(text).filter((statement) => statement.prelude.startsWith('@layer') && statement.parents.length > 0);
      expect(nested.map((statement) => statement.prelude), `${path}: nested @layer`).toEqual([]);
      expect(text.replace(/"[^"]*"|'[^']*'/g, ''), `${path}: & nesting`).not.toMatch(/&/);
    }
  });

  it('names every @keyframes and @property once across all sheets', () => {
    const seen = new Map<string, string>();
    const duplicates: string[] = [];
    for (const path of allSheets()) {
      for (const match of code(path).matchAll(/@(keyframes|property)\s+([\w-]+)/g)) {
        const key = `@${match[1]} ${match[2]}`;
        if (seen.has(key)) duplicates.push(`${key}: ${seen.get(key)} and ${path}`);
        seen.set(key, path);
      }
    }
    expect(duplicates).toEqual([]);
  });

  it('ROUTES_EXCEPTIONS names real lazy sheets, each with a reason', () => {
    for (const [path, reason] of Object.entries(ROUTES_EXCEPTIONS)) {
      expect(existsSync(`${process.cwd()}/${path}`), path).toBe(true);
      expect(INITIAL_COMPONENT_SHEETS).not.toContain(path);
      expect(reason.length, path).toBeGreaterThan(20);
    }
  });
});

describe('PENDING_INTEGRATOR_WRAP', () => {
  it.each(PENDING_INTEGRATOR_WRAP)('%s still exists unwrapped (remove the entry once the integrator wraps it)', (path) => {
    expect(existsSync(`${process.cwd()}/${path}`), `${path} exists`).toBe(true);
    expect(code(path), `${path} is wrapped: delete its PENDING_INTEGRATOR_WRAP entry`).not.toMatch(/@layer\b/);
  });
});
