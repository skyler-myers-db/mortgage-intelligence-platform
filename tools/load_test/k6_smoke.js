// k6 smoke load for Module 0 hot endpoints.
//
// Alternative to tools/load_test/locustfile.py for engineers who have
// k6 installed (`brew install k6`) but not Locust. Same five endpoints,
// flatter structure, weighted via scenario executor rather than per-
// task weights.
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

// Per-endpoint Trend metrics so the summary table breaks the five
// endpoints out cleanly instead of lumping everything into
// http_req_duration.
const healthTrend = new Trend('health_ms', true);
const kpisTrend = new Trend('kpis_ms', true);
const leadsTrend = new Trend('leads_ms', true);
const borrowerTrend = new Trend('borrower_ms', true);
const segmentsTrend = new Trend('segments_ms', true);

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
    http_req_failed: ['rate<0.02'],
  },
};

const SEGMENTS = [
  'in_the_money',
  'heloc_candidate',
  'cash_out',
  'listed_for_sale',
  'investor',
  'retention',
];

export default function () {
  // Health. Shallow, every iteration.
  let r = http.get(`${BASE_URL}/api/health`, { tags: { endpoint: 'health' } });
  healthTrend.add(r.timings.duration);
  check(r, { 'health 200': (res) => res.status === 200 });

  // KPI strip (home page).
  r = http.post(`${BASE_URL}/api/portfolio/preview`, JSON.stringify({}), {
    headers: { 'Content-Type': 'application/json' },
    tags: { endpoint: 'kpis' },
  });
  kpisTrend.add(r.timings.duration);
  check(r, { 'kpis 200': (res) => res.status === 200 });

  // Leads queue. Rotate segment filter across the six codes.
  const seg = Math.random() < 0.33 ? randomItem(SEGMENTS) : null;
  const leadsUrl = seg
    ? `${BASE_URL}/api/leads?segment=${seg}`
    : `${BASE_URL}/api/leads`;
  r = http.get(leadsUrl, { tags: { endpoint: 'leads' } });
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
        const br = http.get(`${BASE_URL}/api/borrowers/${row.borrower_id}`, {
          tags: { endpoint: 'borrower', name: '/api/borrowers/{id}' },
        });
        borrowerTrend.add(br.timings.duration);
        check(br, { 'borrower 200 or 404': (res) => res.status === 200 || res.status === 404 });
      }
    }
  }

  // Segments strip.
  r = http.get(`${BASE_URL}/api/segments`, { tags: { endpoint: 'segments' } });
  segmentsTrend.add(r.timings.duration);
  check(r, { 'segments 200': (res) => res.status === 200 });

  sleep(Math.random() * 2 + 1); // 1-3s think time
}
