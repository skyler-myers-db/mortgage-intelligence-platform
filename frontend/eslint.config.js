import tsParser from "@typescript-eslint/parser";
import tsPlugin from "@typescript-eslint/eslint-plugin";
import reactHooks from "eslint-plugin-react-hooks";

// ---------------------------------------------------------------------------
// One number and date formatting contract (2026-09-21 audit, responsive-04
// and responsive-07). A bare `toLocaleString()` follows the BROWSER locale
// while currency is pinned to en-US, so separators disagreed on one screen;
// `toFixed` skips grouping and hand-rolls signs ("$-4.41M", "-0.0%");
// `toLocaleDateString` on `new Date(raw)` shifts naive UTC wire timestamps
// into local time; and an `Intl.*Format` built per call costs a formatter per
// table cell. Numbers go through src/lib/formatters.ts, dates and times
// through src/lib/time.ts (and <Timestamp>), and the two machine-facing uses
// of toFixed (SVG geometry, data rounding) through src/lib/fixedPrecision.ts.
// Those three files are the only homes of these calls.
const FORMATTING_HOMES = ["src/lib/formatters.ts", "src/lib/time.ts", "src/lib/fixedPrecision.ts"];

const INTL_FORMATTERS = "/^(NumberFormat|DateTimeFormat|RelativeTimeFormat)$/";
export const FORMATTING_BAN = [
  {
    selector: "CallExpression[callee.property.name='toLocaleString'][arguments.length=0]",
    message: "Bare toLocaleString() follows the browser locale. Use formatCount / formatNumber / formatUsd from lib/formatters (dates: lib/time).",
  },
  {
    selector: "CallExpression[callee.property.name='toLocaleString'][arguments.0.type='Identifier'][arguments.0.name='undefined']",
    message: "toLocaleString(undefined, …) follows the browser locale. Use lib/formatters (numbers) or lib/time (dates).",
  },
  {
    selector: "CallExpression[callee.property.name='toFixed']",
    message: "toFixed renders ungrouped, hand-signed numbers. Display: formatFixed / pct / ratePct from lib/formatters. SVG geometry or data rounding: fixedAttr / roundTo from lib/fixedPrecision.",
  },
  {
    selector: "CallExpression[callee.property.name=/^toLocale(Date|Time)String$/]",
    message: "Dates go through lib/time (formatDate, formatDateTimeShort, formatRelative) or <Timestamp>, which parse naive UTC wire timestamps correctly and name the zone.",
  },
  {
    selector: `NewExpression[callee.object.name='Intl'][callee.property.name=${INTL_FORMATTERS}]`,
    message: "Intl formatters are built once in lib/formatters (numbers) or lib/time (dates); import a formatter instead of constructing one here.",
  },
  {
    selector: `CallExpression[callee.object.name='Intl'][callee.property.name=${INTL_FORMATTERS}]`,
    message: "Intl formatters are built once in lib/formatters (numbers) or lib/time (dates); import a formatter instead of constructing one here.",
  },
];

// Files outside any other lane that still carry a banned call, each with why.
// SHRINK-ONLY: src/lib/formattingBan.test.ts fails when an entry no longer
// reproduces a violation, so a file that is migrated leaves this list in the
// same change. Never add an entry: migrate the call site.
export const FORMATTING_ALLOWLIST = {
  "src/components/mortgage/EvidenceDrawer.tsx":
    "lineage event_count label; the wave-1c formatters lane was scoped to this file's formatNumber only",
};

