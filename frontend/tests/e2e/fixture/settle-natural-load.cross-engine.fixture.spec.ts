/**
 * settle() ends a route's natural load only after the reads the route starts
 * at mount (main CI run 41881146873, fixture-webkit: css-layers' route walk
 * saw Offer Orchestrator's own GET /leads?limit=5, and on the detail route its
 * borrower, recommend and draft reads, land after markNaturalLoad). Those
 * routes paint their h1 with nothing aria-busy and start the reads in the
 * effects after that commit, and the engine hands a request to the mock's
 * route handler some time after the page called fetch(), so one poll could
 * see a painted, idle-looking route while the mock had been quiet for
 * seconds. The audited-read guard means "a read a state entered AFTER the
 * load opened": a read of the load itself must never count, and a read
 * started after it must still.
 *
 * The slow handover is reproduced here, in every engine, by holding the
 * route's requests before they reach the mock API (a route registered after
 * the mock's runs first; `fallback()` passes the request on to it) until the
 * mock has been quiet for longer than settle()'s quiet window plus two polls:
 * the moment the old settle() ended the natural load with the reads still
 * on their way.
 *
 * The other gap, between the commit that paints the h1 and the mount effects
 * that call fetch(), is reproduced by holding the route's reads before the
 * in-page probe sees them, behind an aria-busy marker, until the page has been
 * quiet; the marker then goes, and the reads start right after the first
 * settle() poll that sees the route ready (holdMountReadsPastAReadyPoll). A
 * settle() that trusts one ready poll ends the load at that poll.
 */
import type { Page } from '@playwright/test';
import { settleVerdict, type MockQuiet, type SettleState } from './app';
import { PRIMARY_BORROWER } from './data/borrowers';
import { formatViolations } from './hygiene';
import { PAGE_API_PROBE, type MockApi, type PageApiProbe } from './mockApi';
import { expect, test } from './test';
import { auditedReadsAfter, expectNoAuditedReadSince, markNaturalLoad } from './visual';

/** settle()'s quiet window (300 ms) plus two of its polls (100 ms each), with margin for a slow evaluate. */
const HOLD_QUIET_MS = 1_000;
/** Never hold longer than this; the hold normally ends within a second of the route painting. */
const HOLD_CAP_MS = 10_000;
/** A trapped mount read starts this long after the trap poll if no later poll comes; far longer than settle()'s return after a verdict. */
const LATE_START_MS = 2_000;

const OFFER_DETAIL = `/offer-orchestrator/${PRIMARY_BORROWER.borrower_id}`;
const MOUNT_READS = {
  // TopLeadsQuickPick ("Today's top leads") on the empty Offer Orchestrator.
  offerIndex: /\/api(?:\/v\d+)?\/leads(?:\?|$)/,
  // useOfferSnapshot (borrower + recommend) and useOfferDraft on an open offer.
  offerDetail: /\/api(?:\/v\d+)?\/(?:borrowers\/B-[0-9A-Z]{13}|offers\/recommend|outreach\/draft)(?:\?|$)/,
};

/** Pass the matching API requests on to the mock API only once it has been quiet for HOLD_QUIET_MS. */
async function slowHandover(page: Page, mockApi: MockApi, url: RegExp): Promise<void> {
  await page.route(url, async (route) => {
    const cap = Date.now() + HOLD_CAP_MS;
    while (!(mockApi.inflight === 0 && mockApi.idleMs >= HOLD_QUIET_MS) && Date.now() < cap) {
      await new Promise((resolve) => setTimeout(resolve, 50));
    }
    await route.fallback();
  });
}

/** Every audited read the mock API has answered, in order. */
function auditedReads(mockApi: MockApi): string[] {
  return auditedReadsAfter(mockApi.calls, 0);
}

/** `window[READY_POLL_HOLD]`: what holdMountReadsPastAReadyPoll saw (fixture-only). */
const READY_POLL_HOLD = '__mipFixtureReadyPollHold';

interface ReadyPollHold {
  /**
   * settle() polls that read the route ready (painted h1, no aria-busy in
   * <main>, no page API fetch in flight, the page quiet for quietMs) while
   * the mount reads were held unfetched: where a one-poll settle() ends the load.
   */
  trapPolls: number;
  /** Mount reads passed on to the probe's fetch, so to the mock API. */
  released: number;
  error: string | null;
}

