import { afterEach, describe, expect, it } from 'vitest';
// @ts-expect-error Frontend app types intentionally exclude Node globals; this
// unit test runs the build tools under Vitest only.
import { spawnSync } from 'node:child_process';
// @ts-expect-error Node built-in, Vitest only (see above).
import { copyFileSync, mkdirSync, mkdtempSync, rmSync, symlinkSync } from 'node:fs';
// @ts-expect-error Node built-in, Vitest only (see above).
import { tmpdir } from 'node:os';
// @ts-expect-error Node built-in, Vitest only (see above).
import path from 'node:path';

/**
 * Audit bundle-08 item 2: each build gate decides whether it is the main
 * module by comparing its own URL with argv[1]. Node runs a symlinked script
 * under its REAL path, so the old `path.resolve(argv[1]) === <own path>`
 * guard was false through a symlink and the gate silently exited 0.
 *
 * Each tool is copied into a scratch repo that is missing its inputs (no
 * built dist, no node_modules), a planted failure, and run through a symlink
 * that lives outside that repo. A realpath-safe guard runs main(), which
 * fails; the old guard skipped it and exited 0. The direct run is the
 * control: the planted failure is real.
 */

interface Spawned {
  status: number | null;
  stdout: string;
  stderr: string;
}

const nodeProcess = (globalThis as unknown as { process: { execPath: string; cwd(): string } }).process;
const repoRoot = path.resolve(nodeProcess.cwd(), '..');

const TOOLS = [
  { script: 'check_frontend_budgets.mjs', siblings: ['build_manifest.mjs'], args: [] as string[] },
  { script: 'postbuild_artifacts.mjs', siblings: ['build_manifest.mjs'], args: [] as string[] },
  {
    script: 'react_compiler_coverage.mjs',
    siblings: ['react_compiler_allowlist.mjs'],
    args: ['--check', 'tools/react_compiler_allowlist.json'],
  },
];

const scratch: string[] = [];

function scratchRepo(tool: (typeof TOOLS)[number]): { real: string; link: string; root: string } {
  const root = mkdtempSync(path.join(tmpdir(), 'mip-tool-guard-'));
  scratch.push(root);
  mkdirSync(path.join(root, 'repo', 'tools'), { recursive: true });
  mkdirSync(path.join(root, 'repo', 'frontend'), { recursive: true });
  for (const file of [tool.script, ...tool.siblings]) {
    copyFileSync(path.join(repoRoot, 'tools', file), path.join(root, 'repo', 'tools', file));
  }
  const real = path.join(root, 'repo', 'tools', tool.script);
  const link = path.join(root, 'bin', tool.script);
  mkdirSync(path.join(root, 'bin'));
  symlinkSync(real, link);
  return { real, link, root };
}

function run(script: string, args: string[], cwd: string): Spawned {
  const result = spawnSync(nodeProcess.execPath, [script, ...args], { cwd, encoding: 'utf8', timeout: 60_000 });
  return { status: result.status, stdout: result.stdout, stderr: result.stderr };
}

afterEach(() => {
  for (const dir of scratch.splice(0)) rmSync(dir, { recursive: true, force: true });
});

describe('build tool main guards run through a symlink', () => {
  it.each(TOOLS)('$script runs main() when invoked through a symlink', (tool) => {
    const { real, link, root } = scratchRepo(tool);
    const cwd = path.join(root, 'repo');

    const direct = run(real, tool.args, cwd);
    const viaLink = run(link, tool.args, cwd);

    expect(direct.status, `control: the planted failure fails the direct run\n${direct.stderr}`).not.toBe(0);
    expect(viaLink.status, `through the symlink the gate ran and failed\n${viaLink.stderr}`).not.toBe(0);
    expect(viaLink.stderr).toBe(direct.stderr);
  }, 120_000);
});
