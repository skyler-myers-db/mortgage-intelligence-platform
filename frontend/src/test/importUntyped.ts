/**
 * Load a Node builtin (`node:fs`) or a repo tool module (tools/*.mjs) in a
 * Vitest file without a `@ts-expect-error` (W5e lint depth: no new
 * expect-error outside src/types/wireContract.*.check.ts). The app's types
 * exclude Node, so a static import of either would not type-check; a
 * non-literal dynamic specifier is not resolved by tsc, and the caller names
 * the shape it relies on.
 */
export async function importUntyped<T>(specifier: string | URL): Promise<T> {
  const href = specifier instanceof URL ? specifier.href : specifier;
  return (await import(/* @vite-ignore */ href)) as T;
}

/** The node:fs subset the lint-depth tests read with. */
export interface NodeFs {
  existsSync(path: string): boolean;
  readFileSync(path: string, encoding: 'utf8'): string;
  readdirSync(path: string, options: { recursive: true }): string[];
}