// Files other wave-1c lanes own while this ban lands (each lane migrates its
// own call sites). Globs, so a file a lane creates in parallel does not break
// the merged lint. Retire at wave-1c integration: rerun the ban without this
// block and move whatever still reproduces into FORMATTING_ALLOWLIST.
export const WAVE_1C_LANE_OWNED = [
  // queue-keyboard-review
  "src/components/mortgage/LeadTable*.{ts,tsx}",
  "src/components/mortgage/LeadRowPreview*.{ts,tsx}",
  "src/components/mortgage/useLeadTableHotkeys*.{ts,tsx}",
  "src/components/mortgage/useLeadApprovalActions*.{ts,tsx}",
  "src/lib/keymap*.{ts,tsx}",
  "src/**/ShortcutOverlay*.{ts,tsx}",
  "src/components/command/CommandPalette*.{ts,tsx}",
  "src/components/command/commandActions*.{ts,tsx}",
  // shell-wayfinding
  "src/components/layout/Topbar*.{ts,tsx}",
  "src/components/layout/RouteNav*.{ts,tsx}",
  "src/app.tsx",
  "src/lib/routeMeta*.{ts,tsx}",
  "src/lib/routePreloaders*.{ts,tsx}",
  "src/routes/borrower-360*.{ts,tsx}",
  "src/**/IdentityMenu*.{ts,tsx}",
  // offer-orchestrator
  "src/routes/offer-orchestrator*.{ts,tsx}",
  // session-recovery
  "src/lib/api.ts",
  "src/lib/apiTransport.ts",
  "src/lib/apiClients/**/*.{ts,tsx}",
  "src/lib/queryClient*.{ts,tsx}",
  "src/components/HealthProvider*.{ts,tsx}",
  "src/components/mortgage/DegradedBanner*.{ts,tsx}",
  "src/components/ui/Skeleton*.{ts,tsx}",
  // feedback-guard
  "src/main.tsx",
  "src/**/useUnsavedGuard*.{ts,tsx}",
  "src/**/Toaster*.{ts,tsx}",
  "src/routes/portfolio-builder*.{ts,tsx}",
];
// The formatters lane migrated these two portfolio-builder files, so the
// feedback-guard glob above must not re-open them.
const WAVE_1C_FORMATTERS_OWNED = [
  "src/routes/portfolio-builder.logic.ts",
  "src/routes/portfolio-builder.components.tsx",
];

// Files where react-hooks/set-state-in-effect is already an error (see the
// block after the main config). Grow it as files leave effect-driven state.
export const SET_STATE_IN_EFFECT_SCOPE = [
  "src/components/mortgage/useLeadApprovalActions.ts",
  "src/components/mortgage/useLeadSalesActions.ts",
  "src/components/mortgage/ApprovalBanner.tsx",
  "src/routes/lead-queue.tsx",
  "src/lib/mutations/*.ts",
];

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
  // The files the query-layer migration has converted (wave 2, audit
  // stack-09): their writes run on useMutation and their reads on useQuery,
  // so a synchronous setState in an effect body is a regression here. The
  // repo-wide flip (and the TODO above) stays with the wave-4 lint lane.
  // src/lib/setStateInEffectScope.test.ts pins exactly this scope.
  {
    files: SET_STATE_IN_EFFECT_SCOPE,
    ignores: ["src/**/*.test.{ts,tsx}"],
    rules: {
      "react-hooks/set-state-in-effect": "error",
    },
  },
  // Credential-free e2e fixture harness. Linted at the same bar as src, and
  // it may import TYPES from src (so fixtures are checked against the app's
  // own response contracts) but never runtime code: the harness must drive
  // the built app from outside, not link against it. The legacy specs beside
  // it are not linted yet; their known type errors are tracked separately.
  {
    files: ["tests/e2e/fixture/**/*.ts"],
    languageOptions: {
      parser: tsParser,
      parserOptions: { ecmaVersion: "latest", sourceType: "module" },
    },
    plugins: { "@typescript-eslint": tsPlugin },
    rules: {
      ...tsPlugin.configs.recommended.rules,
      "@typescript-eslint/no-unused-vars": ["warn", { argsIgnorePattern: "^_", varsIgnorePattern: "^_" }],
      "@typescript-eslint/no-restricted-imports": [
        "error",
        {
          patterns: [
            {
              group: ["**/src/**"],
              allowTypeImports: true,
              message: "Fixture harness files may only `import type` from frontend/src.",
            },
          ],
        },
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
  // The formatting contract (see FORMATTING_BAN above). Tests, stories and
  // test helpers may build expected strings however they like.
  {
    files: ["src/**/*.{ts,tsx}"],
    ignores: [
      "src/**/*.test.{ts,tsx}",
      "src/**/*.stories.{ts,tsx}",
      "src/mocks/**",
      "src/test/**",
      ...FORMATTING_HOMES,
    ],
    rules: {
      "no-restricted-syntax": ["error", ...FORMATTING_BAN],
    },
  },
  {
    files: [...Object.keys(FORMATTING_ALLOWLIST), ...WAVE_1C_LANE_OWNED],
    ignores: WAVE_1C_FORMATTERS_OWNED,
    rules: {
      "no-restricted-syntax": "off",
    },
  },
];
