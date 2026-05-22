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

if (process.argv[1] && relative(repoRoot, process.argv[1]) === 'tools/lint_css_literals.mjs') {
  const violations = findCssLiteralViolations();
  if (violations.length > 0) {
    console.error('Hard-coded CSS color literals are not allowed outside tokens.css/design files.');
    for (const violation of violations) {
      console.error(`${violation.file}:${violation.line} ${violation.literal}`);
    }
    process.exit(1);
  }
}
