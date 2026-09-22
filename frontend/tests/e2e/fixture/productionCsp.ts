/**
 * Production Content-Security-Policy for the fixture harness.
 *
 * `vite preview` sends no CSP, so without this the `securitypolicyviolation`
 * hygiene check would be vacuous and the strict policy the Databricks App
 * serves would first be exercised in production. The policy is READ from the
 * Python constant (`SecurityHeadersMiddleware._CSP`, which lives in
 * backend/services/security_headers.py since the wave-0 extraction out of
 * backend/main.py) at test time rather than copied here, so the two cannot
 * drift. Both locations are searched so a future move fails loudly with the
 * paths tried instead of silently pointing at the wrong file.
 */
import fs from 'node:fs';
import path from 'node:path';

const CSP_BLOCK = /_CSP\s*=\s*\(([\s\S]*?)\n\s*\)/;
const STRING_LITERAL = /"((?:[^"\\]|\\.)*)"/g;

const CSP_SOURCES = ['backend/services/security_headers.py', 'backend/main.py'];

export function readProductionCsp(frontendDir: string): string {
  const candidates = CSP_SOURCES.map((relative) => path.resolve(frontendDir, '..', relative));
  let source: string | undefined;
  let block: RegExpExecArray | null = null;
  for (const candidate of candidates) {
    if (!fs.existsSync(candidate)) continue;
    block = CSP_BLOCK.exec(fs.readFileSync(candidate, 'utf8'));
    if (block) {
      source = candidate;
      break;
    }
  }
  if (!block || !source) {
    throw new Error(
      `Fixture harness could not find the _CSP constant in any of: ${candidates.join(', ')}.`,
    );
  }
  const policy = [...block[1].matchAll(STRING_LITERAL)].map((literal) => literal[1]).join('');
  if (!policy.includes("default-src 'self'") || !policy.includes('script-src')) {
    throw new Error(`Fixture harness parsed an implausible CSP from ${source}: "${policy}"`);
  }
  return policy;
}
