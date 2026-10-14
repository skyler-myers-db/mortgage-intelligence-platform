/**
 * The storage registry gate (D-identity-review-b part D): every browser
 * storage key the app touches is classified in lib/actorScope's registry,
 * and only the reviewed files touch storage at all.
 *
 * Over every non-test source file under frontend/src and frontend/public,
 * parsed with the TypeScript compiler (comments and JSX text never count):
 *   (i)   only STORAGE_FILES may name localStorage / sessionStorage (property
 *         names included) or the Storage type;
 *   (ii)  in those files, every getItem / setItem / removeItem / key call on
 *         ANY receiver takes a literal (or a const, local or imported,
 *         initialised by one) naming a registered key, or sits in a reviewed
 *         INDIRECTION function; only actorScope and TRANSITIONAL rows may
 *         name a PRIVATE, PREFERENCE, STAMP or LEGACY key;
 *   (iii) no .clear(), element access, Object.keys/values/entries or for-in
 *         over either storage, anywhere;
 *   (iv)  every /^mip[.-]/ string is a registered key or a reviewed non-key
 *         (Unity Catalog names, the view-transition names, CSV file names,
 *         the Genie turn Web Lock name);
 *   (v)   non-vacuity: the scanner finds the registered keys, and four
 *         injected sources each fail.
 *
 * Adding a storage key: register it in lib/actorScope.ts (docs/testing.md,
 * "Owned storage in fixture specs"). Node environment: reads files only.
 */
// @ts-expect-error Frontend app types intentionally exclude Node globals; this
// unit test reads the source tree under Vitest only.
import { readFileSync, readdirSync } from 'node:fs';
import ts from 'typescript';
import { describe, expect, it } from 'vitest';
import { ACTOR_SCOPE_REGISTRY, PREREGISTERED } from './actorScope';

const FRONTEND = decodeURIComponent(new URL('../../', import.meta.url).pathname);

interface Source {
  /** Path relative to frontend/, with forward slashes. */
  path: string;
  text: string;
}

type KeyClass = keyof typeof ACTOR_SCOPE_REGISTRY;

// --------------------------------------------------------------- the tables

const ACTOR_SCOPE_FILE = 'src/lib/actorScope.ts';

/** The only files that may name browser storage. */
const STORAGE_FILES: readonly string[] = [
  'src/components/AppContext.tsx',
  'src/lib/themePreference.ts',
  'src/components/mortgage/useGenieWindow.ts',
  'src/hooks/scrollOffsetStore.ts',
  'src/lib/rum.ts',
  'src/lib/rumApiRoute.ts',
  'src/lib/staleChunkRecovery.ts',
  'public/theme-boot.js',
  ACTOR_SCOPE_FILE,
];

/** Stores converted to lib/actorScope's guarded accessors: storage-free. */
const GUARDED_STORES: readonly string[] = [
  'src/lib/pinnedInsights.ts',
  'src/routes/portfolio-builder.draft.ts',
  'src/lib/genieConversation.ts',
  'src/lib/genieConversationStore.ts',
  'src/lib/genieInFlightRecord.ts',
  'src/lib/queueContext.ts',
  'src/lib/queueContextPublish.ts',
  'src/lib/keymapPreference.ts',
  'src/components/layout/GenieDock.tsx',
  'src/components/mortgage/bulkApproveStash.ts',
];

interface IndirectionRow {
  file: string;
  /** Functions whose storage calls take the key as a parameter. */
  functions: readonly string[];
  /** The keys that reach them (checked registered). 'ALL': the registry itself. */
  keys: readonly string[] | 'ALL';
  /** The functions land with that lane; exempt from the exists check until then. */
  pendingLane?: string;
  /** TRANSITIONAL: a file that may still name these PRIVATE keys directly. */
  removedBy?: string;
}

const THEME = 'mip.theme';
const ACCENT = 'mip.accent';
const DENSITY = 'mip.density';
const CONSOLE = 'mip.consoleOpen';
const THEME_CHOSEN = 'mip.themeChosen';
const ACCENT_CHOSEN = 'mip.accentChosen';

