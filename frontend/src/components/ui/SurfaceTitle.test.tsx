/**
 * @vitest-environment happy-dom
 *
 * <SurfaceTitle> contract and the source gate behind it (2026-09-21 audit,
 * a11y-03): every surface title is a real h2 / h3 that keeps the prototype's
 * `.h-4` class, and no production source may bring back a `div` title with
 * `.h-4`. The rendered outline (one h1, at least one h2, no skipped level on
 * each product route) is pinned by tests/e2e/fixture/headings.fixture.spec.ts.
 */
// @ts-expect-error Frontend app types intentionally exclude Node globals; this
// test reads the app's TSX sources under Vitest only.
import { readFileSync, readdirSync } from 'node:fs';
// @ts-expect-error see node:fs note above.
import { join } from 'node:path';
import { act, createRef, type ReactNode } from 'react';
import { createRoot, type Root } from 'react-dom/client';
import { afterEach, beforeEach, describe, expect, it } from 'vitest';
import { SurfaceTitle } from './SurfaceTitle';

declare const process: { cwd(): string };

(globalThis as unknown as { IS_REACT_ACT_ENVIRONMENT: boolean }).IS_REACT_ACT_ENVIRONMENT = true;

let container: HTMLDivElement;
let root: Root;

beforeEach(() => {
  container = document.createElement('div');
  document.body.appendChild(container);
  root = createRoot(container);
});

afterEach(() => {
  act(() => root.unmount());
  container.remove();
});

function render(node: ReactNode): void {
  act(() => root.render(node));
}

describe('SurfaceTitle', () => {
  it('renders an h2 carrying exactly the prototype class by default', () => {
    render(<SurfaceTitle>Ranked borrowers</SurfaceTitle>);
    const heading = container.firstElementChild as HTMLElement;
    expect(heading.tagName).toBe('H2');
    expect(heading.className).toBe('h-4');
    expect(heading.textContent).toBe('Ranked borrowers');
  });

  it('renders an h3 for a sub-panel and appends extra classes after h-4', () => {
    render(<SurfaceTitle level={3} className="mt-1">Admin config at decision time</SurfaceTitle>);
    const heading = container.firstElementChild as HTMLElement;
    expect(heading.tagName).toBe('H3');
    expect(heading.className).toBe('h-4 mt-1');
  });

  it('passes id, tabIndex and ref through, so a region can name itself by it and focus can land on it', () => {
    const ref = createRef<HTMLHeadingElement>();
    render(
      <section aria-labelledby="receipt-title">
        <SurfaceTitle id="receipt-title" ref={ref} tabIndex={-1}>Decision receipt</SurfaceTitle>
      </section>,
    );
    const heading = container.querySelector('#receipt-title') as HTMLElement;
    expect(ref.current).toBe(heading);
    expect(heading.getAttribute('tabindex')).toBe('-1');
    expect(container.querySelector('section')?.getAttribute('aria-labelledby')).toBe(heading.id);
  });
});

/** A `div` opening tag whose className starts with the `h-4` token. */
const DIV_TITLE = /<div\b[^>]*\bclassName=\{?\s*["'`]h-4(?=[\s"'`])/g;

function productionTsx(): string[] {
  const src = join(process.cwd(), 'src');
  return (readdirSync(src, { recursive: true }) as string[])
    .map((entry) => entry.split('\\').join('/'))
    .filter((entry) => /\.tsx$/.test(entry) && !/\.test\.tsx$/.test(entry))
    .filter((entry) => !entry.startsWith('test/') && !entry.startsWith('mocks/'))
    .map((entry) => join(src, entry) as string);
}

describe('surface-title source gate (a11y-03)', () => {
  it('the pattern recognises every shape the codemod removed (non-vacuity control)', () => {
    const shapes = [
      '<div className="h-4">Filters</div>',
      '<div className="h-4 mt-1">',
      '<div className="h-4 warming-block__title">',
      '<div className="h-4" id={titleId} ref={headingRef}>',
      '<div id="x" className=\'h-4\'>',
    ];
    for (const shape of shapes) expect(shape.match(DIV_TITLE), shape).not.toBeNull();
    // ...and not the shapes that are allowed.
    for (const shape of ['<h2 className="h-4">', '<span className="h-4">', '<div className="h-40">', '<div className="h-4x">']) {
      expect(shape.match(DIV_TITLE), shape).toBeNull();
    }
  });

  it('no production source renders a div title with .h-4: use <SurfaceTitle>', () => {
    const files = productionTsx();
    expect(files.length).toBeGreaterThan(50);
    const offenders: string[] = [];
    for (const file of files) {
      const text = readFileSync(file, 'utf8') as string;
      for (const match of text.matchAll(DIV_TITLE)) {
        const line = text.slice(0, match.index).split('\n').length;
        offenders.push(`${file.slice(file.indexOf('/src/') + 1)}:${line}`);
      }
    }
    expect(offenders).toEqual([]);
  });
});
