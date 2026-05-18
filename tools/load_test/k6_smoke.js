// k6 smoke load for Module 0 hot endpoints.
//
// Alternative to tools/load_test/locustfile.py for engineers who have
// k6 installed (`brew install k6`) but not Locust. Same hot endpoints,
// flatter structure, weighted via the loop shape rather than per-task
// weights. Write-path probes are opt-in via MIP_LOAD_TEST_WRITE=1.
//
// Run:
//     MIP_API_URL=http://localhost:8000 k6 run tools/load_test/k6_smoke.js
//
// Shape:
//     30s ramp 0 -> 20 VUs, 60s steady @ 20 VUs, 30s ramp-down.
//
// Thresholds reflect the same SLAs called out in README.md:
//     /api/health      p95 < 500ms
//     /api/segments    p95 < 1000ms
//     /api/portfolio   p95 < 1000ms
//     /api/leads       p95 < 1500ms
//     /api/borrowers   p95 < 2000ms (cold-cache-friendly)

import http from 'k6/http';
import { check, sleep } from 'k6';
import { Trend } from 'k6/metrics';
import { randomItem } from 'https://jslib.k6.io/k6-utils/1.4.0/index.js';

const BASE_URL = __ENV.MIP_API_URL || 'http://localhost:8000';
const API_PREFIX = `/${(__ENV.MIP_API_PREFIX || '/api/v1').replace(/^\/+|\/+$/g, '')}`;
const WRITE_ENABLED = ['1', 'true', 'yes', 'on'].includes(String(__ENV.MIP_LOAD_TEST_WRITE || '').toLowerCase());
const GENIE_QUESTION = __ENV.MIP_LOAD_TEST_GENIE_QUESTION || 'Break down the In-the-Money segment by state.';
const AUTH_HEADERS = __ENV.MIP_BEARER_TOKEN
  ? { Authorization: `Bearer ${__ENV.MIP_BEARER_TOKEN}` }
  : {};

function apiPath(path) {
  return `${BASE_URL}${API_PREFIX}/${path.replace(/^\/+/, '')}`;
}

function uuidv4() {
  return 'xxxxxxxx-xxxx-4xxx-yxxx-xxxxxxxxxxxx'.replace(/[xy]/g, (char) => {
    const value = Math.floor(Math.random() * 16);
    const nibble = char === 'x' ? value : ((value & 0x3) | 0x8);
    return nibble.toString(16);
  });
}

function jsonParams(tags = {}) {
  return {
    headers: { ...AUTH_HEADERS, 'Content-Type': 'application/json' },
    tags,
  };
}

// Per-endpoint Trend metrics so the summary table breaks the five
// endpoints out cleanly instead of lumping everything into
// http_req_duration.
const healthTrend = new Trend('health_ms', true);
const kpisTrend = new Trend('kpis_ms', true);
const leadsTrend = new Trend('leads_ms', true);
const borrowerTrend = new Trend('borrower_ms', true);
const segmentsTrend = new Trend('segments_ms', true);
const outreachDraftTrend = new Trend('outreach_draft_ms', true);
const outreachApproveTrend = new Trend('outreach_approve_ms', true);
const portfolioCreateTrend = new Trend('portfolio_create_ms', true);
const genieMessageTrend = new Trend('genie_message_ms', true);
const genieActionTrend = new Trend('genie_action_ms', true);

export const options = {
  scenarios: {
    mip_reads: {
      executor: 'ramping-vus',
      startVUs: 0,
      stages: [
        { duration: '30s', target: 20 },
        { duration: '60s', target: 20 },
        { duration: '30s', target: 0 },
      ],
      gracefulRampDown: '10s',
    },
  },
  thresholds: {
    health_ms: ['p(95)<500'],
    kpis_ms: ['p(95)<1000'],
    leads_ms: ['p(95)<1500'],
    borrower_ms: ['p(95)<2000'],
    segments_ms: ['p(95)<1000'],
    ...(WRITE_ENABLED ? {
      outreach_draft_ms: ['p(95)<2000'],
      outreach_approve_ms: ['p(95)<2000'],
      portfolio_create_ms: ['p(95)<5000'],
      genie_message_ms: ['p(95)<30000'],
      genie_action_ms: ['p(95)<5000'],
    } : {}),
    http_req_failed: ['rate<0.02'],
  },
};

const SEGMENTS = [
  'itm',
  'listed',
  'permit',
  'investor',
  'equity',
  'retention',
];