const INDIRECTION: readonly IndirectionRow[] = [
  { file: 'src/components/AppContext.tsx', functions: ['readStoredBool'], keys: [CONSOLE] },
  {
    file: 'src/lib/themePreference.ts',
    functions: ['readStoredChoice'],
    keys: [THEME, ACCENT, DENSITY, THEME_CHOSEN, ACCENT_CHOSEN],
  },
  {
    // D-theme-nav-a (w5-design-contract): the chosen-theme readers/persisters.
    file: 'src/lib/themePreference.ts',
    functions: [
      'readThemePreference',
      'readAccentPreference',
      'persistThemePreference',
      'persistAccent',
      'persistDensity',
      'writeStoredChoice',
      'storedValue',
    ],
    keys: [THEME, THEME_CHOSEN, ACCENT, ACCENT_CHOSEN, DENSITY],
  },
  { file: 'src/hooks/scrollOffsetStore.ts', functions: ['readOffsets', 'writeOffsets'], keys: ['mip.mainScroll.v1', 'mip.leadTableScroll.v1'] },
  { file: 'src/lib/rumApiRoute.ts', functions: ['isApiCallSampled'], keys: ['mip.rumApiSample'] },
  { file: 'src/lib/staleChunkRecovery.ts', functions: ['claimStaleChunkReload'], keys: ['mip.staleChunkReloadAt'] },
  {
    file: 'public/theme-boot.js',
    functions: ['stored'],
    keys: [THEME, ACCENT, DENSITY, CONSOLE, THEME_CHOSEN, ACCENT_CHOSEN],
  },
  { file: ACTOR_SCOPE_FILE, functions: ['rawRead', 'rawWrite'], keys: 'ALL' },
];

/** Non-key `mip` strings, each reviewed. */
const UC_NAME = /^mip\.(gold|silver|ref|semantics|first_party)(\.|$)/;
/** View Transition class and element names; lib/borrowerMorph.ts names the borrower-id morph pair. */
const VIEW_TRANSITION_NAMES = new Set(['mip-route-enter', 'mip-route-exit', 'mip-borrower-id']);
/** lib/genieTurnLock.ts: `mip-genie-turn:<messageId>` is a Web Lock name, not a storage key. */
const WEB_LOCK_HEADS = new Set(['mip-genie-turn:']);
/** components/ui/tooltipController.ts: the shared tooltip popup's DOM id, not a storage key. */
const DOM_IDS = new Set(['mip-tooltip']);
/** The build-written `<meta name>`s (responsive-10; lib/themePreference.ts, tenantAppearancePlugin.ts, theme-boot.js), not storage keys. */
const META_NAMES = new Set(['mip-default-theme', 'mip-default-accent', 'mip-lender-mark', 'mip-lender-mark-lender']);

const PRIVILEGED_CLASSES: ReadonlySet<KeyClass> = new Set([
  'PRIVATE_LOCAL',
  'PRIVATE_SESSION',
  'ACTOR_PREFERENCE_LOCAL',
  'STAMPS',
  'LEGACY',
]);
const STORAGE_METHODS = new Set(['getItem', 'setItem', 'removeItem', 'key']);
const STORAGE_NAMES = new Set(['localStorage', 'sessionStorage']);

// --------------------------------------------------------------- the registry

function registryClasses(): Map<string, KeyClass[]> {
  const classes = new Map<string, KeyClass[]>();
  for (const [name, entry] of Object.entries(ACTOR_SCOPE_REGISTRY) as Array<[KeyClass, unknown]>) {
    const keys = Array.isArray(entry) ? (entry as string[]) : Object.values(entry as Record<string, string>);
    for (const key of keys) classes.set(key, [...(classes.get(key) ?? []), name]);
  }
  return classes;
}

const CLASSES = registryClasses();
const classOf = (key: string): KeyClass | null => CLASSES.get(key)?.[0] ?? null;

