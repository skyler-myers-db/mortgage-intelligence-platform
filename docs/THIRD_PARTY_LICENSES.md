# Third-Party License Notices

This project is a commercial Databricks App. Production dependencies must not
carry no-commercial-use, GPL-family strong-copyleft, Commons Clause, or other commercial-use
blockers. This notice tracks the small set of dependencies with attribution or
weak-copyleft obligations that customer security reviews are likely to ask
about.

## Production Runtime

| Package | Use | License | Commercial posture |
|---|---|---|---|
| `boto3` / `botocore` | MLflow Unity Catalog model-artifact access on AWS | `Apache-2.0` | Permissive. Required at runtime because MLflow loads the AWS artifact client dynamically during model registration. Preserve the upstream copyright, license, and NOTICE materials in distributions. |
| `psycopg` / `psycopg-binary` | Lakebase PostgreSQL client | `LGPL-3.0-only` | Allowed for commercial use as an unmodified, dynamically loaded Python package. Include this notice and the LGPL license text/reference in distribution materials. |
| `pg8000` / `scramp` / `asn1crypto` | Structured PostgreSQL authentication and replication-denial proof for Lakebase | `BSD-3-Clause` / `MIT-0` / `MIT` | Permissive. Preserve the upstream copyright and license notices in distributions. |
| `us-atlas` | U.S. state TopoJSON for browser maps | `ISC` | Permissive. Replaced the prior no-commercial-use map package. |
| `topojson-client` | Runtime TopoJSON-to-GeoJSON decoding | `ISC` | Permissive. |

LGPL-3.0-only license text for `psycopg` is available from the GNU project:
https://www.gnu.org/licenses/lgpl-3.0.en.html

Apache-2.0 license and NOTICE materials for `boto3` and `botocore` are included
in their Python distributions and upstream source repositories.

BSD-3-Clause, MIT-0, and MIT license materials for `pg8000`, `scramp`, and
`asn1crypto` are included in their Python distributions and upstream source
repositories.

ISC license text for `us-atlas` and `topojson-client` is included in their npm
packages and permits commercial use, copying, modification, and distribution
with the copyright and permission notice retained.

## Development And Test Tooling

| Package | Use | License | Commercial posture |
|---|---|---|---|
| `@axe-core/playwright` / `axe-core` | Accessibility test automation | `MPL-2.0` | Dev/test only. No project files derive from or modify the MPL source. |
| `hypothesis` | Python property/fuzz tests | `MPL-2.0` | Test only. No project files derive from or modify the MPL source. |
| `lightningcss` | Vite/build-time CSS transform dependency | `MPL-2.0` | Build-time transitive dependency. No project files derive from or modify the MPL source. |

MPL-2.0 is file-level weak copyleft. Because these packages are consumed
unmodified, the practical obligation is attribution and preservation of the
upstream license notices.

## Explicitly Prohibited In Production Browser Dependencies

The test suite scans `frontend/package-lock.json` and fails if a production
browser dependency uses restricted commercial-use, GPL-family copyleft, LGPL,
or Commons Clause license terms.
