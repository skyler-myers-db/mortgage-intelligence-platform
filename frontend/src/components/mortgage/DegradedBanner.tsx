// Shell-initial and rendered only on failure: React Compiler memo caches
// would roughly double these three small renderers in the initial chunk for
// no measurable render saving (same call as analytics.charts.tsx).
'use no memo';

import { useEffect, useRef, useState, type ReactNode } from 'react';
import { Icon } from '../Icon';
import { useOptionalHealth } from '../HealthProvider';
import type { ConnectionStatus } from '../connectionState';
import { SessionExpiredDialog } from '../layout/SessionExpiredDialog';
import { apiPath } from '../../lib/apiPaths';

/**
 * DegradedBanner — Slice-6 resilience surface.
 *
 * The banner appears when the backend reports a degraded dependency
 * (warehouse or lakebase down, or a circuit breaker open). It is the
 * ONLY signal the UI is allowed to show when the real-data path
 * fails; we never silently fall back to mock data. The copy reads as
 * "reconnecting to <dep>" so users see a factual status rather than a
 * stack trace.
 *
 * By default the banner consumes the shared `HealthProvider` snapshot
 * (round-2 hole-finder #21, 2026-04-23) so there's one `/api/health`
 * poll per document, not three. Tests that want to exercise the
 * banner in isolation can still inject `fetchHealth` to opt into the
 * legacy standalone poll — this keeps the existing vitest suites
 * green without forcing them to spin up a provider tree.
 *
 * BEM class names (`degraded-banner`, `__ico`, `__body`, `__title`,
 * `__sub`, `__dot`) live in `design-system/components.css` and mirror
 * the `.approval` surface's token vocabulary (amber warning). The
 * `--info` modifier and `__actions` element are used by <VersionNotice>,
 * which shares this slot at the top of `<main>`.
 *
 * Connection states (audit 2026-09-21 `states-02`, `shell-v1`). The provider's
 * `connection` outranks dependency health, because a dependency cannot be
 * judged through a connection that is not there:
 *   - `session_expired` → the blocking SessionExpiredDialog (rendered here so
 *     the shell's one failure slot owns every real-data failure); no banner.
 *   - `offline`         → "You are offline" (no Reload: it would fail too).
 *   - `unreachable`     → "Connection lost" + Reload, after two failed probes.
 *   - otherwise         → the dependency banner below, or nothing.
 */

export interface HealthPayload {
  status?: 'ok' | 'degraded';
  mode?: string;
  warehouse_id?: string | null;
  app_env?: string;
  dependencies?: Record<string, 'up' | 'down'>;
  circuit_breakers?: Record<string, 'closed' | 'open' | 'half_open'>;
  forced_degraded?: {
    active: boolean;
    dependency: string;
    source: string;
    expires_in_s: number;
  } | null;
}

interface DegradedBannerProps {
  /** Optional override for polling interval in ms. */
  pollIntervalOkMs?: number;
  pollIntervalDegradedMs?: number;
  /** Injected fetcher, for tests. Defaults to the canonical health endpoint. */
  fetchHealth?: () => Promise<HealthPayload>;
  /** "Connection lost" Reload action. Defaults to `location.reload()` (keeps the URL). */
  onReload?: () => void;
}

async function defaultFetchHealth(): Promise<HealthPayload> {
  const res = await fetch(apiPath('/health'));
  if (!res.ok) {
    // A non-2xx on /api/health is itself a degraded signal -- we
    // surface a synthetic payload so the banner still renders.
    return {
      status: 'degraded',
      dependencies: { warehouse: 'down', lakebase: 'down' },
    };
  }
  return (await res.json()) as HealthPayload;
}

/**
 * Map internal Databricks product names → buyer-friendly dependency names.
 * The degraded banner is surfaced to the business buyer (Head of Growth,
 * VP Lending), who doesn't know what "lakebase" or "genie" are. Keep the
 * internal name in `data-degraded-dependency` for ops telemetry; show
 * the friendly name in the visible title.
 */
const FRIENDLY_DEP_NAMES: Record<string, string> = {
  warehouse: 'analytics warehouse',
  lakebase: 'operational database',
  genie: 'AI assistant',
};

function friendlyDependencyName(dep: string): string {
  return FRIENDLY_DEP_NAMES[dep] ?? dep;
}

export function degradedDependency(health: HealthPayload | null): string | null {
  if (!health) return null;
  const deps = health.dependencies ?? {};
  if (deps.warehouse === 'down') return 'warehouse';
  if (deps.lakebase === 'down') return 'lakebase';
  if (deps.genie === 'down') return 'genie';
  // Open breaker without a concrete dep ping-down still counts.
  const breakers = health.circuit_breakers ?? {};
  for (const [name, state] of Object.entries(breakers)) {
    if (state === 'open') return name;
  }
  return null;
}

/**
 * Legacy standalone fetcher. Preserved so unit tests that pass
 * `fetchHealth` can keep exercising the banner without wiring up a
 * HealthProvider. Production mounts ignore this path and read the
 * shared provider snapshot instead.
 */