// --------------------------------------------------------------- the sources

function isTestPath(path: string): boolean {
  return (
    /\.(test|spec|test-support|stories)\.[cm]?[jt]sx?$/.test(path) ||
    path.endsWith('.d.ts') ||
    path.startsWith('src/test/')
  );
}

function readTree(root: 'src' | 'public'): Source[] {
  return (readdirSync(`${FRONTEND}${root}/`, { recursive: true }) as string[])
    .map((entry) => `${root}/${entry.split('\\').join('/')}`)
    .filter((path) => /\.(tsx?|[cm]?js)$/.test(path) && !isTestPath(path))
    .sort()
    .map((path) => ({ path, text: readFileSync(`${FRONTEND}${path}`, 'utf8') as string }));
}

const SOURCES: readonly Source[] = [...readTree('src'), ...readTree('public')];

/** Parsed once per path and text: an injected scan reparses only its own file. */
const parsed = new Map<string, { text: string; file: ts.SourceFile }>();

function parse(source: Source): ts.SourceFile {
  const hit = parsed.get(source.path);
  if (hit && hit.text === source.text) return hit.file;
  const kind = source.path.endsWith('.tsx')
    ? ts.ScriptKind.TSX
    : source.path.endsWith('.ts')
      ? ts.ScriptKind.TS
      : ts.ScriptKind.JS;
  const file = ts.createSourceFile(source.path, source.text, ts.ScriptTarget.Latest, true, kind);
  parsed.set(source.path, { text: source.text, file });
  return file;
}

function walk(node: ts.Node, visit: (node: ts.Node) => void): void {
  visit(node);
  ts.forEachChild(node, (child) => walk(child, visit));
}

function lineOf(file: ts.SourceFile, node: ts.Node): number {
  return file.getLineAndCharacterOfPosition(node.getStart(file)).line + 1;
}

// --------------------------------------------------------------- const resolution

/** `const NAME = 'literal'` anywhere in the file (exported or not). */
function constLiterals(file: ts.SourceFile): Map<string, string> {
  const found = new Map<string, string>();
  walk(file, (node) => {
    if (!ts.isVariableDeclarationList(node) || !(node.flags & ts.NodeFlags.Const)) return;
    for (const declaration of node.declarations) {
      const init = declaration.initializer;
      if (ts.isIdentifier(declaration.name) && init && ts.isStringLiteralLike(init)) {
        found.set(declaration.name.text, init.text);
      }
    }
  });
  return found;
}

function resolveModule(fromPath: string, specifier: string, byPath: Map<string, ts.SourceFile>): ts.SourceFile | null {
  if (!specifier.startsWith('.')) return null;
  const parts = fromPath.split('/').slice(0, -1);
  for (const segment of specifier.split('/')) {
    if (segment === '..') parts.pop();
    else if (segment !== '.' && segment !== '') parts.push(segment);
  }
  const base = parts.join('/');
  for (const candidate of [base, `${base}.ts`, `${base}.tsx`, `${base}.js`, `${base}/index.ts`]) {
    const file = byPath.get(candidate);
    if (file) return file;
  }
  return null;
}

/** Local consts plus named imports of consts from other scanned files. */
function resolvableConsts(file: ts.SourceFile, byPath: Map<string, ts.SourceFile>): Map<string, string> {
  const consts = constLiterals(file);
  for (const statement of file.statements) {
    if (!ts.isImportDeclaration(statement) || !ts.isStringLiteral(statement.moduleSpecifier)) continue;
    const bindings = statement.importClause?.namedBindings;
    if (!bindings || !ts.isNamedImports(bindings)) continue;
    const target = resolveModule(file.fileName, statement.moduleSpecifier.text, byPath);
    if (!target) continue;
    const exported = constLiterals(target);
    for (const element of bindings.elements) {
      const value = exported.get((element.propertyName ?? element.name).text);
      if (value !== undefined) consts.set(element.name.text, value);
    }
  }
  return consts;
}

// --------------------------------------------------------------- the checks