export default function () {
  // Health. Shallow, every iteration.
  let r = http.get(apiPath('health'), { headers: AUTH_HEADERS, tags: { endpoint: 'health' } });
  healthTrend.add(r.timings.duration);
  check(r, { 'health 200': (res) => res.status === 200 });

  // KPI strip (home page).
  r = http.post(apiPath('portfolio/preview'), JSON.stringify({}), jsonParams({ endpoint: 'kpis' }));
  kpisTrend.add(r.timings.duration);
  check(r, { 'kpis 200': (res) => res.status === 200 });

  // Leads queue. Rotate segment filter across the six codes.
  const seg = Math.random() < 0.33 ? randomItem(SEGMENTS) : null;
  const leadsUrl = seg
    ? `${apiPath('leads')}?segment=${seg}`
    : apiPath('leads');
  r = http.get(leadsUrl, { headers: AUTH_HEADERS, tags: { endpoint: 'leads' } });
  leadsTrend.add(r.timings.duration);
  const leadsOk = check(r, { 'leads 200': (res) => res.status === 200 });

  // Borrower drill-down -- chained off the leads response.
  if (leadsOk) {
    let rows = [];
    try {
      rows = r.json();
    } catch (_err) {
      rows = [];
    }
    if (Array.isArray(rows) && rows.length > 0) {
      const row = randomItem(rows);
      if (row && row.borrower_id) {
        const br = http.get(apiPath(`borrowers/${row.borrower_id}`), {
          headers: AUTH_HEADERS,
          tags: { endpoint: 'borrower', name: '/api/borrowers/{id}' },
        });
        borrowerTrend.add(br.timings.duration);
        check(br, { 'borrower 200 or 404': (res) => res.status === 200 || res.status === 404 });
        if (WRITE_ENABLED) {
          runWritePath(row.borrower_id);
        }
      }
    }
  }

  // Segments strip.
  r = http.get(apiPath('segments'), { headers: AUTH_HEADERS, tags: { endpoint: 'segments' } });
  segmentsTrend.add(r.timings.duration);
  check(r, { 'segments 200': (res) => res.status === 200 });

  sleep(Math.random() * 2 + 1); // 1-3s think time
}

function runWritePath(borrowerId) {
  const suffix = `${__VU}-${__ITER}-${Date.now()}`;
  let r = http.post(
    apiPath('outreach/draft'),
    JSON.stringify({
      borrower_id: borrowerId,
      channel: 'email',
      variant_name: 'load_test',
    }),
    jsonParams({ endpoint: 'outreach_draft' }),
  );
  outreachDraftTrend.add(r.timings.duration);
  const draftOk = check(r, { 'outreach draft 200': (res) => res.status === 200 });
  if (draftOk) {
    const draft = r.json();
    r = http.post(
      apiPath('outreach/approve'),
      JSON.stringify({
        borrower_id: borrowerId,
        offer_code: draft.offer_code,
        channel: draft.channel || 'email',
        variant_name: 'load_test',
        rationale: 'Concurrent load-test approval path.',
        draft_body: draft.body,
        request_id: uuidv4(),
      }),
      jsonParams({ endpoint: 'outreach_approve' }),
    );
    outreachApproveTrend.add(r.timings.duration);
    check(r, { 'outreach approve 200': (res) => res.status === 200 });
  }

  r = http.post(
    apiPath('portfolio/create'),
    JSON.stringify({
      name: `Load test portfolio ${suffix}`.slice(0, 80),
      criteria: { states: [randomItem(['CA', 'CO', 'FL', 'IL', 'TX'])], min_equity_pct: 25 },
      suppression_policy: { source: 'load_test', max_contacts: 50 },
      message_variants: [{
        variant_name: 'load_test_email',
        channel: 'email',
        subject: 'Review current mortgage options',
        body: 'Governed load-test campaign variant.',
        weight_pct: 100,
      }],
    }),
    jsonParams({ endpoint: 'portfolio_create' }),
  );
  portfolioCreateTrend.add(r.timings.duration);
  check(r, { 'portfolio create 200': (res) => res.status === 200 });

  r = http.post(
    apiPath('genie/message'),
    JSON.stringify({ question: GENIE_QUESTION }),
    jsonParams({ endpoint: 'genie_message' }),
  );
  genieMessageTrend.add(r.timings.duration);
  const messageOk = check(r, { 'genie message 200': (res) => res.status === 200 });
  if (!messageOk) return;
  const message = r.json();
  const actions = Array.isArray(message.actions)
    ? message.actions.filter((item) => item.confirmation_token && item.request_id && item.action_type)
    : [];
  const action = actions.find((item) => item.action_type === 'create_draft_campaign') || actions[0] || null;
  if (!action) return;
  r = http.post(
    apiPath('genie/actions'),
    JSON.stringify({
      action_type: action.action_type,
      conversation_id: message.conversation_id,
      message_id: message.message_id,
      question_hash: message.question_hash,
      borrower_ids: action.borrower_ids || [],
      criteria: action.criteria || {},
      route: action.route,
      request_id: action.request_id,
      confirmed: true,
      confirmation_token: action.confirmation_token,
    }),
    jsonParams({ endpoint: 'genie_action' }),
  );
  genieActionTrend.add(r.timings.duration);
  check(r, { 'genie action 200': (res) => res.status === 200 });
}
