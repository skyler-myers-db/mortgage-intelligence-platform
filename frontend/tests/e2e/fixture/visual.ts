/**
 * Rendered-layer helpers shared by the pixel gate (visual.fixture.spec.ts),
 * the axe matrix (axe.fixture.spec.ts) and the smoke spec (audit visual-10,
 * quality-02, responsive-v2):
 *
 *  - assertPinnedVrtHost: pixel baselines are amd64-Linux renders from the
 *    pinned Playwright container, so a capture anywhere else fails before it
 *    starts and names tools/update_visual_baselines.sh.
 *  - capture: the capture discipline (pointer parked off every hover target,
 *    focus blurred, a soft toHaveScreenshot so one test reports every diff).
 *  - expectNoSurfaceOverflow: no `.surface` may scroll sideways; what did on
 *    the base branch sits in the dated KNOWN_SURFACE_OVERFLOW ratchet.
 *  - the audited-read guard: no state a spec enters after a route's natural
 *    load may call a read that writes an audit row.
 */
import fs from 'node:fs';
import path from 'node:path';
import type { Locator, Page } from '@playwright/test';
import { normalizeApiPath, type ApiCall, type MockApi } from './mockApi';
import { FIXTURE_ROUTES } from './routes';
import { expect } from './test';

// ---------------------------------------------------------------------------
// Pinned host
// ---------------------------------------------------------------------------

export const VRT_UPDATE_SCRIPT = 'tools/update_visual_baselines.sh';

export interface VrtHost {
  image: string | undefined;
  platform: string;
  arch: string;
  /** Installed @playwright/test version (frontend/node_modules). */
  playwrightVersion: string;
}

/** The only image the baselines are valid for: the Playwright image of the installed version. */
export function pinnedVrtImage(playwrightVersion: string): string {
  return `mcr.microsoft.com/playwright:v${playwrightVersion}-noble`;
}

/** Why this host cannot produce or compare baselines (empty when it can). Pure. */
export function vrtHostProblems(host: VrtHost): string[] {
  const image = pinnedVrtImage(host.playwrightVersion);
  const problems: string[] = [];
  if (host.image !== image) problems.push(`MIP_VRT_IMAGE is ${host.image ? `"${host.image}"` : 'unset'}, expected "${image}"`);
  if (host.platform !== 'linux') problems.push(`platform is ${host.platform}, expected linux`);
  if (host.arch !== 'x64') problems.push(`arch is ${host.arch}, expected x64 (amd64)`);
  return problems;
}

/** Throw unless this process runs inside the pinned amd64 container. `configFile` locates frontend/. */
export function assertPinnedVrtHost(configFile: string | undefined): void {
  if (!configFile) throw new Error('The VRT needs frontend/playwright.config.ts to locate @playwright/test.');
  const manifest = path.join(path.dirname(configFile), 'node_modules', '@playwright', 'test', 'package.json');
  const { version } = JSON.parse(fs.readFileSync(manifest, 'utf8')) as { version: string };
  const problems = vrtHostProblems({
    image: process.env.MIP_VRT_IMAGE,
    platform: process.platform,
    arch: process.arch,
    playwrightVersion: version,
  });
  if (problems.length > 0) {
    throw new Error(
      `Visual baselines are amd64-Linux renders from ${pinnedVrtImage(version)} and cannot be captured or ` +
        `compared on this host (${problems.join('; ')}). Run \`bash ${VRT_UPDATE_SCRIPT}\` ` +
        `(regenerate) or \`bash ${VRT_UPDATE_SCRIPT} --check\` (compare) instead.`,
    );
  }
}

// ---------------------------------------------------------------------------
// Capture discipline
// ---------------------------------------------------------------------------

/** Anything that restyles on hover. A capture must show none of them hovered. */
const HOVER_TARGETS = 'a, button, [role="row"], .kpi';
const INTERACTIVE = `${HOVER_TARGETS}, input, select, textarea, label, summary, [role="button"], [role="option"], [tabindex]`;

/**
 * Park the pointer on a point no interactive element covers, blur focus, and
 * assert nothing is hovered, so a capture never depends on where the last
 * click happened to leave the mouse.
 */
export async function parkPointerAndBlur(page: Page): Promise<void> {
  const viewport = page.viewportSize();
  if (!viewport) throw new Error('viewport size is unset');
  const point = await page.evaluate(
    ([width, height, interactive]) => {
      const candidates: Array<[number, number]> = [];
      for (const fy of [0.98, 0.5, 0.02, 0.75, 0.25]) {
        for (const fx of [0.5, 0.35, 0.65, 0.2, 0.8, 0.05, 0.95]) candidates.push([Math.round(width * fx), Math.round(height * fy)]);
      }
      for (const [x, y] of candidates) {
        const element = document.elementFromPoint(x, y);
        if (element && !element.closest(interactive)) return { x, y };
      }
      return null;
    },
    [viewport.width, viewport.height, INTERACTIVE] as const,
  );
  if (!point) throw new Error('no non-interactive point to park the pointer on');
  await page.mouse.move(point.x, point.y);
  await page.evaluate(() => {
    const active = document.activeElement;
    if (active instanceof HTMLElement && active !== document.body) active.blur();
  });
  const hovered = await page.evaluate(
    (selector) =>
      [...document.querySelectorAll(selector)]
        .filter((element) => element.matches(':hover'))
        .map((element) => `${element.tagName.toLowerCase()}.${[...element.classList].join('.')}`),
    HOVER_TARGETS,
  );
  expect(hovered, 'a capture must not show a hovered control').toEqual([]);
}

