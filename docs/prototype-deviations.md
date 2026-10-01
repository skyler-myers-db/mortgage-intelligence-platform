# Prototype deviations

<!-- w5-design-contract creates this register (header, Adopted, Not adopted);
this lane contributes only its pending rows, which the integrator merges. -->

## Pending rows

| id | deviation | prototype cite | class | code | pinning test | finding ids | ruling date | lane |
| --- | --- | --- | --- | --- | --- | --- | --- | --- |
| tooltip | A design-system Tooltip primitive (hover after a short delay with skip-delay between neighbours, keyboard-focus open, Esc through the escape stack, a `<kbd>` shortcut slot, a merged `aria-describedby` with a hidden description) replaces native `title` on the Topbar controls; Chip and Button gain an additive `tooltip` prop (the Rail, quick-pick and remaining call sites migrate in W5d). The prototype has no tooltip; this is the `.map-tip` box (bg-1, line-2 border, shadow-md) at label scale, placed like the evidence hover card. | design_files/Module 0 Prototype.html:868 | accessibility | frontend/src/components/ui/Tooltip.tsx; frontend/src/components/ui/Tooltip.css | frontend/src/components/ui/Tooltip.test.tsx | critic-08 | 2026-09-30 | w5-platform-backend |
