/**
 * The Lead Queue's tooltip descriptions: every row trigger keeps its
 * accessible description, at a cost of one unstyled node per distinct text
 * (post-W5 tooltip-render-cost; ui/tooltipDescriptions.ts).
 *
 * W5e's title= -> Tooltip migration (37802f06, 675e503c) gave each queue row
 * seven triggers (property ref, city, segment chip, offer, score, signal
 * strength, Reject), and each Tooltip portalled its own hidden description
 * `<span>` into <body>: 111 row descriptions (123 on the page) at rest on the
 * 160-row virtual queue, 35-42 mounted and as many unmounted by every
 * virtual-window shift, each a styled element (~950 element styles over
 * focus-clearance-cost's scripted scroll). Now a plain-text description is
 * ONE ref-counted node per distinct text, inside ONE hidden host whose
 * subtree is never styled.
 *
 *  (a) Every row trigger's accessible description is the text it had: the
 *      cell's own text for the property ref, city and offer, the chip label
 *      for the segment, the fixed notes for score, signal strength, Reject
 *      and (an approved row) Revoke. Read twice, by Playwright's accname
 *      (toHaveAccessibleDescription) and by Chromium's own accessibility
 *      tree over CDP (what a screen reader gets), at rest and on the rows a
 *      scroll mounts. It holds at 550dcf97 too: the descriptions did not
 *      change, only what they cost.
 *  (b) The element-count pin: at rest and after every step of the same
 *      scripted scroll focus-clearance-cost measures, the rows' triggers name
 *      exactly as many description nodes as they carry distinct texts, all in
 *      the hidden host; a step adds at most one node per distinct text its
 *      new rows brought, and a trigger that stayed keeps its node. At
 *      550dcf97: 111 nodes for 38 texts at rest, +35..42 per shift, none in a
 *      host. Now: 38 at rest, +5..6 per shift (each new row's property ref).
 *  (c) The style cost: over that scroll, Chromium's style-invalidation
 *      records (the devtools.timeline invalidationTracking trace) name no
 *      description node; at 550dcf97 about 700 did (698 and 728 in two runs,
 *      "Node was inserted into tree"). Its non-vacuity twin, in the same
 *      page: description-shaped spans inserted straight into <body> are
 *      named and re-styled, the same spans inserted into the host are not.
 */
import type { Browser, CDPSession, Locator, Page } from '@playwright/test';
import { registerVirtualQueue } from './data/queueKeyboard';
import { expect, test } from './test';

const ROW_TRIGGERS = 'table.tbl tbody tr[data-borrower-row] [data-tooltip]';
const HOST = '[data-tooltip-descriptions]';

/** The same scroll focus-clearance-cost.fixture.spec.ts scripts. */
const SCROLL_STEPS = 25;
const SCROLL_STEP_PX = 240;

const SCORE_NOTE = 'Opportunity score ranks lead strength on a 0-100 scale. Scores of 75+ are the strongest review'
  + ' candidates and include economics, intent, fit, relationship, and evidence.';
const SIGNAL_STRENGTH_NOTE = 'Signal strength is a deterministic average of the five scoring sub-scores, not a'
  + ' statistical confidence interval.';

/**
 * Each row trigger kind, by the class its trigger carries, and the
 * description it must have: the trigger's own visible text, its chip label,
 * or a fixed note. A trigger matching no kind fails (a)'s census.
 */
const KINDS = [
  { kind: 'property ref', match: 'lead-table__clip', expected: 'own text' },
  { kind: 'city', match: 'lead-table__city', expected: 'own text' },
  { kind: 'segment chip', match: 'chip--segment', expected: 'chip label' },
  { kind: 'offer', match: 'lead-table__offer', expected: 'own text' },
  { kind: 'score', match: 'score', expected: SCORE_NOTE },
  { kind: 'signal strength', match: 'conf', expected: SIGNAL_STRENGTH_NOTE },
  { kind: 'Reject', match: 'lead-table__reject', expected: 'Reject' },
  // Only on an approved row (one in the fixture's first rows).
  { kind: 'Revoke', match: 'lead-table__revoke', expected: 'Revoke approval', optional: true },
] as const;

