import tsParser from "@typescript-eslint/parser";
import tsPlugin from "@typescript-eslint/eslint-plugin";
import reactHooks from "eslint-plugin-react-hooks";

export default [
  {
    ignores: ["dist", "node_modules", "*.config.*", "tsconfig.tsbuildinfo"],
  },
  {
    files: ["src/**/*.{ts,tsx}"],
    languageOptions: {
      parser: tsParser,
      parserOptions: {
        ecmaVersion: "latest",
        sourceType: "module",
        ecmaFeatures: { jsx: true },
      },
      globals: {
        window: "readonly",
        document: "readonly",
        fetch: "readonly",
        console: "readonly",
        setTimeout: "readonly",
        clearTimeout: "readonly",
      },
    },
    plugins: {
      "@typescript-eslint": tsPlugin,
      "react-hooks": reactHooks,
    },
    rules: {
      ...tsPlugin.configs.recommended.rules,
      ...reactHooks.configs["recommended-latest"].rules,
      // The current route layer still has legitimate external-system sync
      // effects while the remaining query-layer migration is in progress.
      // Keep compiler-safety rules on, but do not fail CI on this advisory
      // rule until those effects are converted to query/mutation ownership.
      // TODO(2026-07-15, audit P2-14): re-enable after the post-Summit
      // query-layer migration (tracked in docs/modernization-todo.md
      // alongside the LeadTable/analytics 'use no memo' pragmas — same
      // root cause, same removal slice).
      "react-hooks/set-state-in-effect": "off",
      "@typescript-eslint/no-unused-vars": ["warn", { argsIgnorePattern: "^_", varsIgnorePattern: "^_" }],
      // Re-audit #4 (2026-06-12): the Portfolio Builder save used to call
      // window.prompt() — a synchronous native dialog that froze the
      // renderer (hard hang under any CDP/Playwright session) and broke
      // the enterprise posture. A one-file source-pin test caught that
      // specific site; this bans the whole class repo-wide so no future
      // surface can reintroduce alert/confirm/prompt. Use in-page forms,
      // status callouts, and the ApprovalBanner pattern instead.
      "no-restricted-globals": [
        "error",
        { name: "prompt", message: "Native prompt() blocks the renderer (it froze the Save flow and hangs automation). Use an in-page form — see the portfolio-builder save panel." },
        { name: "alert", message: "Native alert() blocks the renderer and is un-themeable. Use a status callout / ApprovalBanner." },
        { name: "confirm", message: "Native confirm() blocks the renderer and is un-themeable. Use an in-page confirm affordance (see pendingReject in LeadTable)." },
      ],
      "no-restricted-properties": [
        "error",
        { object: "window", property: "prompt", message: "window.prompt blocks the renderer. Use an in-page form." },
        { object: "window", property: "alert", message: "window.alert blocks the renderer. Use a status callout." },
        { object: "window", property: "confirm", message: "window.confirm blocks the renderer. Use an in-page confirm affordance." },
      ],
    },
  },
  // Import-hygiene guard: production code (routes, lib, non-test components)
  // must not silently pull from src/mocks. Tests + Storybook may import
  // fixture data; production code must not. Regressions here re-introduce
  // the "silent mock fallback" pattern that CLAUDE.md explicitly forbids.
  {
    files: [
      "src/routes/**/*.{ts,tsx}",
      "src/lib/**/*.{ts,tsx}",
      "src/components/**/*.{ts,tsx}",
      "src/app.tsx",
      "src/main.tsx",
    ],
    ignores: [
      "src/**/*.test.{ts,tsx}",
      "src/**/*.stories.{ts,tsx}",
    ],
    rules: {
      "no-restricted-imports": [
        "error",
        {
          patterns: [
            {
              group: ["**/mocks/*", "**/mocks"],
              message:
                "Production code must not import from src/mocks — those fixtures are test-only. See CLAUDE.md 'Negative prompting': no mock fallback in the running app.",
            },
          ],
        },
      ],
    },
  },
];
