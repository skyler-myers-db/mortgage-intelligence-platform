/**
 * The server's per-request actor binding, emulated for fixture specs
 * (D-identity-review-a4; backend/services/actor_binding.py is the real one).
 *
 * One `page.route('**\/api/**')` registered AFTER the mock API, so it runs
 * first (Playwright tries the newest matching route first). For a non-exempt
 * /api request that carries X-MIP-Actor-Key naming neither the served actor
 * nor one of its aliases, it answers the real 409 body; every other request
 * falls back to the mock API untouched (no header passes, as on the server).
 * Health, session and telemetry are exempt, closed set, as on the server.
 *
 * Every /api request is logged: method, path, the key it carried, the actor
 * served when it arrived, and what the emulation did. The real 409 makes the
 * browser log "the server responded with a status of 409", so a spec that
 * expects refusals allows that console line (docs/testing.md, "Actor binding
 * in fixture specs"). Imports no runtime src.
 */
import type { Page } from '@playwright/test';

export const ACTOR_KEY_HEADER = 'x-mip-actor-key';
export const ACTOR_CHANGED_BODY = {
  detail: 'The signed-in user changed in this browser. Nothing was recorded.',
  reason: 'actor_changed',
} as const;

/** /api[/v1]/health and /session (one trailing '/'), and /api[/v1]/telemetry/*. */
const EXEMPT = /^\/api(?:\/v\d+)?\/(?:(?:health|session)\/?$|telemetry\/)/;

export interface BindingLogEntry {
  method: string;
  /** The requested path (canonical /api/v1/...), without the query. */
  path: string;
  /** The X-MIP-Actor-Key value, or null when the request carried none. */
  key: string | null;
  /** The actor served when the request arrived (null: nobody). */
  served: string | null;
  outcome: 'exempt' | 'passed' | 'refused';
}

export interface ActorBindingEmulation {
  readonly log: BindingLogEntry[];
  /** Entries at and after `from` whose path is not exempt. */
  bound(from?: number): BindingLogEntry[];
}

export function isExemptPath(path: string): boolean {
  return EXEMPT.test(path);
}

export async function installActorBinding(
  page: Page,
  served: () => string | null,
  aliases: () => readonly string[] = () => [],
): Promise<ActorBindingEmulation> {
  const log: BindingLogEntry[] = [];
  await page.route('**/api/**', async (route) => {
    const request = route.request();
    const path = new URL(request.url()).pathname;
    if (!/^\/api(\/|$)/.test(path)) {
      await route.fallback();
      return;
    }
    const raw = await request.headerValue(ACTOR_KEY_HEADER);
    const key = raw === null ? null : raw.trim();
    const actor = served();
    const entry: BindingLogEntry = { method: request.method(), path, key, served: actor, outcome: 'passed' };
    log.push(entry);
    if (isExemptPath(path)) {
      entry.outcome = 'exempt';
      await route.fallback();
      return;
    }
    if (key === null || (actor !== null && (key === actor || aliases().includes(key)))) {
      await route.fallback();
      return;
    }
    entry.outcome = 'refused';
    await route.fulfill({ status: 409, contentType: 'application/json', body: JSON.stringify(ACTOR_CHANGED_BODY) });
  });
  return {
    log,
    bound: (from = 0) => log.slice(from).filter((entry) => !isExemptPath(entry.path)),
  };
}