interface RowTrigger {
  row: string;
  kind: string;
  expected: string;
}

/** Every rendered row trigger, tagged `data-desc-probe=<index>`, with its expected description. */
async function rowTriggers(page: Page): Promise<RowTrigger[]> {
  return page.locator(ROW_TRIGGERS).evaluateAll((nodes, kinds) => {
    document.querySelectorAll('[data-desc-probe]').forEach((stale) => stale.removeAttribute('data-desc-probe'));
    return nodes.map((node, index) => {
      node.setAttribute('data-desc-probe', String(index));
      const row = node.closest('tr')?.getAttribute('data-borrower-row') ?? '?';
      const found = kinds.find((entry) => node.classList.contains(entry.match));
      if (!found) return { row, kind: `unknown trigger ${node.tagName}.${node.className}`, expected: '' };
      const text = (element: Element | null) => (element?.textContent ?? '').trim();
      const expected = found.expected === 'own text'
        ? text(node)
        : found.expected === 'chip label' ? text(node.querySelector('.chip__label')) : found.expected;
      return { row, kind: found.kind, expected };
    });
  }, KINDS);
}

/**
 * Chromium's computed description of each `[data-desc-probe]` trigger, in
 * document order (the probe indices' order: rowTriggers numbers them so).
 */
async function chromiumDescriptions(cdp: CDPSession): Promise<Array<string | null>> {
  const { root } = await cdp.send('DOM.getDocument', { depth: 0 });
  const { nodeIds } = await cdp.send('DOM.querySelectorAll', { nodeId: root.nodeId, selector: '[data-desc-probe]' });
  const out: Array<string | null> = [];
  for (const nodeId of nodeIds) {
    const { nodes } = await cdp.send('Accessibility.getPartialAXTree', { nodeId, fetchRelatives: false });
    const value = nodes[0]?.description?.value;
    out.push(typeof value === 'string' ? value : null);
  }
  return out;
}

async function expectDescriptions(page: Page, cdp: CDPSession, label: string): Promise<number> {
  const triggers = await rowTriggers(page);
  const unknown = triggers.filter((trigger) => trigger.kind.startsWith('unknown'));
  expect(unknown, `${label}: every row trigger is a known kind`).toEqual([]);
  for (const entry of KINDS) {
    if ('optional' in entry) continue;
    expect(triggers.filter((trigger) => trigger.kind === entry.kind).length, `${label}: rows carry ${entry.kind} triggers`)
      .toBeGreaterThan(0);
  }
  for (const [index, trigger] of triggers.entries()) {
    expect(trigger.expected, `${label}: ${trigger.row} ${trigger.kind} has text to describe`).not.toBe('');
    await expect(page.locator(`[data-desc-probe="${index}"]`), `${label}: ${trigger.row} ${trigger.kind} (accname)`)
      .toHaveAccessibleDescription(trigger.expected);
  }
  const chromium = await chromiumDescriptions(cdp);
  expect(chromium, `${label}: Chromium's accessibility tree`).toEqual(triggers.map((trigger) => trigger.expected));
  return triggers.length;
}

function scrollRegion(page: Page): Locator {
  return page.getByRole('region', { name: 'Ranked borrowers table scroll region' });
}

async function openQueue(page: Page, gotoRoute: (path: string) => Promise<unknown>): Promise<void> {
  await gotoRoute('/lead-queue');
  await expect(page.locator('table.tbl tbody tr[data-borrower-row]').first()).toBeVisible();
  // The descriptions are committed with their triggers; one settled frame.
  await scrollRegion(page).evaluate(() => new Promise<void>((resolve) => requestAnimationFrame(() => requestAnimationFrame(() => resolve()))));
}

