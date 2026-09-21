/**
 * Production Content-Security-Policy for the fixture harness.
 *
 * `vite preview` sends no CSP, so without this the `securitypolicyviolation`
 * hygiene check would be vacuous and the strict policy the Databricks App
 * serves would first be exercised in production. The policy is READ from the
 * Python constant (`SecurityHeadersMiddleware._CSP` in backend/main.py) at
 * test time rather than copied here, so the two cannot drift.
 */
import fs from 'node:fs';
import path from 'node:path';

const CSP_BLOCK = /_CSP\s*=\s*\(([\s\S]*?)\n\s*\)/;
const STRING_LITERAL = /"((?:[^"\\]|\\.)*)"/g;

export function readProductionCsp(frontendDir: string): string {
  const source = path.resolve(frontendDir, '..', 'backend', 'main.py');
  const block = CSP_BLOCK.exec(fs.readFileSync(source, 'utf8'));
  if (!block) {
    throw new Error(`Fixture harness could not find the _CSP constant in ${source}.`);
  }
  const policy = [...block[1].matchAll(STRING_LITERAL)].map((literal) => literal[1]).join('');
  if (!policy.includes("default-src 'self'") || !policy.includes('script-src')) {
    throw new Error(`Fixture harness parsed an implausible CSP from ${source}: "${policy}"`);
  }
  return policy;
}