/**
 * Init script: the route's mount reads (`source`) reach the in-page probe only
 * after a settle() poll has read the route ready with them unfetched. They
 * start when the NEXT poll reads the probe, just before it reads the counts:
 * the latest moment settle()'s second poll can still see a mount read start,
 * which is the premise of its two-poll rule. If no poll comes (a settle()
 * that trusted the one ready poll has already returned) they start after
 * `lateMs`, so they land after markNaturalLoad and the guard names them.
 *
 * Until the page has been quiet for `quietMs`, a hidden aria-busy marker in
 * <main> keeps every poll from reading the route ready, so the trap poll is
 * the first ready poll and comes with the mock API long quiet, as on CI when
 * the route painted seconds after the shell's own reads.
 *
 * Only settle()'s poll reads the probe's `doc`, and it reads `started` and
 * `settled` right after, in the same evaluate, so a getter on `doc` is the
 * poll's signal.
 */
function holdMountReadsPastAReadyPoll({ probeKey, holdKey, source, quietMs, lateMs }: {
  probeKey: string;
  holdKey: string;
  source: string;
  quietMs: number;
  lateMs: number;
}): void {
  const hold: ReadyPollHold = { trapPolls: 0, released: 0, error: null };
  Object.defineProperty(window, holdKey, { value: hold });
  const probe = (window as unknown as Record<string, PageApiProbe | undefined>)[probeKey];
  if (!probe) {
    hold.error = 'the in-page API probe was not installed before this hold';
    return;
  }
  const pattern = new RegExp(source);
  // The probe's own wrapper: a held read reaches it, so is counted, only once released.
  const probed = window.fetch;
  const held: Array<() => void> = [];
  const marker = document.createElement('span');
  marker.hidden = true;
  marker.setAttribute('aria-busy', 'true');
  marker.setAttribute('data-fixture-hold', '');
  let awaitingPoll = false;
  let seen = { started: probe.started, settled: probe.settled };
  let changedAt = performance.now();
  const pageQuiet = () => {
    if (probe.started !== seen.started || probe.settled !== seen.settled) {
      seen = { started: probe.started, settled: probe.settled };
      changedAt = performance.now();
    }
    return probe.started === probe.settled && performance.now() - changedAt >= quietMs;
  };
  const routeLooksReady = () => {
    const main = document.querySelector('#main-content');
    return Boolean(main?.querySelector('h1')) && main?.querySelectorAll('[aria-busy="true"]').length === 0;
  };
  const markBusy = () => {
    awaitingPoll = false;
    if (!marker.isConnected) document.querySelector('#main-content')?.append(marker);
  };
  window.setInterval(() => {
    if (pageQuiet() && held.length > 0 && !awaitingPoll) {
      marker.remove();
      awaitingPoll = true;
    }
  }, 20);
  // Reads a trap poll saw unfetched: they start at the next poll, or after lateMs.
  const trapped: Array<() => void> = [];
  const startTrapped = () => trapped.splice(0).forEach((start) => start());
  const doc = probe.doc;
  Object.defineProperty(probe, 'doc', {
    enumerable: true,
    configurable: true,
    get() {
      startTrapped();
      if (awaitingPoll) {
        if (!pageQuiet()) {
          markBusy();
        } else if (routeLooksReady()) {
          awaitingPoll = false;
          hold.trapPolls += 1;
          trapped.push(...held.splice(0));
          window.setTimeout(startTrapped, lateMs);
        }
      }
      return doc;
    },
  });
  window.fetch = function fetchHeldPastAReadyPoll(input: RequestInfo | URL, init?: RequestInit): Promise<Response> {
    const url = typeof input === 'string' ? input : input instanceof URL ? input.href : input.url;
    if (!pattern.test(url)) return probed.call(window, input, init);
    markBusy();
    return new Promise<Response>((resolve, reject) => {
      held.push(() => {
        hold.released += 1;
        probed.call(window, input, init).then(resolve, reject);
      });
    });
  };
}