test('(a) every Lead Queue row trigger keeps its accessible description, at rest and on the rows a scroll mounts', async ({ app, mockApi, page }) => {
  // ~270 accname reads and as many CDP round trips (13 s unloaded).
  test.slow();
  registerVirtualQueue(mockApi);
  await openQueue(page, (path) => app.gotoRoute(path));
  const cdp = await page.context().newCDPSession(page);
  try {
    const atRest = await expectDescriptions(page, cdp, 'at rest');
    expect(atRest, 'precondition: the virtual window rendered rows of triggers').toBeGreaterThan(7 * 10);

    await scrollRegion(page).evaluate(async (element, distance) => {
      element.scrollTop += distance;
      await new Promise<void>((resolve) => requestAnimationFrame(() => requestAnimationFrame(() => resolve())));
    }, SCROLL_STEP_PX * 12);
    await expectDescriptions(page, cdp, 'after a scroll');
  } finally {
    await cdp.detach();
  }
});

interface Census {
  /** Row triggers rendered. */
  triggers: number;
  /** Distinct description nodes the row triggers name. */
  nodes: number;
  /** Distinct description texts the row triggers carry. */
  texts: number;
  /** Named nodes outside the hidden host (or missing). */
  outsideHost: string[];
}

interface Step extends Census {
  /** Description nodes inserted into / removed from the document by this step. */
  added: number;
  removed: number;
  /** Row triggers this step mounted, and the distinct texts they carry. */
  newTriggers: number;
  newTexts: number;
  /** Triggers that stayed through the step but now name a different node object. */
  swappedNodes: number;
}

