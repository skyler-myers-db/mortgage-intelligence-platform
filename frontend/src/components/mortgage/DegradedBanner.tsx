import { useEffect, useRef, useState } from 'react';
import { Icon } from '../Icon';

/**
 * DegradedBanner — Slice-6 resilience surface.
 *
 * The banner appears when the backend reports a degraded dependency
 * (warehouse or lakebase down, or a circuit breaker open). It is the
 * ONLY signal the UI is allowed to show when the real-data path
 * fails; we never silently fall back to mock data. The copy reads as
 * "backend is warming up" so users see a calibrated, enterprise-grade
 * message rather than a stack trace.
 *
 * Behavior:
 *  - Polls `/api/health` every `pollIntervalMs` (default 8s in the
 *    ok state, 3s while degraded so the UI recovers quickly).
 *  - Renders only when `status !== 'ok'` or any dependency is down.
 *  - A small live-dot communicates the retry heartbeat.
 *
 * BEM class names (`degraded-banner`, `__ico`, `__body`, `__title`,
 * `__sub`, `__dot`) live in `design-system/components.css` and mirror
 * the `.approval` surface's token vocabulary (amber warning).
 */

export interface HealthPayload {
  status?: 'ok' | 'degraded';
  mode?: string;
  warehouse_id?: string | null;
  app_env?: string;
  dependencies?: Record<string, 'up' | 'down'>;
  circuit_breakers?: Record<string, 'closed' | 'open' | 'half_open'>;
}

interface DegradedBannerProps {
  /** Optional override for polling interval in ms. */
  pollIntervalOkMs?: number;
  pollIntervalDegradedMs?: number;
  /** Injected fetcher, for tests. Defaults to `fetch('/api/health')`. */
  fetchHealth?: () => Promise<HealthPayload>;
}

async function defaultFetchHealth(): Promise<HealthPayload> {
  const res = await fetch('/api/health');
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

function degradedDependency(health: HealthPayload | null): string | null {
  if (!health) return null;
  const deps = health.dependencies ?? {};
  if (deps.warehouse === 'down') return 'warehouse';
  if (deps.lakebase === 'down') return 'lakebase';
  // Open breaker without a concrete dep ping-down still counts.
  const breakers = health.circuit_breakers ?? {};
  for (const [name, state] of Object.entries(breakers)) {
    if (state === 'open') return name;
  }
  return null;
}

export function DegradedBanner({
  pollIntervalOkMs = 8000,
  pollIntervalDegradedMs = 3000,
  fetchHealth = defaultFetchHealth,
}: DegradedBannerProps = {}) {
  const [health, setHealth] = useState<HealthPayload | null>(null);
  // Keep the latest degraded flag in a ref so the effect's interval
  // can read it without needing to re-register when the state flips.
  const degradedRef = useRef(false);

  useEffect(() => {
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
        // Fetch itself failed -- treat as degraded so the banner
        // shows and the next tick retries faster.
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
  }, [fetchHealth, pollIntervalDegradedMs, pollIntervalOkMs]);

  const downDep = degradedDependency(health);
  if (!downDep) return null;

  const title = `Backend is warming up — ${downDep} dependency recovering`;
  const sub = `Retrying automatically every ${Math.round(pollIntervalDegradedMs / 1000)}s. Live data will appear as soon as it's available — no mock fallback.`;

  return (
    <div
      className="degraded-banner"
      role="status"
      aria-live="polite"
      data-degraded-dependency={downDep}
    >
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
    </div>
  );
}
