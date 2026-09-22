/**
 * Production Content-Security-Policy for the fixture harness.
 *
 * `vite preview` sends no CSP, so without this the `securitypolicyviolation`
 * hygiene check would be vacuous and the strict policy the Databricks App
 * serves would first be exercised in production. The policy is READ from the
 * Python constant (`SecurityHeadersMiddleware._CSP`) at test time rather than
 * copied here, so the two cannot drift.
 *
 * The middleware moved from backend/main.py to
 * backend/services/security_headers.py (wave 0, `refactor(api): extract
 * SecurityHeadersMiddleware`). Both locations are searched so the harness
 * survives the next such move with a clear error instead of a silent stale
 * path; the first file that holds the constant wins.
 */
import fs from 'node:fs';
import path from 'node:path';

const CSP_BLOCK = /_CSP\s*=\s*\(([\s\S]*?)\n\s*\)/;
const STRING_LITERAL = /"((?:[^"\\]|\\.)*)"/g;

/** Candidate sources, relative to the repo root, most likely first. */
const CSP_SOURCES = ['backend/services/security_headers.py', 'backend/main.py'] as const;

export function readProductionCsp(frontendDir: string): string {
  const repoRoot = path.resolve(frontendDir, '..');
  const candidates = CSP_SOURCES.map((relative) => path.join(repoRoot, relative));
  for (const source of candidates) {
    if (!fs.existsSync(source)) continue;
    const block = CSP_BLOCK.exec(fs.readFileSync(source, 'utf8'));
    if (!block) continue;
    const policy = [...block[1].matchAll(STRING_LITERAL)].map((literal) => literal[1]).join('');
    if (!policy.includes("default-src 'self'") || !policy.includes('script-src')) {
      throw new Error(`Fixture harness parsed an implausible CSP from ${source}: "${policy}"`);
    }
    return policy;
  }
  throw new Error(
    `Fixture harness could not find the _CSP constant in any of: ${candidates.join(', ')}.`,
  );
}