export interface CaptureOptions {
  /** Capture one element instead of the viewport. */
  element?: Locator;
  /**
   * Regions the double-run determinism check proved unstable. Mask nothing by
   * default; every mask carries a comment at its call site naming the cause,
   * and the caller asserts the masked element's content another way.
   */
  mask?: Locator[];
}

/** Soft screenshot assertion after the capture discipline, so one test reports every diff. */
export async function capture(page: Page, name: string, options: CaptureOptions = {}): Promise<void> {
  await parkPointerAndBlur(page);
  const screenshot = options.mask ? { mask: options.mask } : {};
  if (options.element) await expect.soft(options.element).toHaveScreenshot(name, screenshot);
  else await expect.soft(page).toHaveScreenshot(name, screenshot);
}

/** Scroll `.main` (the app's scroll container) down by one viewport of itself and let two frames paint. */
export async function scrollMainBy(page: Page, pages: number): Promise<void> {
  await page.locator('main.main').evaluate((main, count) => {
    main.scrollTo({ top: main.clientHeight * count, behavior: 'instant' });
  }, pages);
  await page.evaluate(() => new Promise((resolve) => requestAnimationFrame(() => requestAnimationFrame(() => resolve(undefined)))));
}

// ---------------------------------------------------------------------------
// Surface overflow
// ---------------------------------------------------------------------------

export interface SurfaceOverflowKey {
  route: string;
  state: string;
  theme: string;
}

export interface KnownSurfaceOverflow {
  /** Audit finding id that owns the fix. */
  finding: string;
  /** ISO date the entry was recorded against the base branch. */
  recorded: string;
  /** Themes it reproduces in. It must reproduce in each. */
  themes: readonly string[];
  /** Selector every overflowing `.surface` must match. */
  nodes: string;
}

/**
 * `${route}|${state}` → owner. A RATCHET like axe.ts KNOWN_VIOLATIONS: an
 * entry whose overflow no longer reproduces fails as stale, so a fix retires
 * it in the same change. Recorded from the first full scan (wave 2).
 */
export const KNOWN_SURFACE_OVERFLOW: Readonly<Record<string, KnownSurfaceOverflow>> = {
  // stack-06 (hand-rolled chart maths, no edge padding for axis labels): the
  // last x tick of "Evidence Events Per Day" is centred on the chart's right
  // edge (translateX(-50%)) and ends ~1.4px past the surface's padding box
  // (scrollWidth 1319 > clientWidth 1318 at 1440x900).
  'analytics-signals|default': {
    finding: 'stack-06',
    recorded: '2026-09-24',
    themes: ['dark', 'light'],
    nodes: 'section.surface:has(.analytics-chart__tick--x)',
  },
};

/** Every state a spec passes to expectNoSurfaceOverflow (visual.fixture.spec.ts, smoke). */
export const OVERFLOW_STATES = [
  'default',
  'second-page',
  'console',
  'compact',
  'evidence-drawer',
  'command-palette',
  'genie',
  'degraded',
  'expanded-row',
  'empty',
] as const;

/** Ratchet keys that name no route or checked state would never be compared: problems, empty when valid. Pure. */
export function surfaceOverflowKeyProblems(
  known: Readonly<Record<string, KnownSurfaceOverflow>>,
  routeNames: readonly string[],
): string[] {
  const states = new Set<string>(OVERFLOW_STATES);
  return Object.keys(known)
    .filter((key) => {
      const [route, state, extra] = key.split('|');
      return extra !== undefined || !routeNames.includes(route) || !states.has(state);
    })
    .map((key) => `KNOWN_SURFACE_OVERFLOW key "${key}" does not name a checked route|state`);
}

const overflowKeyProblems = surfaceOverflowKeyProblems(
  KNOWN_SURFACE_OVERFLOW,
  FIXTURE_ROUTES.map((route) => route.name),
);
if (overflowKeyProblems.length > 0) throw new Error(overflowKeyProblems.join('\n'));

interface MeasuredOverflow {
  surface: string;
  scrollWidth: number;
  clientWidth: number;
  covered: boolean;
}