test.describe('a mount-time read the engine hands over late is part of the natural load', () => {
  test("Offer Orchestrator, no borrower: Today's top leads (VIEW_LEADS)", async ({ app, mockApi, page }) => {
    await slowHandover(page, mockApi, MOUNT_READS.offerIndex);
    await app.gotoRoute('/offer-orchestrator');
    const natural = markNaturalLoad(mockApi);
    // Nothing the load started may still be on its way when the guard reads the log.
    await expect.poll(() => auditedReads(mockApi), { timeout: HOLD_CAP_MS * 2 }).toEqual(['GET /api/leads?limit=5 (VIEW_LEADS)']);
    expectNoAuditedReadSince(mockApi, natural, 'offer-orchestrator-index');
    await expect(page.locator('#main-content').getByText("Today's top leads")).toBeVisible();
  });

  test('Offer Orchestrator, one borrower: borrower, recommend and draft', async ({ app, mockApi, page }) => {
    await slowHandover(page, mockApi, MOUNT_READS.offerDetail);
    await app.gotoRoute(OFFER_DETAIL);
    const natural = markNaturalLoad(mockApi);
    await expect
      .poll(() => [...auditedReads(mockApi)].sort(), { timeout: HOLD_CAP_MS * 2 })
      .toEqual([
        `GET /api/borrowers/${PRIMARY_BORROWER.borrower_id} (VIEW_BORROWER)`,
        'POST /api/offers/recommend (RECOMMEND_OFFER)',
        'POST /api/outreach/draft (DRAFT_OUTREACH)',
      ]);
    expectNoAuditedReadSince(mockApi, natural, 'offer-orchestrator-detail');
    await expect(page.locator('#main-content').getByText('NMLS #000000').first()).toBeVisible();
  });

  test('a read opened after the natural load is still reported', async ({ app, mockApi, page }) => {
    await slowHandover(page, mockApi, MOUNT_READS.offerIndex);
    await app.gotoRoute('/offer-orchestrator');
    const natural = markNaturalLoad(mockApi);
    // Opening a quick-pick offer is a deliberate open: its reads follow the mark.
    await page.locator('#main-content').getByRole('link', { name: /· \d+$/ }).first().click();
    await app.settle();
    expect(auditedReadsAfter(mockApi.calls, natural).sort()).toEqual([
      expect.stringMatching(/^GET \/api\/borrowers\/B-[0-9A-Z]{13} \(VIEW_BORROWER\)$/),
      'POST /api/offers/recommend (RECOMMEND_OFFER)',
      'POST /api/outreach/draft (DRAFT_OUTREACH)',
    ]);
  });
});

test.describe('a mount-time read the page has not fetched when the route first reads ready is part of the natural load', () => {
  const VISITS = [
    {
      label: 'offer-orchestrator-index',
      path: '/offer-orchestrator',
      reads: MOUNT_READS.offerIndex,
      expected: ['GET /api/leads?limit=5 (VIEW_LEADS)'],
    },
    {
      label: 'offer-orchestrator-detail',
      path: OFFER_DETAIL,
      reads: MOUNT_READS.offerDetail,
      expected: [
        `GET /api/borrowers/${PRIMARY_BORROWER.borrower_id} (VIEW_BORROWER)`,
        'POST /api/offers/recommend (RECOMMEND_OFFER)',
        'POST /api/outreach/draft (DRAFT_OUTREACH)',
      ],
    },
  ];

  for (const visit of VISITS) {
    test(`${visit.label}: settle() waits a poll past the first ready one`, async ({ app, mockApi, page }) => {
      await page.addInitScript(holdMountReadsPastAReadyPoll, {
        probeKey: PAGE_API_PROBE,
        holdKey: READY_POLL_HOLD,
        source: visit.reads.source,
        quietMs: HOLD_QUIET_MS,
        lateMs: LATE_START_MS,
      });
      await app.gotoRoute(visit.path);
      const natural = markNaturalLoad(mockApi);
      const hold = await page.evaluate((key) => (window as unknown as Record<string, ReadyPollHold>)[key], READY_POLL_HOLD);
      expect(hold.error).toBeNull();
      // The gap was there: a poll read the route ready with the reads still unfetched.
      expect(hold.trapPolls).toBeGreaterThanOrEqual(1);
      await expect
        .poll(() => [...auditedReads(mockApi)].sort(), { timeout: HOLD_CAP_MS * 2 })
        .toEqual([...visit.expected].sort());
      expectNoAuditedReadSince(mockApi, natural, visit.label);
      // Every one of them went through the hold.
      expect(await page.evaluate((key) => (window as unknown as Record<string, ReadyPollHold>)[key].released, READY_POLL_HOLD))
        .toBe(visit.expected.length);
    });
  }
});

