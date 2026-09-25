---
name: pixel-neutrality-probe
description: How to PROVE an element swap (div -> h2/span/p) is pixel-neutral without touching VRT baselines: a throwaway fixture spec that dumps geometry from the built app before and after.
metadata:
  type: project
---

VRT PNGs are regenerated only by the integrator in CI's pinned container (never
run MIP_VRT=1 locally), so a "no capture moves" claim needs its own proof.

Method used for a11y-03 (2026-09-25), all through the fixture harness
(`E2E_FIXTURE_PORT=<548x> E2E_FIXTURE_WORKERS=1 npm --prefix frontend run
e2e:fixture -- <spec>` after a build):
1. Throwaway `tests/e2e/fixture/zz-*.fixture.spec.ts` (serial, not committed)
   that visits every `FIXTURE_ROUTES` entry (+ Console-open states), and writes
   JSON of every target element's rounded `getBoundingClientRect`, a list of
   computed styles, every `.surface` box and `documentElement.scrollHeight` to
   a path from an env var.
2. Build + run on the old tree, build + run on the new tree, diff the JSON.
3. For swaps that legitimately change the element box (a block div -> inline
   span), clone the live parent twice in `page.evaluate`, swap the element back
   in one clone, and compare `Range.getBoundingClientRect()` of every text
   node: identical text geometry = identical pixels.

**Why:** `.h-4` resets font-size/weight/margin, so h2/h3 is neutral, but an
inline span inside a `<button>` (UA `text-align: center`) reports a different
box while painting the same glyphs; only the text-range check settles it.

**How to apply:** any "no visual change" refactor of element types or wrapper
structure. Move the throwaway specs out of the tree before committing. Related:
[[feedback-load-the-built-app]].
