---
name: Project TS/JSX quirk
description: This repo's tsconfig + React 18 jsx-runtime exposes no global JSX namespace; using JSX.Element breaks the build.
type: project
---

`frontend/tsconfig.json` sets `"jsx": "react-jsx"` and does not import `@types/react` into the global namespace. As a result `JSX.Element` fails with `TS2503: Cannot find namespace 'JSX'`.

**Why:** react-jsx runtime removes the implicit React import, and this repo's tsconfig doesn't add `"types": ["react"]` or similar to restore the global.

**How to apply:** In this codebase, use `import type { ReactElement, ReactNode } from 'react'` and type children/slots as `ReactElement` or `ReactNode`. Never reach for `JSX.Element`. Applies to every .tsx file under `frontend/src/` until someone deliberately widens the tsconfig.
