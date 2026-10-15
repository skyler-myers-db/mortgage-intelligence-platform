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

// ---------------------------------------------------------------------------
// One name for the economic-incentive rule (2026-09-21 audit critic-11,
// D-platform-process-g step 2): "In the Money". The retired variants ("Prime
// Refi ...", "refi(nance) economics", "refi screen") are banned in string
// literals, template text and JSX text; src/lib/lexicon.ts is their only home
// (as input aliases). src/lib/lexiconBan.test.ts proves the selectors bite.
const LEXICON_RE = String.raw`/\bprime[- ]refi\w*|\brefi(?:nance)?[- ]economics\b|\brefi screen\b/i`;
const LEXICON_MESSAGE =
  "Retired name for the In the Money rule. Use LEXICON.inTheMoney from src/lib/lexicon.ts ('In the Money', 'in the money', 'in-the-money', 'In the Money rule').";
export const LEXICON_BAN = [
  { selector: `Literal[value=${LEXICON_RE}]`, message: LEXICON_MESSAGE },
  { selector: `TemplateElement[value.raw=${LEXICON_RE}]`, message: LEXICON_MESSAGE },
  { selector: `JSXText[value=${LEXICON_RE}]`, message: LEXICON_MESSAGE },
];

// Files that still hold a retired variant because another W5e lane renames
// them in parallel (each merges before w5-lexicon-copy). SHRINK-ONLY:
// lexiconBan.test.ts fails when an entry no longer reproduces a hit. Emptied
// by the integrator at the w5-lexicon-copy merge (2026-10-15); the export
// stays because lexiconBan.test.ts reads it. Never add an entry.
export const LEXICON_PENDING = {};

// Files that still carry a banned call, each with why.
// SHRINK-ONLY: src/lib/formattingBan.test.ts fails when an entry no longer
// reproduces a violation, so a file that is migrated leaves this list in the
// same change. Never add an entry: migrate the call site. (The wave-1c
// lane-owned exemption block was retired in wave 2; the last entry,
// commandActions.ts's selection verb labels, was migrated in wave 4.) Empty
// now; the export stays because formattingBan.test.ts reads it.
export const FORMATTING_ALLOWLIST = {};

// react-hooks/set-state-in-effect is an ERROR for production sources repo-wide
// (audit quality-10 step 4 / stack-09 item 5, W5e): a synchronous setState in
// an effect body renders twice for one change. Derive the value during
// render, or set state from an event, a callback or a subscription.
// SHRINK-ONLY: the files that still carried a hit on the W5e base, each
// with the W5e lane that owns its fix (w5-compiler-lint for the residual
// partition). src/lib/setStateInEffectScope.test.ts fails when an entry no
// longer reproduces a hit, so a fixed file leaves this list in the same
// change. Never add an entry: fix the effect.
export const SET_STATE_IN_EFFECT_PENDING = {
  "src/components/AppContext.tsx": "w5-compiler-lint (residual partition)",
  "src/components/activation/ActivationLoopPanel.tsx": "w5-compiler-lint",
  "src/components/feedback/Toaster.tsx": "w5-compiler-lint",
  "src/components/mortgage/EvidenceDrawer.tsx": "w5-compiler-lint (residual partition)",
  "src/components/mortgage/GenieChat.tsx": "w5-compiler-lint",
  "src/components/mortgage/GenieProgress.tsx": "w5-compiler-lint (residual partition)",
  "src/components/mortgage/GenieRefusalCard.tsx": "w5-compiler-lint",
  "src/components/mortgage/TriageDeck.tsx": "w5-compiler-lint (residual partition)",
  "src/components/mortgage/useGenieWindow.ts": "w5-compiler-lint (residual partition)",
  "src/components/ui/useMenuPlacement.ts": "w5-compiler-lint (residual partition)",
  "src/hooks/useRoutePending.ts": "w5-compiler-lint (residual partition)",
  "src/routes/admin-config.tsx": "w5-lexicon-copy",
  "src/routes/ask-genie.tsx": "w5-compiler-lint",
};

/** Fixture harness files may import TYPES from frontend/src, never runtime code. */
const FIXTURE_SRC_TYPE_ONLY = {
  group: ["**/src/**"],
  allowTypeImports: true,
  message: "Fixture harness files may only `import type` from frontend/src.",
};

