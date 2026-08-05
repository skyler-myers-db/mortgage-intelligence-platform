# Databricks support ticket — account service-principal secret creation does not persist

Status: ready to file. Blocks the `deploy-dev` GitHub Actions workflow at step 4.

## Summary

`AccountClient.service_principals.service_principal_secrets.create()` returns
HTTP 200 with a real secret id and secret value, but the credential is never
persisted. An inventory of the principal's secrets taken immediately before and
immediately after the call is byte-identical — the returned id is absent from
it. Authenticating with the returned secret then fails with `invalid_client`.

The defect reproduces only from GitHub-hosted runners. The identical code path
against the identical account and service principal succeeds from a local
machine.

## Impact

`scripts/deploy.sh` mints a short-lived OAuth credential to prove the target
identity's workspace membership before promoting App source. With the credential
non-persistent, the proof cannot pass, and the deploy fails closed at step 4.
This is the sanctioned promotion path, so CI deploys are blocked entirely.

## Evidence

Exact-inventory instrumentation around the create call:

```
before=['c87b1020']  after=['c87b1020']  added=[]
```

The `create()` response carried an id that does not appear in `after`. The
subsequent token request with the returned secret was rejected:

```
invalid_client: Client authentication failed
```

Error ids from three separate runs:

- `ea23bd08-7326-49be-ad32-d9b88e7fcc81`
- `9c3e929b-84d3-4335-b827-c39ddbc51593`
- `058fa7e1-3a8b-473a-8209-bcdbedd2dc57`

## Hypotheses ruled out

Each was instrumented and disproved before filing:

1. **Credential propagation delay.** Local runs at the same 300s lifetime
   authenticated in 1.4–2.3s across three trials. Retries on the runner never
   succeed, and the credential never appears in inventory at all — this is not a
   settle-window problem.
2. **Ambient auth interference on the runner.** The probe strips ambient
   Databricks auth env vars for the duration of the call. Instrumentation
   confirmed `client_id_matches=true` and `ambient_auth_env_remaining=<none>` at
   the moment of failure, so the rejected request used the intended client id.
3. **Correlation with the rebase flag.** The flag is read only after step 4 and
   by no tool involved in the credential path.

## Requested

Confirm whether account-level service-principal secret creation is rate-limited,
regionally partitioned, or otherwise conditioned in a way that returns success
without durably writing the credential, and whether the calling network origin
(GitHub-hosted runner egress) is a factor.

## Environment

- Account API host: `https://accounts.cloud.databricks.com`
- Calling identity: account-admin service principal (`mip-account-scim-ci-sp`)
- SDK: `databricks-sdk` for Python, invoked from `ubuntu-latest` GitHub-hosted runners
- Local control: same SDK version, same account, same target principal — succeeds
