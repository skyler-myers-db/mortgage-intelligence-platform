/**
 * Module 0 — Live Unity Catalog end-to-end spec (Slice 9).
 *
 * This spec exercises the deployed app against REAL Unity Catalog + Lakebase
 * + Genie. It is gated on ``E2E_LIVE=1`` so local dev + PR CI never runs it
 * (those run ``module0.spec.ts`` against in-process repositories). Only the
 * nightly ``playwright-e2e-live`` workflow job sets ``E2E_LIVE=1`` and the
 * ``MIP_APP_URL`` pointing at the deployed Databricks App.
 *
 * What it proves end-to-end:
 *   1. Dashboard renders a non-zero segment count from live gold tables.
 *   2. A ranked borrower row (from /api/leads) opens the Borrower 360,
 *      Evidence Drawer shows ≥ 2 real evidence rows from
 *      mip.gold.evidence_events.
 *   3. The Genie floating FAB returns a non-empty natural-language answer
 *      within 20s (first cold call can be 10-15s; we allow headroom).
 *   4. Approving an outreach produces a new row in /api/audit within 5s,
 *      proving the Lakebase audit write path.
 *   5. Mid-run, issuing the admin-gated browser-local degraded proof cookie
 *      causes the DegradedBanner to appear within 5s — the resilience story
 *      works on the deployed app without stopping shared infrastructure.
 *
 * Non-negotiables per CLAUDE.md:
 *   * No mock fallback. If the app can't reach UC, the spec fails — that is
 *     correct behaviour (the nightly workflow gets paged).
 *   * No real PII asserted; we assert on synthetic borrower IDs (B-*).
 *   * Resilient selectors (getByRole, getByText, aria-label) matching the
 *     prototype's BEM class names; no brittle xpath.
 */
import { randomUUID } from 'node:crypto';
import {
  test,
  expect,
  type APIRequestContext,
  type Locator,
  type Page,
} from '@playwright/test';

import {
  archiveLiveCampaign,
  reconcileGenieCampaignAction,
} from '../../src/lib/liveCampaignProof';

// Gate: skip everything unless E2E_LIVE=1 is set by the nightly workflow.
const LIVE = process.env.E2E_LIVE === '1';
test.skip(!LIVE, 'Set E2E_LIVE=1 in the nightly workflow to run real-UC e2e.');

// Target URL — nightly workflow sets MIP_APP_URL to the deployed app; local
// ad-hoc runs fall back to the local dev stack. Use `||` (not `??`) so that
// an EMPTY STRING (the GitHub Actions default when a secret isn't set) also
// falls through to the localhost default. Without this, `page.goto('/')`
// sees an empty baseURL and emits `Cannot navigate to invalid URL`.
const APP_URL = process.env.MIP_APP_URL || 'http://127.0.0.1:5173';
const API_URL =
  process.env.MIP_API_URL || APP_URL.replace(':5173', ':8000');

// Deployed Databricks Apps sit behind OAuth — a fresh browser hit to `/`
// 302-redirects to a consent page. Pass a workspace Bearer token on every
// request so the Apps auth middleware short-circuits the redirect. The
// nightly workflow wires DATABRICKS_TOKEN into the env; local ad-hoc runs
// against a localhost uvicorn don't need it (FastAPI accepts the unauth
// request).
const BEARER = process.env.MIP_BEARER_TOKEN || process.env.DATABRICKS_TOKEN || '';
const ADMIN_BEARER = process.env.MIP_ADMIN_BEARER_TOKEN || '';
const LIVE_CAMPAIGN_RUN_MARKER = process.env.MIP_LIVE_CAMPAIGN_RUN_MARKER || '';
const AUTH_HEADERS: Record<string, string> = BEARER
  ? { Authorization: `Bearer ${BEARER}` }
  : {};
if (LIVE_CAMPAIGN_RUN_MARKER) {
  AUTH_HEADERS['X-MIP-Live-Campaign-Run-Marker'] = LIVE_CAMPAIGN_RUN_MARKER;
}

test.use({ baseURL: APP_URL, extraHTTPHeaders: AUTH_HEADERS });

async function gotoApp(page: Page, path: string): Promise<void> {
  await page.goto(path, { waitUntil: 'domcontentloaded', timeout: 60_000 });
  await expect(page.locator('main')).toBeVisible({ timeout: 30_000 });
}

type LeadRow = {
  borrower_id: string;
  clip?: string;
  equity_estimate?: number;
  current_lien_balance?: number;
  evidence_ids?: string[];
  approval_status?: string;
  marketing_eligible?: boolean;
  consent_status?: string;
  recommended_offer?: string;
};

type EvidenceEventRow = {
  source_product: string;
  source_table: string;
  signal_type: string;
};

type BorrowerDossier = LeadRow & {
  trigger_timeline?: EvidenceEventRow[];
  evidence_events?: EvidenceEventRow[];
};

type AuditRow = {
  event_id?: string;
  entity_id?: string;
  action?: string;
  event_type?: string;
  evidence_ids?: string[];
  payload_json?: {
    borrower_id?: string;
    forced_state?: string;
    proof_scope?: string;
  };
};

type LiveGeniePayload = {
  answer?: string | null;
  source?: string | null;
  trusted_assets?: string[];
  conversation_id?: string | null;
  message_id?: string | null;
  genie_status?: string | null;
  sql_query?: string | null;
  follow_up_questions?: string[];
  reasoning_trace?: Array<{ kind?: string | null; content?: string | null }>;
  proof?: {
    trusted?: boolean | null;
    source_assets?: string[];
    sql_query?: string | null;
    conversation_id?: string | null;
    message_id?: string | null;
    reasoning_trace?: Array<{ kind?: string | null; content?: string | null }>;
  } | null;
};

type CapabilityRow = {
  key: string;
  status: string;
  claimable: boolean;
};

type CampaignRecommendationPayload = {
  generation_mode: 'supervisor' | 'reviewed_fallback';
  generator_label: string;
  performance_status: 'qualified' | 'insufficient_sample' | 'unavailable';
  audience_summary: string;
  strategy: string;
  variants: Array<{
    variant_name: string;
    subject: string;
    body: string;
    hypothesis: string;
    provenance_token?: string | null;
  }>;
  holdout_pct: number;
  evidence: Array<{ label: string; value: string; source_asset: string }>;
  warnings: string[];
};

const NATIVE_GENIE_CONVERSATION_QUESTION =
  'Using mip.gold.borrower_360, return borrower count and average modeled equity percentage grouped by first position loan type, ordered by borrower count descending.';

function expectGovernedGeniePayload(payload: LiveGeniePayload, label: string): void {
  expect(
    ['genie', 'trusted_sql'],
    `${label}: answer source must be a governed Genie or verified-SQL tier`,
  ).toContain(payload.source);
  expect(payload.answer?.trim().length ?? 0, `${label}: API answer must be nonempty`).toBeGreaterThan(20);
  expect(
    payload.trusted_assets?.length ?? 0,
    `${label}: API answer must name at least one governed source asset`,
  ).toBeGreaterThan(0);
  expect(payload.proof, `${label}: API answer must include governed proof`).toBeTruthy();
  expect(payload.proof?.trusted, `${label}: governed proof must be trusted`).toBe(true);
  expect(
    payload.proof?.source_assets?.length ?? 0,
    `${label}: proof must name at least one governed source asset`,
  ).toBeGreaterThan(0);
  expect(
    (payload.sql_query ?? payload.proof?.sql_query ?? '').trim().length,
    `${label}: governed proof must include verified SQL`,
  ).toBeGreaterThan(0);
}

function expectLiveGenieTurn(payload: LiveGeniePayload, label: string): void {
  expectGovernedGeniePayload(payload, label);
  expect(payload.conversation_id?.trim(), `${label}: live turn must include conversation_id`).toBeTruthy();
  expect(payload.message_id?.trim(), `${label}: live turn must include message_id`).toBeTruthy();
  expect(payload.genie_status, `${label}: live turn must complete before the API responds`).toBe('COMPLETED');
  const reasoningSteps = payload.reasoning_trace ?? [];
  expect(
    reasoningSteps.length,
    `${label}: completed native turn must expose public API reasoning summaries`,
  ).toBeGreaterThan(0);
  for (let index = 0; index < reasoningSteps.length; index += 1) {
    expect(reasoningSteps[index].kind?.trim(), `${label}: reasoning step ${index + 1} kind`).toBeTruthy();
    expect(reasoningSteps[index].content?.trim(), `${label}: reasoning step ${index + 1} text`).toBeTruthy();
  }
  expect(
    payload.proof?.reasoning_trace,
    `${label}: proof must preserve the same public reasoning summaries`,
  ).toEqual(reasoningSteps);

  const followUps = payload.follow_up_questions ?? [];
  expect(
    followUps.length,
    `${label}: completed native turn must expose follow-up suggestions`,
  ).toBeGreaterThan(0);
  for (let index = 0; index < followUps.length; index += 1) {
    expect(followUps[index].trim(), `${label}: follow-up ${index + 1} must be nonempty`).not.toBe('');
  }
}

async function expectLiveGenieUi(
  root: Locator,
  payload: LiveGeniePayload,
  label: string,
): Promise<void> {
  await expect(root, `${label}: answer surface must render`).toBeVisible({ timeout: 90_000 });
  await expect(root.locator('.genie-proof-toggle').getByText('trusted', { exact: true })).toBeVisible();
  await expect(root.getByLabel('Answer source: Databricks Genie Conversation API')).toBeVisible();
  await expect(root.getByTestId('genie-feedback-up')).toBeVisible();
  await expect(root.getByTestId('genie-feedback-down')).toBeVisible();

  const reasoningSteps = payload.reasoning_trace ?? [];
  const reasoning = root.locator('.genie-answer__reasoning');
  await expect(reasoning, `${label}: Public Preview reasoning returned by the API must render`).toBeVisible();
  await expect(reasoning).not.toHaveAttribute('open', '');
  await reasoning.locator('summary').click();
  for (let index = 0; index < reasoningSteps.length; index += 1) {
    const step = reasoningSteps[index];
    expect(step.kind?.trim(), `${label}: reasoning step ${index + 1} must include a public kind`).toBeTruthy();
    expect(step.content?.trim(), `${label}: reasoning step ${index + 1} must include public text`).toBeTruthy();
    await expect(reasoning.locator('.genie-answer__reasoning-step').nth(index)).toContainText(
      step.content!.trim(),
    );
  }

  const followUps = payload.follow_up_questions ?? [];
  const renderedFollowUps = root.locator('.genie-answer__followups .filter--question');
  await expect(
    renderedFollowUps,
    `${label}: API follow-up suggestions must render as actions`,
  ).toHaveCount(Math.min(followUps.length, 5));
  for (let index = 0; index < Math.min(followUps.length, 5); index += 1) {
    await expect(renderedFollowUps.nth(index)).toContainText(followUps[index].trim());
  }
}

type MapDrillTarget = {
  state: string;
  stateName: string;
  countyFips: string;
  countyName: string;
};

function escapeRegExp(value: string): string {
  return value.replace(/[.*+?^${}()|[\]\\]/g, '\\$&');
}

async function fetchLeads(request: APIRequestContext, limit = 10): Promise<LeadRow[]> {
  const resp = await request.get(`${API_URL}/api/leads?limit=${limit}`, {
    headers: AUTH_HEADERS,
  });
  expect(resp.status(), 'GET /api/leads returned non-200').toBe(200);
  return (await resp.json()) as LeadRow[];
}

async function fetchActionablePendingLead(request: APIRequestContext): Promise<LeadRow> {
  const resp = await request.get(`${API_URL}/api/leads?limit=100&approval_status=pending`, {
    headers: AUTH_HEADERS,
  });
  expect(resp.status(), 'GET /api/leads?approval_status=pending returned non-200').toBe(200);
  const leads = (await resp.json()) as LeadRow[];
  const candidates = leads.filter((row) =>
    row.approval_status === 'pending' &&
    row.marketing_eligible !== false &&
    (row.consent_status ?? 'opt_in') === 'opt_in',
  );
  for (const lead of candidates) {
    const borrower = await fetchBorrower(request, lead.borrower_id);
    if (
      borrower.approval_status === 'pending' &&
      borrower.marketing_eligible !== false &&
      (borrower.consent_status ?? 'opt_in') === 'opt_in'
    ) {
      return lead;
    }
  }
  throw new Error('No actionable pending lead available for live approval test.');
}

async function fetchBorrower(request: APIRequestContext, borrowerId: string): Promise<BorrowerDossier> {
  const resp = await request.get(`${API_URL}/api/borrowers/${borrowerId}`, {
    headers: AUTH_HEADERS,
  });
  expect(resp.status(), `GET /api/borrowers/${borrowerId} returned non-200`).toBe(200);
  return (await resp.json()) as BorrowerDossier;
}

async function fetchAuditEvents(
  request: APIRequestContext,
  limit = 100,
  bearer = BEARER,
): Promise<AuditRow[] | null> {
  const headers = bearer ? { Authorization: `Bearer ${bearer}` } : AUTH_HEADERS;
  const resp = await request.get(`${API_URL}/api/audit/events?limit=${limit}`, {
    headers,
  });
  if (resp.status() === 403) return null;
  expect(resp.status(), 'GET /api/audit/events returned non-200').toBe(200);
  const payload = await resp.json();
  expect(Array.isArray(payload), 'GET /api/audit/events should return an array').toBe(true);
  return payload as AuditRow[];
}

