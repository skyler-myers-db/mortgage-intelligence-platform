# Gap 3 Live Readiness Evidence

> Internal validation artifact. Not approved for public release. This is a
> partial Module 0 readiness record, not a claim that MLS/listing or
> building-permit feeds are live.

Date: 2026-06-05
Commit: `f9763f4b6abdfd6ceb026e66a3529ae1e30b175f`
App: `https://mip-app-2543889327043640.aws.databricksapps.com`
Deployment: `01f161360bf61de6a7b8cbf00f9f1127`
CI: `27045308620`

## Proofs Passed

- Source package hygiene: `make zip` produced `dist/mortgage-intelligence-platform.zip`; `tools/release_hygiene.py` returned OK.
- CI: push workflow `27045308620` completed successfully on `main`.
- Deploy: `./scripts/deploy.sh -t dev --no-confirm --skip-silver` completed successfully; bundle validate returned `Validation OK`; app snapshot started successfully; Lakebase migrate, gold refresh, lifecycle sync, Genie rebind, and `scripts/smoke_live.sh` all passed.
- Live Playwright: admin phone canary `1 passed`; route matrix `10 passed`; responsive suite `38 passed`.
- Visual proof: `/tmp/mip-gap2-admin-mobile-deployed-f9763f4.png`; 390 px viewport had `bodyScrollWidth=390`, one-column admin grid, `clipped=[]`, and no console errors.
- Genie eval: `tools/genie_eval.py` against the deployed app with canonical SQL env passed `16/16`, overall score `100.0`; report at `/tmp/mip-genie-eval-f9763f4-canonical/2026-06-05T23-54-45Z.md`.
- Non-admin RBAC: rotated and saved `mip-nightly-ci-sp` M2M credentials to GitHub Actions secrets; live proof returned `/api/health=200`, `/api/leads?limit=1=200`, `/api/audit/events?limit=1=403`, `/api/admin/rules=403`, `/api/admin/sources=403`, `/api/admin/operations=403`.
- Resilience/degraded drill: live Playwright `real_data.spec.ts` forced a browser-local warehouse degraded state and verified the DegradedBanner within 5s: `1 passed`.
- Databricks live integration: `test_sql_python_parity.py` + `test_source_readiness_live.py` passed `54` tests.
- Lakebase round trip: `test_lakebase_round_trip.py` passed against deployed Lakebase `mip_app_state`; deploy smoke also passed outreach approval audit write.
- Release-readiness artifact: `/tmp/mip-release-readiness-f9763f4.md` marks every proof gate passed except the two external pending feeds below.

## Cannot Claim

- MLS/listing/listed-for-sale triggers are not live until the Cotality MLS/Listings feed lands and source readiness proves live rows.
- Building-permit/renovation triggers are not live until the Cotality Building Permits feed lands and source readiness proves live rows.

This evidence supports partial signoff under the agreed external-feed carveout only.
