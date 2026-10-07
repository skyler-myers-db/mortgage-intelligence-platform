// @ts-expect-error Frontend app types intentionally exclude Node globals; this
// test reads the app's sources under Vitest only.
import { readdirSync, readFileSync } from 'node:fs';
// @ts-expect-error see node:fs note above.
import { join } from 'node:path';
import { describe, expect, it } from 'vitest';

declare const process: { cwd(): string };

/**
 * No vendor presenter coaching in the customer product (D-shell-deviations-e1,
 * audit critic-05). Administration used to tell a lender's admin "Do not claim
 * HITRUST", list "Safe claims" and name the "DAIS-2026 stack": copy written for
 * the vendor's presenter. The honest boundaries stay; they are now stated as
 * facts ("delivery is unverified until it responds"), not as instructions
 * about what to claim.
 *
 * Every non-test source under src/components, src/routes and src/lib is read
 * with its comments stripped, so a comment may still explain a rule; only
 * shipped strings and code are held to it. The bare 'Do not call:' phrase
 * (the do-not-call list) is product vocabulary and stays allowed.
 */

const VENDOR_COPY_RE =
  /\bDAIS\b|do not claim|safe claims|\bclaim (live )?delivery|\bclaims? stay|making [a-z-]+ claims|do not call them|claim boundaries|(delivery|staging|connector) claims|before claims/i;

const SCANNED_DIRS = ['components', 'routes', 'lib'];

function sourceFiles(dir: string): string[] {
  const entries = readdirSync(dir, { recursive: true }) as string[];
  return entries
    .map((entry) => entry.split('\\').join('/'))
    .filter((entry) => /\.(ts|tsx)$/.test(entry) && !/\.test\.(ts|tsx)$/.test(entry) && !entry.endsWith('.d.ts'))
    .map((entry) => join(dir, entry) as string);
}

/** Drop block and line comments (a line comment only where `//` is not inside a string or URL). */
function stripComments(text: string): string {
  return text
    .replace(/\/\*[\s\S]*?\*\//g, ' ')
    .replace(/(^|[^:'"`\\])\/\/[^\n]*/g, '$1');
}

function offenders(): string[] {
  const src = join(process.cwd(), 'src');
  const hits: string[] = [];
  for (const dir of SCANNED_DIRS) {
    for (const file of sourceFiles(join(src, dir))) {
      const code = stripComments(readFileSync(file, 'utf8') as string);
      const match = code.match(VENDOR_COPY_RE);
      if (match) hits.push(`${file.slice(src.length + 1)}: ${match[0]}`);
    }
  }
  return hits;
}

describe('vendor presenter copy (D-shell-deviations-e1)', () => {
  it('catches every phrase it bans and leaves the do-not-call list alone (non-vacuity control)', () => {
    for (const phrase of [
      'Do not claim HITRUST or certification.',
      'Safe claims: governed staging.',
      'DAIS-2026 stack.',
      'Claim live delivery only for connected destinations.',
      'Delivery claims stay unverified.',
      'Retry before making source-coverage claims.',
      'Do not call them a trained MIP ML model.',
      'Buyer readiness claim boundaries',
      'connector claims are unverified',
      'Reading activation rows before claims.',
    ]) {
      expect(VENDOR_COPY_RE.test(phrase), phrase).toBe(true);
    }
    expect(VENDOR_COPY_RE.test('Do not call: this borrower opted out.')).toBe(false);
    expect(VENDOR_COPY_RE.test('No third-party certification such as HITRUST is claimed.')).toBe(false);
    expect(stripComments("const a = 'x'; // Do not claim\n/* Safe claims */ const b = 'https://e.x';")).not.toMatch(
      VENDOR_COPY_RE,
    );
  });

  it('ships none of it in src/components, src/routes or src/lib', () => {
    const files = SCANNED_DIRS.flatMap((dir) => sourceFiles(join(join(process.cwd(), 'src'), dir)));
    expect(files.length, 'the scan reached the sources').toBeGreaterThan(200);
    expect(offenders()).toEqual([]);
  });
});
