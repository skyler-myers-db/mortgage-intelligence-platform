# Design review notes

The current design direction is strong:

- It feels enterprise, data-dense, and credible.
- The dark Databricks-adjacent theme is stage-friendly.
- Evidence chips, confidence meters, approval gate, Genie, and audit log directly support the core value proposition.
- The map-first / segment-first / table-first variants are useful: map-first for executives, table-first for technical/product reviews.

Recommended fixes before converting to React routes:

1. Standardize the default tenant lender to `Summit Mortgage` unless partner stakeholders choose another anonymized lender.
2. Fix copy typo: `Cotaliy` → `Cotality`.
3. Align rail labels to the spec:
   - M0 Top-of-Funnel
   - M1 Pipeline Command Center
   - M2 Loan Officer Workbench
   - M3 Underwriting Copilot
   - M4 Risk & Retention
4. Hide the Tweaks panel by default in presentation mode.
5. Move Genie to a route plus optional drawer; the overlay can obscure evidence during live sessions.
6. Replace stylized SVG map with MapLibre + GeoJSON only if time allows; stylized map is acceptable as a presentation-safe fallback.
7. Keep the evidence drawer and approval banner mandatory on all recommendation/action pages.
8. Use synthetic borrower names and no real contact info.
9. Ensure all numbers shown in mock mode are internally consistent with the backend seed data.
