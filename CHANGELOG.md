# Changelog

All notable API-facing changes for the Mortgage Intelligence Platform are tracked here.

This project follows an additive-first API contract: new optional fields and new
versioned endpoints may be added in a minor release, while removals require a
deprecation window first.

## Unreleased

- Added admin-only `/api/v1/admin/operations` and
  `/api/v1/admin/operations/run` for audited Databricks refresh job status and
  launch.
- Moved live Databricks validation from a daily schedule to manual
  `workflow_dispatch` so expensive refresh, Genie, and Playwright gates run
  only for release/signoff events.

## 0.1.0 - 2026-05-17

- Added canonical `/api/v1/*` API routes.
- Kept unversioned `/api/*` routes as deprecated compatibility aliases.
- Added `X-API-Version: v1` on API responses.
- Added an OpenAPI compatibility baseline gate for removed paths, removed
  methods, removed fields, optional-to-required field flips, and enum narrowing.
- Wired OpenAPI `info.version` to the package version.
