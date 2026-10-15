/**
 * frontend/eslint-suppressions.json (W5e w5-compiler-lint item 4, audit
 * quality-10 step 2): ESLint's native bulk suppressions, applied by the CLI
 * from frontend/ with no flag. It is the typed-lint ratchet: an unused entry
 * fails `eslint .` (so it only shrinks; prune with --prune-suppressions), and
 * a new hit is fixed in code, never suppressed. These pins keep it that:
 *   - it names only the three typed rules, never no-restricted-syntax (the
 *     formatting and title bans), react-hooks/* or no-restricted-imports;
 *   - every key is a production source file that exists;
 *   - nothing passes --pass-on-unpruned-suppressions or --suppressions-location.
 */
import { beforeAll, describe, expect, it } from 'vitest';
import { importUntyped, type NodeFs } from './importUntyped';

const FRONTEND = decodeURIComponent(new URL('../../', import.meta.url).pathname);
const REPO = decodeURIComponent(new URL('../../../', import.meta.url).pathname);
const TYPED_RULES = [
  '@typescript-eslint/no-base-to-string',
  '@typescript-eslint/no-floating-promises',
  '@typescript-eslint/no-misused-promises',
];

type Suppressions = Record<string, Record<string, { count: number }>>;

let fs: NodeFs;
let suppressions: Suppressions;

beforeAll(async () => {
  fs = await importUntyped<NodeFs>('node:fs');
  suppressions = JSON.parse(fs.readFileSync(`${FRONTEND}eslint-suppressions.json`, 'utf8')) as Suppressions;
});

describe('eslint-suppressions.json', () => {
  it('names only the three typed rules, each with a positive count', () => {
    for (const [file, rules] of Object.entries(suppressions)) {
      expect(Object.keys(rules).length, file).toBeGreaterThan(0);
      for (const [rule, entry] of Object.entries(rules)) {
        expect(TYPED_RULES, `${file}: ${rule} may not be bulk-suppressed`).toContain(rule);
        expect(Number.isInteger(entry.count) && entry.count > 0, `${file}: ${rule}`).toBe(true);
      }
    }
  });

  it('keys only production source files that exist', () => {
    for (const file of Object.keys(suppressions)) {
      expect(file, file).toMatch(/^src\/.+\.tsx?$/);
      expect(file, file).not.toMatch(/\.test\.tsx?$|^src\/(test|mocks)\//);
      expect(fs.existsSync(`${FRONTEND}${file}`), `${file} no longer exists: prune it`).toBe(true);
    }
  });

  it('is never loosened or relocated from a script or a CI step', () => {
    const scripts = (JSON.parse(fs.readFileSync(`${FRONTEND}package.json`, 'utf8')) as { scripts: Record<string, string> }).scripts;
    const sources = [
      ...Object.entries(scripts).map(([name, command]) => [`package.json scripts.${name}`, command]),
      ['.github/workflows/ci.yml', fs.readFileSync(`${REPO}.github/workflows/ci.yml`, 'utf8')],
    ];
    for (const [where, text] of sources) {
      expect(text, where).not.toContain('--pass-on-unpruned-suppressions');
      expect(text, where).not.toContain('--suppressions-location');
      expect(text, where).not.toContain('--suppress-all');
    }
  });
});
