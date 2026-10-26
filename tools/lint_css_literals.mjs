import { readFileSync } from 'node:fs';
import { relative } from 'node:path';
import { fileURLToPath } from 'node:url';
import { globSync } from 'node:fs';

const repoRoot = fileURLToPath(new URL('../', import.meta.url));
const COLOR_LITERAL = /#[0-9a-fA-F]{3,8}\b|rgba?\([^)]*\)/g;
const ALLOWED = new Set([
  'frontend/src/design-system/tokens.css',
  'design_files/index.html',
  'design_files/Module 0 Prototype.html',
  'design_files/Design System.html',
]);

export function findCssLiteralViolations() {
  const files = globSync('frontend/src/**/*.css', { cwd: repoRoot });

  return files.flatMap((file) => {
    if (ALLOWED.has(file)) return [];
    const text = readFileSync(new URL(file, `file://${repoRoot}`), 'utf8');
    const lines = text.split(/\r?\n/);
    return lines.flatMap((line, index) => {
      const matches = line.match(COLOR_LITERAL);
      if (!matches) return [];
      return matches.map((match) => ({
        file,
        line: index + 1,
        literal: match,
      }));
    });
  });
}

// ---------------------------------------------------------------------------
// The BEM selector gate (2026-09-21 audit quality-10 step 3; W5e
// w5-compiler-lint item 5). Dependency-free on purpose: stylelint's
// selector-class-pattern would do this, but stylelint still reaches braces
// 3.0.3 (GHSA-vfj7-8cjw-p6xm, no patched release on 2026-10-14), so it stays
// out of the tree (tests/unit/test_dependency_pins.py).
//
// Every class selector in a RULE prelude under frontend/src/**/*.css must
// follow the prototype's BEM grammar: block(-word)* [__element(-word)*]
// [--modifier(-word)*], lowercase ASCII. At-rule preludes (@media,
// @container, @supports, @layer, @property, @keyframes and its percentage
// selectors, ...) are not selectors and are skipped; comments and strings
// are stripped first. A class that departs from the grammar is listed in
// BEM_EXCEPTIONS with its reason, which must cite a design_files line or a
// registered deviation id from docs/prototype-deviations.md. Never rename a
// prototype class to satisfy the gate.
// ---------------------------------------------------------------------------
export const BEM_CLASS = /^[a-z][a-z0-9]*(?:-[a-z0-9]+)*(?:__[a-z0-9]+(?:-[a-z0-9]+)*)?(?:--[a-z0-9]+(?:-[a-z0-9]+)*)?$/;

export const BEM_EXCEPTIONS = {
  'layoutA-grid':
    'prototype-native class, design_files/Module 0 Prototype.html:968; kept verbatim under the BEM-parity rule (CLAUDE.md)',
  'layoutA-grid--segment-workbench':
    'a modifier on the prototype-native layoutA-grid block (design_files/Module 0 Prototype.html:968): the block keeps its prototype spelling, so its modifiers inherit it',
  'growth-agent-step--review_required':
    'the modifier carries the wire status value review_required verbatim (ask-genie.compose-plan-card.tsx renders growth-agent-step--${status}); the step rows reuse the Genie panel step list, design_files/Module 0 Prototype.html:1365, as the growth-agent-* deviation rows cite',
};

/** At-rules whose block holds declarations or keyframe selectors, never style rules. */
const NON_RULE_BLOCKS = new Set(['keyframes', 'font-face', 'property', 'page', 'counter-style', 'font-feature-values', 'view-transition']);

/** Blank out comments and string contents, keeping every offset and newline. */
function maskCss(text) {
  return text.replace(/\/\*[\s\S]*?\*\/|"(?:[^"\\\n]|\\.)*"|'(?:[^'\\\n]|\\.)*'/g, (match) => {
    const body = match.replace(/[^\n]/g, ' ');
    return match.startsWith('/*') ? body : `${match[0]}${body.slice(1, -1)}${match[0]}`;
  });
}