function enclosingFunctionName(node: ts.Node): string | null {
  for (let current: ts.Node | undefined = node.parent; current; current = current.parent) {
    if ((ts.isFunctionDeclaration(current) || ts.isMethodDeclaration(current)) && current.name) {
      return current.name.getText();
    }
    if (ts.isFunctionExpression(current) && current.name) return current.name.text;
    if (ts.isFunctionExpression(current) || ts.isArrowFunction(current)) {
      const parent = current.parent;
      if (ts.isVariableDeclaration(parent) && ts.isIdentifier(parent.name)) return parent.name.text;
      if (ts.isPropertyAssignment(parent)) return parent.name.getText();
    }
  }
  return null;
}

function namesStorage(node: ts.Node): boolean {
  if (ts.isIdentifier(node) && STORAGE_NAMES.has(node.text)) return true;
  return ts.isTypeReferenceNode(node) && ts.isIdentifier(node.typeName) && node.typeName.text === 'Storage';
}

/** `localStorage`, `window.sessionStorage`, `globalThis.localStorage`, `self...`. */
function isStorageRef(node: ts.Node): boolean {
  if (ts.isParenthesizedExpression(node)) return isStorageRef(node.expression);
  if (ts.isIdentifier(node)) return STORAGE_NAMES.has(node.text);
  return (
    ts.isPropertyAccessExpression(node) &&
    STORAGE_NAMES.has(node.name.text) &&
    ts.isIdentifier(node.expression) &&
    ['window', 'globalThis', 'self'].includes(node.expression.text)
  );
}

function mipLiteral(node: ts.Node): string | null {
  if (ts.isStringLiteral(node) || ts.isNoSubstitutionTemplateLiteral(node) || ts.isTemplateHead(node)) {
    return /^mip[.-]/.test(node.text) ? node.text : null;
  }
  return null;
}

function isReviewedNonKey(node: ts.Node, text: string): boolean {
  if (UC_NAME.test(text) || VIEW_TRANSITION_NAMES.has(text) || DOM_IDS.has(text) || META_NAMES.has(text) || text.endsWith('.csv')) return true;
  if (!ts.isTemplateHead(node)) return false;
  if (WEB_LOCK_HEADS.has(text)) return true;
  const template = node.parent as ts.TemplateExpression;
  const last = template.templateSpans[template.templateSpans.length - 1];
  return last.literal.text.endsWith('.csv');
}

interface ScanResult {
  violations: string[];
  /** Registered keys found as literals anywhere. */
  foundKeys: Set<string>;
  /** Files that name storage at all. */
  storageFiles: Set<string>;
}

