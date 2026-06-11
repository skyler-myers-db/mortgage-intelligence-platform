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
