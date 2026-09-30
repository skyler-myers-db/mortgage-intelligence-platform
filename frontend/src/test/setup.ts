// Vitest setup (vite.config.ts `test.setupFiles`), run before every test file.
//
// React's act() environment, once for the whole suite (audit quality-06): with
// the flag unset, every act() call warns "The current testing environment is
// not configured to support act(...)", which is why so many suites repeated
// this line. It is harmless in the node environment, which never renders.
// src/test/render.tsx mount() relies on it.
(globalThis as typeof globalThis & { IS_REACT_ACT_ENVIRONMENT?: boolean }).IS_REACT_ACT_ENVIRONMENT = true;
