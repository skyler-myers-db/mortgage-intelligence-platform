/**
 * The wire bases (data/wireBases.ts) as fixture-contract samples (W5e
 * w5-wire-types B3): every base the backend serves directly is validated
 * against its route's response model and checked for omitted keys, so a base
 * the server would refuse or complete differently fails the contract.
 * Separate from wireBases.ts so the src/test factories that import the bases
 * never pull contractSamples.ts and the harness into the app's tsc program.
 */
import type { ContractSample } from '../contractSamples';
import {
  APPROVE_RESULT_BASE,
  AUDIT_ROLLUP_BASE,
  BORROWER_360_BASE,
  GROWTH_AGENT_RUN_BASE,
  LEAD_SUMMARY_BASE,
  REJECT_RESULT_BASE,
  SESSION_BASE,
} from './wireBases';

function sample(source: string, method: 'GET' | 'POST', pattern: string, body: unknown, path = pattern): ContractSample {
  return { source, method, pattern, path, query: '', status: 200, body };
}

/** Every base the backend serves directly, validated by the fixture contract. */
export function contractSamples(): ContractSample[] {
  return [
    sample('LEAD_SUMMARY_BASE', 'GET', '/api/leads', [LEAD_SUMMARY_BASE]),
    sample('BORROWER_360_BASE', 'GET', '/api/borrowers/:id', BORROWER_360_BASE, `/api/borrowers/${BORROWER_360_BASE.borrower_id}`),
    sample('SESSION_BASE', 'GET', '/api/session', SESSION_BASE),
    sample('APPROVE_RESULT_BASE', 'POST', '/api/outreach/approve', APPROVE_RESULT_BASE),
    sample('REJECT_RESULT_BASE', 'POST', '/api/outreach/reject', REJECT_RESULT_BASE),
    sample('GROWTH_AGENT_RUN_BASE', 'POST', '/api/growth-agent/agent/run', GROWTH_AGENT_RUN_BASE),
    sample('AUDIT_ROLLUP_BASE', 'GET', '/api/audit/rollups', [AUDIT_ROLLUP_BASE]),
  ];
}