// ---------------------------------------------------------------------------
// The title= ban (2026-09-21 audit critic-08). A native title is mouse-only:
// it appears late, in OS chrome outside the design system, never on keyboard
// focus or touch, and cannot show a shortcut, so the facts a reviewer needs
// were effectively hidden. ui/Tooltip (and the Chip/Button `tooltip` prop)
// replaces it. The second selector catches the five components that forward
// `title` to the DOM. SVG <title> ELEMENTS and an iframe's required title are
// untouched; other components' `title` props (PageShell, SectionHeader, ...)
// are their own API and are caught where they render. Pinned by
// src/lib/titleBan.test.ts; tools/lint_depth_probe.mjs embeds a copy.
export const TITLE_BAN = [
  {
    selector: "JSXOpeningElement[name.name=/^[a-z]/]:not([name.name='iframe']) > JSXAttribute[name.name='title']",
    message:
      'A native title= is mouse-only, appears late in OS chrome and never on keyboard focus or touch (audit critic-08). Use <Tooltip> (ui/Tooltip), or the Chip/Button `tooltip` prop. An icon-only control also needs an aria-label.',
  },
  {
    selector: "JSXOpeningElement[name.name=/^(Chip|Button|EvidenceChip|Link|NavLink)$/] > JSXAttribute[name.name='title']",
    message:
      'This component renders `title` as a native DOM title. Use the `tooltip` prop (Chip, Button), rely on the evidence hover card (EvidenceChip), or wrap the Link in <Tooltip>.',
  },
];

// Files that still carry a title= site, each '<owner lane> - <n> sites - <why>'.
// SHRINK-ONLY: src/lib/titleBan.test.ts fails when an entry no longer
// reproduces a TITLE_BAN hit, so a migrated file leaves this list in the same
// change. Never add an entry: migrate the call site (the R1-R6 rules in
// docs/testing.md "Lint depth"). The override block below keeps
// FORMATTING_BAN on for these files; it never turns the rule off.
export const TITLE_BAN_ALLOWLIST = {
  "src/components/Primitives.tsx": "integrator residual - 2 sites - the deprecated Chip/EvidenceChip/Button title passthrough, removed once no call site passes title",
  "src/components/mortgage/LeadTableRow.tsx": "w5-lead-table-compile - 6 sites - the file owner migrates its own sites (W5e)",
  "src/components/mortgage/LeadTableStatusChips.tsx": "w5-lead-table-compile - 5 sites - the file owner migrates its own sites (W5e)",
  "src/components/mortgage/DecisionReceipt.tsx": "w5-lead-table-compile - 1 site - the file owner migrates its own site (W5e)",
  "src/components/mortgage/LeadTableBulkActions.tsx": "w5-lead-table-compile - 1 site - the file owner migrates its own site (W5e)",
  "src/components/mortgage/LeadTableHeader.tsx": "w5-lead-queue-columns - 1 site - the file owner migrates its own site (W5e)",
  "src/routes/lead-queue.filterBar.tsx": "w5-lead-queue-columns - 1 site - the file owner migrates its own site (W5e)",
  "src/routes/borrower-360.tsx": "w5-lead-queue-columns - 2 sites - the synthetic-data note and the local Field; the file owner migrates its own sites (W5e)",
  "src/routes/analytics.equity-scatter.tsx": "w5-analytics - 3 sites - the file owner migrates its own sites (W5e)",
  "src/routes/analytics.charts.tsx": "w5-analytics - 1 site - the file owner migrates its own site (W5e)",
  "src/routes/ask-genie.answer-panel.tsx": "w5-lexicon-copy - 6 sites - the file owner migrates its own sites (W5e)",
  "src/routes/ask-genie.growth-agent.helpers.tsx": "w5-lexicon-copy - 2 sites - the file owner migrates its own sites (W5e)",
  "src/components/mortgage/GenieAnswer.tsx": "w5-lexicon-copy - 2 sites - the file owner migrates its own sites (W5e)",
  "src/components/mortgage/GenieAnswerToolbar.tsx": "w5-lexicon-copy - 2 sites - the file owner migrates its own sites (W5e)",
  "src/routes/admin-config.tsx": "w5-lexicon-copy - 1 site - the file owner migrates its own site (W5e)",
  "src/routes/portfolio-builder.governance.tsx": "w5-lexicon-copy - 1 site - the file owner migrates its own site (W5e)",
  "src/components/mortgage/EvidenceDrawerBody.tsx": "w5-lexicon-copy - 2 sites - the file owner migrates its own sites (W5e)",
  "src/components/mortgage/SegmentCard.tsx": "w5-lexicon-copy - 2 sites - the EvidenceChip title and the count title; the file owner migrates its own sites (W5e)",
};

