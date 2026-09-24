/**
 * Scenario module for the w2-warehouse-delivery lane (audit delivery-01,
 * delivery-v1 and the wave-1c status-pill follow-up). Nothing here is in the
 * default registry: a test swaps GET /api/health through `switchHealth`.
 */
import type { HealthPayload } from '../../../../src/lib/apiTypes';
import { json, type MockApi } from '../mockApi';
import { HEALTH_OK } from './shell';

const CLOSED = { warehouse: 'closed', lakebase: 'closed', genie: 'closed' };

/** The serverless warehouse STARTING from auto-stop: not an outage. */
export const HEALTH_WAREHOUSE_RESUMING: HealthPayload = {
  ...HEALTH_OK,
  status: 'ok',
  dependencies: { warehouse: 'resuming', lakebase: 'up', genie: 'up' },
  circuit_breakers: CLOSED,
};

/** A real warehouse outage: probe down and its breaker open. */
export const HEALTH_WAREHOUSE_DOWN: HealthPayload = {
  ...HEALTH_OK,
  status: 'degraded',
  dependencies: { warehouse: 'down', lakebase: 'up', genie: 'up' },
  circuit_breakers: { ...CLOSED, warehouse: 'open' },
};

export interface HealthSwitch {
  /** What the next GET /api/health answers. */
  set(payload: HealthPayload): void;
}

/** Answer GET /api/health from a payload the test can change between polls. */
export function switchHealth(mockApi: MockApi, initial: HealthPayload): HealthSwitch {
  let current = initial;
  mockApi.register('GET', '/api/health', () => json<HealthPayload>(current));
  return {
    set(payload) {
      current = payload;
    },
  };
}