test.describe('settleVerdict', () => {
  const READY: SettleState = {
    heading: true,
    busy: 0,
    paintedPath: '/offer-orchestrator',
    urlPath: '/offer-orchestrator',
    painted: true,
    fetches: { doc: 'doc-1', started: 4, settled: 4 },
  };
  const QUIET: MockQuiet = { inflight: 0, idleMs: 5_000 };
  const ready = (overrides: Partial<SettleState> = {}): SettleState => ({ ...READY, ...overrides });

  test('one ready poll is never settled, however long the mock has been quiet', () => {
    expect(settleVerdict(null, READY, QUIET)).toBe(false);
    expect(settleVerdict(ready({ heading: false }), READY, QUIET)).toBe(false);
    expect(settleVerdict(ready({ busy: 1 }), READY, QUIET)).toBe(false);
  });

  test('two ready polls of one route, no fetch started between them, the mock quiet: settled', () => {
    expect(settleVerdict(READY, ready(), QUIET)).toBe(true);
  });

  test('a fetch the page started between the polls, or still has in flight, is not settled', () => {
    expect(settleVerdict(READY, ready({ fetches: { doc: 'doc-1', started: 5, settled: 5 } }), QUIET)).toBe(false);
    expect(settleVerdict(READY, ready({ fetches: { doc: 'doc-1', started: 5, settled: 4 } }), QUIET)).toBe(false);
    const inFlight = ready({ fetches: { doc: 'doc-1', started: 5, settled: 4 } });
    expect(settleVerdict(inFlight, inFlight, QUIET)).toBe(false);
  });

  test('the mock API busy, or quiet for less than the window, is not settled', () => {
    expect(settleVerdict(READY, READY, { inflight: 1, idleMs: 5_000 })).toBe(false);
    expect(settleVerdict(READY, READY, { inflight: 0, idleMs: 299 })).toBe(false);
    expect(settleVerdict(READY, READY, { inflight: 0, idleMs: 300 })).toBe(true);
  });

  test('another document or another route between the polls is not settled', () => {
    expect(settleVerdict(READY, ready({ fetches: { doc: 'doc-2', started: 4, settled: 4 } }), QUIET)).toBe(false);
    expect(settleVerdict(READY, ready({ paintedPath: '/lead-queue', urlPath: '/lead-queue' }), QUIET)).toBe(false);
  });

  test('a page without the in-page API probe never settles', () => {
    const blind = ready({ fetches: null });
    expect(settleVerdict(blind, blind, QUIET)).toBe(false);
  });
});

test.describe('the in-page API probe', () => {
  // The aborted fetch below fails its request and rejects unhandled on purpose.
  test.use({ hygieneOptOut: ['pageerror', 'request-failed'] });

  test('counts an API fetch until it settles, and leaves an unhandled rejection unhandled', async ({ app, hygiene, page }) => {
    await app.gotoRoute('/glossary');
    const counts = await page.evaluate(async (probeKey) => {
      const probe = (window as unknown as Record<string, PageApiProbe>)[probeKey];
      const before = { started: probe.started, settled: probe.settled };
      const controller = new AbortController();
      // Discarded on purpose: nothing handles this fetch's rejection.
      void fetch('/api/v1/health?probe=unhandled', { signal: controller.signal });
      const inFlight = probe.started - probe.settled;
      controller.abort();
      await new Promise((resolve) => setTimeout(resolve, 50));
      return { started: probe.started - before.started, inFlight, settled: probe.settled - before.settled };
    }, PAGE_API_PROBE);
    expect(counts).toEqual({ started: 1, inFlight: 1, settled: 1 });
    await expect.poll(() => hygiene.collected().filter((violation) => violation.check === 'pageerror').length).toBe(1);
    expect(formatViolations('probe', hygiene.collected())).toMatch(/\[pageerror\] .*abort/i);
  });
});