/** Scan `sources` (every one resolves imports; `only`, when given, limits the walked files). */
function scan(sources: readonly Source[], only?: ReadonlySet<string>): ScanResult {
  const files = sources.map(parse);
  const byPath = new Map(files.map((file) => [file.fileName, file]));
  const violations: string[] = [];
  const foundKeys = new Set<string>();
  const storageFiles = new Set<string>();

  for (const file of files) {
    if (only && !only.has(file.fileName)) continue;
    const path = file.fileName;
    const listed = STORAGE_FILES.includes(path);
    const rows = INDIRECTION.filter((row) => row.file === path);
    const indirection = new Set(rows.flatMap((row) => row.functions));
    const transitional = new Set(rows.filter((row) => row.removedBy).flatMap((row) => (row.keys === 'ALL' ? [] : row.keys)));
    const consts = listed ? resolvableConsts(file, byPath) : new Map<string, string>();

    walk(file, (node) => {
      const at = `${path}:${lineOf(file, node)}`;
      if (namesStorage(node)) {
        storageFiles.add(path);
        if (!listed) violations.push(`(i) ${at} names browser storage outside STORAGE_FILES`);
      }

      if (listed && ts.isCallExpression(node) && ts.isPropertyAccessExpression(node.expression)) {
        const method = node.expression.name.text;
        if (STORAGE_METHODS.has(method)) {
          const [first] = node.arguments;
          const key = !first
            ? null
            : ts.isStringLiteralLike(first)
              ? first.text
              : ts.isIdentifier(first)
                ? (consts.get(first.text) ?? null)
                : null;
          if (key === null) {
            const fn = enclosingFunctionName(node);
            if (!fn || !indirection.has(fn)) {
              violations.push(`(ii) ${at} .${method}() with an unresolved key outside a reviewed INDIRECTION function (${fn ?? 'top level'})`);
            }
          } else {
            const keyClass = classOf(key);
            if (keyClass === null) violations.push(`(ii) ${at} .${method}('${key}') names an unregistered key`);
            else if (PRIVILEGED_CLASSES.has(keyClass) && path !== ACTOR_SCOPE_FILE && !transitional.has(key)) {
              violations.push(`(ii) ${at} .${method}('${key}') touches a ${keyClass} key outside lib/actorScope`);
            }
          }
        }
      }

      if (ts.isCallExpression(node) && ts.isPropertyAccessExpression(node.expression)) {
        const callee = node.expression;
        if (callee.name.text === 'clear' && isStorageRef(callee.expression)) {
          violations.push(`(iii) ${at} clears a whole storage area`);
        }
        if (
          ts.isIdentifier(callee.expression) &&
          callee.expression.text === 'Object' &&
          ['keys', 'values', 'entries'].includes(callee.name.text) &&
          node.arguments.some(isStorageRef)
        ) {
          violations.push(`(iii) ${at} enumerates a storage area`);
        }
      }
      if (ts.isElementAccessExpression(node) && isStorageRef(node.expression)) {
        violations.push(`(iii) ${at} indexes a storage area`);
      }
      if (ts.isForInStatement(node) && isStorageRef(node.expression)) {
        violations.push(`(iii) ${at} iterates a storage area`);
      }

      const literal = mipLiteral(node);
      if (literal !== null) {
        if (CLASSES.has(literal)) foundKeys.add(literal);
        else if (!isReviewedNonKey(node, literal)) {
          violations.push(`(iv) ${at} '${literal}' is neither a registered key nor a reviewed non-key`);
        }
      }
    });
  }
  return { violations, foundKeys, storageFiles };
}

function definedFunctions(source: Source): Set<string> {
  const names = new Set<string>();
  walk(parse(source), (node) => {
    if ((ts.isFunctionDeclaration(node) || ts.isMethodDeclaration(node)) && node.name) names.add(node.name.getText());
    if (
      ts.isVariableDeclaration(node) &&
      ts.isIdentifier(node.name) &&
      node.initializer &&
      (ts.isArrowFunction(node.initializer) || ts.isFunctionExpression(node.initializer))
    ) {
      names.add(node.name.text);
    }
  });
  return names;
}

// --------------------------------------------------------------- the tests

const REAL = scan(SOURCES);
const sourceAt = (path: string) => SOURCES.find((source) => source.path === path);