// Typed lint (2026-09-21 audit quality-10 step 2, stack-07): three
// type-aware rules over production source, at their default options (no
// checksVoidReturn exemption). Pre-existing hits in files other W5e lanes own
// live in frontend/eslint-suppressions.json, ESLint's native bulk
// suppressions: shrink-only (an unused entry fails `eslint .`), pinned by
// src/test/eslintSuppressions.test.ts.
export const TYPED_LINT_RULES = {
  "@typescript-eslint/no-floating-promises": "error",
  "@typescript-eslint/no-misused-promises": "error",
  "@typescript-eslint/no-base-to-string": "error",
};

export default [
  {
    ignores: ["dist", "bundle-analyze", "node_modules", "*.config.*", "tsconfig.tsbuildinfo"],
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
      // See SET_STATE_IN_EFFECT_PENDING above (test files: the block below).
      "react-hooks/set-state-in-effect": "error",
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
  // react-hooks/set-state-in-effect gates production code only: a test's
  // harness component may drive state from an effect. The pending files
  // (SET_STATE_IN_EFFECT_PENDING) stay off until their owners fix them;
  // src/lib/setStateInEffectScope.test.ts pins both.
  {
    files: ["src/**/*.test.{ts,tsx}"],
    rules: {
      "react-hooks/set-state-in-effect": "off",
    },
  },
  // An empty `files` array throws in @eslint/config-array, so the override
  // exists only while the pending list has an entry.
  ...(Object.keys(SET_STATE_IN_EFFECT_PENDING).length
    ? [{ files: Object.keys(SET_STATE_IN_EFFECT_PENDING), rules: { "react-hooks/set-state-in-effect": "off" } }]
    : []),
  // Credential-free e2e fixture harness. Linted at the same bar as src, and
  // it may import TYPES from src (so fixtures are checked against the app's
  // own response contracts) but never runtime code: the harness must drive
  // the built app from outside, not link against it. The legacy specs beside
  // it get the same rules in the next block (and `npm run typecheck:e2e`),
  // without the type-only import ban.
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
          patterns: [FIXTURE_SRC_TYPE_ONLY],
        },
      ],
    },
  },
  // The legacy Playwright specs and helpers directly under tests/e2e (audit
  // stack-07 item 2 / quality-10 step 1): the same recommended rules as the
  // fixture harness. The glob is non-recursive; fixture/ keeps its own
  // blocks. No FIXTURE_SRC_TYPE_ONLY here: layout-stability.spec.ts imports
  // src/mocks/fixtureData at runtime by design, and the contract exporter
  // never loads a legacy spec.
  {
    files: ["tests/e2e/*.ts"],
    languageOptions: {
      parser: tsParser,
      parserOptions: { ecmaVersion: "latest", sourceType: "module" },
    },
    plugins: { "@typescript-eslint": tsPlugin },
    rules: {
      ...tsPlugin.configs.recommended.rules,
      "@typescript-eslint/no-unused-vars": ["warn", { argsIgnorePattern: "^_", varsIgnorePattern: "^_" }],
    },
  },
  // The fixture data the contract exporter loads on bare Node (audit
  // quality-09 step 3, tools/export_e2e_fixtures.mjs): Node strips types but
  // resolves every surviving import, so a type imported as a value would load
  // frontend/src at runtime, and a bare package would need node_modules.
  // node: builtins are fine. This block restates the src rule because a
  // later block's options replace an earlier block's for the same rule.
  // Type stripping erases only a whole `import type` / `export type`: an
  // all-inline `import { type A } from "x"` becomes `import {} from "x"`, and
  // `export { type A } from "x"` becomes `export {} from "x"`, which still
  // load "x". no-restricted-imports' allowTypeImports accepts both spellings,
  // so they are banned here outright (no-import-type-side-effects also fixes
  // the inline form consistent-type-imports' inline fix would produce).
  {
    files: [
      "tests/e2e/fixture/data/**/*.ts",
      "tests/e2e/fixture/registry.ts",
      "tests/e2e/fixture/mockApi.ts",
      "tests/e2e/fixture/contractSamples.ts",
    ],
    rules: {
      "@typescript-eslint/consistent-type-imports": ["error", { prefer: "type-imports", fixStyle: "inline-type-imports" }],
      "@typescript-eslint/no-import-type-side-effects": "error",
      "no-restricted-syntax": [
        "error",
        {
          selector: "ExportNamedDeclaration[source][exportKind='value']:not(:has(ExportSpecifier[exportKind='value']))",
          message:
            "Write `export type { … } from …`: Node's type stripping keeps `export { type A } from …` as a runtime load of that module, which the contract exporter runs on bare Node.",
        },
        {
          selector: "ImportExpression",
          message:
            "Fixture data is loaded by the contract exporter on bare Node: use a static relative import, or `import type`, never import().",
        },
      ],
      "@typescript-eslint/no-restricted-imports": [
        "error",
        {
          patterns: [
            FIXTURE_SRC_TYPE_ONLY,
            {
              regex: "^(?![./]|node:)",
              allowTypeImports: true,
              message:
                "Fixture data is loaded by the contract exporter on bare Node: import a package with `import type` only (node: builtins are fine).",
            },
          ],
        },
      ],
    },
  },
  // Import-hygiene guard: production code (routes, lib, non-test components)
  // must not silently pull from src/mocks. Tests may import
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
            // Test helpers (mount(), installLocalStorage, the CSS readers)
            // are test-only too (audit quality-06).
            {
              group: ["**/test/*"],
              message: "Production code must not import from src/test: those helpers are test-only.",
            },
            // The e2e fixture data and wire bases (W5e w5-wire-types B3).
            {
              group: ["**/tests/**"],
              message: "Production code must not import e2e fixture data: no mock fallback in the running app.",
            },
          ],
        },
      ],
    },
  },
  // The formatting contract (see FORMATTING_BAN above) and the lexicon (see
  // LEXICON_BAN). Tests and test helpers may build expected strings however
  // they like. Flat-config rule options REPLACE each other, so every block
  // below restates the bans that still apply to its files.
  {
    files: ["src/**/*.{ts,tsx}"],
    ignores: [
      "src/**/*.test.{ts,tsx}",
      "src/mocks/**",
      "src/test/**",
      ...FORMATTING_HOMES,
      "src/lib/lexicon.ts",
      ...Object.keys(LEXICON_PENDING),
    ],
    rules: {
      "no-restricted-syntax": ["error", ...FORMATTING_BAN, ...LEXICON_BAN, ...TITLE_BAN],
    },
  },
  // The lexicon home and the pending files: the formatting contract and the
  // title ban.
  {
    files: ["src/lib/lexicon.ts", ...Object.keys(LEXICON_PENDING)],
    rules: {
      "no-restricted-syntax": ["error", ...FORMATTING_BAN, ...TITLE_BAN],
    },
  },
  // The formatting homes: the lexicon and the title ban.
  {
    files: FORMATTING_HOMES,
    rules: {
      "no-restricted-syntax": ["error", ...LEXICON_BAN, ...TITLE_BAN],
    },
  },
  // TITLE_BAN_ALLOWLIST files keep every other restricted-syntax ban (the
  // formatting contract and the lexicon): flat-config options replace rather
  // than merge, so the override restates the array without TITLE_BAN. Never
  // 'off'. Guarded like the formatting override below: an empty `files`
  // array throws.
  ...(Object.keys(TITLE_BAN_ALLOWLIST).length
    ? [
        {
          files: Object.keys(TITLE_BAN_ALLOWLIST),
          ignores: ["src/**/*.test.{ts,tsx}"],
          rules: { "no-restricted-syntax": ["error", ...FORMATTING_BAN, ...LEXICON_BAN] },
        },
      ]
    : []),
  // An empty `files` array throws in @eslint/config-array ("Expected value
  // to be a non-empty array") and would break ALL lint, so the override
  // exists only while the allowlist has an entry. An allowlisted file keeps
  // the lexicon and title bans.
  ...(Object.keys(FORMATTING_ALLOWLIST).length
    ? [{ files: Object.keys(FORMATTING_ALLOWLIST), rules: { "no-restricted-syntax": ["error", ...LEXICON_BAN, ...TITLE_BAN] } }]
    : []),
  // Typed lint (see TYPED_LINT_RULES). projectService resolves each file's
  // program from tsconfig.json; tests, src/test and src/mocks stay untyped.
  {
    files: ["src/**/*.{ts,tsx}"],
    ignores: ["src/**/*.test.{ts,tsx}", "src/test/**", "src/mocks/**"],
    languageOptions: {
      parserOptions: { projectService: true, tsconfigRootDir: import.meta.dirname },
    },
    rules: { ...TYPED_LINT_RULES },
  },
];
