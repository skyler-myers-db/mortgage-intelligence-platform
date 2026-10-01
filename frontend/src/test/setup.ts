// Vitest setup (vite.config.ts `test.setupFiles`), run before every test file.
//
// React's act() environment, once for the whole suite (audit quality-06): with
// the flag unset, every act() call warns "The current testing environment is
// not configured to support act(...)", which is why so many suites repeated
// this line. It is harmless in the node environment, which never renders.
// src/test/render.tsx mount() relies on it.
import { beforeEach } from 'vitest';
import { NOBODY, _resetActorScopeForTests, _setResetDocumentForTests } from '../lib/actorScope';

(globalThis as typeof globalThis & { IS_REACT_ACT_ENVIRONMENT?: boolean }).IS_REACT_ACT_ENVIRONMENT = true;

// The actor gate (lib/actorScope, D-identity-review-b) starts every document
// 'pending', where private storage reads return null. Store suites run as an
// open gate for nobody; the gate's own suites, the AppShell actor-boundary
// suites and GenieDock.resume reset to 'pending' themselves. A suite that
// calls vi.resetModules() and re-imports a store re-applies this on the fresh
// actorScope instance.
//
// A proven actor change resets the document (D-identity-review-a3) through
// window.location.replace('/'): no suite may make happy-dom navigate, so the
// reset is a no-op unless a suite installs its own vi.fn to assert it (a suite
// that resets the gate again mid-test re-installs one before a change).
beforeEach(() => {
  _resetActorScopeForTests({ status: 'open', owner: NOBODY });
  _setResetDocumentForTests(() => undefined);
});