/** Every `.surface` that scrolls sideways, and whether it matches `coveredBy`. */
export async function measureSurfaceOverflow(page: Page, coveredBy: string | null = null): Promise<MeasuredOverflow[]> {
  return page.evaluate((covered) => {
    const describe = (element: Element): string => {
      const heading = element.querySelector('h1, h2, h3, h4, .surface__title')?.textContent?.trim().slice(0, 60);
      const classes = [...element.classList].join('.');
      return `${element.tagName.toLowerCase()}.${classes}${heading ? ` "${heading}"` : ''}`;
    };
    return [...document.querySelectorAll('.surface')]
      .filter((element) => element.scrollWidth > element.clientWidth)
      .map((element) => ({
        surface: describe(element),
        scrollWidth: element.scrollWidth,
        clientWidth: element.clientWidth,
        covered: covered !== null && element.matches(covered),
      }));
  }, coveredBy);
}

/** Unrecorded overflow plus stale ratchet entries for one captured state. */
export async function surfaceOverflowProblems(
  page: Page,
  key: SurfaceOverflowKey,
  known: Readonly<Record<string, KnownSurfaceOverflow>> = KNOWN_SURFACE_OVERFLOW,
): Promise<string[]> {
  const entryKey = `${key.route}|${key.state}`;
  const entry = known[entryKey] && known[entryKey].themes.includes(key.theme) ? known[entryKey] : null;
  const found = await measureSurfaceOverflow(page, entry?.nodes ?? null);
  const problems = found
    .filter((overflow) => !overflow.covered)
    .map((overflow) => `${overflow.surface} scrolls sideways (scrollWidth ${overflow.scrollWidth} > clientWidth ${overflow.clientWidth})`);
  if (entry && !found.some((overflow) => overflow.covered)) {
    problems.push(`${entryKey} (${entry.finding}, recorded ${entry.recorded}) no longer overflows in ${key.theme}: retire it`);
  }
  return problems;
}

/** No `.surface` may have scrollWidth > clientWidth beyond the ratchet. Declared inner scrollers (`.tbl-wrap`) do not trip it. */
export async function expectNoSurfaceOverflow(
  page: Page,
  key: SurfaceOverflowKey,
  known: Readonly<Record<string, KnownSurfaceOverflow>> = KNOWN_SURFACE_OVERFLOW,
): Promise<void> {
  const problems = await surfaceOverflowProblems(page, key, known);
  expect(problems, `surface overflow on ${key.route} (${key.state}, ${key.theme})`).toEqual([]);
}

// ---------------------------------------------------------------------------
// Audited-read guard
// ---------------------------------------------------------------------------

/**
 * Reads that write an audit row (backend/api/*: VIEW_LEADS, VIEW_BORROWER,
 * VIEW_BORROWER_PROOF, DRAFT_OUTREACH, RECOMMEND_OFFER, PROPERTY_LOOKUP).
 * A spec may trigger one only as part of a route's natural load (the Offer
 * Orchestrator detail route loads recommend and draft by design); no state it
 * enters afterwards (a drawer, a scroll, a hover, the Console) may.
 */
export const AUDITED_READS: ReadonlyArray<{ method: string; path: RegExp; event: string }> = [
  { method: 'GET', path: /^\/api\/leads$/, event: 'VIEW_LEADS' },
  { method: 'GET', path: /^\/api\/borrowers\/(?!search$)[^/]+$/, event: 'VIEW_BORROWER' },
  { method: 'GET', path: /^\/api\/borrowers\/[^/]+\/proof$/, event: 'VIEW_BORROWER_PROOF' },
  { method: 'POST', path: /^\/api\/outreach\/draft$/, event: 'DRAFT_OUTREACH' },
  { method: 'POST', path: /^\/api\/offers\/recommend$/, event: 'RECOMMEND_OFFER' },
  { method: 'POST', path: /^\/api\/lookup\/property-loan$/, event: 'PROPERTY_LOOKUP' },
];

/** The audit event a call would write, or null. */
export function auditedEvent(call: Pick<ApiCall, 'method' | 'path'>): string | null {
  const normalized = normalizeApiPath(call.path);
  return AUDITED_READS.find((read) => read.method === call.method && read.path.test(normalized))?.event ?? null;
}

/** Audited reads among the calls made after index `naturalLoadEnd`. Pure. */
export function auditedReadsAfter(calls: readonly ApiCall[], naturalLoadEnd: number): string[] {
  return calls
    .slice(naturalLoadEnd)
    .map((call) => ({ call, event: auditedEvent(call) }))
    .filter((hit): hit is { call: ApiCall; event: string } => hit.event !== null)
    .map(({ call, event }) => `${call.method} ${call.path}${call.search ? `?${call.search}` : ''} (${event})`);
}

/** Mark the end of the route's natural load; pass the result to expectNoAuditedReadSince. */
export function markNaturalLoad(mockApi: MockApi): number {
  return mockApi.calls.length;
}

export function expectNoAuditedReadSince(mockApi: MockApi, naturalLoadEnd: number, label: string): void {
  expect(auditedReadsAfter(mockApi.calls, naturalLoadEnd), `${label}: an entered state opened an audited read`).toEqual([]);
}
