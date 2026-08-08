---
name: reuse-later-declared-css-primitives
description: components.css is one 6k-line flat file with no layers — reusing a primitive declared LATER than your override silently loses the cascade.
metadata:
  type: project
---

`frontend/src/design-system/components.css` is a single flat ~6,300-line stylesheet with **no `@layer`** and almost entirely single-class selectors. Specificity ties are therefore broken by **source order**, and the file is organized by feature area, not by dependency order.

Concretely: the shared dropdown primitive `.filter-menu` / `.filter-menu__item` lives around **line 4860**, while the Genie panel block lives around **line 2540**. Adding `.genie-history__menu { left: auto; inset-inline-end: 0; }` next to the other `.genie-*` rules put the override ~2,300 lines *before* the thing it was overriding, so `.filter-menu { left: 0 }` won and the menu anchored to the wrong edge. Same trap hit `align-items` on the item rows.

**Why:** reusing existing primitives is the right call for the tight CSS budget ([[css-budget-headroom-is-thin]]), but "reuse" means your customizations are now competing with a rule that may be declared later in the file.

**How to apply:** when you compose a new component out of an existing primitive class, **grep both selectors' line numbers first**:

```
grep -n "^\.the-primitive {\|^\.your-override" frontend/src/design-system/components.css
```

If your override is earlier, don't move the block — raise specificity by scoping to the parent block class (`.genie-history .genie-history__menu`), which is order-independent and self-documents the dependency. Leave a comment saying why the parent selector is there, or the next person will "simplify" it back into a bug.

Verify in the browser rather than trusting the diff: `preview_start` the `mip-frontend` launch config, then use `javascript_tool` to read `getBoundingClientRect()` / `getComputedStyle()` on the element. Numeric assertions catch sub-pixel misalignment that a screenshot cannot (the Browser pane's `zoom` action does not support region cropping).