async function findBorrowerWithEvidenceProducts(
  request: APIRequestContext,
  products: string[],
): Promise<BorrowerDossier> {
  const leads = await fetchLeads(request, 40);
  for (const lead of leads) {
    const borrower = await fetchBorrower(request, lead.borrower_id);
    const seen = new Set([
      ...(borrower.trigger_timeline ?? []).map((e) => e.source_product),
      ...(borrower.evidence_events ?? []).map((e) => e.source_product),
    ]);
    if (products.every((product) => seen.has(product))) return borrower;
  }
  throw new Error(`No borrower found with evidence products: ${products.join(', ')}`);
}

function collectCotalityIdValues(payload: unknown, out: Array<{ key: string; value: string }> = []) {
  if (Array.isArray(payload)) {
    for (const item of payload) collectCotalityIdValues(item, out);
    return out;
  }
  if (!payload || typeof payload !== 'object') return out;
  for (const [key, value] of Object.entries(payload as Record<string, unknown>)) {
    if (
      (key === 'clip' || key === 'clip_id' || key === 'subject_clip' || key === 'owner_link_id') &&
      typeof value === 'string' &&
      value.length > 0
    ) {
      out.push({ key, value });
    }
    collectCotalityIdValues(value, out);
  }
  return out;
}

function expectMaskedCotalityIds(payload: unknown, label: string) {
  const values = collectCotalityIdValues(payload);
  for (const { key, value } of values) {
    if (key === 'owner_link_id') {
      expect(
        /^(owner_link_ref_|ol_demo_)/.test(value),
        `${label} exposed raw Owner Link in ${key}: ${value}`,
      ).toBe(true);
    } else {
      expect(
        /^(clip_ref_|clip_demo_)/.test(value),
        `${label} exposed raw CLIP in ${key}: ${value}`,
      ).toBe(true);
    }
  }
}

/**
 * Click the segment card's selection control, not the card box. `.seg-card`
 * clicks land on the card's geometric centre, which the product/channel
 * breakdown chips can occupy on deployments whose data renders them — that
 * click opens the Evidence Drawer instead of selecting. `.seg-card__select`
 * is the stretched selection button and is environment-independent.
 */
async function clickSegmentCard(page: Page, label: string) {
  const card = page.locator('.seg-card', { hasText: label });
  await expect(card, `segment card ${label} should be ready`).toBeVisible({ timeout: 45_000 });
  await card.locator('.seg-card__select').click();
}

async function chooseFilter(page: Page, label: string, value: string) {
  const trigger = page.getByRole('button', { name: new RegExp(`^${escapeRegExp(label)}:`) });
  await expect(trigger, `filter ${label} should be ready`).toBeVisible({ timeout: 45_000 });
  await trigger.click();
  await page.getByRole('option', { name: value, exact: true }).click();
}

type GenieTurnRequest = { question?: string; conversation_id?: string | null };

/**
 * Drive one Genie turn and return both halves of it.
 *
 * The interactive surfaces moved off the single blocking `/genie/message`
 * call to the async lifecycle (submit → progress → complete), so a live turn
 * no longer produces one request/response pair:
 *
 *  - `/message/submit` always happens and carries `{question, conversation_id}`
 *    — the request-shape assertions belong to it.
 *  - the governed answer arrives on `/message/complete`, EXCEPT for turns that
 *    resolve deterministically (guardrail refusal, sales-ops, footprint,
 *    degraded fallback), which never reach `/complete` and instead return
 *    `{completed: true, response}` on `/submit` itself.
 *
 * Accept whichever terminal call the turn actually makes and hand back the
 * unwrapped GenieMessageResponse, so callers assert on one payload shape.
 */
async function captureGenieTurn(
  page: Page,
  act: () => Promise<void>,
  timeout = 120_000,
): Promise<{ request: GenieTurnRequest; payload: LiveGeniePayload }> {
  const isSubmit = (url: string) => /\/api\/(?:v1\/)?genie\/message\/submit$/.test(url);
  const submitPromise = page.waitForResponse(
    (candidate) => candidate.request().method() === 'POST' && isSubmit(candidate.url()),
    { timeout },
  );
  const terminalPromise = page.waitForResponse(async (candidate) => {
    if (candidate.request().method() !== 'POST') return false;
    const url = candidate.url();
    if (/\/api\/(?:v1\/)?genie\/message\/complete$/.test(url)) return true;
    if (!isSubmit(url)) return false;
    try {
      return ((await candidate.json()) as { completed?: boolean }).completed === true;
    } catch {
      return false;
    }
  }, { timeout });

  await act();

  const submit = await submitPromise;
  expect(submit.status(), 'Genie submit returned non-200').toBe(200);
  const terminal = await terminalPromise;
  expect(terminal.status(), 'Genie terminal call returned non-200').toBe(200);
  const raw = (await terminal.json()) as LiveGeniePayload & {
    completed?: boolean;
    response?: LiveGeniePayload;
  };
  const payload = raw.completed === true && raw.response ? raw.response : (raw as LiveGeniePayload);
  return { request: submit.request().postDataJSON() as GenieTurnRequest, payload };
}

async function clickSvgRegion(page: Page, target: Locator, label: string) {
  let lastError: unknown;
  for (let attempt = 0; attempt < 3; attempt += 1) {
    try {
      await expect(target, `${label} SVG region should be visible`).toBeVisible({ timeout: 10_000 });
      await target.scrollIntoViewIfNeeded({ timeout: 5_000 });
      const point = await target.evaluate((node) => {
        const rect = node.getBoundingClientRect();
        if (!(node instanceof SVGGeometryElement) || !node.ownerSVGElement) {
          return { x: rect.left + rect.width / 2, y: rect.top + rect.height / 2 };
        }

        const bbox = node.getBBox();
        const ctm = node.getScreenCTM();
        const svgPoint = node.ownerSVGElement.createSVGPoint();
        if (!ctm || bbox.width === 0 || bbox.height === 0) {
          return { x: rect.left + rect.width / 2, y: rect.top + rect.height / 2 };
        }

        // SVG geography paths are irregular. Playwright's default path click
        // targets the bounding-box center, which can be outside the painted
        // state/county shape. Sample the path box and click a painted point.
        for (const xStep of [0.5, 0.35, 0.65, 0.2, 0.8]) {
          for (const yStep of [0.5, 0.35, 0.65, 0.2, 0.8]) {
            svgPoint.x = bbox.x + bbox.width * xStep;
            svgPoint.y = bbox.y + bbox.height * yStep;
            if (node.isPointInFill(svgPoint) || node.isPointInStroke(svgPoint)) {
              const clientPoint = svgPoint.matrixTransform(ctm);
              return { x: clientPoint.x, y: clientPoint.y };
            }
          }
        }

        const pathPoint = node.getPointAtLength(node.getTotalLength() / 2).matrixTransform(ctm);
        return { x: pathPoint.x, y: pathPoint.y };
      });
      await page.mouse.click(point.x, point.y);
      return;
    } catch (error) {
      lastError = error;
      await page.waitForTimeout(250);
    }
  }
  throw lastError;
}

async function discoverMapDrillTarget(
  request: APIRequestContext,
  segmentCodes: string[] = [],
): Promise<MapDrillTarget> {
  const params = new URLSearchParams();
  if (segmentCodes.length > 0) {
    params.set('segment_codes', segmentCodes.join(','));
    params.set('segment_mode', 'any');
  }
  params.set('marketing_eligibility', 'Eligible only');
  const suffix = params.toString() ? `?${params.toString()}` : '';
  const statesResp = await request.get(`${API_URL}/api/geo/state-rollups${suffix}`, {
    headers: AUTH_HEADERS,
  });
  expect(statesResp.status(), 'state rollups target discovery').toBe(200);
  const statesPayload = await statesResp.json();
  const stateRollup = statesPayload.rollups.find((row: { state?: string; addressable?: number }) =>
    row.state && Number(row.addressable ?? 0) > 0,
  );
  expect(stateRollup, 'state rollups should expose at least one populated state').toBeTruthy();

  const footprintResp = await request.get(`${API_URL}/api/config/footprint`, {
    headers: AUTH_HEADERS,
  });
  expect(footprintResp.status(), 'footprint target discovery').toBe(200);
  const footprint = await footprintResp.json();
  const stateName =
    footprint.states?.find((row: { state_code?: string }) => row.state_code === stateRollup.state)
      ?.state_name ?? stateRollup.state;

  const countyResp = await request.get(
    `${API_URL}/api/geo/county-rollups?state=${stateRollup.state}${params.toString() ? `&${params.toString()}` : ''}`,
    { headers: AUTH_HEADERS },
  );
  expect(countyResp.status(), 'county rollups target discovery').toBe(200);
  const countyPayload = await countyResp.json();
  let county:
    | { fips_5?: string; county_name?: string; addressable_borrowers?: number }
    | undefined;
  for (const row of countyPayload.rollups as Array<{
    fips_5?: string;
    county_name?: string;
    addressable_borrowers?: number;
  }>) {
    if (!row.fips_5 || Number(row.addressable_borrowers ?? 0) <= 0) continue;
    const zipResp = await request.get(
      `${API_URL}/api/geo/zip-rollups?county_fips=${row.fips_5}${params.toString() ? `&${params.toString()}` : ''}`,
      { headers: AUTH_HEADERS },
    );
    if (zipResp.status() !== 200) continue;
    const zipPayload = await zipResp.json();
    if (Array.isArray(zipPayload.rollups) && zipPayload.rollups.length > 0) {
      county = row;
      break;
    }
  }
  expect(county, 'county rollups should expose at least one populated county').toBeTruthy();
  if (!county) throw new Error('No populated county with ZIP rollups was found');
  const countyFips = String(county.fips_5 ?? '').trim();
  expect(countyFips, 'populated county rollup must include a FIPS code').not.toBe('');
  const rawCountyName = String(county.county_name || countyFips);
  const countyName = rawCountyName.toLowerCase().endsWith('county')
    ? rawCountyName
    : `${rawCountyName} County`;

  return {
    state: stateRollup.state,
    stateName,
    countyFips,
    countyName,
  };
}

async function drillStateToCounty(page: Page, target: MapDrillTarget) {
  const map = page.locator('.map-wrap').first();
  const state = map.getByRole('button', { name: new RegExp(`^${escapeRegExp(target.stateName)}$`) }).first();
  const county = map.getByRole('button', { name: new RegExp(escapeRegExp(target.countyName), 'i') }).first();
  let lastError: unknown;
  for (let attempt = 0; attempt < 3; attempt += 1) {
    await bringMapIntoViewport(page);
    await expectMapIdle(page, 'state');
    await clickSvgRegion(page, state, target.stateName);
    try {
      await expect(
        county,
        `${target.stateName} pointer click should drill into counties`,
      ).toBeVisible({ timeout: 5_000 });
      return;
    } catch (error) {
      lastError = error;
    }
  }
  throw lastError;
}

async function drillCountyToZips(page: Page, target: MapDrillTarget) {
  const map = page.locator('.map-wrap').first();
  const county = map.getByRole('button', { name: new RegExp(escapeRegExp(target.countyName), 'i') }).first();
  const zipTiles = page.locator('.zip-tiles');
  let lastError: unknown;
  for (let attempt = 0; attempt < 3; attempt += 1) {
    await bringMapIntoViewport(page);
    await expectMapIdle(page, 'county');
    await expect(county).toBeVisible({ timeout: 10_000 });
    await clickSvgRegion(page, county, target.countyName);
    try {
      await expect(
        zipTiles,
        `${target.countyName} pointer click should drill into ZIPs`,
      ).toBeVisible({ timeout: 5_000 });
      await expect(page.locator('.map-crumbs')).toContainText(target.countyName, {
        timeout: 5_000,
      });
      return;
    } catch (error) {
      lastError = error;
    }
  }
  throw lastError;
}

async function bringMapIntoViewport(page: Page) {
  await page.evaluate(() => {
    const scroller = document.querySelector('.main') as HTMLElement | null;
    const map = document.querySelector('.map-wrap') as HTMLElement | null;
    if (scroller && map) scroller.scrollTop = Math.max(0, map.offsetTop - 120);
  });
}

async function expectMapIdle(page: Page, label: string) {
  await expect(
    page.locator('.map-levels').first(),
    `${label} map should finish loading before drill`,
  ).toHaveAttribute('aria-busy', 'false', { timeout: 15_000 });
}

async function expectMapCornerIconsCompact(page: import('@playwright/test').Page, label: string) {
  const boxes = await page.locator('.map-corner-chips .chip svg').evaluateAll((nodes) =>
    nodes.map((node) => {
      const rect = node.getBoundingClientRect();
      return { width: rect.width, height: rect.height };
    }),
  );
  expect(boxes.length, `${label}: map header chips should include icons`).toBeGreaterThan(0);
  for (const box of boxes) {
    expect(box.width, `${label}: map chip icon width should stay compact`).toBeLessThanOrEqual(16);
    expect(box.height, `${label}: map chip icon height should stay compact`).toBeLessThanOrEqual(16);
  }
}

function urlIncludesApiPath(url: string, path: string): boolean {
  const normalized = path.startsWith('/') ? path : `/${path}`;
  return url.includes(normalized) || url.includes(normalized.replace('/api/', '/api/v1/'));
}

async function openGeniePanel(page: Page) {
  const visibleFab = page.locator('.genie__fab:visible').first();
  if (await visibleFab.isVisible()) {
    await visibleFab.click();
    return;
  }
  await page.getByRole('button', { name: /Toggle Genie chat/i }).click();
}

