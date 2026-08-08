---
name: project-e2e-specs-unchecked
description: frontend/tests/e2e is typechecked by nothing — tsconfig includes only src, and eslint's TS block is files:["src/**"]. Verify Playwright spec edits with a one-off tsc project.
metadata:
  type: project
---

**`frontend/tests/e2e/*.spec.ts` is checked by neither `tsc` nor `eslint`.**
`frontend/tsconfig.json` has `"include": ["src"]`, and `frontend/eslint.config.js`
scopes its TypeScript block to `files: ["src/**/*.{ts,tsx}"]`. So
`npm --prefix frontend run lint` and `run build` both pass with a Playwright
spec full of type errors — a renamed helper or a deleted field on a shared
type will sail straight through local validation and CI.

**Why:** discovered 2026-08-08 while re-keying four geo specs off a removed
`MapDrillTarget.countyFips`. Lint and build were green with stale references
still in the tree; only an explicit typecheck found them.

**How to apply:**

1. After editing any `frontend/tests/e2e/*.spec.ts`, typecheck it explicitly:
   ```
   cd frontend && cat > tsconfig.e2echeck.json <<'JSON'
   { "extends": "./tsconfig.json",
     "compilerOptions": { "types": ["node"] },
     "include": ["tests/e2e", "src"] }
   JSON
   ./node_modules/.bin/tsc -p tsconfig.e2echeck.json --noEmit --pretty false
   rm -f tsconfig.e2echeck.json
   ```
2. **Expect pre-existing noise** — `growth_agent_live.spec.ts`,
   `home_summary.spec.ts`, and several `src/**` files with
   `@ts-expect-error` directives already report errors under that ad-hoc
   config. Read the output filtered to the files you touched; do not "fix"
   the rest.
3. When a shared type changes, `grep` the whole `tests/e2e` directory for the
   removed field before you finish. Four specs referenced the geo drill
   target, not the one the task named.
4. Live specs also pin **claims about the data**, not just selectors. When a
   grain dies, hunt for assertions like "should expose at least one populated
   county" — see [[project-live-spec-stale-assertions]].
