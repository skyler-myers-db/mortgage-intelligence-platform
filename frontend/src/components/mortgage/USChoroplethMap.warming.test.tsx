/**
 * @vitest-environment happy-dom
 */
/**
 * The geography map's warming stage is never blank (audit dataviz-04, review
 * round 2). On a cold warehouse the health poll reports the warehouse down,
 * WarmingUpBlock renders nothing (the DegradedBanner owns the story), and the
 * stage used to be an empty box. These tests render the real block and the
 * real stage under the real HealthProvider for every health x dependency
 * case and pin two invariants:
 *  - parity: `warmingBlockDefersToBanner` is true exactly when the real
 *    WarmingUpBlock renders nothing, so the map's copy of the rule cannot
 *    drift from the block's;
 *  - the stage: it always names what it waits for, and shows exactly one of
 *    the block or its own one-line status, never both and never neither.
 */
import { act } from 'react';
import { createRoot, type Root } from 'react-dom/client';
import { afterEach, beforeEach, describe, expect, it } from 'vitest';
import type { HealthPayload } from '../../lib/api';
import type { WarmingUpState } from '../../lib/useWarmingUpRetry';
import { HealthProvider, useOptionalHealth } from '../HealthProvider';
import { WarmingUpBlock } from '../ui/WarmingUpBlock';
import { MapUnavailable } from './USChoroplethMapUnavailable';
import { useWarmingBlockDefers, warmingBlockDefersToBanner } from './USChoroplethMap.warming';
import type { GeoRead } from './useChoroplethLiveFacts';

(globalThis as unknown as { IS_REACT_ACT_ENVIRONMENT: boolean }).IS_REACT_ACT_ENVIRONMENT = true;

const UP = { warehouse: 'up', lakebase: 'up', genie: 'up' };
const CLOSED = { warehouse: 'closed', lakebase: 'closed', genie: 'closed' };
const HEALTH: Record<string, HealthPayload> = {
  ok: { status: 'ok', mode: 'live', dependencies: UP, circuit_breakers: CLOSED },
  'warehouse down': { status: 'degraded', mode: 'live', dependencies: { ...UP, warehouse: 'down' }, circuit_breakers: CLOSED },
  'lakebase down': { status: 'degraded', mode: 'live', dependencies: { ...UP, lakebase: 'down' }, circuit_breakers: CLOSED },
  'genie down': { status: 'degraded', mode: 'live', dependencies: { ...UP, genie: 'down' }, circuit_breakers: CLOSED },
  'degraded, every dependency up': { status: 'degraded', mode: 'live', dependencies: UP, circuit_breakers: CLOSED },
  'warehouse breaker open': { status: 'ok', mode: 'live', dependencies: UP, circuit_breakers: { ...CLOSED, warehouse: 'open' } },
};
const DEPENDENCIES: Array<string | null> = ['warehouse', 'lakebase', 'genie', null];
const WHAT = 'State borrower rollups';

function warming(dependency: string | null): WarmingUpState {
  return { dependency, label: 'Warehouse warming up', attempt: 2, maxAttempts: 6, correlationId: null };
}

function warmingRead(state: WarmingUpState): GeoRead<unknown> {
  return { data: null, warmingUp: state, error: null, loading: false, updating: false, retry: () => undefined };
}

/** Publishes what the hook decides, and whether the provider has answered yet. */
function DefersProbe({ state }: { state: WarmingUpState }) {
  const defers = useWarmingBlockDefers(state);
  const health = useOptionalHealth()?.health ?? null;
  return <div data-probe="defers" data-defers={String(defers)} data-ready={String(health !== null)} />;
}

let root: Root;

beforeEach(() => {
  document.body.innerHTML = '<div id="root"></div>';
  root = createRoot(document.getElementById('root') as HTMLElement);
});

afterEach(() => {
  act(() => root.unmount());
  document.body.innerHTML = '';
});