test('(b) one unstyled description node per distinct text: at rest and after every virtual-window shift', async ({ app, mockApi, page }) => {
  registerVirtualQueue(mockApi);
  await openQueue(page, (path) => app.gotoRoute(path));

  const steps = await scrollRegion(page).evaluate(async (element, { steps: count, step: distance, hostSelector, triggerSelector }) => {
    const frame = () => new Promise<void>((resolve) => requestAnimationFrame(() => resolve()));
    const isDescription = (node: Node): number => {
      if (!(node instanceof Element)) return 0;
      return (node.id.startsWith('tooltip-desc') ? 1 : 0) + node.querySelectorAll('[id^="tooltip-desc"]').length;
    };
    let added = 0;
    let removed = 0;
    const observer = new MutationObserver((records) => {
      for (const record of records) {
        record.addedNodes.forEach((node) => { added += isDescription(node); });
        record.removedNodes.forEach((node) => { removed += isDescription(node); });
      }
    });
    observer.observe(document.body, { childList: true, subtree: true });
    const describedBy = (trigger: Element) => document.getElementById(trigger.getAttribute('data-tooltip') ?? '');
    const census = () => {
      const triggers = [...document.querySelectorAll(triggerSelector)];
      const nodes = new Set<Element>();
      const texts = new Set<string>();
      const outsideHost: string[] = [];
      for (const trigger of triggers) {
        const node = describedBy(trigger);
        if (!node) {
          outsideHost.push(`missing ${trigger.getAttribute('data-tooltip')}`);
          continue;
        }
        nodes.add(node);
        texts.add(node.textContent ?? '');
        const host = node.closest(hostSelector);
        if (!host || !(host as HTMLElement).hidden || node.parentElement !== host) outsideHost.push(node.id);
      }
      return { triggers, nodes: nodes.size, texts: texts.size, outsideHost: [...new Set(outsideHost)] };
    };
    const results = [];
    let before = census();
    results.push({ ...before, triggers: before.triggers.length, added: 0, removed: 0, newTriggers: 0, newTexts: 0, swappedNodes: 0 });
    for (let index = 0; index < count; index += 1) {
      const seen = new Map(before.triggers.map((trigger) => [trigger, describedBy(trigger)]));
      added = 0;
      removed = 0;
      element.scrollTop += distance;
      await frame();
      await frame();
      observer.takeRecords().forEach((record) => {
        record.addedNodes.forEach((node) => { added += isDescription(node); });
        record.removedNodes.forEach((node) => { removed += isDescription(node); });
      });
      const after = census();
      const fresh = after.triggers.filter((trigger) => !seen.has(trigger));
      const swappedNodes = after.triggers.filter((trigger) => seen.has(trigger) && seen.get(trigger) !== describedBy(trigger)).length;
      results.push({
        ...after,
        triggers: after.triggers.length,
        added,
        removed,
        newTriggers: fresh.length,
        newTexts: new Set(fresh.map((trigger) => describedBy(trigger)?.textContent ?? '')).size,
        swappedNodes,
      });
      before = after;
    }
    observer.disconnect();
    return results;
  }, { steps: SCROLL_STEPS, step: SCROLL_STEP_PX, hostSelector: HOST, triggerSelector: ROW_TRIGGERS }) as Step[];

  const [rest, ...shifts] = steps;
  const table = steps.map((step, index) => `${index === 0 ? 'rest' : `step ${index}`}: ${step.triggers} triggers -> `
    + `${step.nodes} nodes / ${step.texts} texts; +${step.added} -${step.removed} (new: ${step.newTriggers} triggers, ${step.newTexts} texts)`).join('\n');
  console.log(`[tooltip-descriptions] row description census on the virtual queue\n${table}`);

  expect(rest.triggers, 'precondition: the window rendered rows of triggers').toBeGreaterThan(7 * 10);
  expect(rest.nodes, `at rest: one node per distinct text, not one per trigger (${rest.triggers} triggers)`).toBe(rest.texts);
  expect(rest.outsideHost, 'at rest: every description sits in the hidden host').toEqual([]);
  const moved = shifts.filter((step) => step.newTriggers > 0);
  expect(moved.length, 'non-vacuity: the scroll shifted the virtual window, step after step').toBeGreaterThan(SCROLL_STEPS / 2);
  expect(shifts.reduce((sum, step) => sum + step.added, 0), 'non-vacuity: shifts brought new texts (each new row\'s property ref)')
    .toBeGreaterThan(0);
  for (const [index, step] of shifts.entries()) {
    const label = `step ${index + 1} (${step.newTriggers} new triggers)`;
    expect(step.nodes, `${label}: one node per distinct text`).toBe(step.texts);
    expect(step.added, `${label}: at most one node added per distinct text the new rows brought`).toBeLessThanOrEqual(step.newTexts);
    expect(step.outsideHost, `${label}: every description sits in the hidden host`).toEqual([]);
    expect(step.swappedNodes, `${label}: a trigger that stayed keeps its node`).toBe(0);
  }
});

interface TraceEvent {
  name?: string;
  args?: { elementCount?: unknown; data?: { nodeName?: unknown; reason?: unknown } };
}

interface StyleTrace {
  /** Elements re-styled (UpdateLayoutTree elementCount). */
  restyled: number;
  /** Style-invalidation records, and those that name a node whose id starts `marker`. */
  invalidations: number;
  named: number;
}

/** Run `script` under a timeline + invalidation-tracking trace. */
async function styleTrace(browser: Browser, page: Page, marker: string, script: () => Promise<unknown>): Promise<StyleTrace> {
  await browser.startTracing(page, {
    categories: ['devtools.timeline', 'disabled-by-default-devtools.timeline.invalidationTracking'],
  });
  await script();
  const trace = JSON.parse((await browser.stopTracing()).toString('utf8')) as { traceEvents?: TraceEvent[] };
  const events = trace.traceEvents ?? [];
  const invalidations = events.filter((event) => /InvalidationTracking$/.test(event.name ?? '') && event.name?.startsWith('Style'));
  return {
    restyled: events
      .filter((event) => event.name === 'UpdateLayoutTree')
      .reduce((sum, event) => sum + (typeof event.args?.elementCount === 'number' ? event.args.elementCount : 0), 0),
    invalidations: invalidations.length,
    named: invalidations.filter((event) => String(event.args?.data?.nodeName ?? '').includes(`id='${marker}`)).length,
  };
}