function useStandaloneHealth({
  enabled,
  pollIntervalOkMs,
  pollIntervalDegradedMs,
  fetchHealth,
}: {
  enabled: boolean;
  pollIntervalOkMs: number;
  pollIntervalDegradedMs: number;
  fetchHealth: () => Promise<HealthPayload>;
}): HealthPayload | null {
  const [health, setHealth] = useState<HealthPayload | null>(null);
  const degradedRef = useRef(false);

  useEffect(() => {
    if (!enabled) return undefined;
    let cancelled = false;
    let timer: ReturnType<typeof setTimeout> | null = null;

    async function tick() {
      try {
        const payload = await fetchHealth();
        if (cancelled) return;
        setHealth(payload);
        const isDegraded =
          payload.status === 'degraded' || !!degradedDependency(payload);
        degradedRef.current = isDegraded;
      } catch {
        if (cancelled) return;
        setHealth({
          status: 'degraded',
          dependencies: { warehouse: 'down', lakebase: 'down' },
        });
        degradedRef.current = true;
      }
      if (cancelled) return;
      const delay = degradedRef.current ? pollIntervalDegradedMs : pollIntervalOkMs;
      timer = setTimeout(tick, delay);
    }

    void tick();
    return () => {
      cancelled = true;
      if (timer !== null) clearTimeout(timer);
    };
  }, [enabled, fetchHealth, pollIntervalDegradedMs, pollIntervalOkMs]);

  return health;
}

export function shouldUseStandaloneHealth(
  hasInjectedFetcher: boolean,
  hasProviderContext: boolean,
): boolean {
  return hasInjectedFetcher || !hasProviderContext;
}

export function DegradedBanner({
  pollIntervalOkMs = 8000,
  pollIntervalDegradedMs = 3000,
  fetchHealth,
  onReload = reloadPage,
}: DegradedBannerProps = {}) {
  // When a caller injects a fetcher, run the legacy standalone loop so
  // existing unit tests keep exercising the banner. When mounted inside
  // AppShell, use the shared HealthProvider poll and do not start another
  // `/api/health` interval.
  const providerCtx = useOptionalHealth();
  const isStandalone = shouldUseStandaloneHealth(fetchHealth !== undefined, providerCtx !== null);
  const standaloneHealth = useStandaloneHealth({
    enabled: isStandalone,
    pollIntervalOkMs,
    pollIntervalDegradedMs,
    fetchHealth: fetchHealth ?? defaultFetchHealth,
  });
  const providerHealth = (providerCtx?.health as HealthPayload | null) ?? null;
  const health = isStandalone ? standaloneHealth : providerHealth;
  const connection: ConnectionStatus = isStandalone ? 'online' : providerCtx?.connection ?? 'online';

  const seconds = Math.round(pollIntervalDegradedMs / 1000);
  let banner: ReactNode = null;
  if (connection === 'offline') {
    banner = (
      <Banner
        title="You are offline"
        sub="Data loads on its own when your connection returns. Approvals and other changes are not recorded while you are offline."
        data={{ 'data-connection': 'offline' }}
      />
    );
  } else if (connection === 'unreachable') {
    banner = (
      <Banner
        title="Connection lost"
        sub={`The app did not answer the last two checks. Checking again every ${seconds} seconds; panels reload on their own once it answers.`}
        data={{ 'data-connection': 'unreachable' }}
        onReload={onReload}
      />
    );
  } else if (connection === 'online') {
    const downDep = degradedDependency(health);
    // Truthful copy (audit 2026-09-21 `states-03`): it is the health CHECK
    // that repeats every few seconds, not the page. When the check sees the
    // dependency back, HealthProvider refetches the mounted panels that
    // failed because of it (healthRecovery.ts), which is what makes "on
    // their own" true.
    if (downDep) {
      banner = (
        <Banner
          title={`Reconnecting to ${friendlyDependencyName(downDep)}`}
          sub={`Checking the connection every ${seconds} seconds. Panels that could not load will reload on their own once it is back.`}
          data={{ 'data-degraded-dependency': downDep }}
        />
      );
    }
  }

  return (
    <>
      <SessionExpiredDialog />
      {banner}
    </>
  );
}

function reloadPage(): void {
  window.location.reload();
}

/** The `.degraded-banner` anatomy every state shares (amber family). */
function Banner({
  title,
  sub,
  data,
  onReload,
}: {
  title: string;
  sub: string;
  data: Record<`data-${string}`, string>;
  onReload?: () => void;
}) {
  return (
    <div className="degraded-banner" role="status" aria-live="polite" {...data}>
      <div className="degraded-banner__ico" aria-hidden="true">
        <Icon name="bolt" size={16} />
      </div>
      <div className="degraded-banner__body">
        <div className="degraded-banner__title">
          <span className="degraded-banner__dot" aria-hidden="true" />
          {title}
        </div>
        <div className="degraded-banner__sub">{sub}</div>
      </div>
      {onReload && (
        <div className="degraded-banner__actions">
          <button type="button" className="btn btn--ghost btn--sm" onClick={onReload}>
            Reload
          </button>
        </div>
      )}
    </div>
  );
}
