# Testing strategy

## Unit tests

- Scoring functions.
- Offer rules.
- Evidence formatting.
- Pydantic schema validation.
- Frontend component rendering.

## Integration tests

- API health.
- Portfolio preview.
- Borrower detail.
- Offer recommendation.
- Approval writes audit.
- Genie fallback.

## E2E tests

Playwright path:

1. Open home.
2. Build portfolio.
3. Select In the Money segment.
4. Open lead queue.
5. Expand borrower.
6. Open Borrower 360.
7. Generate offer.
8. Approve outreach.
9. Confirm audit row.

## Acceptance tests

- Evidence visible.
- Human approval visible.
- Synthetic data only.
- Route performance acceptable under live load.
- Table-first backup layout works.