async function renderUnder(health: HealthPayload, state: WarmingUpState): Promise<void> {
  await act(async () => {
    root.render(
      <HealthProvider fetchHealth={() => Promise.resolve(health)} debounceUpMs={0} pollIntervalOkMs={600_000} pollIntervalDegradedMs={600_000}>
        <DefersProbe state={state} />
        <div data-probe="block">
          <WarmingUpBlock state={state} title={WHAT} compact />
        </div>
        <MapUnavailable read={warmingRead(state)} what={WHAT} />
      </HealthProvider>,
    );
  });
  for (let i = 0; i < 50; i += 1) {
    if (document.querySelector('[data-probe="defers"]')?.getAttribute('data-ready') === 'true') return;
    await act(async () => {
      await new Promise((resolve) => window.setTimeout(resolve, 0));
    });
  }
  throw new Error('the health provider never answered');
}

describe('warming stage under the shared health poll (dataviz-04)', () => {
  for (const [healthName, health] of Object.entries(HEALTH)) {
    for (const dependency of DEPENDENCIES) {
      it(`${healthName}, block names ${dependency ?? 'no dependency'}: the stage says what it waits for, once`, async () => {
        const state = warming(dependency);
        await renderUnder(health, state);

        const defers = document.querySelector('[data-probe="defers"]')?.getAttribute('data-defers') === 'true';
        const blockShown = document.querySelector('[data-probe="block"] [data-testid="warming-up-block"]') !== null;
        expect(defers, 'the map defers exactly when the real WarmingUpBlock renders nothing').toBe(!blockShown);
        expect(warmingBlockDefersToBanner(health, state)).toBe(defers);

        const stage = document.querySelector('.map-stage--status');
        expect(stage?.textContent ?? '').toContain(WHAT);
        const stageBlock = stage?.querySelector('[data-testid="warming-up-block"]') ?? null;
        const stageLine = stage?.querySelector('.map-center-card') ?? null;
        expect(stageBlock !== null, 'the stage shows the block when it renders').toBe(blockShown);
        expect(stageLine !== null, 'the stage shows its own line only when the block steps aside').toBe(!blockShown);
        if (stageLine) {
          expect(stageLine.textContent).toBe(`${WHAT}: Warehouse warming up. Retrying automatically (attempt 2 of 6).`);
        }
      });
    }
  }

  it('control: the cold-warehouse case really is one the block steps aside for', async () => {
    await renderUnder(HEALTH['warehouse down'], warming('warehouse'));
    expect(document.querySelector('[data-probe="block"]')?.childElementCount).toBe(0);
    expect(document.querySelector('.map-stage--status [data-testid="warming-up-block"]')).toBeNull();
    expect(document.querySelector('.map-stage--status .map-center-card')).not.toBeNull();
  });

  // The shared rule (audit states-03 part a): the block steps aside exactly
  // when the banner shows for its dependency. Deliberate changes from the
  // old private copy, pinned on the real block through the parity loop above
  // and here on the rule itself.
  it.each([
    ['a status-only degraded payload hides nothing (no banner shows then)', 'degraded, every dependency up', null, false],
    ['genie down defers a genie block like warehouse / lakebase', 'genie down', 'genie', true],
    ['an open warehouse breaker defers a warehouse block', 'warehouse breaker open', 'warehouse', true],
    ['warehouse down still keeps a lakebase block', 'warehouse down', 'lakebase', false],
  ] as const)('%s', (_label, healthName, dependency, expected) => {
    expect(warmingBlockDefersToBanner(HEALTH[healthName], warming(dependency))).toBe(expected);
  });

  it.each(['offline', 'unreachable', 'session_expired'] as const)(
    'never defers while the connection is %s (the banner does not name a dependency then)',
    (connection) => {
      expect(warmingBlockDefersToBanner(HEALTH['warehouse down'], warming('warehouse'), connection)).toBe(false);
    },
  );

  it('outside a HealthProvider the block always renders and the stage adds nothing', async () => {
    const state = warming('warehouse');
    await act(async () => {
      root.render(<MapUnavailable read={warmingRead(state)} what={WHAT} />);
    });
    expect(document.querySelector('.map-stage--status [data-testid="warming-up-block"]')).not.toBeNull();
    expect(document.querySelector('.map-stage--status .map-center-card')).toBeNull();
  });
});