async function assertSourceDrawer(
  page: Page,
  scope: Locator,
  chipLabel: string | RegExp,
  drawerTitle: string | RegExp,
) {
  const chip = scope.locator('.evidence-chip').filter({ hasText: chipLabel }).first();
  await expect(chip, `evidence chip ${String(chipLabel)} should be visible`).toBeVisible({
    timeout: 10_000,
  });
  await chip.click();
  const drawer = page.locator('.drawer.is-open').first();
  await expect(drawer).toBeVisible({ timeout: 3_000 });
  await expect(drawer.locator('.drawer__subtitle')).toHaveText(drawerTitle);
  await drawer.getByRole('button', { name: /Close drawer/i }).click();
  await expect(drawer).toBeHidden({ timeout: 3_000 });
}

test.describe('Module 0 — real-UC golden path (nightly only)', () => {
  test('API payloads mask Cotality identifiers', async ({ request }) => {
    const leadsResp = await request.get(`${API_URL}/api/leads?limit=5`, { headers: AUTH_HEADERS });
    expect(leadsResp.status(), 'GET /api/leads returned non-200').toBe(200);
    const leads = await leadsResp.json();
    expectMaskedCotalityIds(leads, '/api/leads');

    const firstBorrower = (leads as LeadRow[])[0]?.borrower_id;
    expect(firstBorrower, 'need a borrower id for PII boundary check').toBeTruthy();

    const borrowerResp = await request.get(`${API_URL}/api/borrowers/${firstBorrower}`, {
      headers: AUTH_HEADERS,
    });
    expect(borrowerResp.status(), 'GET /api/borrowers/:id returned non-200').toBe(200);
    expectMaskedCotalityIds(await borrowerResp.json(), '/api/borrowers/:id');

    const auditResp = await request.post(`${API_URL}/api/audit/event`, {
      headers: AUTH_HEADERS,
      data: {
        actor: 'playwright',
        action: 'view.masking_check',
        entity_type: 'qa',
        entity_id: 'playwright',
        subject_clip: '1234567890',
      },
    });
    if (auditResp.status() === 403) {
      const body = await auditResp.json();
      expect(body.detail, 'non-admin audit write should fail closed without leaking PII').toBe('forbidden');
      return;
    }
    expect(auditResp.status(), 'POST /api/audit/event returned non-200').toBe(200);
    expectMaskedCotalityIds(await auditResp.json(), '/api/audit/event');
  });

  test('dashboard renders non-zero segment counts from gold', async ({ page }) => {
    // Segment cards live on /segment-intelligence, not the home dashboard.
    // Home is a narrative/launchpad; segments (.seg-card__count BEM class)
    // are the Segment Intelligence route's primary surface.
    await gotoApp(page, '/segment-intelligence');
    const counts = page.locator('.seg-card__count');
    await expect(counts.first()).toBeVisible({ timeout: 20_000 });
    const rendered = await counts.allTextContents();
    const hasPositive = rendered.some((raw) => {
      const n = Number.parseInt(raw.replace(/[^0-9]/g, ''), 10);
      return Number.isFinite(n) && n > 0;
    });
    expect(hasPositive, `no segment card had a positive count: ${rendered.join(' | ')}`).toBe(true);
  });

  test('segment multi-select re-queries ranked list and map in any-mode', async ({ page }) => {
    await gotoApp(page, '/segment-intelligence');
    await expect(page.getByRole('button', { name: /Any selected/i })).toHaveAttribute('aria-pressed', 'true');

    const rankedHeader = page
      .locator('.section-hdr', { hasText: 'Ranked borrowers' })  // eyebrow is conditional since 192d5d6: '· full eligible queue' before selection, '· selected segment cohort' after
      .locator('.h-2')
      .first();
    await expect(rankedHeader).toContainText(/ranked borrowers/, { timeout: 45_000 });

    await clickSegmentCard(page, 'Prime Refi Candidates');
    await expect(rankedHeader).toContainText(/segment filter: Prime Refi Candidates/, { timeout: 45_000 });

    const isAnyModeSegmentResponse = (url: string, path: string) => {
      if (!urlIncludesApiPath(url, path)) return false;
      const parsed = new URL(url);
      const codes = parsed.searchParams.get('segment_codes') ?? '';
      return (
        parsed.searchParams.get('segment_mode') === 'any' &&
        codes.includes('itm') &&
        codes.includes('equity')
      );
    };

    const leadsResponse = page.waitForResponse((response) =>
      isAnyModeSegmentResponse(response.url(), '/api/leads'),
    );
    const geoResponse = page.waitForResponse((response) =>
      isAnyModeSegmentResponse(response.url(), '/api/geo/state-rollups'),
    );
    const segmentCardsResponse = page.waitForResponse((response) =>
      isAnyModeSegmentResponse(response.url(), '/api/segments'),
    );

    await clickSegmentCard(page, 'Home Equity Candidate');

    const [leads, geo, segmentCards] = await Promise.all([
      leadsResponse,
      geoResponse,
      segmentCardsResponse,
    ]);
    expect(leads.status(), 'ranked list segment any-mode response').toBe(200);
    expect(geo.status(), 'map segment any-mode response').toBe(200);
    expect(segmentCards.status(), 'segment card any-mode response').toBe(200);
    const segmentUrl = new URL(page.url());
    expect((segmentUrl.searchParams.get('segment_codes') ?? '').split(',').sort()).toEqual(['equity', 'itm']);
    expect(segmentUrl.searchParams.get('segment_mode')).toBe('any');
    await expect(rankedHeader).toContainText(
      /segment filter: Prime Refi Candidates or Home Equity Candidate/,
      { timeout: 45_000 },
    );
    await expect(rankedHeader).toContainText(/any selected segment, de-duplicated/);
  });

  test('segment match-all mode narrows to borrowers in every selected segment', async ({ page }) => {
    await gotoApp(page, '/segment-intelligence');

    const rankedHeader = page
      .locator('.section-hdr', { hasText: 'Ranked borrowers' })  // eyebrow is conditional since 192d5d6: '· full eligible queue' before selection, '· selected segment cohort' after
      .locator('.h-2')
      .first();
    await expect(rankedHeader).toContainText(/ranked borrowers/, { timeout: 45_000 });

    await clickSegmentCard(page, 'Prime Refi Candidates');
    await clickSegmentCard(page, 'Home Equity Candidate');

    const allModeLeads = page.waitForResponse((response) => {
      if (response.status() !== 200 || !urlIncludesApiPath(response.url(), '/api/leads')) return false;
      const parsed = new URL(response.url());
      const codes = parsed.searchParams.get('segment_codes') ?? '';
      return (
        parsed.searchParams.get('segment_mode') === 'all' &&
        codes.includes('itm') &&
        codes.includes('equity')
      );
    });
    const allModeSegments = page.waitForResponse((response) => {
      if (response.status() !== 200 || !urlIncludesApiPath(response.url(), '/api/segments')) return false;
      const parsed = new URL(response.url());
      const codes = parsed.searchParams.get('segment_codes') ?? '';
      return (
        parsed.searchParams.get('segment_mode') === 'all' &&
        codes.includes('itm') &&
        codes.includes('equity')
      );
    });
    await page.getByRole('button', { name: /All selected/i }).click();
    await Promise.all([allModeLeads, allModeSegments]);
    const segmentUrl = new URL(page.url());
    expect((segmentUrl.searchParams.get('segment_codes') ?? '').split(',').sort()).toEqual(['equity', 'itm']);
    expect(segmentUrl.searchParams.get('segment_mode')).toBe('all');

    await expect(rankedHeader).toContainText(
      /segment filter: Prime Refi Candidates and Home Equity Candidate/,
      { timeout: 45_000 },
    );
    await expect(rankedHeader).toContainText(/all selected segments/);

    await page.getByRole('link', { name: /Deep-dive lead queue/i }).click();
    await expect(page).toHaveURL(/\/lead-queue\?/, { timeout: 20_000 });
    const url = new URL(page.url());
    expect(url.searchParams.get('segment_mode')).toBe('all');
    expect(url.searchParams.get('segment_codes')).toContain('itm');
    expect(url.searchParams.get('segment_codes')).toContain('equity');
    await expect(page.getByRole('button', { name: /SEGMENT:\s*2 segments selected \(all selected\)/i })).toBeVisible({ timeout: 20_000 });
  });

  test('segment map drill preserves segment filters through county ZIP and Lead Queue', async ({ page, request }) => {
    await gotoApp(page, '/segment-intelligence');

    const selectedSegments = ['Prime Refi Candidates', 'Home Equity Candidate'];
    const target = await discoverMapDrillTarget(request, ['itm', 'equity']);
    const filteredGeoResponse = (path: string) => (response: { url: () => string; status: () => number }) => {
      if (response.status() !== 200 || !urlIncludesApiPath(response.url(), path)) return false;
      const parsed = new URL(response.url());
      const codes = parsed.searchParams.get('segment_codes') ?? '';
      return (
        parsed.searchParams.get('segment_mode') === 'any' &&
        codes.includes('itm') &&
        codes.includes('equity')
      );
    };

    const filteredStateResponse = page.waitForResponse(
      filteredGeoResponse('/api/geo/state-rollups'),
      { timeout: 45_000 },
    );
    for (const label of selectedSegments) {
      await clickSegmentCard(page, label);
    }
    await filteredStateResponse;

    const countyResponse = page.waitForResponse(
      filteredGeoResponse('/api/geo/county-rollups'),
      { timeout: 45_000 },
    );
    await drillStateToCounty(page, target);
    await countyResponse;

    const zipResponse = page.waitForResponse(
      filteredGeoResponse('/api/geo/zip-rollups'),
      { timeout: 45_000 },
    );
    await drillCountyToZips(page, target);
    await zipResponse;

    await expect(page.locator('.zip-tiles')).toBeVisible({ timeout: 10_000 });
	    const crumbs = await page.locator('.map-crumbs').boundingBox();
	    const chips = await page.locator('.map-corner-chips').boundingBox();
	    const header = await page.locator('.map-hdr').boundingBox();
	    const zipGrid = await page.locator('.zip-tiles').boundingBox();
	    expect(crumbs, 'map crumbs should render').toBeTruthy();
	    expect(chips, 'map corner chips should render').toBeTruthy();
	    expect(header, 'map header should render').toBeTruthy();
	    expect(zipGrid, 'ZIP grid should render').toBeTruthy();
	    if (crumbs && chips) {
	      const separated =
	        crumbs.x + crumbs.width <= chips.x ||
	        chips.x + chips.width <= crumbs.x ||
	        crumbs.y + crumbs.height <= chips.y ||
	        chips.y + chips.height <= crumbs.y;
	      expect(separated, 'map breadcrumbs and corner chips must not overlap').toBe(true);
	    }
	    if (header && zipGrid) {
	      expect(
	        header.y + header.height,
	        'map header must not visually overlap ZIP tiles',
	      ).toBeLessThanOrEqual(zipGrid.y + 1);
	    }
    await expectMapCornerIconsCompact(page, 'segment ZIP map');

	    const leadsResponse = page.waitForResponse(filteredGeoResponse('/api/leads'));
	    await page.locator('.zip-tile').first().click();
	    await leadsResponse;
	    const url = new URL(page.url());
	    expect(url.pathname).toBe('/lead-queue');
    expect(url.searchParams.get('state')).toBe(target.state);
    expect(url.searchParams.get('county')).toBe(target.countyFips);
    expect(url.searchParams.get('segment_mode')).toBe('any');
    expect(url.searchParams.get('segment_codes')).toContain('equity');
    expect(url.searchParams.get('zip')).toMatch(/^\d{5}$/);
  });

  test('segment secondary filters are forwarded to live geo rollups', async ({ page }) => {
    await gotoApp(page, '/segment-intelligence');

    const seenStateRollups = new Set<string>();
    page.on('response', (response) => {
      if (response.status() === 200 && urlIncludesApiPath(response.url(), '/api/geo/state-rollups')) {
        seenStateRollups.add(response.url());
      }
    });

    await chooseFilter(page, 'OCCUPANCY', 'Owner-occupied');
    await chooseFilter(page, 'LIEN', 'Open 1st lien only');
    await chooseFilter(page, 'OWNER LINK', 'Multi-property (2-4)');
    await chooseFilter(page, 'PURCHASE INTENT', 'Listed for sale');
    await chooseFilter(page, 'CASH-OUT', 'Equity ≥ 25%');

    await expect
      .poll(
        () =>
          [...seenStateRollups].some((url) => {
            const parsed = new URL(url);
            return (
              parsed.searchParams.get('occupancy') === 'Owner-occupied' &&
              parsed.searchParams.get('lien_status') === 'Open 1st lien' &&
              parsed.searchParams.get('owner_link') === 'Multi-property (2-4)' &&
              parsed.searchParams.get('purchase_intent') === 'Listed for sale' &&
              parsed.searchParams.get('min_equity_pct_label') === '≥ 25%'
            );
          }),
        { timeout: 60_000 },
      )
      .toBe(true);
  });

  test('home map drills to ZIP layer and deep-links without overlay collisions', async ({ page, request }) => {
    await gotoApp(page, '/');
    const target = await discoverMapDrillTarget(request);

    const map = page.locator('.map-wrap').first();
    await expect(map).toBeVisible({ timeout: 30_000 });
    await map.scrollIntoViewIfNeeded();

    await drillStateToCounty(page, target);

    await drillCountyToZips(page, target);

    await expect(page.locator('.zip-tiles')).toBeVisible({ timeout: 10_000 });

    const header = await page.locator('.map-hdr').boundingBox();
    const zipGrid = await page.locator('.zip-tiles').boundingBox();
    const crumbs = await page.locator('.map-crumbs').boundingBox();
    const chips = await page.locator('.map-corner-chips').boundingBox();
    expect(header, 'home map header should render').toBeTruthy();
    expect(zipGrid, 'home map ZIP grid should render').toBeTruthy();
    expect(crumbs, 'home map breadcrumbs should render').toBeTruthy();
    expect(chips, 'home map corner chips should render').toBeTruthy();
    if (header && zipGrid) {
      expect(header.y + header.height, 'home map header must not overlap ZIP tiles').toBeLessThanOrEqual(
        zipGrid.y + 1,
      );
    }
    if (crumbs && chips) {
      const separated =
        crumbs.x + crumbs.width <= chips.x ||
        chips.x + chips.width <= crumbs.x ||
        crumbs.y + crumbs.height <= chips.y ||
        chips.y + chips.height <= crumbs.y;
      expect(separated, 'home map breadcrumbs and corner chips must not overlap').toBe(true);
    }
    await expectMapCornerIconsCompact(page, 'home ZIP map');

    await page.locator('.zip-tile').first().click();
    const url = new URL(page.url());
    expect(url.pathname).toBe('/lead-queue');
    expect(url.searchParams.get('state')).toBe(target.state);
    expect(url.searchParams.get('county')).toBe(target.countyFips);
    expect(url.searchParams.get('zip')).toMatch(/^\d{5}$/);
    await expect(page.locator('table.tbl')).toBeVisible({ timeout: 45_000 });
  });

  test('ranked borrower -> evidence drawer shows >= 2 rows', async ({ page, request }) => {
    const leads = await fetchLeads(request);
    expect(leads.length, 'need >= 1 ranked borrower from /api/leads').toBeGreaterThan(0);

    // Pick the first real borrower id; navigate directly to the dossier so we
    // don't depend on row-click target rewriting.
    const target = leads[0].borrower_id;
    await gotoApp(page, `/borrower-360/${target}`);

    // Evidence drawer lives as `.drawer` per the prototype BEM. The evidence
    // list renders rows with a `.trig` / trigger-timeline class; we accept
    // either entry point as "evidence is visible".
    const evidenceRows = page.locator('.trig, .drawer .evidence-row, .evidence-chip');
    await expect
      .poll(() => evidenceRows.count(), { timeout: 15_000 })
      .toBeGreaterThanOrEqual(2);
  });

  test('home: every headline KPI opens an evidence drawer citing the headline metric view', async ({ page }) => {
    await gotoApp(page, '/');

    const kpiRow = page.locator('.kpi-row');
    await expect(kpiRow).toBeVisible({ timeout: 45_000 });
    await expect(kpiRow.locator('.kpi__source .evidence-chip')).toHaveCount(4, {
      timeout: 15_000,
    });

    // S1 acceptance: each home KPI resolves to the named
    // mip.semantics.portfolio_headline_metric_view and its drawer lineage
    // cites that view plus an underlying row asset.
    const kpiChips: Array<[string | RegExp, string]> = [
      ['Marketable population', 'Marketable population'],
      [/Rate \+ equity screen/, 'Rate + equity screen'],
      ['Opportunity score', 'Opportunity score'],
      [/How the offer path was selected/, 'How the offer path was selected'],
    ];
    for (const [chipLabel, drawerSubtitle] of kpiChips) {
      const chip = kpiRow.locator('.evidence-chip').filter({ hasText: chipLabel }).first();
      await expect(chip, `home KPI chip ${String(chipLabel)}`).toBeVisible({ timeout: 10_000 });
      await chip.click();
      const drawer = page.locator('.drawer.is-open').first();
      await expect(drawer).toBeVisible({ timeout: 3_000 });
      await expect(drawer.locator('.drawer__subtitle')).toHaveText(drawerSubtitle);
      await expect(
        drawer.locator('.lineage-node__name', {
          hasText: 'mip.semantics.portfolio_headline_metric_view',
        }),
      ).toBeVisible();
      await drawer.getByRole('button', { name: /Close drawer/i }).click();
      await expect(drawer).toBeHidden({ timeout: 3_000 });
    }
  });

  test('genie FAB returns a non-empty answer and source chip opens lineage', async ({ page }) => {
    test.setTimeout(120_000);
    await gotoApp(page, '/');

    await openGeniePanel(page);
    const panel = page.getByRole('dialog', { name: 'Genie chat' });
    await expect(panel).toBeVisible();

    const canonicalQ =
      'How many borrowers across current refreshed coverage are currently in-the-money?';
    await panel.getByLabel('Ask Genie').fill(canonicalQ);
    const { payload: canonicalPayload } = await captureGenieTurn(page, async () => {
      await panel.getByRole('button', { name: /Ask/i }).click();
    }, 60_000);
    expectGovernedGeniePayload(canonicalPayload, 'canonical FAB answer');
    expect(
      canonicalPayload.source,
      'the canonical count must use the deterministic governed SQL tier',
    ).toBe('trusted_sql');
    // A recognized-shape question is recomputed deterministically -- hence the
    // trusted_sql tier and the "trusted" chip -- but since the governed-draft
    // rework it leads with the live turn's own narrative and carries that
    // turn's real reasoning trace and follow-ups (backend
    // `_adapt_genie_response`, pinned by
    // test_recognized_shape_live_turn_preserves_genie_voice_and_live_fields).
    // Suppressing them would hide Genie's involvement in prose that is
    // Genie's, which is the less honest disclosure.
    //
    // The invariant that actually protects the user is anti-fabrication:
    // Genie-authored fields may appear ONLY when a live turn produced them.
    // A deterministic answer with no live turn behind it must carry none.
    const liveTurnBackedTheAnswer = Boolean(
      canonicalPayload.genie_status && canonicalPayload.message_id,
    );
    if (liveTurnBackedTheAnswer) {
      expect(
        canonicalPayload.reasoning_trace ?? [],
        'a live-turn-backed answer must expose that turn real reasoning summaries',
      ).not.toEqual([]);
    } else {
      expect(
        canonicalPayload.reasoning_trace ?? [],
        'a deterministic answer with no live turn must not expose reasoning summaries',
      ).toEqual([]);
      expect(
        canonicalPayload.proof?.reasoning_trace ?? [],
        'a deterministic answer with no live turn must not fabricate proof reasoning',
      ).toEqual([]);
      expect(
        canonicalPayload.follow_up_questions ?? [],
        'a deterministic answer with no live turn must not claim Genie follow-ups',
      ).toEqual([]);
    }

    // Cold Genie space = 10-15s; allow 40s (a cold warehouse + Genie
    // compilation can push past 20s on the first question of a session).
    // We assert the answer region renders at least one non-empty character
    // that is NOT the spinner glyph. The component uses `genie__msg--ai`
    // for assistant bubbles (see components/mortgage/GenieChat.tsx).
    // The pending indicator specifically -- not "any role=status in the
    // panel". The rendered answer keeps an sr-only role="status" live region
    // so screen readers hear the answer when it lands, and matching on the
    // role would wait forever for that (correct) element to disappear.
    await expect(panel.locator('.genie-progress')).toHaveCount(0, { timeout: 60_000 });
    const aiMessage = panel.locator('.genie__msg--ai').last();
    const answer = aiMessage.locator('.bubble');
    await expect(answer).toBeVisible({ timeout: 40_000 });
    await expect
      .poll(async () => (await answer.innerText()).trim().length, { timeout: 40_000 })
      .toBeGreaterThan(20);
    await expect(aiMessage.locator('.genie-proof-toggle').getByText('trusted', { exact: true })).toBeVisible();
    await expect(aiMessage.getByTestId('genie-feedback-up')).toBeVisible();
    await expect(aiMessage.getByTestId('genie-feedback-down')).toBeVisible();
    await expect(
      aiMessage.locator('.genie-answer__reasoning'),
      liveTurnBackedTheAnswer
        ? 'a live-turn-backed answer must disclose that turn reasoning in the UI'
        : 'a deterministic answer with no live turn must not render a reasoning disclosure',
    ).toHaveCount(liveTurnBackedTheAnswer ? 1 : 0);
    const feedbackResponsePromise = page.waitForResponse((response) => (
      response.request().method() === 'POST'
      && /\/api\/(?:v1\/)?genie\/feedback$/.test(response.url())
    ));
    await aiMessage.getByTestId('genie-feedback-up').click();
    const feedbackResponse = await feedbackResponsePromise;
    expect(feedbackResponse.status(), 'Genie thumbs-up feedback should persist').toBe(200);
    await expect(aiMessage.getByText('Feedback recorded')).toBeVisible();

    const sourceChip = aiMessage.locator('.sources .evidence-chip').first();
    await expect(sourceChip).toBeVisible({ timeout: 10_000 });
    await sourceChip.click();
    const drawer = page.locator('.drawer.is-open').first();
    await expect(drawer).toBeVisible({ timeout: 3_000 });
    await expect(drawer.locator('.drawer__subtitle')).toHaveText(
      /Marketable population|Ranked lead population|Lead score model|Borrower 360 feature set|Lead-generation metric view|Borrower opportunity metric view/,
    );
    await expect(drawer.getByText(/Compact semantics: Overview de-duplicates/i)).toBeVisible();
    const overviewAssets = drawer.locator('.governed-assets__list .lineage-node__chip');
    await expect(overviewAssets.first()).toBeVisible({ timeout: 10_000 });
    const overviewLinkState = await overviewAssets.evaluateAll((nodes) => nodes.map((node) => ({
      tag: node.tagName,
      href: node instanceof HTMLAnchorElement ? node.href : '',
    })));
    expect(overviewLinkState.length).toBeGreaterThan(0);
    expect(
      overviewLinkState.every(({ tag, href }) => tag === 'A' && /explore\/data/.test(href)),
      'Every governed Overview asset must deep-link to Catalog Explorer.',
    ).toBe(true);

    const catalogAsset = overviewAssets.first();
    const catalogHref = await catalogAsset.getAttribute('href');
    expect(catalogHref, 'lineage asset must expose a Catalog Explorer destination').toMatch(
      /^https:\/\/[^/]+\/explore\/data\//,
    );
    const destinationRequestPromise = page.context().waitForEvent('request', {
      predicate: (request) => request.isNavigationRequest() && request.url() === catalogHref,
      timeout: 30_000,
    });
    const popupPromise = page.waitForEvent('popup', { timeout: 30_000 });
    await catalogAsset.click();
    const [catalogPopup, destinationRequest] = await Promise.all([
      popupPromise,
      destinationRequestPromise,
    ]);
    expect(
      destinationRequest.url(),
      'clicking a lineage asset must open its exact Catalog Explorer destination',
    ).toBe(catalogHref);
    await catalogPopup.close();

    await drawer.getByRole('tab', { name: 'Lineage' }).click();
    await expect(drawer.getByText(/Ordered semantics: arrows follow/i)).toBeVisible();
    const lineageAssets = drawer.locator('#drawer-panel-lineage .lineage-node > .lineage-node__chip');
    await expect(lineageAssets.first()).toBeVisible({ timeout: 10_000 });
    const lineageLinkState = await lineageAssets.evaluateAll((nodes) => nodes.map((node) => ({
      tag: node.tagName,
      href: node instanceof HTMLAnchorElement ? node.href : '',
    })));
    expect(lineageLinkState.length).toBeGreaterThanOrEqual(overviewLinkState.length);
    expect(
      lineageLinkState.every(({ tag, href }) => tag === 'A' && /explore\/data/.test(href)),
      'Every governed Lineage asset must deep-link to Catalog Explorer.',
    ).toBe(true);
    await drawer.getByRole('button', { name: /Close drawer/i }).click();
  });

  test('Growth Agent actionable total matches the destination Lead Queue total', async ({ page }) => {
    test.setTimeout(120_000);
    await gotoApp(page, '/ask-genie');

    const workflowCard = page.locator('.growth-agent-card', {
      hasText: 'Daily Refi Opportunity Brief',
    }).first();
    await expect(workflowCard).toBeVisible({ timeout: 45_000 });
    const runResponsePromise = page.waitForResponse((response) => (
      response.request().method() === 'POST'
      && /\/api\/(?:v1\/)?growth-agent\/workflows\/daily_refi_brief\/run$/.test(
        new URL(response.url()).pathname,
      )
    ), { timeout: 90_000 });
    await workflowCard.getByRole('button', { name: 'Run', exact: true }).click();
    const runResponse = await runResponsePromise;
    expect(runResponse.status(), 'Growth Agent daily refi run returned non-200').toBe(200);
    const run = await runResponse.json() as {
      workflow: { title: string; action_label: string };
      broad_label: string;
      actionable_label: string;
      broad_total: number;
      actionable_total: number;
      route: string;
    };
    expect(run.broad_total).toBeGreaterThanOrEqual(run.actionable_total);
    expect(run.actionable_total).toBeGreaterThanOrEqual(0);
    expect(run.route).toContain('/lead-queue?');

    const latestRun = page.getByLabel('Latest Growth Agent run');
    await expect(latestRun).toBeVisible({ timeout: 45_000 });
    const broadMetric = latestRun.locator('.growth-agent-run__metrics > div', {
      hasText: run.broad_label,
    });
    const actionableMetric = latestRun.locator('.growth-agent-run__metrics > div', {
      hasText: run.actionable_label,
    });
    await expect(broadMetric.locator('strong')).toHaveText(run.broad_total.toLocaleString('en-US'));
    await expect(actionableMetric.locator('strong')).toHaveText(
      run.actionable_total.toLocaleString('en-US'),
    );

    const destinationResponsePromise = page.waitForResponse((response) => (
      response.request().method() === 'GET'
      && urlIncludesApiPath(response.url(), '/api/leads')
      && response.status() === 200
    ), { timeout: 60_000 });
    await latestRun.getByRole('button', { name: run.workflow.action_label, exact: true }).click();
    await expect(page).toHaveURL(/\/lead-queue\?/, { timeout: 30_000 });
    const destinationResponse = await destinationResponsePromise;
    expect(
      Number(destinationResponse.headers()['x-total-matching'] ?? -1),
      'destination /api/leads total must match the Growth Agent actionable total',
    ).toBe(run.actionable_total);

    const footer = page.locator('.stable-refresh-region--table .surface__ft');
    await expect(footer).toBeVisible({ timeout: 45_000 });
    const footerText = await footer.innerText();
    const footerMatch = footerText.match(/of ([\d,]+) total matching filters/);
    expect(footerMatch, `Lead Queue footer did not expose its destination total: ${footerText}`).toBeTruthy();
    expect(Number(footerMatch![1].replace(/,/g, ''))).toBe(run.actionable_total);
  });

  test('brand favicon and Genie dock chrome do not regress', async ({ page, request }) => {
    await gotoApp(page, '/');

    const iconHref = await page
      .locator('link[rel~="icon"][type="image/png"]')
      .first()
      .getAttribute('href');
    expect(iconHref, 'browser tab should prefer the Entrada PNG favicon').toContain(
      '/favicon.png?v=entrada-20260505',
    );

    const iconResponse = await request.get(`${APP_URL}/favicon.png?v=entrada-20260505`, {
      headers: AUTH_HEADERS,
    });
    expect(iconResponse.status(), 'Entrada favicon asset should be served by the deployed app').toBe(200);
    expect(iconResponse.headers()['content-type'] ?? '').toContain('image/png');

    await openGeniePanel(page);
    const panel = page.getByRole('dialog', { name: 'Genie chat' });
    await expect(panel).toBeVisible();

    const resizeBox = await panel.locator('.genie__resize').boundingBox();
    const avatarBox = await panel.locator('.genie__avatar').boundingBox();
    expect(resizeBox, 'Genie resize affordance should exist').toBeTruthy();
    expect(avatarBox, 'Genie avatar should exist').toBeTruthy();

    const overlapW = Math.max(
      0,
      Math.min(resizeBox!.x + resizeBox!.width, avatarBox!.x + avatarBox!.width) -
        Math.max(resizeBox!.x, avatarBox!.x),
    );
    const overlapH = Math.max(
      0,
      Math.min(resizeBox!.y + resizeBox!.height, avatarBox!.y + avatarBox!.height) -
        Math.max(resizeBox!.y, avatarBox!.y),
    );
    expect(overlapW * overlapH, 'Genie resize affordance must not overlap the avatar').toBeLessThanOrEqual(1);
  });

  test('approve outreach writes a new audit row visible in /api/audit within 5s', async ({ page, request }) => {
    test.skip(!ADMIN_BEARER, 'Requires MIP_ADMIN_BEARER_TOKEN to verify the persisted approval audit row.');
    const target = (await fetchActionablePendingLead(request)).borrower_id;

    const before = await fetchAuditEvents(request, 100, ADMIN_BEARER);
    if (before === null) throw new Error('Admin bearer could not read the audit ledger.');
    const beforeIds = new Set(before.map((e) => e.event_id).filter(Boolean));

    await gotoApp(page, `/offer-orchestrator/${target}`);
    // The ApprovalBanner primary button — same selector as module0.spec.ts.
    await page
      .getByRole('button', { name: /^Approve$|approve outreach/i })
      .first()
      .click();

    await expect(page.getByText(/Approved.*(?:audit|outreach queue)/i)).toBeVisible({
      timeout: 5_000,
    });

    // Poll /api/audit for a new row within 10s. Real Lakebase round-trip
    // including INSERT + SELECT is typically < 500 ms, but network +
    // serverless coldness can push it; 10s is a generous upper bound.
    //
    // Audit row shape: `entity_id` is the APPROVAL UUID (outreach.py sets
    // entity_id = approval_id, not borrower_id). The borrower id lives
    // in `payload_json.borrower_id`. Match on both the new-event-id and
    // the payload's borrower_id to avoid false positives from a sibling
    // approval that landed concurrently.
    await expect
      .poll(
        async () => {
          const rows = await fetchAuditEvents(request, 100, ADMIN_BEARER);
          if (rows === null) throw new Error('Admin bearer lost audit-ledger access.');
          return rows.find(
            (r) =>
              (r.action === 'outreach.approve' ||
                r.action === 'outreach_approve') &&
              r.payload_json?.borrower_id === target &&
              !beforeIds.has(r.event_id),
          );
        },
        { timeout: 10_000 },
      )
      .toBeTruthy();
  });

  test('forcing a 503 surfaces the DegradedBanner within 5s', async ({ page, request }) => {
    // Preconditions: the nightly workflow sets MIP_ADMIN_BEARER_TOKEN to an
    // admin-scoped PAT. The endpoint writes a Lakebase audit row and sets an
    // HttpOnly, browser-local proof cookie; it does not mutate global health.
    const adminBearer = process.env.MIP_ADMIN_BEARER_TOKEN;
    test.skip(
      !adminBearer,
      'Requires MIP_ADMIN_BEARER_TOKEN to invoke the admin force-degraded endpoint.',
    );

    const flip = async (state: 'on' | 'off') => {
      const status = await page.evaluate(async ({ apiUrl, bearer, nextState }) => {
        const resp = await fetch(`${apiUrl}/api/admin/force-degraded`, {
          method: 'POST',
          credentials: 'include',
          headers: {
            Authorization: `Bearer ${bearer}`,
            'content-type': 'application/json',
          },
          body: JSON.stringify({ state: nextState, dependency: 'warehouse', ttl_s: 30 }),
        });
        return resp.status;
      }, { apiUrl: API_URL, bearer: adminBearer!, nextState: state });
      expect([200, 204]).toContain(status);
    };

    const forcedHealth = async () =>
      page.evaluate(async ({ apiUrl }) => {
        const resp = await fetch(`${apiUrl}/api/health`, { credentials: 'include' });
        return resp.json();
      }, { apiUrl: API_URL });

    try {
      await gotoApp(page, '/');
      await flip('on');
      await page.reload({ waitUntil: 'domcontentloaded' });
      await expect
        .poll(
          async () => {
            const body = await forcedHealth();
            return body.forced_degraded?.source;
          },
          { timeout: 5_000 },
        )
        .toBe('admin_drill_cookie');

      const forced = await forcedHealth();
      expect(forced.dependencies?.warehouse).toBe('down');
      expect(forced.forced_degraded?.dependency).toBe('warehouse');

      await expect
        .poll(
          async () => {
            const rows = await fetchAuditEvents(request, 20, adminBearer!);
            return rows?.some(
              (row) =>
                row.event_type === 'FORCE_DEGRADED' &&
                row.payload_json?.forced_state === 'on' &&
                row.payload_json?.proof_scope === 'browser_cookie',
            );
          },
          { timeout: 10_000 },
        )
        .toBe(true);

      // DegradedBanner convention: top-of-page strip with role="alert" and
      // class `degraded-banner` (or data-testid `degraded-banner`).
      const banner = page
        .locator('[data-testid="degraded-banner"], .degraded-banner, [role="alert"]')
        .first();
      await expect(banner).toBeVisible({ timeout: 5_000 });
      await expect(banner).toContainText(/degraded|warming|unavailable|reconnecting/i);
    } finally {
      if (!page.isClosed()) {
        await flip('off').catch(() => undefined);
      }
    }
  });

  // ============================================================
  // Per-route real-data coverage (Slice-10 follow-up)
  // ============================================================
  // Each route below asserts:
  //   1. Navigate + a unique-to-that-route element appears.
  //   2. Click the route's primary CTA; assert downstream state.
  //   3. At least one piece of real data is rendered (no stuck
  //      Loading…, no "—" placeholder in a slot meant for a number).
  //
  // Real-data tolerance:
  //   - First call after a cold warehouse can take 30-60 s
  //     (docs/load-baseline.md §"Cold-start footnote"). Assertions
  //     that hit UC use timeouts of 30-45 s. Subsequent calls are
  //     warm and fast (p95 ~1300 ms for /api/leads).

  test('portfolio-builder: filters + CTA + KPIs come from /api/portfolio/preview', async ({ browser, page, request }) => {
    test.setTimeout(300_000);
    expect(
      ADMIN_BEARER,
      'live campaign capability proof requires the distinct admin bearer provisioned by nightly',
    ).not.toBe('');
    const capabilitiesResponse = await request.get(
      `${API_URL}/api/v1/admin/capabilities?live=1`,
      { headers: { Authorization: `Bearer ${ADMIN_BEARER}` }, timeout: 120_000 },
    );
    expect(capabilitiesResponse.status(), 'live capability probe returned non-200').toBe(200);
    const capabilitiesBody = await capabilitiesResponse.json() as
      | CapabilityRow[]
      | { capabilities?: CapabilityRow[] };
    const capabilities = Array.isArray(capabilitiesBody)
      ? capabilitiesBody
      : capabilitiesBody.capabilities ?? [];
    const supervisorCapability = capabilities.find((row) => row.key === 'agent_orchestrator');
    expect(supervisorCapability, 'capability matrix must include the Supervisor agent row').toBeTruthy();

    const campaignRecommendationResponse = page.waitForResponse((response) => (
      response.request().method() === 'POST'
      && urlIncludesApiPath(response.url(), '/api/portfolio/campaign-recommendation')
    ), { timeout: 120_000 });
    await gotoApp(page, '/portfolio-builder');
    const campaignResponse = await campaignRecommendationResponse;
    expect(campaignResponse.status(), 'live campaign recommendation returned non-200').toBe(200);
    const recommendation = await campaignResponse.json() as CampaignRecommendationPayload;
    const supervisorClaimable = Boolean(
      supervisorCapability?.claimable && supervisorCapability.status === 'available',
    );
    if (supervisorClaimable) {
      expect(
        recommendation.generation_mode,
        'a claimable Supervisor capability must produce the AI campaign recommendation',
      ).toBe('supervisor');
      expect(recommendation.generator_label).toBe('Databricks Agent Responses');
      expect(recommendation.warnings).toEqual([]);
    }
    expect(['supervisor', 'reviewed_fallback']).toContain(recommendation.generation_mode);
    expect(recommendation.audience_summary.trim().length).toBeGreaterThan(20);
    expect(recommendation.strategy.trim().length).toBeGreaterThan(20);
    expect(['qualified', 'insufficient_sample', 'unavailable']).toContain(
      recommendation.performance_status,
    );
    expect(recommendation.holdout_pct).toBeGreaterThanOrEqual(5);
    expect(recommendation.holdout_pct).toBeLessThanOrEqual(30);
    expect(recommendation.variants).toHaveLength(2);
    for (const variant of recommendation.variants) {
      expect(variant.hypothesis.trim().length, `${variant.variant_name} hypothesis`).toBeGreaterThan(20);
      expect(variant.provenance_token?.trim(), `${variant.variant_name} provenance`).toBeTruthy();
    }
    expect(recommendation.evidence.length, 'campaign response must carry governed evidence').toBeGreaterThan(0);
    for (const row of recommendation.evidence) {
      expect(row.label.trim(), 'campaign evidence label').not.toBe('');
      expect(row.value.trim(), `campaign evidence value for ${row.label}`).not.toBe('');
      expect(row.source_asset, `campaign evidence source for ${row.label}`).toMatch(
        /^(mip\.|mip_app\.)/,
      );
    }
    if (recommendation.performance_status === 'qualified') {
      expect(recommendation.evidence.map((row) => row.source_asset)).toEqual(
        expect.arrayContaining(['mip_app.call_dispositions', 'mip_app.lead_outcomes']),
      );
    }

    // Unique-to-route: the lender-conversation filter set renders with the
    // prototype BEM, including Owner Link and purchase-intent overlays.
    for (const label of [
      'GEO',
      'OCCUPANCY',
      'LIEN STATUS',
      'RELATIONSHIP',
      'TARGET LIEN HOLDER',
      'OWNER LINK',
      'PURCHASE INTENT',
      'PRODUCT',
      'EQUITY',
    ]) {
      await expect(page.getByRole('button', { name: new RegExp(`^${label}:`) })).toBeVisible();
    }
    // The route lede, verbatim: "Apply geography, lien, relationship, Owner
    // Link, purchase intent, product, and equity filters." The old pattern
    // hyphenated "purchase-intent", which the copy has never used on this
    // branch or on main, so it could not match anywhere.
    await expect(
      page.getByText(/Owner Link, purchase intent, product, and equity filters/i),
    ).toBeVisible();
    // Governance disclosure on this surface. The previous assertion looked for
    // "staged cadence only", a phrase that appears nowhere in the product;
    // pin the eligibility gate the page actually states before approval.
    await expect(page.getByText(/eligible-only policy required before approval/i)).toBeVisible();
    const dataOperationsLink = page.getByRole('link', { name: /Admin Data Operations/i });
    await expect(dataOperationsLink).toBeVisible();
    const adminContext = await browser.newContext({
      baseURL: APP_URL,
      extraHTTPHeaders: { Authorization: `Bearer ${ADMIN_BEARER}` },
    });
    const adminPage = await adminContext.newPage();
    try {
      await gotoApp(adminPage, '/portfolio-builder');
      await adminPage.getByRole('link', { name: /Admin Data Operations/i }).click();
      await expect(adminPage).toHaveURL(/\/admin-config#data-operations$/);
      await expect(adminPage.getByRole('heading', { name: 'Rules, data sources, and audit' })).toBeVisible();
      await expect(adminPage.locator('#data-operations')).toBeVisible({ timeout: 10_000 });
      await expect(adminPage.getByLabel('Data freshness snapshot')).toBeVisible({ timeout: 30_000 });
      await expect(adminPage.getByText('Latest refresh')).toBeVisible();
    } finally {
      await adminContext.close();
    }

    // Primary CTA per prototype (design_files/Module 0 Prototype.html line
    // 1780): the "Run build" button in the filter row. Not a forward-nav;
    // its purpose is triggering the build animation / preview refresh.
    // We assert click → no navigation and the page stays on the route.
    const runBuild = page.getByRole('button', { name: /^Run build$/i });
    await expect(runBuild).toBeVisible();
    await runBuild.click();
    await expect(page).toHaveURL(/\/portfolio-builder$/);

    // Real data: /api/portfolio/preview drives the first KPI card
    // "Marketable population". Cold-UC takes up to ~30 s on first hit;
    // warm is <200 ms. We assert the value is a parseable non-zero
    // number (not "—", not "Loading…").
    const firstKpi = page.locator('.kpi').first();
    await expect(firstKpi).toBeVisible({ timeout: 30_000 });
    await expect
      .poll(
        async () => {
          const raw = (await firstKpi.locator('.kpi__value').innerText()).trim();
          const n = Number.parseInt(raw.replace(/[^0-9]/g, ''), 10);
          return Number.isFinite(n) && n > 0 ? n : 0;
        },
        { timeout: 30_000 },
      )
      .toBeGreaterThan(0);

    const economics = page.getByTestId('roi-projector');
    await expect(economics).toBeVisible({ timeout: 45_000 });
    await expect(economics).toContainText(/selected cohort/i);
    await expect(economics).toContainText(/team benchmark/i);
    await expect(economics).not.toContainText(/illustrative|operator-set/i);

    const campaign = page.locator('.surface', { hasText: 'Campaign setup' }).first();
    await expect(campaign.getByText('Message hypotheses', { exact: true })).toBeVisible({
      timeout: 90_000,
    });
    await expect(campaign.locator('[aria-label$=" hypothesis"]')).toHaveCount(2);
    await expect(campaign.locator('[aria-label="Recommendation evidence"] .evidence-chip').first())
      .toBeVisible();
    await expect(campaign).toContainText(recommendation.generator_label);
    await expect(campaign).toContainText(
      recommendation.performance_status === 'qualified'
        ? 'Qualified team performance'
        : recommendation.performance_status === 'insufficient_sample'
          ? 'Cohort data only · sample too small'
          : 'Cohort data only · operations unavailable',
    );
    await expect(campaign.locator('.campaign-recommendation__strategy')).toHaveText(
      recommendation.strategy,
    );
    await expect(campaign).not.toContainText(
      /No borrower identities, contact data, or outbound messages are included/i,
    );
    await campaign.getByRole('button', { name: 'Apply variants' }).click();
    await expect(page.getByLabel('Benefit-led subject')).not.toHaveValue('');
    await expect(page.getByLabel('Guidance-led subject')).not.toHaveValue('');
    await expect(page.getByLabel('Benefit-led message')).not.toHaveValue('');
    await expect(page.getByLabel('Guidance-led message')).not.toHaveValue('');

    const leadQueue = page.getByRole('link', { name: /Open lead queue/i });
    await expect(leadQueue).toBeVisible({ timeout: 30_000 });
    await leadQueue.click();
    await expect(page).toHaveURL(/\/lead-queue\?/);
    await expect
      .poll(() => new URL(page.url()).searchParams.has('geography'), { timeout: 5_000 })
      .toBe(false);
    await expect
      .poll(() => new URL(page.url()).searchParams.has('states'), { timeout: 5_000 })
      .toBe(false);
    await expect(page).toHaveURL(/occupancy=Owner-occupied/);
    await expect(page).toHaveURL(/lien_status=Open\+1st\+lien/);
    await expect(page).toHaveURL(/min_equity_pct_label=/);
    await expect(page.getByText(/occupancy = Owner-occupied/i)).toBeVisible({ timeout: 30_000 });
    await expect(page.getByText(/equity = ≥ 15%/i)).toBeVisible();
    await expect(page.locator('table.tbl')).toBeVisible({ timeout: 45_000 });

    await gotoApp(page, '/portfolio-builder?owner_link=Portfolio+investor+%285%2B%29&purchase_intent=HELOC+intent');
    const segmentResponse = page.waitForResponse((response) => {
      if (response.status() !== 200 || !urlIncludesApiPath(response.url(), '/api/segments')) return false;
      const parsed = new URL(response.url());
      return parsed.searchParams.get('owner_link') === 'Portfolio investor (5+)'
        && parsed.searchParams.get('purchase_intent') === 'HELOC intent';
    });
    // Forward nav CTA (secondary, but the product's intended progression).
    await page.getByRole('link', { name: /Next: (?:segment intelligence|segments)/i }).click();
    await segmentResponse;
    await expect(page).toHaveURL(/\/segment-intelligence\?/);
    await expect(page.getByRole('button', { name: /OWNER LINK: Portfolio investor \(5\+\)/i })).toBeVisible({ timeout: 30_000 });
    await expect(page.getByRole('button', { name: /PURCHASE INTENT: HELOC intent/i })).toBeVisible();
  });

  test('sales outcomes: live manual import writes Lakebase ledger and Lead Queue explains status', async ({ page, request }) => {
    const today = new Date();
    const from = new Date(today.getTime() - 7 * 24 * 60 * 60 * 1000);
    const fromDate = from.toISOString().slice(0, 10);
    const toDate = today.toISOString().slice(0, 10);
    const summaryUrl = `${API_URL}/api/sales/outcomes/summary?from=${fromDate}&to=${toDate}`;

    const beforeResp = await request.get(summaryUrl, { headers: AUTH_HEADERS });
    expect(beforeResp.status(), 'sales outcome summary should be manager-visible').toBe(200);
    const before = await beforeResp.json();
    expect(Array.isArray(before.source_statuses), 'outcome source statuses should render').toBe(true);
    const manual = before.source_statuses.find((row: { source_system?: string }) =>
      row.source_system === 'manual_import',
    );
    expect(manual?.configured, 'manual imports remain available for governed backfill').toBe(true);

    const lead = (await fetchLeads(request, 1))[0];
    expect(lead?.borrower_id, 'need a live borrower for outcome ingestion').toBeTruthy();
    const requestId = randomUUID();
    const outcomeResp = await request.post(`${API_URL}/api/leads/${lead.borrower_id}/outcome`, {
      headers: AUTH_HEADERS,
      data: {
        outcome_type: 'application_submitted',
        source_system: 'manual_import',
        source_record_ref: requestId,
        request_id: requestId,
      },
    });
    expect(outcomeResp.status(), 'manual-import lead outcome write').toBe(200);
    const outcomeBody = await outcomeResp.json();
    expect(outcomeBody.audit_event_id, 'outcome write should create server-owned audit event').toBeTruthy();
    expect(outcomeBody.outcome.borrower_id).toBe(lead.borrower_id);
    expect(outcomeBody.outcome.source_system).toBe('manual_import');

    await expect
      .poll(
        async () => {
          const resp = await request.get(summaryUrl, { headers: AUTH_HEADERS });
          if (resp.status() !== 200) return -1;
          const body = await resp.json();
          return Number(body.applications_submitted ?? 0);
        },
        { timeout: 10_000 },
      )
      .toBeGreaterThanOrEqual(Number(before.applications_submitted ?? 0) + 1);

    // The Sales ops snapshot moved off Lead Queue to Analytics -> Sales ops
    // (analytics.tsx mounts SalesOpsSection for tab === 'sales-ops', and
    // lead-queue.test.tsx pins that no residual is left behind), so assert the
    // ledger where it now lives. The heading swaps to the dry-run variant when
    // the deployment has dry-run outcome rows -- both are the same panel.
    await gotoApp(page, '/analytics');
    await page.getByRole('tab', { name: /^Sales ops$/i }).click();
    await expect(
      page.getByText(/Closed-loop outcomes|Outcome ledger · includes dry run/),
    ).toBeVisible({ timeout: 45_000 });
    await expect(page.getByText(/Imported, read-only outcome ledger/i)).toBeVisible();
    // Verbatim disclosure: "Imported, read-only outcome ledger; no write-back
    // to customer systems." The old pattern ("does not write back") never
    // matched that wording.
    await expect(page.getByText(/no write-back to customer systems/i)).toBeVisible();
  });

  test('analytics: multi-select filters drive API queries and URL state', async ({ page }) => {
    test.setTimeout(90_000);

    await gotoApp(page, '/analytics');
    await expect(page.getByRole('heading', { name: 'Analytics', exact: true })).toBeVisible({ timeout: 30_000 });

    const stateButton = page.getByRole('button', { name: /^State:/i });
    await stateButton.click();
    await expect(page.getByRole('listbox', { name: 'State', exact: true })).toBeVisible();
    const keyboardStateResponse = page.waitForResponse((response) => {
      if (!response.url().includes('/api/v1/analytics/executive')) return false;
      return new URL(response.url()).searchParams.get('states') === 'IL';
    }, { timeout: 45_000 });
    await page.getByRole('option', { name: 'IL', exact: true }).click();
    await keyboardStateResponse;
    await expect(page.getByRole('button', { name: /State: IL/i })).toBeVisible();

    const stateResponse = page.waitForResponse((response) => {
      if (!response.url().includes('/api/v1/analytics/executive')) return false;
      const params = new URL(response.url()).searchParams;
      return params.get('states') === 'IL,CA';
    });
    await page.getByRole('option', { name: 'CA', exact: true }).click();
    await stateResponse;
    await page.keyboard.press('Escape');
    await expect(page.getByRole('button', { name: /State: 2 selected/i })).toBeVisible();

    await page.getByRole('button', { name: /^Segment:/i }).click();
    await page.getByRole('option', { name: 'Prime Refi Candidates', exact: true }).click();
    const segmentResponse = page.waitForResponse((response) => {
      if (!response.url().includes('/api/v1/analytics/executive')) return false;
      const params = new URL(response.url()).searchParams;
      return (
        params.get('states') === 'IL,CA' &&
        params.get('segment_codes') === 'itm,equity' &&
        params.get('segment_mode') === 'any'
      );
    });
    await page.getByRole('option', { name: 'Home Equity Candidate', exact: true }).click();
    await segmentResponse;
    await page.keyboard.press('Escape');
    await expect(page.getByRole('button', { name: /Segment: 2 selected/i })).toBeVisible();

    await page.getByRole('tab', { name: /Signals/i }).click();
    await page.getByRole('button', { name: /^Signal:/i }).click();
    await page.getByRole('option', { name: 'Equity', exact: true }).click();
    await page.getByRole('option', { name: 'Rate spread', exact: true }).click();
    await page.keyboard.press('Escape');
    await expect(page.getByRole('button', { name: /Signal: 2 selected/i })).toBeVisible();

    const signalResponse = page.waitForResponse((response) => {
      if (!response.url().includes('/api/v1/analytics/signals')) return false;
      const params = new URL(response.url()).searchParams;
      return (
        params.get('states') === 'IL,CA' &&
        params.get('segment_codes') === 'itm,equity' &&
        params.get('segment_mode') === 'any' &&
        params.get('signal_types') === 'equity,rate_spread' &&
        params.get('days') === '7'
      );
    });
    await page.getByRole('button', { name: /^Window:/i }).click();
    await page.getByRole('option', { name: 'Last 7 days', exact: true }).click();
    await signalResponse;

    const url = new URL(page.url());
    expect(url.pathname).toBe('/analytics');
    expect(url.searchParams.get('states')).toBe('IL,CA');
    expect(url.searchParams.get('segment_codes')).toBe('itm,equity');
    expect(url.searchParams.get('segment_mode')).toBe('any');
    expect(url.searchParams.get('signal_types')).toBe('equity,rate_spread');
    expect(url.searchParams.get('days')).toBe('7');
    await expect(page.getByText(/Evidence by Signal Type/i)).toBeVisible({ timeout: 30_000 });
  });

  test('lead-queue: rows render, row expand opens inline dossier preview', async ({ page }) => {
    await gotoApp(page, '/lead-queue');

    // Unique-to-route: the table has the prototype BEM `table.tbl`.
    const table = page.locator('table.tbl');
    await expect(table).toBeVisible({ timeout: 30_000 });

    // Real data: wait for >=1 borrower row (cold UC can add 30-60 s).
    // `/api/leads` warm p95 is ~1300 ms per docs/load-baseline.md.
    const rows = table.locator('tbody tr').filter({ hasNot: page.locator('.tbl__expand') });
    await expect.poll(async () => rows.count(), { timeout: 45_000 }).toBeGreaterThanOrEqual(1);

    // Primary CTA per prototype: row click opens the inline preview
    // (`.tbl__expand` sibling row). This is the table's central interaction
    // — there's no "go" button; expanding the row IS the primary action.
    const firstRow = rows.first();
    await expect(firstRow).toBeVisible();
    await firstRow.getByText(/B-[A-Z0-9]+/).first().click({ noWaitAfter: true });

    // Downstream state: a `.tbl__expand` row is inserted right after the
    // clicked row, revealing the mini-dossier preview.
    await expect(page.locator('.tbl__expand').first()).toBeVisible({ timeout: 3_000 });
    await expect(page.locator('.tbl__expand-inner').first()).toContainText(/Customer 360 preview|Property ref|Segments/i);
  });

  test('lead-queue: row-preview evidence chips open distinct source drawers', async ({ page, request }) => {
    const leads = await fetchLeads(request, 25);
    const target = leads.find(
      (lead) => (lead.equity_estimate ?? 0) > 0 && (lead.current_lien_balance ?? 0) > 0,
    );
    expect(target, 'need a lead carrying both AVM/equity and lien evidence').toBeTruthy();

    await gotoApp(page, '/lead-queue');
    const table = page.locator('table.tbl');
    await expect(table).toBeVisible({ timeout: 30_000 });

    const targetRow = table.locator('tbody tr', { hasText: target!.borrower_id }).first();
    await expect(targetRow).toBeVisible({ timeout: 45_000 });
    await targetRow.getByText(target!.borrower_id).first().click({ noWaitAfter: true });

    const preview = page.locator('.tbl__expand').first();
    await expect(preview).toBeVisible({ timeout: 3_000 });

    const assertDrawer = async (chipLabel: string, drawerTitle: string) => {
      const chip = preview.getByRole('button', { name: chipLabel });
      await expect(chip).toBeVisible();
      await chip.click();
      const drawer = page.locator('.drawer.is-open').first();
      await expect(drawer).toBeVisible({ timeout: 3_000 });
      await expect(drawer.locator('.drawer__subtitle')).toHaveText(drawerTitle);
      await drawer.getByRole('button', { name: /Close drawer/i }).click();
      await expect(drawer).toBeHidden({ timeout: 3_000 });
    };

    await assertDrawer('Property + owner graph', 'Property + owner graph');
    await assertDrawer('AVM equity', 'AVM equity');
    await assertDrawer('Voluntary lien', 'Voluntary lien');
    await assertDrawer('Opportunity score', 'Opportunity score');
  });

  test('lead-queue: inline approval writes selected evidence ids to audit', async ({ page, request }) => {
    test.skip(!ADMIN_BEARER, 'Requires MIP_ADMIN_BEARER_TOKEN to verify the persisted inline-approval audit row.');
    const before = await fetchAuditEvents(request, 100, ADMIN_BEARER);
    if (before === null) throw new Error('Admin bearer could not read the audit ledger.');
    const beforeIds = new Set(before.map((e) => e.event_id).filter(Boolean));

    const target = await fetchActionablePendingLead(request);
    await gotoApp(page, `/lead-queue?approval_status=pending&borrower_ids=${encodeURIComponent(target.borrower_id)}`);
    const approveButton = page.getByTestId(`lead-approve-${target.borrower_id}`);
    await expect(approveButton).toBeVisible({ timeout: 45_000 });
    const testId = await approveButton.getAttribute('data-testid');
    const borrowerId = testId?.replace(/^lead-approve-/, '');
    expect(borrowerId, 'need a visible approvable borrower').toBeTruthy();

    expect(target.evidence_ids?.length ?? 0, `need evidence ids for ${borrowerId}`).toBeGreaterThan(0);

    const row = page.locator('table.tbl tbody tr', { hasText: borrowerId! }).first();
    await expect(row).toBeVisible({ timeout: 45_000 });
    await approveButton.click();

    await expect
      .poll(
        async () => {
          const rows = await fetchAuditEvents(request, 100, ADMIN_BEARER);
          if (rows === null) throw new Error('Admin bearer lost audit-ledger access.');
          return rows.find(
            (r) =>
              (r.action === 'outreach.approve' || r.action === 'outreach_approve') &&
              r.payload_json?.borrower_id === borrowerId &&
              !beforeIds.has(r.event_id),
          )?.evidence_ids?.length ?? 0;
        },
        { timeout: 10_000 },
      )
      .toBeGreaterThan(0);
  });

  test('borrower-360: why, timeline, and supporting evidence chips open specific drawers', async ({ page, request }) => {
    const borrower = await findBorrowerWithEvidenceProducts(request, [
      'Voluntary Lien',
      'AVM',
      'Market Rates',
      'Owner Link',
      'Property',
      'Mortgage Domain',
    ]);
    const target = borrower.borrower_id;

    await gotoApp(page, `/borrower-360/${target}`);

    // Unique-to-route: the refi economics surface with ITM/rate-spread chips.
    await expect(page.getByText(/Refi economics check/i)).toBeVisible({ timeout: 30_000 });

    // Real data: the borrower dossier surface shows masked property and owner
    // graph refs. Raw Cotality identifiers must not appear in the UI.
    const c360 = page.locator('.surface', { hasText: /Borrower dossier/i }).first();
    await expect(c360).toBeVisible();
    await expect(c360).toContainText(/Property ref/i);
    await expect(c360).toContainText(/clip_ref_|clip_demo_/i);

    const economics = page.locator('.surface', { hasText: /Refi economics check/i }).first();
    await assertSourceDrawer(page, economics, 'Market rate comparison', 'Market rate comparison');
    await assertSourceDrawer(page, economics, 'Refinance economics screen', 'Rate + equity screen');

    const timeline = page.locator('.surface', { hasText: /Trigger timeline/i }).first();
    await assertSourceDrawer(page, timeline, 'Rate spread evidence', 'Rate spread evidence');
    await assertSourceDrawer(page, timeline, 'AVM equity', 'AVM equity');
    await assertSourceDrawer(page, timeline, 'Market rate comparison', 'Market rate comparison');

    const supporting = page.locator('.surface', { hasText: /Supporting evidence/i }).first();
    await assertSourceDrawer(page, supporting, 'Owner Link', 'Property + owner graph');
    await assertSourceDrawer(page, supporting, 'Property', 'Property profile');
    await assertSourceDrawer(page, supporting, 'Mortgage Domain', 'Mortgage Domain events');

    // Forward CTA.
    await page.getByRole('link', { name: /Build outreach draft/i }).click();
    await expect(page).toHaveURL(new RegExp(`/offer-orchestrator/${target}$`));
  });

  test('offer-orchestrator: draft surface renders with real borrower data', async ({ page, request }) => {
    const leads = await fetchLeads(request);
    expect(leads.length).toBeGreaterThan(0);
    const target = leads[0].borrower_id;

    await gotoApp(page, `/offer-orchestrator/${target}`);

    // Unique-to-route: the governed draft surface on the right. The heading
    // was "Draft outreach · review only" before the governed-draft rework
    // renamed it; assert the current heading AND the review-only control it
    // labels, so this keeps failing if the "no automatic outreach" posture
    // ever leaves the surface rather than merely being reworded.
    await expect(
      page.getByText(/Governed outreach.*exact audited copy/i),
    ).toBeVisible({ timeout: 30_000 });
    await expect(page.getByLabel('Outreach draft — review only')).toBeVisible();

    // Real data: the draft textarea is prefilled with a borrower-aware
    // message ("Hi <first name> — based on recent public-record signals…").
    // We only assert it is non-empty; the actual lender / city strings vary
    // per borrower.
    const draft = page.locator('textarea').first();
    await expect(draft).toBeVisible();
    await expect
      .poll(async () => ((await draft.inputValue()) ?? '').trim().length, { timeout: 30_000 })
      .toBeGreaterThan(40);
    await expect(draft).not.toHaveValue(/public-record signals|the right offer/i);
    const intelligence = page.getByTestId('offer-message-intelligence');
    await expect(intelligence).toBeVisible({ timeout: 30_000 });
    await expect(intelligence).toContainText(
      /Supervisor-optimized message|Governed message framework/,
    );
    await expect(intelligence.locator('[aria-label="Message evidence"]')).toBeVisible();
    await expect(intelligence.locator('[aria-label="Message source assets"] .evidence-chip').first())
      .toBeVisible();

    // Primary CTA is the Approve button, already covered by the
    // `approve outreach writes a new audit row…` test above. Here we
    // assert the "Thresholds applied" and "Considered alternatives"
    // surfaces renderd — these are the route's differentiators from the
    // simpler prototypes.
    await expect(page.getByText(/Thresholds applied/i).first()).toBeVisible();
    await expect(page.getByText(/Considered alternatives/i)).toBeVisible();

    const primaryOffer = page.locator('.surface', { hasText: /Primary offer/i }).first();
    await assertSourceDrawer(page, primaryOffer, 'Primary offer rules', 'How the offer path was selected');
    await assertSourceDrawer(page, primaryOffer, 'Opportunity score', 'Opportunity score');
    const sourceLabels = await primaryOffer
      .locator('.evidence-chip')
      .evaluateAll((nodes) => nodes.map((node) => node.textContent?.trim() ?? ''));
    if (sourceLabels.includes('Market rate comparison')) {
      await assertSourceDrawer(page, primaryOffer, 'Market rate comparison', 'Market rate comparison');
      await assertSourceDrawer(page, primaryOffer, 'Refinance economics screen', 'Rate + equity screen');
    } else if (sourceLabels.includes('MLS listing activity')) {
      await assertSourceDrawer(page, primaryOffer, 'MLS listing activity', 'MLS listing');
    } else if (sourceLabels.includes('HELOC propensity')) {
      await assertSourceDrawer(page, primaryOffer, 'HELOC propensity', 'HELOC propensity signal');
    } else {
      throw new Error(`Primary offer did not expose a recognized offer-driver source: ${sourceLabels.join(', ')}`);
    }
  });

  test('ask-genie: native Conversation API turn renders governed proof and feedback', async ({ page }) => {
    test.setTimeout(420_000);

    await gotoApp(page, '/ask-genie');

    // Unique-to-route: the "Trusted assets" surface listing UC metric
    // views. Only the standalone /ask-genie page has this; the floating
    // FAB from Home (covered in `genie FAB returns a non-empty answer`
    // above) does NOT.
    await expect(page.getByText(/Trusted assets/i)).toBeVisible({ timeout: 5_000 });
    await expect(page.getByText(/gold\.lead_population/)).toBeVisible();
    await expect(page.getByText(/semantics\.lead_generation_metric_view/)).toBeVisible();

    // This multi-metric grouping is intentionally outside the reviewed
    // deterministic question catalog. In live-first mode it must exercise a
    // native Databricks Genie Conversation API turn rather than a canned SQL
    // answer. A disconnected or deterministic-only deployment therefore
    // fails this gate instead of satisfying it with an answer-shaped fallback.
    await page.locator('textarea[aria-label="Ask Genie — question"]').fill(
      NATIVE_GENIE_CONVERSATION_QUESTION,
    );
    const askButton = page.getByRole('button', { name: /^Ask Genie$/i }).first();
    await expect(askButton).toBeVisible();
    const { payload } = await captureGenieTurn(page, async () => {
      await askButton.click();
    });
    expect(payload.source, 'native aggregation must use the Genie answer tier').toBe('genie');
    expectLiveGenieTurn(payload, 'standalone native Genie answer');

    const sourceDisclosure = page.getByLabel(
      'Answer source: Databricks Genie Conversation API',
    );
    const answerSurface = page.locator('.surface.surface--inset', {
      has: sourceDisclosure,
    }).first();
    await expectLiveGenieUi(answerSurface, payload, 'standalone native Genie answer');
    await expect(sourceDisclosure).toHaveText('Databricks Genie Conversation API');
    await expect(answerSurface.locator('.sources .evidence-chip').first()).toBeVisible();
    await expect(answerSurface.locator('.genie-answer__table tbody tr').first()).toBeVisible({
      timeout: 10_000,
    });

    await answerSurface.getByRole('button', { name: /Show proof/i }).click();
    const proofDrawer = page.getByRole('dialog', { name: /Genie answer proof/i });
    await expect(proofDrawer).toBeVisible();
    await expect(proofDrawer.getByText(/Trusted SELECT on curated assets/i)).toBeVisible();
    await expect(proofDrawer.locator('.evidence-chip').first()).toBeVisible();
    await expect(proofDrawer.locator('pre.genie-proof__sql')).toContainText(/borrower_360/i);
    await proofDrawer.getByRole('button', { name: /Close Genie proof/i }).click();

    const firstConversationId = payload.conversation_id!.trim();
    const followUpQuestion = payload.follow_up_questions![0].trim();
    // Reload to prove the ordinary composer path restores the server-owned
    // conversation from browser state. The former regression passed an ID
    // only from answer-surface buttons, while typed/sample questions silently
    // started a new thread after restoration.
    await page.reload({ waitUntil: 'domcontentloaded' });
    const restoredQuestion = page.locator('textarea[aria-label="Ask Genie — question"]');
    await expect(restoredQuestion).toBeVisible();
    await restoredQuestion.fill(followUpQuestion);
    const { request: followUpRequest, payload: followUpPayload } = await captureGenieTurn(
      page,
      async () => {
        await page.getByRole('button', { name: /^Ask Genie$/i }).first().click();
      },
    );
    expect(followUpRequest.question).toBe(followUpQuestion);
    expect(
      followUpRequest.conversation_id,
      'live follow-up POST must carry the first native conversation id',
    ).toBe(firstConversationId);
    expect(followUpPayload.source, 'follow-up must remain on the native Genie tier').toBe('genie');
    expect(followUpPayload.conversation_id).toBe(firstConversationId);
    expectLiveGenieTurn(followUpPayload, 'standalone native Genie follow-up');

    const followUpSurface = page.locator('.surface.surface--inset', {
      has: page.getByLabel('Answer source: Databricks Genie Conversation API'),
    }).last();
    await expectLiveGenieUi(
      followUpSurface,
      followUpPayload,
      'standalone native Genie follow-up',
    );

    const sampleGroup = page.getByRole('group', { name: 'Suggested Genie questions' });
    const sampleButton = sampleGroup.getByRole('button').first();
    await expect(sampleButton).toBeVisible();
    const sampleQuestion = (await sampleButton.textContent())?.trim();
    expect(sampleQuestion, 'restored composer sample question is empty').toBeTruthy();
    const { request: sampleRequest } = await captureGenieTurn(page, async () => {
      await sampleButton.click();
    });
    expect(sampleRequest.question).toBe(sampleQuestion);
    expect(
      sampleRequest.conversation_id,
      'live sample POST must keep the restored native conversation id',
    ).toBe(firstConversationId);
  });

  test('ask-genie: listed days-on-market prompt returns trusted app proof', async ({ page }) => {
    test.setTimeout(120_000);

    await gotoApp(page, '/ask-genie');

    await page
      .locator('textarea[aria-label="Ask Genie — question"]')
      .fill(
        'Among listed-for-sale borrowers, what is the average listing days on market by state for the top five states?',
      );
    await page.getByRole('button', { name: /^Ask Genie$/i }).first().click();

    const answerSurface = page
      .locator('.surface', { hasText: /top state by listed borrower count/i })
      .first();
    await expect(answerSurface).toBeVisible({ timeout: 90_000 });
    await expect(answerSurface).toContainText(/mip\.gold\.borrower_360/);
    await expect(answerSurface).toContainText(/average listing days on market/i);
    await expect(answerSurface.locator('.genie-answer__table tbody tr').first()).toBeVisible({
      timeout: 10_000,
    });

    await answerSurface.getByRole('button', { name: /Show proof/i }).click();
    const proofDrawer = page.getByRole('dialog', { name: /Genie answer proof/i });
    await expect(proofDrawer).toBeVisible();
    await expect(proofDrawer.getByText(/Trusted SELECT on curated assets/i)).toBeVisible();
    await expect(proofDrawer.locator('.evidence-chip', { hasText: /mip\.gold\.borrower_360/ })).toBeVisible();
    await expect(proofDrawer.getByText(/AVG\(listing_days_on_market\)/)).toBeVisible();
    await expect(
      proofDrawer.locator('pre.genie-proof__sql', { hasText: /listed_for_sale = TRUE/ }),
    ).toBeVisible();
  });

  test('ask-genie: shows governed progress while a live request is pending', async ({ page }) => {
    // Async lifecycle: the UI's first call is /message/submit; delaying it
    // holds the null-progress "Waiting for Genie response" placeholder on
    // screen (live stage labels only start with the first progress poll).
    await page.route('**/api/genie/message/submit', async (route) => {
      await new Promise((resolve) => setTimeout(resolve, 1500));
      await route.continue();
    });
    await gotoApp(page, '/ask-genie');

    await page
      .locator('textarea[aria-label="Ask Genie — question"]')
      .fill('Which ZIPs have the most in-the-money refinance candidates?');
    await page.getByRole('button', { name: /^Ask Genie$/i }).first().click();
    await expect(page.getByText('Waiting for Genie response')).toBeVisible({
      timeout: 2_000,
    });
    await expect(page.locator('.surface', { hasText: /Source:/i }).first()).toBeVisible({ timeout: 45_000 });
  });

  test('ask-genie: dynamic chart, proof drawer, and governed action confirmation', async ({ page }) => {
    test.setTimeout(180_000);
    expect(
      ADMIN_BEARER,
      'live Genie action proof requires the distinct admin bearer for teardown',
    ).not.toBe('');
    expect(
      LIVE_CAMPAIGN_RUN_MARKER,
      'live Genie action proof requires a durable workflow run marker',
    ).toMatch(/^gha[a-j]+r[a-j]+$/);

    await gotoApp(page, '/ask-genie');

    await page
      .locator('textarea[aria-label="Ask Genie — question"]')
      .fill('Break down in-the-money borrowers by state and return the count as a table.');
    await page.getByRole('button', { name: /^Ask Genie$/i }).first().click();

    await expect(page.locator('.genie-chart').first()).toBeVisible({ timeout: 45_000 });
    const stateCells = await page
      .locator('.genie-answer__table tbody tr td:first-child')
      .evaluateAll((nodes) => nodes.map((node) => node.textContent?.trim() ?? ''));
    expect(stateCells.some((value) => /^[A-Z]{2}$/.test(value))).toBeTruthy();

    await page.getByRole('button', { name: /Show proof/i }).click();
    const proofDrawer = page.getByRole('dialog', { name: /Genie answer proof/i });
    await expect(proofDrawer).toBeVisible();
    const proof = proofDrawer.locator('.genie-proof').first();
    await expect(proof.getByText(/Generated SQL/i)).toBeVisible();
    await expect(proof.getByText(/gold\./i).first()).toBeVisible();
    await expect(proof.getByText(/Trusted SELECT on curated assets/i)).toBeVisible();
    const proofSourceChip = proof.locator('.evidence-chip').first();
    await expect(proofSourceChip).toBeVisible();
    await proofSourceChip.click();
    await expect(proofDrawer).toBeHidden({ timeout: 3_000 });
    const sourceDrawer = page.locator('.drawer.is-open').first();
    await expect(sourceDrawer).toBeVisible({ timeout: 3_000 });
    await expect(sourceDrawer.locator('.drawer__subtitle')).toHaveText(
      /Marketable population|Ranked lead population|Segment population|Lead score model|Borrower 360 feature set|Evidence stream|Lead-generation metric view|Segment performance metric view|Borrower opportunity metric view/,
    );
    await sourceDrawer.getByRole('button', { name: /Close drawer/i }).click();
    await expect(sourceDrawer).toBeHidden({ timeout: 3_000 });

    const draftAction = page.locator('.genie-action', { hasText: /Create draft campaign/i }).first();
    await expect(draftAction).toBeVisible();
    await draftAction.getByRole('button', { name: /Run/i }).click();
    const actionRequest = page.waitForRequest((request) =>
      /\/api\/(?:v1\/)?genie\/actions(?:\?|$)/.test(request.url()) &&
      request.method() === 'POST',
    );
    const actionResponse = page.waitForResponse((response) =>
      /\/api\/(?:v1\/)?genie\/actions(?:\?|$)/.test(response.url()) &&
      response.request().method() === 'POST',
    );
    await draftAction.getByRole('button', { name: /Confirm/i }).click();
    let submittedPayload: Record<string, unknown> | null = null;
    let campaignId = '';
    try {
      const submittedRequest = await actionRequest;
      submittedPayload = submittedRequest.postDataJSON() as Record<string, unknown>;
      const response = await actionResponse;
      expect(response.status(), 'Create draft campaign action should succeed').toBe(200);
      const actionPayload = await response.json();
      campaignId = typeof actionPayload.campaign_id === 'string'
        ? actionPayload.campaign_id.trim()
        : '';
      expect(actionPayload.action_type).toBe('create_draft_campaign');
      expect(actionPayload.audit_event_id).toBeTruthy();
      expect(campaignId).toBeTruthy();
      await expect(page).toHaveURL(/\/lead-queue\?/, { timeout: 20_000 });
    } finally {
      if (submittedPayload) {
        if (!campaignId) {
          campaignId = await reconcileGenieCampaignAction(page.request, {
            apiUrl: API_URL,
            authHeaders: AUTH_HEADERS,
            submittedPayload,
          });
        }
        await archiveLiveCampaign(page.request, {
          adminBearer: ADMIN_BEARER,
          apiUrl: API_URL,
          campaignId,
          expectedName: `Genie strategy draft ${LIVE_CAMPAIGN_RUN_MARKER}`,
        });
      }
    }
  });

  test('ask-genie: open cohort action carries ZIP answer filters into Lead Queue', async ({ page }) => {
    test.setTimeout(120_000);

    await gotoApp(page, '/ask-genie');

    await page
      .locator('textarea[aria-label="Ask Genie — question"]')
      .fill('Which ZIPs have the most in-the-money refinance candidates?');
    await page.getByRole('button', { name: /^Ask Genie$/i }).first().click();

    const zipCells = page.locator('.genie-answer__table').first().locator('tbody tr td:first-child');
    await expect(zipCells.first()).toBeVisible({ timeout: 60_000 });
    const answerZips = await zipCells.evaluateAll((nodes) =>
      nodes.map((node) => node.textContent?.trim() ?? '').filter(Boolean),
    );
    expect(answerZips.some((zip) => /^\d{5}$/.test(zip))).toBeTruthy();
    const cohortAction = page.locator('.genie-action', { hasText: /Open this cohort in Lead Queue/i }).first();
    await expect(cohortAction).toBeVisible();
    await cohortAction.getByRole('button', { name: /Run/i }).click();
    await cohortAction.getByRole('button', { name: /Confirm/i }).click();

    await expect(page).toHaveURL(/\/lead-queue\?.*zips=/, { timeout: 20_000 });
    await expect(page).toHaveURL(/segment=itm/, { timeout: 20_000 });
    await expect(page.getByText(/zips = \d+ selected/i)).toBeVisible({ timeout: 20_000 });
    await expect(page.getByText(/segment = Prime Refi Candidates/i)).toBeVisible();
    await expect(page.getByText(/Loading leads/i)).toBeHidden({ timeout: 45_000 });
    await expect(page.locator('.lead-table__zip').first()).toBeVisible({ timeout: 45_000 });

    const url = new URL(page.url());
    const allowedZips = new Set((url.searchParams.get('zips') ?? '').split(',').filter(Boolean));
    expect(allowedZips.size).toBeGreaterThan(0);

    const rowZips = await page.locator('.lead-table__zip').evaluateAll((nodes) =>
      nodes.slice(0, 25).map((node) => node.textContent?.trim() ?? ''),
    );
    expect(rowZips.length).toBeGreaterThan(0);
    expect(rowZips.every((zip) => allowedZips.has(zip))).toBeTruthy();
  });

  test('admin-config: presentation controls flip theme + density', async ({ page }) => {
    test.skip(!ADMIN_BEARER, 'Requires MIP_ADMIN_BEARER_TOKEN for Admin browser coverage.');
    await page.setExtraHTTPHeaders({ Authorization: `Bearer ${ADMIN_BEARER}` });
    await gotoApp(page, '/admin-config');

    // Unique-to-route: the source-readiness and per-user appearance surfaces.
    await expect(page.getByRole('heading', { name: 'Rules, data sources, and audit' })).toBeVisible({ timeout: 30_000 });
    await expect(page.getByText('Data source readiness', { exact: true })).toBeVisible({ timeout: 5_000 });
    const appearanceToggle = page.getByRole('button', { name: /Workspace appearance/i });
    await expect(appearanceToggle).toBeVisible({ timeout: 5_000 });
    await expect(page.getByText(/grant needed|read error/i)).toHaveCount(0);

    const auditExplorer = page.locator('#audit');
    await expect(auditExplorer.getByText('Audit explorer', { exact: true })).toBeVisible({
      timeout: 30_000,
    });
    const auditTable = auditExplorer.getByRole('table', { name: 'Audit events' });
    await expect(auditTable).toBeVisible({ timeout: 30_000 });
    const expandFirstAudit = auditTable.getByRole('button', { name: /Expand audit event/i }).first();
    await expect(expandFirstAudit).toBeVisible();
    await expandFirstAudit.click();
    await expect(auditExplorer.getByText('Event details', { exact: true })).toBeVisible();
    await expect(auditExplorer.getByRole('button', { name: /Copy Lakebase query for audit event/i }))
      .toBeVisible();
    await expect(auditExplorer.getByText('REVIEWED METADATA', { exact: true })).toBeVisible();

    const html = page.locator('html');

    await appearanceToggle.click();

    // Primary CTA per prototype: Theme toggle.
    const initialTheme = await html.getAttribute('data-theme');
    const expectedNext = initialTheme === 'dark' ? 'light' : 'dark';
    // Scope to the page body so we don't race with the Topbar icon button.
    await page.locator('main').getByRole('button', { name: new RegExp(`^${expectedNext}$`, 'i') }).click();
    await expect
      .poll(() => html.getAttribute('data-theme'), { timeout: 3_000 })
      .toBe(expectedNext);

    // Real data: admin route is a pure config surface — "real data" here
    // is the state roundtrip into the live AppContext + document attr,
    // which we just asserted. Also assert Density toggle mutates
    // data-density so we cover that axis.
    const initialDensity = await html.getAttribute('data-density');
    const nextDensity = initialDensity === 'compact' ? 'Comfortable' : 'Compact';
    await page.locator('main').getByRole('button', { name: new RegExp(`^${nextDensity}$`, 'i') }).click();
    await expect
      .poll(() => html.getAttribute('data-density'), { timeout: 3_000 })
      .toBe(nextDensity.toLowerCase());

    // Flip theme back so this test doesn't leave the session dirty.
    await page.locator('main').getByRole('button', { name: new RegExp(`^${initialTheme}$`, 'i') }).click();
    await expect
      .poll(() => html.getAttribute('data-theme'), { timeout: 3_000 })
      .toBe(initialTheme);
  });
});