const CONTROL_SPANS = 20;

test('(c) mounting and unmounting descriptions costs no style work over the scroll', async ({ app, browser, mockApi, page }) => {
  // Three traces with invalidation tracking, which records a stack per invalidation (30 s unloaded).
  test.slow();
  registerVirtualQueue(mockApi);
  await openQueue(page, (path) => app.gotoRoute(path));
  const wrap = scrollRegion(page);
  await wrap.evaluate(() => {
    const counter = { added: 0 };
    (window as unknown as { __descAdded: typeof counter }).__descAdded = counter;
    new MutationObserver((records) => {
      for (const record of records) {
        record.addedNodes.forEach((node) => {
          if (node instanceof Element && node.id.startsWith('tooltip-desc')) counter.added += 1;
        });
      }
    }).observe(document.body, { childList: true, subtree: true });
  });

  const scroll = await styleTrace(browser, page, 'tooltip-desc', () => wrap.evaluate(async (element, { steps, step }) => {
    const frame = () => new Promise<void>((resolve) => requestAnimationFrame(() => resolve()));
    for (let index = 0; index < steps; index += 1) {
      element.scrollTop += step;
      await frame();
      await frame();
    }
  }, { steps: SCROLL_STEPS, step: SCROLL_STEP_PX }));
  const added = await page.evaluate(() => (window as unknown as { __descAdded: { added: number } }).__descAdded.added);
  const report = (trace: StyleTrace) => `${trace.restyled} re-styled, ${trace.invalidations} invalidations, ${trace.named} naming a description`;
  console.log(`[tooltip-descriptions] scroll (${added} descriptions added): ${report(scroll)}`);
  expect(added, 'non-vacuity: the scroll mounted descriptions').toBeGreaterThan(0);
  expect(scroll.invalidations, 'non-vacuity: the trace recorded the scroll\'s style invalidations').toBeGreaterThan(0);
  expect(scroll.named, `no style invalidation names a description over the scroll (${report(scroll)})`).toBe(0);

  // The twin, which a passing scroll leg needs: the same spans, outside the
  // host and inside it.
  const insert = (into: 'body' | 'host') => styleTrace(browser, page, 'tooltip-desc-control', () => page.evaluate(async ({ target, count, hostSelector }) => {
    const parent = target === 'body' ? document.body : document.querySelector(hostSelector);
    if (!parent) throw new Error('no description host');
    const spans = Array.from({ length: count }, (_, index) => {
      const span = document.createElement('span');
      span.hidden = true;
      span.id = `tooltip-desc-control-${target}-${index}`;
      span.textContent = 'control';
      return span;
    });
    parent.append(...spans);
    await new Promise<void>((resolve) => requestAnimationFrame(() => requestAnimationFrame(() => resolve())));
    spans.forEach((span) => span.remove());
  }, { target: into, count: CONTROL_SPANS, hostSelector: HOST }));
  const intoBody = await insert('body');
  const intoHost = await insert('host');

  console.log(`[tooltip-descriptions] ${CONTROL_SPANS} spans into <body>: ${report(intoBody)}; into the host: ${report(intoHost)}`);
  expect(intoBody.named, 'non-vacuity: a description-shaped span inserted into <body> is named').toBeGreaterThanOrEqual(CONTROL_SPANS);
  expect(intoBody.restyled, 'non-vacuity: and re-styled').toBeGreaterThanOrEqual(CONTROL_SPANS);
  expect(intoHost.named, 'the host\'s subtree is never invalidated').toBe(0);
  expect(intoHost.restyled, 'nor re-styled').toBeLessThan(CONTROL_SPANS);
});