describe('the storage registry (lib/actorScope)', () => {
  it('classifies each key exactly once', () => {
    const doubled = [...CLASSES].filter(([, classes]) => classes.length !== 1).map(([key]) => key);
    expect(doubled).toEqual([]);
    for (const [key, lane] of Object.entries(PREREGISTERED)) {
      expect(CLASSES.has(key), `${key} (pre-registered for ${lane}) is in the registry`).toBe(true);
    }
  });

  it('the tree passes (i)-(iv)', () => {
    expect(REAL.violations).toEqual([]);
  });

  it('(i) STORAGE_FILES is exact: every listed file still names storage, and the guarded stores never do', () => {
    expect([...STORAGE_FILES].filter((path) => !REAL.storageFiles.has(path)), 'stale STORAGE_FILES entries').toEqual([]);
    for (const path of GUARDED_STORES) {
      expect(sourceAt(path), `${path} exists`).toBeDefined();
      expect(STORAGE_FILES).not.toContain(path);
      expect(REAL.storageFiles.has(path), `${path} goes through lib/actorScope only`).toBe(false);
    }
  });

  it('(ii) every INDIRECTION row names registered keys, allowed classes and existing functions', () => {
    for (const row of INDIRECTION) {
      expect(STORAGE_FILES, row.file).toContain(row.file);
      if (row.keys !== 'ALL') {
        for (const key of row.keys) {
          const keyClass = classOf(key);
          expect(keyClass, `${row.file}: ${key} is registered`).not.toBeNull();
          if (keyClass && PRIVILEGED_CLASSES.has(keyClass)) {
            expect(row.removedBy, `${row.file}: ${key} is ${keyClass}; only TRANSITIONAL rows may name one`).toBeTruthy();
          }
        }
      }
      if (row.pendingLane) continue;
      const defined = definedFunctions(sourceAt(row.file) ?? { path: row.file, text: '' });
      expect(row.functions.filter((fn) => !defined.has(fn)), `${row.file}: stale INDIRECTION functions`).toEqual([]);
    }
  });

  it('(v) non-vacuity: every registered key but the pre-registered ones is found as a literal (at least 26)', () => {
    const expected = [...CLASSES.keys()].filter((key) => !(key in PREREGISTERED));
    expect(expected.filter((key) => !REAL.foundKeys.has(key)), 'registered keys no source spells').toEqual([]);
    expect(expected.filter((key) => REAL.foundKeys.has(key)).length).toBeGreaterThanOrEqual(26);
  });

  describe('(v) non-vacuity: injected sources each fail', () => {
    // Only the injected file is walked (every file still resolves imports),
    // so each case reparses one file rather than the whole tree.
    const withSource = (path: string, text: string) => {
      const others = SOURCES.filter((source) => source.path !== path);
      const base = sourceAt(path)?.text ?? '';
      return scan([...others, { path, text: `${base}\n${text}\n` }], new Set([path])).violations;
    };

    it("a literal 'mip.newThing'", () => {
      expect(withSource('src/lib/injectedNewThing.ts', "export const NEW_THING = 'mip.newThing';")).toEqual([
        expect.stringMatching(/^\(iv\) src\/lib\/injectedNewThing\.ts:\d+ 'mip\.newThing'/),
      ]);
    });

    it('an unlisted file calling sessionStorage.getItem(x)', () => {
      const violations = withSource('src/lib/injectedReader.ts', 'export const read = (x: string) => sessionStorage.getItem(x);');
      expect(violations.some((violation) => violation.startsWith('(i) src/lib/injectedReader.ts'))).toBe(true);
    });

    it("a listed file calling store.setItem('mip.newThing2', v)", () => {
      const violations = withSource(
        'src/lib/rumApiRoute.ts',
        "export function injected(store: Pick<Storage, 'setItem'>, v: string) { store.setItem('mip.newThing2', v); }",
      );
      expect(violations).toContainEqual(expect.stringMatching(/^\(ii\) src\/lib\/rumApiRoute\.ts:\d+ \.setItem\('mip\.newThing2'\) names an unregistered key/));
    });

    it("a listed non-actorScope file calling localStorage.getItem('mip.pinnedInsights')", () => {
      const violations = withSource(
        'src/lib/themePreference.ts',
        "export const injectedPins = () => window.localStorage.getItem('mip.pinnedInsights');",
      );
      expect(violations).toEqual([
        expect.stringMatching(/^\(ii\) src\/lib\/themePreference\.ts:\d+ \.getItem\('mip\.pinnedInsights'\) touches a PRIVATE_LOCAL key/),
      ]);
    });

    it('a whole-area clear, index, enumeration and for-in', () => {
      const violations = withSource(
        'src/lib/injectedSweep.ts',
        'export function sweep() { globalThis.localStorage.clear(); const a = window.sessionStorage["x"]; Object.keys(localStorage); for (const k in sessionStorage) void k; return a; }',
      );
      expect(violations.filter((violation) => violation.startsWith('(iii)'))).toHaveLength(4);
    });
  });
});