/**
 * The class selectors of every rule prelude in `text`, as
 * `{ name, line }`. Attribute selectors are dropped (their values are not
 * classes); pseudo-class arguments (`:not(.x)`, `:has(.x)`) are kept.
 */
export function extractClassSelectors(text) {
  const masked = maskCss(text);
  const found = [];
  // Each open block: 'rule' (a style rule: declarations and nested rules),
  // 'group' (@media, @supports, @layer, @container, ...: nested rules) or
  // 'skip' (@keyframes, @font-face, @property, ...: no style rule inside).
  const stack = [];
  let start = 0;
  for (let i = 0; i < masked.length; i += 1) {
    const ch = masked[i];
    if (ch !== '{' && ch !== '}' && ch !== ';') continue;
    const prelude = masked.slice(start, i);
    const offset = start + (prelude.length - prelude.trimStart().length);
    start = i + 1;
    if (ch === '}') {
      stack.pop();
      continue;
    }
    const trimmed = prelude.trim();
    const top = stack.at(-1);
    if (ch === ';') continue; // a declaration or an at-rule statement (@import, @layer a, b;)
    if (top === 'skip') {
      stack.push('skip');
      continue;
    }
    if (trimmed.startsWith('@')) {
      const name = /^@([a-z-]+)/i.exec(trimmed)?.[1]?.toLowerCase().replace(/^-[a-z]+-/, '') ?? '';
      stack.push(NON_RULE_BLOCKS.has(name) ? 'skip' : 'group');
      continue;
    }
    stack.push('rule');
    const selector = trimmed.replace(/\[[^\]]*\]/g, '');
    const baseLine = masked.slice(0, offset).split('\n').length;
    for (const match of selector.matchAll(/\.((?:\\.|[A-Za-z0-9_-])+)/g)) {
      // `.5` in a selector is not a class (it cannot start with a digit unescaped).
      if (/^[0-9]/.test(match[1])) continue;
      const line = baseLine + selector.slice(0, match.index).split('\n').length - 1;
      found.push({ name: match[1], line });
    }
  }
  return found;
}

/** BEM violations in one stylesheet's text: `{ file, line, name }`, minus BEM_EXCEPTIONS. */
export function findBemViolationsInText(text, file, exceptions = BEM_EXCEPTIONS) {
  return extractClassSelectors(text)
    .filter(({ name }) => !BEM_CLASS.test(name) && !Object.hasOwn(exceptions, name))
    .map(({ name, line }) => ({ file, line, name }));
}

/** Every stylesheet under frontend/src, repo-relative, sorted. */
export function frontendStylesheets() {
  return globSync('frontend/src/**/*.css', { cwd: repoRoot }).sort();
}

/** BEM violations over frontend/src/**\/*.css. */
export function findBemViolations() {
  return frontendStylesheets().flatMap((file) =>
    findBemViolationsInText(readFileSync(new URL(file, `file://${repoRoot}`), 'utf8'), file),
  );
}

if (process.argv[1] && relative(repoRoot, process.argv[1]) === 'tools/lint_css_literals.mjs') {
  const violations = findCssLiteralViolations();
  if (violations.length > 0) {
    console.error('Hard-coded CSS color literals are not allowed outside tokens.css/design files.');
    for (const violation of violations) {
      console.error(`${violation.file}:${violation.line} ${violation.literal}`);
    }
  }
  const bem = findBemViolations();
  if (bem.length > 0) {
    console.error('CSS class selectors must follow the prototype BEM grammar (block__element--modifier, lowercase).');
    console.error('A genuine prototype-native or deviation-registered class goes in BEM_EXCEPTIONS (tools/lint_css_literals.mjs) with its cite.');
    for (const violation of bem) console.error(`${violation.file}:${violation.line} .${violation.name}`);
  }
  if (violations.length > 0 || bem.length > 0) process.exit(1);
}
