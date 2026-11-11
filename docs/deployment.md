# Deployment guide

## Local

```bash
cp .env.example .env.local
pip install -r requirements-dev.txt  # the App's requirements.txt plus the test tools
npm --prefix frontend install
npm --prefix frontend run dev
uvicorn backend.main:app --reload
```

## Build frontend

```bash
npm --prefix frontend run build
```

FastAPI serves `frontend/dist` automatically if present.

## App source contract (prebuilt frontend, no root npm manifest)

The Databricks App serves the **prebuilt** `frontend/dist/` bundle compiled by
`scripts/deploy.sh` under the audited CI Node version. The promoted App source
tree must never contain a root `package.json`/`package-lock.json`: when a root
`package.json` with a `build` script is present, the Databricks Apps build
phase runs it on every deployment with the platform's own Node (22.16 today,
vs the audited 22.22 — the engines mismatch is only an EBADENGINE warning, so
the build *succeeds*) and recompiles `frontend/dist/` from the uploaded
`frontend/src/**`, silently replacing the prebuilt bundle. This is how a
dist-only hotfix kept reverting on the paychex dev workspace on 2026-08-05
(~5 hours of debugging): the tree still carried a stale root manifest, so
every deployment rebuilt the old sources over the freshly-synced dist.

Since the governed per-release App source (next section), the App runs from
an immutable `<bundle root>/app-src/<release id>` folder, never from the bundle
files root. Enforcement is three-layer, pinned by
`tests/unit/test_static_app_bundle_contract.py` and
`tests/unit/test_app_release_deploy_contract.py`:

1. **`databricks.yml` `sync.exclude`** keeps `/package.json` and
   `/package-lock.json` out of bundle uploads. Verified on Databricks CLI
   v1.10.0 (2026-08-05 scratch-bundle experiment): a bundle deploy with the
   exclusion not only skips uploading the manifests but also **deletes
   previously-uploaded copies** from the target tree, with or without local
   sync-snapshot state. The exclusion cannot protect a tree that only ever
   receives deploys from checkouts predating it — the 2026-08-05 paychex
   re-upload came from an old-lineage worktree whose `databricks.yml` had
   neither this exclusion nor the current workspace-host anchor. It still
   matters: jobs read the bundle files root (`files/`), and a deployment made
   before the per-release folder may still name it.
2. **The release folder never carries one.** `app_runtime_closure.py stage`
   copies only the App's runtime closure, which has no root Node manifest,
   and `app_release_source.py publish` refuses a staged folder that holds a
   root `package.json` or `package-lock.json` before any workspace call.
3. **`tools/databricks/converge_static_app_source.py` in release mode** runs
   inside `scripts/deploy.sh` immediately before each of the three
   activations (first install, the cutover, the Agent Evaluation redeploy).
   The source path must be exactly `<bundle root>/app-src/<release id>` for
   the deploy commit and the authenticated bundle identity; the manifest must
   be staged for that commit with that id, which must recompute from its
   files; an exact recursive listing must show the manifest's paths, each a
   FILE of the manifest's size; and the manifest must hold a non-empty
   `frontend/dist/index.html`. A root Node manifest in the folder is refused,
   never deleted: a release folder is immutable. Each root manifest name is
   also probed with `get_status`, so an empty directory under that name, which
   a recursive listing never shows, is refused too. Its files-root mode (no
   release arguments) is the governed way to clean a stale bundle files root
   by hand: it requires a non-empty `frontend/dist/index.html`, validates the
   exact bundle-files path shape and deletes nothing but the two root
   manifest files.

`sync.exclude` also keeps two geometry trees out of the App source (W5c,
audit `dataviz-01`), pinned by the same test:

- **`frontend/src/geo/**`**: the committed per-state ZCTA TopoJSON sources.
  The geometry reaches the App only as hashed `frontend/dist/assets/*.topo-*.json`
  files with `.br`/`.gz` siblings, emitted by the `npm --prefix frontend run
  build` step `scripts/deploy.sh` already runs (precompression included) and
  served same-origin by `backend/services/static_assets.py`. Nothing is
  fetched from the Census Bureau at deploy time or at runtime. Since the
  approved build (2026-10-26) that is 153 files and about 25 MiB of the
  upload (51 states, each with its `.br` and `.gz`). Their brotli 11 is
  about a minute of CPU, which `tools/precompress_assets.mjs` spreads over
  Node's thread pool (about 18 s wall on an 8-core machine).
- **`tools/geo/**`**: the operator-only geometry tool, with its own
  `package.json` and lock. It never runs in CI or deploy, and a nested npm
  manifest has no place in the App source tree.

Two operational facts to know before assembling any ad-hoc App source tree
(emergency recovery only — the command of record is the governed `deploy-dev.yml`
dispatch, which runs `./scripts/deploy.sh`):

- **Source lister budget.** The Apps deployment source snapshot has a hard
  60-second listing budget; the failure reads `Failed to snapshot source
  code ... list files timed out after 1m0s. Please consider reducing the
  number of files in your app.` The full repo tree (~2,400 files) can exceed
  it on slow workspaces, while ~1,400 files listed fine on the same
  workspace. Keep ad-hoc source trees well under that.
- **The frontend typecheck reaches outside `frontend/`.** Two src-co-located
  Vitest specs (`frontend/src/lib/campaignPrefill.test.ts`,
  `frontend/src/lib/opportunityScore.test.ts`) import
  `../../../tests/fixtures/campaign_prefill_segment_parity.json` and
  `../../../tests/fixtures/score_band_golden.json`, and `tsc -b` typechecks
  all of `src/**`. Any tree that performs (or accidentally triggers) a
  frontend build must therefore include those root `tests/fixtures/` files,
  or the build fails with TS2307.

## Governed per-release App source

`./scripts/deploy.sh` promotes the App from one immutable folder per release,
`<bundle root>/app-src/<release id>`, never from the bundle files root. Why:
the App compute downloads every file under the deployment's source path again
at start, at about 10 files/s, inside the 10-minute start window (measured
2026-10-28), and the bundle files root only grows (it held 6,018 files when a
deployment from it was recorded FAILED). A release folder holds only the App's
runtime closure, about 1,180 files.

`<bundle root>` is `/Workspace/Users/<deploy principal>/.bundle/mortgage-intelligence-platform/<leaf>`,
where `<leaf>` is the target. `app-src/` is a
sibling of `files/` that bundle sync never touches. The release id is
`<commit[:12]>-<digest[:12]>`, where the digest is the sha256 of one
`<path>\t<size>\t<sha256>\n` line per staged file in path order
(`tools/databricks/app_release_contract.py`), so the same bytes of the same
commit always name the same folder.

The flow, every step in `scripts/deploy.sh`:

1. **Preflight** (step 0): `python -m tools.databricks.app_runtime_closure
   check --ref <sha>` runs the git-side refusals and the tracked-file
   allowance with no dist and no workspace call, so a commit whose tracked
   files step 1 could not stage is refused (exit 2) before the lease. The
   dist-side refusals (the dist allowlist, the `.br` check, the total file
   budget) can only run at step 1, after the build; CI's built-dist test
   catches them before merge. A dry run reports `app runtime closure: <state>`
   and continues.
2. **Stage** (step 1, after the frontend build): `stage --ref <sha> --dist
   frontend/dist --out .databricks/app-release/source --manifest-out
   .databricks/app-release/manifest.json --require-brotli-check`, then
   `python -m tools.databricks.app_release_contract release-id` reads the id
   back from the manifest. The stage root is gitignored; only a real run
   clears it. `--require-brotli-check` refuses a host with no `node` instead
   of shipping gzip only.
3. **Publish** (step 5, after the Lakebase migration and the reviewed-grant
   proofs, before the first activation): `python -m
   tools.databricks.app_release_source publish` runs every local check first
   (the manifest, the bundle identity, the stage folder byte for byte, at most
   1,300 files and none over 10 MiB, `app.yaml`, `requirements.txt` and a
   non-empty `frontend/dist/index.html` present, no root Node manifest, no
   notebook-shaped file), then makes one create-only workspace AUTO import per
   file, 4 at a time by default (`--workers`). An object that already exists is
   reused only when its bytes are equal; other bytes are refused and nothing is
   overwritten. It then requires an exact recursive listing (each object a FILE
   of the manifest's size) and downloads every file to compare its sha256. It
   never deletes, so a re-run of the same release uploads nothing. A rate limit
   (HTTP 429) on an upload is retried, and only a rate limit
   (`tools/databricks/app_release_retry.py`: six attempts per upload,
   exponential backoff with jitter, the larger of the backoff and the
   Retry-After, cut to 30 s per wait, one `[app-release]` line per retry).
   The SDK never retries a workspace import itself, which is how a 429 at 8
   workers ended a 1,177-file publish on 2026-10-29. A retried upload is still
   create-only: if the throttled attempt landed, the retry meets the existing
   object and the byte compare above decides. A download or the listing is a
   GET, which the SDK retries itself on a 429 until its own
   `retry_timeout_seconds` (300 s by default) runs out; the tool then refuses
   it at once rather than spend a second budget. Either way a call still
   throttled is a refusal (exit 2) that names the object and the budget spent
   (the sixth upload attempt, or the SDK's timeout). Any other error is
   handled as before, on its first occurrence: a failed upload is still read
   back once, and counts as uploaded only if its bytes are there.
4. **Converge** before each of the three activations (release mode, see
   "App source contract"), then the Apps deployment names the release folder
   as its `source_code_path`. The platform copies the folder into the
   deployment's own `/src/` snapshot (`deployment_artifacts.source_code_path`),
   which is what the signed capture records.
5. **Prune** (the last step, best effort): see Retention below.

A dry run prints the stage, publish and prune commands without running them,
uses the release id `<sha12>-dry-run` and the source path
`/Workspace/dry-run/mortgage-intelligence-platform/app-src/<sha12>-dry-run`,
and makes no Databricks call.

Where a failure lands:

| Where it fails | What happens to the App |
|---|---|
| Preflight check | Exit 2 before the lease; the App is untouched. |
| Step-1 stage or release id | Like a failed frontend build: an existing App is stopped and quiesced (Lakebase access is not yet proven). CI's built-dist test makes a dist-side refusal unlikely. |
| Step-5 publish | Upgrade: signed blue is restored. First install: stopped and quiesced. At most one partial content-addressed folder is left (a rate limit that outlasts the retry budget leaves one too); the next run of the same bytes completes it when every object left there is absent or byte-equal, or a later green run prunes it. An object left with other bytes (an ambiguous upload) makes every publish of that release id refuse, because publish never overwrites, until a green run of another commit prunes the folder or an owner removes it. |
| Converge before an activation | Upgrade: signed blue is restored. First install: stopped and quiesced. |
| Prune | A warning only; the release stays live. |

**Retention.** `tools/databricks/app_release_prune.py` runs only after a green
capture (never on the rollback branch) and deletes, as the deploy identity
through the SDK, only DIRECTORY children of the exact `<bundle root>/app-src`
whose names are release ids. It keeps the current release (which must be the
App's active deployment source), the folder of the signed rollback record's
deployment, every folder any record in the App's rollback scope names (both
slots, every version from v5 to v7 and every provenance, a retired tombstone
included, read as raw JSON; a slot that cannot be read skips the run; M2-PR7),
the folder the App's `default_source_code_path` names,
the two newest other release folders the App's deployments reference (every
page of the deployment listing, newest first) and any folder a listed deployment's
`/src/` snapshot lies in. The rollback record's source and the App's default
source keep a folder by any path segment that is its release id, so a
trailing slash, a missing `/Workspace` prefix or another case of the
principal still keeps it. Before each delete it re-asserts the signed
deployment lease, re-reads the App (no pending deployment, the active one
still the current release, and neither it nor the App's default source naming
the folder) and re-reads the folder; after it, the folder must be gone, and a
delete it cannot reconcile stops the run. At most 20 folders go per run.
No delete starts once 10 minutes have passed since the run began. An
uncertainty in the reads before the first delete skips the run and deletes
nothing: no lease, a failed App or deployment read, another App id, a pending
deployment, an active deployment that is not the current release or is missing
from the listing, a listed deployment without a source or a `create_time`, an
unlisted rollback deployment, or a release root that cannot be listed. One the
re-check before a later delete finds (a lost lease, a new pending deployment,
the time budget spent) stops the run there and deletes nothing more; each
folder already deleted was proven unreferenced by its own re-check. `files/`, `/src/`
snapshots, names that are not release ids (such as a legacy
`app-src/<short sha>` folder) and plain files are never touched. Steady state
is at most three folders; a folder published but never deployed lives until
the next green run. This assumes one governed App per bundle root, which the
single `apps.mip_app` binding enforces. The deploy never asks an operator to
delete workspace folders.

**Rollback does not depend on these folders.** A signed record stores the
deployment's `/src/` snapshot and a restore redeploys that snapshot, so a
prune can never break a rollback.

**Transitional exposure.** On an install whose signed blue was deployed from
the bundle files root, the first upgrade after this change can still start
that blue (`apps start` before the cutover) and, if it rolls back, restore that
same large snapshot; either can miss the start window. The exposure ends after
the first green capture of a release folder.

**How to verify a live install.** `databricks apps get <app>`: the
`active_deployment.source_code_path` ends in `/app-src/<release id>` and its
`deployment_artifacts.source_code_path` is a `/src/` snapshot. Then, read-only:

```bash
python -m tools.databricks.app_release_source verify-deployment \
  --app-name <app> --manifest .databricks/app-release/manifest.json \
  --release-id <release id>
```

It requires that snapshot to hold exactly the manifest's files and compares
every sha256: the bytes the running App uses. It is not wired into
`scripts/deploy.sh`.

**Observed on the emergency path** (dev App, 2026-10-28). One deployment
from a slim folder named by the earlier emergency recipe (uploaded with
`databricks workspace import-dir` into a legacy `app-src/<short sha>`
folder, not published under a release id with publish's SDK upload) was
read back after it SUCCEEDED with `remote_files`, `compare_listing` and
`verify_hashes` (`tools/databricks/app_release_source.py`), against the
manifest `stage --manifest-out` writes for the same commit and dist (its
staged folder equals the uploaded one byte for byte, 1,177 files):

- the workspace list API returned `/Users/...` paths for every object;
- every object, both `.html` files included, was a FILE;
- after the activation the folder still held exactly the manifest's paths,
  sizes and sha256, so that activation wrote no file into its
  `source_code_path` folder (a recursive listing never shows a directory,
  so an added empty directory would not show);
- the deployment's `/src/` snapshot held exactly the manifest's files, every
  sha256 equal;
- the platform set `App.default_source_code_path` to that deployment's
  source folder; prune keeps the folder that path names when its name is a
  release id.

**Not yet observed live** (each fails closed): that publish's SDK upload
with format AUTO keeps every closure file type, `.html` included, as a FILE
(the observation above used the CLI's `import-dir`; publish's listing
refuses anything else); that the second and third activations of one run
also write nothing into the release folder (if one did, the exact-listing
converge before the next would refuse the deploy: signed blue restored on
an upgrade, a first install stopped and quiesced); that the step-5 bundle
summary's `resources.apps.mip_app.source_code_path` equals
`workspace.file_path` on the CLI the run uses (step 5 requires it); and how
`default_source_code_path` behaves on `apps start` and in a source-free App
resource payload (which may not carry it). The first governed
`deploy-dev.yml` run on the CI install is the proof.

### A prebuilt release (M1-PR7)

`./scripts/deploy.sh --prebuilt-release <dir>` takes step 1's two outputs
from a folder built elsewhere instead of building them: `frontend/dist` and
the staged closure with its manifest. `deploy-dev.yml` uses it so that its
`deploy` job, the one that holds an ID token, runs no Node and no npm; its
`build` job, which holds none, makes the folder. The layout and the checks are
`tools/databricks/prebuilt_release.py`'s:

- `SHA256SUMS.json` lists every file of `dist/`, `app-release/` (the stage's
  `source/` and `manifest.json`) and `wheelhouse/` with its sha256 and size,
  plus the commit and the sha256 of the `requirements-deploy.hashes.txt` the
  wheelhouse came from. The build job prints the manifest's own sha256.
- `MIP_PREBUILT_RELEASE_SHA256` must be that sha256 (a one-run export, never
  `.env.local`). Before any Databricks call, in a dry run too, deploy.sh
  re-hashes every file and refuses a missing, extra, resized or changed one,
  a symlink or other non-regular file, a listed path that is not a plain
  relative path, an empty part, another commit or another requirements file.
- The manifest proves transport only: the build job runs npm, and both
  manifests are its own word. So every check also re-derives the release's
  tracked half from the deploying checkout's own commit, with the stage's
  git-side plan and refusals (`app_runtime_closure.tracked_blobs`): the files
  `app-release/source` holds outside `frontend/dist` must be exactly the
  closure files git tracks at the commit, each with its blob's size and
  sha256; `app-release/source/frontend/dist` must be `dist/`;
  `app-release/manifest.json` must be this commit's release manifest and list
  exactly the staged files; and the staged dist must pass the stdlib dist
  review again, against the commit's `frontend/public`. A `.br`'s decode check
  needs node, which the deploy job does not run, so those are the build job's
  check alone, as every byte of the built JavaScript is the build job's. Only
  the dist's bytes come from the build job.
- Step 1 re-hashes it again, replaces `frontend/dist` and
  `.databricks/app-release` whole with copies, re-hashes the copies and reads
  the release id from the staged manifest as usual (`app_release_contract
  release-id --expected-commit`). No npm runs, and the frontend dependency
  check is skipped.
- Every tenant build input vite bakes into the dist refuses a prebuilt
  release: a configured lender mark (`MIP_LENDER_MARK_FILE`, a local file the
  build job cannot see) and a `MIP_DEFAULT_THEME` or `MIP_DEFAULT_ACCENT`
  other than the product default (`dark`, `bright`), which the build job
  never sets. The first re-hash reads all three as the preflight does (shell,
  then `.env.local`) and refuses before any Databricks call, so a live run
  never reaches the lease, the rollback scope or the runtime secrets; step 1
  checks the preflight's resolved values again. Such a release builds
  locally.

## Emergency recovery: a slim per-release App source folder

**Emergency recovery only. The command of record is the governed
`deploy-dev.yml` dispatch, whose `./scripts/deploy.sh -t client` run stages,
publishes and promotes a per-release folder itself (previous section).** A hand-made slim folder is for the case that path cannot
run at all, which today includes every ship to the live install: it predates
the client target and is reached only through the recipe below until it is
adopted (M2-PR6). A deploy from it bypasses three governed steps, and `stage`
replaces only part of one of them:

- **Signed promotion.** None of `./scripts/deploy.sh`'s signed workspace lease
  or signed blue proof steps run, so its rollback path holds no signed record
  of the slim deployment.
- **`tools/databricks/converge_static_app_source.py`.** The governed
  release-mode check of the uploaded folder against its manifest runs only if
  the operator runs it by hand before the payload deploy.
- **The in-process frontend build and its postbuild gate**
  (`tools/postbuild_artifacts.mjs`). `stage` takes a dist built elsewhere. It
  applies its own allowlist to that dist (below), but it cannot prove the
  JavaScript was built from `--ref`; only the `frontend/public` files are
  compared byte for byte.

Why it exists: the App compute downloads every file under the deployment's
source path again at start, at about 10 files/s, inside the 10-minute start
window (measured 2026-10-28). The bundle files root (`<bundle root>/files`) only
grows, because `workspace import-dir` and piecewise uploads never delete. It
held 6,018 files when a deployment from it was recorded FAILED. A folder with
only the App's runtime closure is about 1,200 files (481 tracked plus 694
`frontend/dist` files on `ab6235e0`, the 209 `.br` files included once `stage`
has checked them), or about 970 when no `node` is on `PATH` to check them and
`stage` leaves them out.

`tools/databricks/app_runtime_closure.py` is the single source of truth for
that closure. It holds the upload set (`app.yaml`, `requirements.txt`,
`requirements.in`, `uv.lock`, `backend/`, `jobs/`, `lakebase/` and a prebuilt
`frontend/dist/`) and the files the read nets find the backend reading
outside `backend/`. `python -m tools.databricks.app_runtime_closure list`
prints each entry with its reader. Two entries are optional and are never in git:

- `genie/space_id.txt`: the space id comes from the environment.
- `sql/_rendered/...`: `backend/services/lifecycle_sync.py` falls back to
  `sql/transformations/gold_funnel_snapshot_daily.sql` and substitutes the
  catalog itself (it resolves both against its source root, not the working
  directory). A slim folder always takes this fallback. That file has no
  feed toggle and the fallback's `mip.`-to-catalog swap matches the
  renderer's, so the result is the same today; the difference predates the
  slim folder.

Two static scans of `backend/` and the first-party modules it imports guard
the closure. They are a tripwire for accidental reads outside `backend/`, not a
proof that none exists. The flow scan (`tests/fixtures/app_runtime_reads.py`)
follows path values rooted in a bare `__file__` or the working directory, and
strings passed into first-party functions it can name, to the code that reads
them. Anything it cannot follow (a library call, an f-string, a function
returned to callers it cannot see) counts as a read of the whole subtree under
the value, so an escaping repo root can never be covered. The roots it is
known not to follow (`backend.__file__`, `sys.modules[__name__].__file__`,
`globals()["__file__"]` and other lookups by name, `__spec__`, any
`co_filename`, a frame from `sys._getframe`, `inspect.currentframe`,
`inspect.stack` or the other frame helpers the scan lists, the `origin` or
`submodule_search_locations` read on a `find_spec` call or on a name bound to
one, `inspect.getfile`, `sys.argv`, `sys.path`, `importlib.resources.files`,
`pkgutil.get_data`) are reported as unhandled wherever they appear in those
forms; the same roots reached another way are residuals (below). The literal
sweep (`tests/fixtures/app_runtime_literals.py`) follows nothing. It flags a string literal that names a repo path outside
`backend/` by its own text (a `/` path such as `"docs/a.md"`, or a dotted
string naming a tracked first-party module such as `"tools.x"`), or by the
path call, `/` join or environment default around it. A bare name such as
`"docs"` passed into a function is not flagged by the sweep; only the flow
scan follows it. `tests/unit/test_app_runtime_closure_reads.py` fails when
either finds a read the closure does not cover (a literal may instead be
reviewed in that test, with a reason), or when a closure entry is no longer
read, and `tests/unit/test_app_runtime_read_shapes.py` pins the shapes each
net catches.

The scans are static, and their residual list is known, not complete. These
are the shapes known to escape both nets (`RESIDUALS` in
`tests/unit/test_app_runtime_read_residuals.py` pins each one, so a net that
learns one fails loudly until it moves to the caught shapes):

- a first-party callee the flow scan cannot name (a method reached through an
  object, a function passed as a value), which is assumed to read only under
  the path it receives;
- a bare top-level name with no `/` handed to a library loader
  (`StaticFiles(directory="docs")`, `jinja2.FileSystemLoader("docs")`) or to
  any other call that is neither a path constructor nor a first-party function
  the scan can name (bare names are not swept: the backend uses `"lakebase"`
  and `"genie"` as dependency labels hundreds of times);
- a bare top-level name held in a container (`{"k": "docs"}["k"]`,
  `["docs"][0]`) or in a named tuple that a loop walks (a loop over a literal
  tuple or list is followed);
- a working-directory path built with `str.format`, `%` or `str.join` from
  parts that hold no `/` (`"{}/{}".format("docs", name)`,
  `"/".join(["docs", name])`, `os.sep.join(...)`); a `__file__` root
  formatted into a string still counts as a read of the whole repository;
- a starred argument to a path constructor or `os.path.join`
  (`os.path.join(*["docs", "x.md"])`, `Path(*parts)`);
- a path read from an environment variable with no default;
- `getattr`, `exec`, `eval` and a module named by a computed string;
- a module or frame file reached through a helper the scan does not list
  (`traceback.extract_tb(...)[0].filename`, `logging.getLogger().findCaller()`,
  `pkgutil.get_loader(...).get_filename()`), or a `find_spec` result whose
  `origin` is read through an alias, a walrus, an attribute (`self.spec`) or
  a parameter;
- the working directory reached without `os.getcwd`, `Path.cwd` or a string:
  `os.getcwdb()`, and `os.listdir()` or `os.scandir()` with no argument;
- a `nonlocal` name bound two or more scopes up;
- a folder name the scan cannot read as a string: keywords unpacked with
  `**{...}` into a first-party function, a computed string (`"DOCS".lower()`),
  a bytes path (`open(b"docs/x.md")`) or a library keyword
  (`subprocess.run(..., cwd=DOCS)`).

No backend read uses those shapes today. An AST pass over `backend/` and
`jobs/sync_lifecycle_state.py` on 2026-10-28 found no starred argument,
container subscript, `str.format` or `%` path, environment read without a
default, library loader, `exec`, `eval` or loop over a named tuple feeding a
path call; its two `" / ".join` calls build display labels, its one computed
`find_spec(name)` is a presence check for `databricks.sdk`, and none of its
dynamic `getattr` calls is passed to a path call. A grep the same day found
no `extract_tb`, `findCaller`, `get_loader`, `getcwdb`, `nonlocal`,
argument-less `listdir` or `scandir`, `.origin`, `cwd=` or bytes path, and its
`**{...}` uses build dicts, model fields and scoring arguments, never a path.
The readers that matter degrade silently when their file is missing (`county_names` returns `{}`, the
Genie space-id fallback returns `None`), so a new App read outside `backend/`
must use the shapes the scans follow, a module-level constant rooted in
`Path(__file__)` plus literal segments
(`ROOT = Path(__file__).resolve().parents[2]`, then
`ROOT / "sql" / "x.sql"`), and must be added to `READS` in
`tools/databricks/app_runtime_closure.py`.

`tests/unit/test_app_runtime_closure_stage.py` holds the closure to its file
budget: 1,300 staged files, about 130 s of download, of which at most 600 are
tracked and the rest is the dist allowance.

```bash
npm --prefix frontend run build    # or reuse a dist built from the same commit
python -m tools.databricks.app_runtime_closure stage \
  --ref <sha> --dist frontend/dist --out <new empty directory>
```

`stage` builds the folder from `git archive <sha>`, so no untracked or
uncommitted repo file ships, and adds the given dist. The dist may hold only
what a governed build emits (`tools/databricks/app_runtime_dist.py`):
`index.html`, `index.home.html`, one `branding/lender-mark.png` or `.webp`,
exactly the `frontend/public` files tracked at `<sha>`, byte for byte, and
under `assets/` only Vite's flat hashed names, `<name>-<8-character
hash>.<ext>` with `<ext>` one of js, css, json, webp, png, svg, woff2, woff or
wasm (the naming follows `frontend/vite.config.ts`, and
`tests/unit/test_app_runtime_closure_dist.py` fails when that config changes
it). Vite copies `public/` verbatim, so an untracked, edited or missing public
file is refused. The `/assets` route serves a `.br` or `.gz` sibling in place
of the asset to a client that accepts the encoding, so a compressed file may
carry exactly one `.gz` or `.br` suffix, each `.gz` must decompress to exactly
its source's bytes and carry a bare gzip header (FLG 0: no file name,
comment, extra field or header CRC, as Node's `zlib.gzip` in
`tools/precompress_assets.mjs` writes it), and each `.br` must decode to
exactly its source's bytes too, with no bytes after the stream. `stage`
checks each `.br` with `node` on `PATH`
(`tools/databricks/app_runtime_brotli.py`: `zlib.brotliDecompressSync` with
the output capped one byte past the source and every input byte consumed,
the paths passed as arguments, each process time-bound), ships it only when
it matches, and refuses the whole dist when one does not or when the checker
itself fails. It never uses the `brotli` Python module, even when it is
installed, because that decoder has no output cap. Without `node`, `stage`
leaves every `.br` out, says how many, and the App serves the checked `.gz`.
A `node` too old for the check (its `node:` imports need Node 16, and the
frontend build asks for 22.22 or later) refuses every `.br` rather
than leaving it out; run `stage` with the `node` the build used.
`backend/main.py` serves any real file under `frontend/dist`, so anywhere in
the dist a dot segment (`.env`, `.vite/`, an empty `.git/objects` or
`assets/.cache` directory), a `*.map` in any letter case,
`build-manifest.json`, `build-modules.json`, Python junk, a symlink, or a JS,
CSS or HTML file with a `sourceMappingURL` comment anywhere in it is refused
too.

The `assets/` rule checks name shape, compression integrity and gzip header
flags only, never what an asset contains: a hashed `.js` built from another
commit, or edited after the build, passes it. The dist must therefore come
from the governed build of the same ref, `npm --prefix frontend run build` run
on a checkout of `<sha>` (it also runs the `tools/postbuild_artifacts.mjs`
gate); only the `frontend/public` files are compared with `<sha>` byte for
byte.

Before it writes anything, `stage` also refuses a symlink, a submodule or a
hidden path (a segment starting with `.`, such as `backend/.env.local`)
tracked at `<sha>`, a missing required entry, a `requirements.txt` whose
deps-digest line does not match `uv.lock` and `requirements.in` at `<sha>`
(next section), an `--out` that is not empty, and an `--out` inside `--dist`
or around it (compared by device and inode, so a symlink, `..`, another letter
case on a case-insensitive volume or a macOS firmlink cannot hide it). It
then copies the dist into a temporary sibling of `--out` without following symlinks, reviews the
copy and ships the copy, so a dist that changes after the review cannot
change what ships. It refuses more than 600 tracked files or 1,300 in all,
adds the tracked files and renames the folder into place, so a refusal or a
failure partway leaves `--out` as it was. It prints the file count and exits
2 on any refusal. It makes no Databricks call.

### The emergency recipe, command by command (legacy dev root)

Until the live install is adopted into the client target's deployer root
(M2-PR6), every App ship to the live App goes through these commands, and only
these. They address the **legacy root**
`/Workspace/Users/<owner>/.bundle/mortgage-intelligence-platform/dev`, where
`<owner>` is the user who owns the live install's bundle state. The `dev`
target left `databricks.yml`, so the legacy root is named by explicit flags:
`--expected-target dev --expected-root-leaf dev` on every tool that checks
the bundle identity. Those tools check the flags against the path shape
`/Workspace/Users/<principal>/.bundle/mortgage-intelligence-platform/<leaf>`
(`tools/databricks/app_release_contract.py`), never against the bundle's
target list. `tests/unit/test_docs_recipe_commands.py` parses every command in
the block below and runs it offline (an in-memory workspace for the publish
and the converge), so a change that stops one parsing or moves the legacy
root fails CI.

```bash
python -m tools.databricks.app_runtime_closure stage --ref <sha> \
  --dist frontend/dist --out <stage-dir> \
  --manifest-out <manifest> --require-brotli-check
python -m tools.databricks.app_release_contract release-id \
  --manifest <manifest> --expected-commit <sha>
DATABRICKS_CONFIG_PROFILE=<profile> python -m tools.databricks.app_release_source publish \
  --release-id <release-id> --stage-dir <stage-dir> --manifest <manifest> \
  --files-root /Workspace/Users/<owner>/.bundle/mortgage-intelligence-platform/dev/files \
  --expected-commit <sha> --expected-principal <owner> \
  --expected-target dev --expected-root-leaf dev
DATABRICKS_CONFIG_PROFILE=<profile> python -m tools.databricks.converge_static_app_source \
  --source-code-path /Workspace/Users/<owner>/.bundle/mortgage-intelligence-platform/dev/app-src/<release-id> \
  --release-id <release-id> --release-manifest <manifest> --expected-git-sha <sha> \
  --expected-principal <owner> --expected-target dev --expected-root-leaf dev
MIP_GIT_SHA=<sha> MIP_SALESFORCE_BINDINGS=<salesforce-bindings> python -m tools.databricks.app_deploy_payload \
  --source-code-path /Workspace/Users/<owner>/.bundle/mortgage-intelligence-platform/dev/app-src/<release-id> \
  --install-class demo --app-env sandbox --catalog mip --schema gold \
  --lakebase-instance <lakebase-instance> > <payload>
databricks apps deploy <app-name> --json @<payload> -p <profile>
```

`<salesforce-bindings>` is the profile's `MIP_SALESFORCE_BINDINGS` for the
live App: `off` unless that App already binds the three Salesforce secrets,
then `on`. The recipe never changes the App's bindings, so it must match
them. The payload builder refuses an unset switch when every connector
input is set (`app_deploy_payload: refused:`, exit 2, no payload), so the
recipe always names it.

**The freeze marker (M2-PR7).** During the cutover the owner freezes this
recipe with `python -m tools.databricks.app_deployment_lease freeze --app-name
<app-name> --until <epoch>` (with the deploy's proof-signing key exported),
and lifts it with `thaw --marker-id <id>`; both write a signed, create-only
marker in the lease root and nothing is deleted. A freeze is signed before
the root is touched, so a refused one (no signing key, a bad `--until`)
changes nothing. Neither ever sets a permission: a missing lease root is
created bare (the first governed lease converges its ACL) and judged at once
like a root with no lease record, and an existing one is checked read-only,
exactly protected for the App lease's own holder and writer or, while the
App has no lease record, held by no accessor but admins and the freezing
identity (`freeze_root_unprotected` otherwise; a refusal over a root the
freeze just created leaves that root bare and empty, never deleted), so a
freeze from the owner's shell never takes the root's MANAGE from the
deployer and nobody else can delete its marker, the first one included.
Whether a new root's inherited ACL passes in a given workspace is not yet
probed live: if it carries any accessor but admins and the freezing
identity, the first freeze refuses instead of writing. `app_release_source publish`
reads every marker after its local checks and before any upload, and
refuses with exit 2 while one is frozen. A marker reads as thawed or
expired only when its signature verifies, so a shell
without the deploy's public verify key (`MIP_AI_GATEWAY_PROOF_VERIFY_KEY`)
reads every marker as frozen: export that key to publish after a thaw. To
publish one release folder while frozen anyway, add `--override-freeze
<marker id> --override-freeze-ack <sha256>`, the `override_freeze` digest the
refusal prints for that marker and that exact folder; it lifts no other
marker and carries to no other folder. `./scripts/deploy.sh`'s own publish
passes `--held-lease-app-name` and `--held-lease-id`, and its proven lease
exempts that App's own markers only: the governed run under the signed lease
is the coordinated path the freeze protects, and every other App's marker is
still judged, so a lease taken on a decoy App lifts nothing. A lease root that
does not exist changes nothing. Once it exists, though (a governed lease
creates it, or a freeze creates it bare until the first lease converges it),
the publisher lists it on every publish, so the identity that runs this
recipe must be able to list it (a workspace admin can: admins manage it), or
the publish refuses (`freeze_unreadable`) even with no marker; and since
markers are never deleted, after any freeze every later publish needs
`MIP_AI_GATEWAY_PROOF_VERIFY_KEY` exported to read an expired or thawed marker
as such. The freeze blocks only this publisher, not an old session's own
deploy driver.

Publish, converge and deploy are the three commands that call the workspace,
and each names the live workspace's CLI profile, the same `<profile>` in all
three. Without it the two Python tools build their client from the ambient
environment or the `DEFAULT` profile, which may name another workspace with
the same `/Workspace/Users` path: the publish would land there and the deploy
would then fail on the live one. Run them in a shell with no
`DATABRICKS_HOST` or `DATABRICKS_TOKEN` exported, because the SDK lets those
override a profile.

1. **Stage** the closure at the commit and read its release id back from the
   manifest. `<stage-dir>` is a new empty directory and `<manifest>` a new
   file beside it.
2. **Publish** the folder under that governed name in the legacy root,
   create-only and byte-verified, never with `workspace import-dir` and never
   into a folder that is already used: a folder under the governed name whose
   bytes differ makes every later publish of that release id refuse. A publish
   that stops part way, for example on a rate limit (HTTP 429) that outlasts
   the retry budget, leaves a partial folder: run the same command again,
   which byte-compares what is already there and uploads only what is
   missing.
3. **Converge**: the release-mode check `scripts/deploy.sh` runs before each
   activation. It refuses unless the folder is exactly the release folder of
   that commit in the legacy root and still holds exactly the manifest's
   files.
4. **Payload**: the deployment JSON for that folder. Run it from the checkout
   whose `.env.local` holds the live install's operator values (lender, admin
   allowlist and the rest); `--install-class demo` keeps the live install's demo
   payload policy (the builder takes no target name since M1-PR3). The
   payload leaves the campaign-treatment runtime off, so health reports
   `campaign_treatment_runtime: disabled_baseline_deploy`, the honest state of
   a deployment made outside signed promotion.
5. **Deploy** from outside the checkout, with the CLI profile of the live
   workspace (`-p <profile>`), so no bundle context can choose the host.

A governed `./scripts/deploy.sh -t client` run never addresses the legacy
root: it publishes under its own deployer root
(`/Workspace/Users/<deployer>/.bundle/mortgage-intelligence-platform/<client>`)
and its prune lists only that root's `app-src/`. Folders published by this
recipe, and legacy `app-src/<short sha>` folders (which are not release ids),
stay owner cleanup until the adoption.

**The freeze during the first-client cutover.** Cutover step 1 freezes this
recipe ([docs/clients/first-client-cutover.md](clients/first-client-cutover.md)).
The marker command above needs the deploy's proof-signing key pair in the
shell, and no shell holds it: `python -m tools.clients.github_environment`
generates a client's signing key in memory and pipes it into a write-only
environment secret, and no workflow step runs `freeze`. So on `main` the
freeze is a message to every peer session plus the owner's rule, not a
marker; a CI-run freeze and thaw is a follow-up. The governed run is never
blocked by a marker of its own App (its held lease exempts it, above).

### Owner break-glass: a raw `apps start`

Owner-only, with the owner's own CLI profile for the client's workspace
(`<owner-profile>`; never an agent's decision). `apps start` creates a new
SNAPSHOT deployment of the App's current `default_source_code_path` bytes,
with the newest deployment's env-name set (spike S7); it never deploys other
source. Read the window journal first, read-only (codes and booleans only):

```bash
DATABRICKS_CONFIG_PROFILE=<owner-profile> python -m tools.databricks.quiesce_window status --unverified --app-name <app-name>
databricks apps start <app-name> -p <owner-profile>
```

- **A stopped App no signed record governs** (the journal reads `absent`).
  This is cutover step 10A: adopt-live signs only a running App, so the owner
  starts it right before the adopt phase, which then signs the deployment the
  start created (its `tree_drift` precondition checks that folder against its
  snapshot).
- **An open quiesce window** (the journal in any state but `absent` or
  `closed`) whose dead-man has not restarted blue. Prefer the governed
  path: the dead-man starts blue at the job's deadline, within its start
  budget, and the next governed run re-pins that start (`repin
  --from-deadman`). Break glass only when the outage cannot wait. The raw start
  serves the App again, but it is not a governed restore: no proof ran, the
  window's journal stays open and no dead-man restart is recorded, so every
  later governed run refuses `window_journal_open` before its lease. On `main`
  no tool re-pins an owner start or closes that journal, so "the next run
  re-pins" is not yet true for this case: the governed path stays blocked
  until a reviewed follow-up adds that re-pin. Record the start (when, which
  deployment, why) for it. A dead-man still armed then finds the App no longer
  its stopped blue at its deadline, records `deadman_skipped` and starts
  nothing.
- **Never break glass on a contract window** (the window plan's
  `blue_restorable` false, the dead-man's `deadman_left_stopped`): blue's code
  would serve the migrated schema. That outage is the acknowledged one; escalate
  instead.
- **An adopted record with no restore payload that no longer matches the App**
  (`no_restore_payload`, `tier_a_refused`, or the pre-stop clause's
  `rollback_record_drift`, whose remedies name the owner break-glass): a raw
  start makes a new deployment, which is still not the record's, so it
  restores service, never the record. The record's governed exit is an
  adopt-live supersede onto the live deployment (`--phase adopt` with
  `MIP_ADOPT_LIVE_SUPERSEDE` and its `MIP_ACK_SUPERSEDE` ack). Through
  `deploy-dev.yml` both ride `operation_acks`: the selector
  `supersedes_deployment=<sha256>` (the drift remedy prints it, a digest of the
  record's deployment id) and `supersede=<sha256>` beside the bind's
  `adopt_plan=<sha256>`, after a read-only dispatch with the selector alone
  printed both digests
  ([docs/clients/first-client-cutover.md](clients/first-client-cutover.md),
  10B, "Supersede").

## App Python dependencies: the deps-digest line

What was observed: the 2026-10-28 deploy that carried PR #274
(databricks-sql-connector 4.4.0 to 4.8.0, oauthlib 3.3.1 to 4.0.0) logged
`Requirements have not changed. Skipping installation.` in its Apps build and
kept the Python environment the App compute already had, which persists
across deployments. `uv.lock` had changed; `requirements.txt` had not. Until
2026-10-28 it was only the `-c uv.lock` and `-r requirements.in` include
lines, byte-identical since 2026-05-17.

Confirmed on 2026-10-28: the build decides whether to reinstall by
comparing `requirements.txt` with the previous deployment's copy and does not
follow the file's includes. The first deploy whose only change to the file
was the digest line below reinstalled, upgrading 21 packages (oauthlib 3.3.1
to 4.0.0, databricks-sql-connector 4.4.0 to 4.8.0, certifi, mlflow,
OpenTelemetry and the rest). Before it, no lock refresh had reached a running
App since 2026-05-17.

`requirements.txt` therefore starts with a generated comment carrying the
sha256 of both included files:

```text
# deps-digest: uv.lock sha256=<64 hex> requirements.in sha256=<64 hex>
-c uv.lock
-r requirements.in
```

Every change to `uv.lock` or `requirements.in` ends with
`python tools/requirements_digest.py --write`, which rewrites only that line.
Both files are hashed with CRLF folded to LF, and `.gitattributes` checks all
three out with `eol=lf`, so a `core.autocrlf` checkout computes the same
digest. Four gates run `--check`'s comparison, because the repo has no
required CI checks:

- `tests/unit/test_requirements_digest.py` in CI, which fails with the remedy
  when the line is stale or missing;
- `./scripts/deploy.sh` step 0, right after the interpreter pin check: a dry
  run reports `requirements digest: stale` and continues, a real run refuses
  with exit 2 before any workspace call;
- `app_runtime_closure stage`, which recomputes the line from the blobs at
  `--ref` and refuses a ref whose `requirements.txt` digest line is stale or
  missing;
- `app_runtime_closure check --ref`, the same blob-side comparison in
  `./scripts/deploy.sh` step 0, before the lease: a dry run reports
  `app runtime closure: <state>`, a real run refuses with exit 2.

pip, pip-audit and `tools/python_deps_check.py` all skip comment lines, so
they read the file as before.

**Verify the reinstall on every deploy that changes the digest.** The App's
deployment build log must show pip output (`Collecting ...`, `Successfully
installed ...`) instead of `Requirements have not changed. Skipping
installation.` The App's own boot log then carries one `dependency_lock_check`
event (logger `mip-runtime`, emitted by
`backend/services/dependency_lock_check.py` from the FastAPI lifespan). It
reports `outcome` `ok`, `drift` or `lock_unreadable`, with `pins_total`,
`pins_checked`, `not_applicable` and `unpinned` and the package names behind
those two counts (`not_applicable_names`, `unpinned_names`: sorted, at most 50
each, so a count above the list's length means the list was cut),
`drifted_count` and a `drifted` list of `{name, locked, installed}`
(`installed` is null for a missing package). It
also reports `python` and the `lock_python` that `uv.lock` was compiled for (a
mismatch is `drift`, as it is for `tools/python_deps_check.py`), and
`requirements_digest`: `current`, `stale`, `missing` or `unreadable`. The
`remedy` follows the cause. A stale or missing digest can leave the bytes of
`requirements.txt` unchanged, which the build treats as no reason to
reinstall, so restamp, commit and redeploy. A current digest with
drift means the build skipped or failed the reinstall anyway, so restamping
changes nothing: read the deployment build log and apply the fallback below.
It reads `uv.lock`, `requirements.in` and `requirements.txt` from the App
source root and walks the installed closure of `requirements.in` the way
`tools/python_deps_check.py` does, so a pin that this interpreter never
installs is not applicable rather than drift. It reads
local files only and makes no network call. It never raises, so it cannot
stop startup, and the lifespan runs it in a worker thread, off the event loop
(about 0.3 s on a local interpreter). Run against a local interpreter built
before PR #274, it reported `drift` for exactly databricks-sql-connector
(locked 4.8.0, installed 4.4.0) and oauthlib (locked 4.0.0, installed 3.3.1),
the same two packages that `tools/python_deps_check.py` named, with
`requirements_digest` `current`. On the first deploy that carried it
(2026-10-28) it reported `ok`: 137 pins checked, none drifted,
`requirements_digest` `current`. It also reported `unpinned` 1, which the next
section names and closes.

A comment-only change defeats the skip: the 2026-10-28 deploy that added
only the digest line reinstalled. If a future build stops honouring comment
changes (for example because it compares the file with comments stripped),
the fallback is an include line
whose path carries the digest, so the parsed file changes even with comments
removed: commit the lock under a digest-named path and point the constraint
at it (`-c locks/uv-<digest>.lock`). That line names no package. A
marker-false requirement such as `mip-deps-digest==0+<hex>; python_version <
"3"` would also change the parsed file, but it puts a package name that is
not on PyPI into `requirements.txt`, which invites name squatting and
dependency confusion and may be looked up by `pip-audit` or the deps report,
so it is not the fallback. The `-c` carrier is not implemented, because it
is not yet shown to be needed and it touches every reader:
`backend/services/dependency_lock_check.py` reads `uv.lock` by name,
`tests/unit/test_supply_chain_licenses.py` asserts `-c uv.lock`, the digest
tool would have to write the lock copy and the line, and superseded
digest-named locks would need pruning. `tools/python_deps_check.py` already
follows whatever path the `-c` line names.

### Runtime only: what the App installs and what CI adds

Since 2026-10-28 `requirements.in` lists only what the App runs. The test,
lint and type-check tools (pytest, pytest-cov, pytest-xdist, ruff, mypy,
hypothesis) live in `requirements-dev.in`, pinned by `requirements-dev.lock`:

| File | Read by | Holds |
| --- | --- | --- |
| `requirements.txt` | the Apps build, the deploy interpreter, `pip-audit` (through the dev file) | digest line, `-c uv.lock`, `-r requirements.in` |
| `requirements.in` + `uv.lock` | the two above, `dependency_lock_check`, `python_deps_check` | the App's direct pins and their whole closure |
| `requirements-dev.in` + `requirements-dev.lock` | CI and local development only | the dev tools and the part of their closure the App does not need |
| `requirements-dev.txt` | `pip install -r requirements-dev.txt` in CI and locally | `-r requirements.txt`, `-c requirements-dev.lock`, `-r requirements-dev.in` |

`requirements-dev.lock` is compiled with `uv.lock` as a constraint, so a
package that both closures need (packaging, typing-extensions, attrs) carries
the App's pin in both files. pip refuses two different constraints for one
package, so `tests/unit/test_requirements_dev_split.py` fails when a narrow
`uv.lock` bump of a shared package has not been carried into the dev lock.
Refresh either lock the same narrow way:

```bash
uv pip compile requirements.in --python-version 3.11 \
  --python-platform x86_64-manylinux_2_28 --format requirements.txt \
  --output-file uv.lock --upgrade-package <pkg>
uv pip compile requirements-dev.in -c uv.lock --python-version 3.11 \
  --python-platform x86_64-manylinux_2_28 --format requirements.txt \
  --output-file requirements-dev.lock --upgrade-package <pkg>
python tools/requirements_digest.py --write   # only uv.lock and requirements.in are digested
```

**The deploy job's hashed install (M1-PR7).** `requirements-deploy.hashes.txt`
is `uv.lock` again, with every distribution's sha256: the App runtime closure
only, never the dev tools. `deploy-dev.yml`'s `build` job (no ID token)
downloads its wheelhouse from it, and the `deploy` job installs from that
wheelhouse alone (`pip install --no-index --find-links <wheelhouse>
--require-hashes`), so no package index is reached by a job that can request
an ID token. Every `uv.lock` refresh regenerates it in the same change
(`tests/unit/test_requirements_deploy_hashes.py` fails until it does):

```bash
uv pip compile requirements.in -c uv.lock --python-version 3.11 \
  --python-platform x86_64-manylinux_2_28 --generate-hashes --format requirements.txt \
  --output-file requirements-deploy.hashes.txt
```

Both locks are compiled for the App's platform (next section). The dev
closure has no machine-keyed dependency today: compiled for
`aarch64-apple-darwin` and for `x86_64-manylinux_2_28`, it pins the same 20
packages at the same versions.

A dev-only bump never touches `uv.lock` or `requirements.in`, so it leaves the
digest line alone and never makes the App reinstall. Every workflow job that
runs pytest, ruff or mypy installs `requirements-dev.txt` (the same test
pins this), and CI's `pip-audit -r requirements-dev.txt --strict` audits both
sets in one run.

The same change removed databricks-sql-connector 4.8.0, which nothing here
imports, together with the floors that existed only for its closure (oauthlib,
thrift, PyJWT). Warehouse reads go through the Statement Execution REST API
with stdlib `urllib` (`backend/services/databricks_sql.py`). The proof is
an offline import of every module under `backend/` (435, including
`backend.main` and `backend.runtime`), `jobs/` (18) and `tools/` (272), run in
a venv built from the old `requirements.txt` with an import hook installed.
None of them loaded or even tried to import `databricks.sql`, oauthlib,
thrift, lz4, pybreaker, openpyxl or jwt. All of them still import in a venv
built from the new runtime-only `requirements.txt`.
`docs/audits/supply-chain-audit.md` keeps the full record.

**A removed requirement stays installed in the running App.** The Apps build
runs `pip install -r requirements.txt` into the environment that persists
across deployments, and pip never uninstalls what a requirements file stops
naming. The first deploy after this change therefore shows a pip run (the
digest changed) in which this change moves no runtime version. The
connector, its closure and the old test tools stay on disk, unimported. `dependency_lock_check` walks only what
`requirements.in` reaches, so it reports `ok` with them present. The App
image is runtime-only for every package it resolves from the files here,
but the leftover wheels only go away when the App's environment is
recreated.

## App Python dependencies: the lock's platform

What was observed: the 2026-10-28 paychex deploy of main 32cc4384 logged
`dependency_lock_check` with `outcome` `ok`, `pins_total` 138, `pins_checked`
137, `not_applicable` 1 and `unpinned` 1, and the event did not yet name
either package. Replaying the walk offline reproduces all four numbers. Install
the deployed `requirements.txt` for Linux x86_64 into a directory
(`uv pip install --target <dir> --python-platform x86_64-manylinux_2_28
--python-version 3.11 -r requirements.txt`), then run `compute_drift` over
that directory's package metadata with Linux x86_64 markers. The
not-applicable entry was tomli, which coverage required only below Python
3.11.0a6 and which has lived in `requirements-dev.lock` since the split above.
The unpinned package is greenlet. sqlalchemy (via mlflow) requires greenlet only where
`platform_machine` is `x86_64`, `aarch64`, `ppc64le`, `amd64` or `win32`.
`uv.lock` was compiled on Apple Silicon (`arm64`) with `--python-version 3.11`
and no platform, and uv then resolves for the compiling machine. So the lock
never pinned greenlet, and pip installed whichever greenlet was newest at
build time, on the App and on every CI runner.

`uv.lock` is therefore compiled for the App's platform. A refresh still names
the packages it moves (docs/audits/supply-chain-audit.md, "Python lock
refresh"):

```bash
uv pip compile requirements.in --python-version 3.11 \
  --python-platform x86_64-manylinux_2_28 --format requirements.txt \
  --output-file uv.lock [--upgrade-package <package> ...]
python tools/requirements_digest.py --write
```

`x86_64-manylinux_2_28` is Linux x86_64 with glibc 2.28 as the floor for
wheel selection, so the resolver never picks a release whose only Linux wheels
need a newer glibc. uv takes the existing output file's pins as preferences,
so adding the flag moved no pin. The lock gained `greenlet==3.5.6` and a new
header line, and nothing else changed. pip-audit over the new lock (PyPI and
OSV feeds) reports no known vulnerabilities.

Why one platform and not `--universal`: resolved for each platform from this
lock, macOS arm64 is the lock minus greenlet, and macOS x86_64 and Linux
aarch64 are the lock exactly. One Linux x86_64 resolution therefore pins every
closure that runs this code: the App, CI's ubuntu runners, `deploy-dev.yml`
and local Macs. A universal lock adds forked pins and marker lines (Windows-only
colorama and the like) for packages this repo never installs. It would also
need `tools/python_deps_check.py` to accept marker lines, because that tool
refuses anything but a bare `name==version` pin.

What each environment reports:

- The App and CI (Linux x86_64): `unpinned` 0 and nothing is not
  applicable (`pins_total` 114, `pins_checked` 114, since the runtime-only
  split; 139 and 138 with tomli before it).
- A Mac on Apple Silicon: greenlet is not applicable (`pins_checked` 113).
  pip ignores a constraint for a package nothing requires, and
  `tools/python_deps_check.py` still exits 0.
- `tests/unit/test_dependency_lock_check.py` replays a clean install of the
  real lock with Linux x86_64 and macOS arm64 markers. It fails if any reached
  package is unpinned or if the lock's header loses the platform flag. Against
  the 32cc4384 lock, it fails on Linux and names greenlet.

**Verify on the next deploy:** the `dependency_lock_check` event reports
`unpinned` 0, `unpinned_names` `[]` and `not_applicable_names` `[]`. A
non-empty `unpinned_names` names a dependency that the App's platform needs
and the lock lacks. Recompile with the command above, restamp and redeploy.

## Databricks App

1. Fill `.env.local` with workspace, warehouse, lender/runtime values, and the
   deployment-owned resource namespace (or set the corresponding GitHub
   Actions variables). A customer lender is a digest-pinned record
   (`MIP_LENDER_IDENTITY_JSON` + `MIP_LENDER_IDENTITY_SHA256`, see
   [Customer lender identity](#customer-lender-identity-a-digest-pinned-record));
   a bare name/NMLS pair is accepted only for the source-controlled demo pair
   in `backend/schemas/lender_identity.py`:

```bash
MIP_LENDER_NAME="<exact reviewed customer legal lender name>"
MIP_LENDER_NMLS_ID="<matching reviewed customer lender NMLS id>"
# Optional; defaults from MIP_LENDER_NAME when unset.
MIP_TENANT_ID="<customer_lender_slug>"
MIP_DEFAULT_CATALOG="acme_mip"
# Use a deployment-owned namespace in any isolated staging/customer workspace.
MIP_APP_NAME="acme-mip-app"
MIP_LAKEBASE_INSTANCE="acme-mip-state"
MIP_LAKEBASE_SYNC_CATALOG="acme_mip_state"
LAKEBASE_DATABASE="mip_app_state"
MIP_GENIE_SPACE_NAME="Acme Mortgage Lead Intelligence"
MIP_RUNTIME_SECRET_SCOPE="acme-mip-runtime"
MIP_APP_ROLLBACK_SECRET_SCOPE="acme-mip-app-rollback"
# The runtime HMAC keys need no input: a first install generates them in
# MIP_RUNTIME_SECRET_SCOPE, and every later deploy keeps them unread. Set one
# only to seed an absent key with a value the install already uses.
# MIP_COTALITY_ID_MASK_SECRET="<deployment-scoped-random-secret>"
# MIP_GENIE_ACTION_SECRET_CURRENT="<deployment-scoped-random-secret>"
MIP_GENIE_ACTION_SECRET_KID="v2"
```

   See [`docs/se-onboarding.md`](se-onboarding.md) for the full
   one-workspace-one-lender tenancy checklist.
2. Fill the client profile (`deploy/clients/example.env`) into `.env.local`.
   `databricks.yml` commits no workspace host: its one deployable target,
   `client`, takes the host from the authenticated environment, roots the
   bundle at `/Workspace/Users/<deployer>/.bundle/mortgage-intelligence-platform/<client>`
   and runs every job and the pipeline as the profile's deployer. The deploy
   refuses (exit 2) before any other step unless that host is the profile's
   `DATABRICKS_HOST` and the read-only live checks find the profile's
   workspace id, metastore id and deployer (`tools/clients/live_checks.py`).
   The same call then runs the read-only install checks
   (`tools/clients/install_checks.py`, M1-PR3), all before the signed lease
   and any App stop. Each refuses on a read error rather than reading it as
   absent, and every refusal is reported together:
   - **adoption**: an App named `MIP_APP_NAME` that this deployer's bundle
     state (`<root>/state/resources.json`) does not bind as `apps.mip_app` is
     an existing install it never adopted. The one exception is an App a
     first-install journal may own, which the pre-lease bundle plan gate
     judges with the verified journal;
   - **install marker**: the catalog property `mip.install_key`, when
     present, equals `MIP_CLIENT`. The deploy stamps it in the very call that
     creates the catalog (`tools/databricks/ensure_pipeline_namespace.py
     --install-key "$CLIENT_KEY"`), so a first install interrupted anywhere
     after that create finds its own catalog marked; a catalog the bootstrap
     found absent must read back with exactly that marker. An existing catalog
     without it was not created by this install's deploy and is accepted only
     when this deployer's state binds the App (an install from before the
     marker), so `CREATE CATALOG IF NOT EXISTS` never silently reuses another
     install's catalog; the bootstrap never alters an existing catalog. No
     catalog is a first install;
   - **Cotality share**: the source catalog is a Delta Sharing catalog from
     `MIP_COTALITY_SHARE_PROVIDER` / `MIP_COTALITY_SHARE_NAME` (when set), and
     all seven source tables resolve;
   - **storage root**: a first install needs the metastore's default storage
     root, or `MIP_UC_CATALOG_STORAGE_ROOT`, which the catalog DDL and the
     pipeline namespace bootstrap then use as the catalog's `MANAGED LOCATION`
     (named to both tools, never printed). When the field is set and the
     catalog exists, it must be the catalog's own storage root (a trailing `/`
     aside), since a managed location never moves;
   - **account principal**: `MIP_IDENTITY_IDS_JSON.account_principal` holds no
     account admin role (read from account SCIM with its own credential).
   After the live checks, the preflight takes the SQL warehouse from this deployer's bundle
   state (`python -m tools.databricks.bundle_state warehouse-id`, the
   warehouse the bundle created, M1-PR3): `DATABRICKS_WAREHOUSE_ID` is read
   only as a first install's bootstrap warehouse, for the steps before the
   bundle apply creates its own (the deploy switches to that one right after
   the apply), and refuses when it names another warehouse than the state
   binds. `databricks.yml` declares no `sql_warehouse_id` variable: every
   resource uses the bundle's own warehouse.
   `deploy-dev.yml` runs one client environment's phase per manual dispatch
   (M1-PR7), and `nightly.yml` validates one client per dispatch (M1-PR8,
   "Live validation per client"). The command of record is the governed
   dispatch of `deploy-dev.yml` (step 3), which runs `./scripts/deploy.sh -t
   client --phase <phase>` in the client's GitHub Environment; a live phase,
   `plan` included, is CI-only. Before the first deployment into an isolated
   workspace, run locally:

```bash
python tools/databricks/bundle_env.py validate -t client
./scripts/deploy.sh -t client --dry-run
```

   The governed deploy's first plan must report only additions (`0 to
   change`, `0 to delete`). A change or deletion means the bundle state or
   resource namespace still points at another installation; stop and
   reconcile it.
   `MIP_LAKEBASE_INSTANCE`/`LAKEBASE_INSTANCE_NAME` and
   `LAKEBASE_DATABASE`/`MIP_LAKEBASE_DATABASE_NAME` are compatibility alias
   pairs and must match when both names are present. The App reads
   `LAKEBASE_DATABASE` before the `PGDATABASE` its database binding injects.
   So `scripts/deploy.sh` hands `app_deploy_payload.py` the database it binds
   (`--lakebase-database "$LAKEBASE_DATABASE"`, the value it also exports as
   `BUNDLE_VAR_lakebase_database_name`), and the payload names it whenever it
   is not the App's default `mip_app_state` (M3-PR4). The payload tool itself
   reads no ambient or `.env.local` value for it. An adopted core blue's
   restore payload is rebuilt with the same flag (`app_core_rollback
   restore-payload --lakebase-database`), so a blue bound to another database
   rebuilds with its own env-name set. The default install's App env-name set
   does not change, and the emergency recipe above passes no flag, so it
   builds the same payload; for a client bound to another database, add
   `--lakebase-database <database>` to its `app_deploy_payload` line.

3. Dispatch the governed deploy, one client and one phase per run, plan
   first (`make deploy-dev` prints this command and deploys nothing). In CI it
   runs `./scripts/deploy.sh -t client --phase <phase>`, which builds the
   frontend, provisions Genie if needed, validates/plans/deploys the direct
   bundle through the env-aware wrapper, publishes the staged per-release App
   source folder and promotes it to the running Databricks App ("Governed
   per-release App source"), and runs the refresh/smoke steps (the data jobs in
   `phase=data`). The order of phases for a new client, and the acks each
   needs, is [docs/clients/onboarding.md](clients/onboarding.md).

```bash
gh workflow run deploy-dev.yml --ref main -f client=<client> -f phase=plan -f acknowledge_single_maintainer_break_glass=true
```

The command of record deploys only an exact committed revision. Before any
workspace mutation, and again immediately before bundle upload, it refuses
staged or unstaged tracked changes and untracked non-ignored files. Standard
ignored artifacts such as `.env.local`, `frontend/dist/`, `sql/_rendered/`,
and `.databricks/` are preserved and do not block deployment. Run the gate by
itself with:

```bash
./scripts/deploy.sh --verify-source-only
```

The SHA printed by this gate is the value advertised as `MIP_GIT_SHA` and
verified by the live smoke test.

Frontend dependencies are checked in step 0, before the confirmation prompt,
by `python -m tools.frontend_deps_check`. If the install is `fresh`, the deploy
continues. If it is `shared` (this checkout's `frontend/node_modules` is a
symlink) or the check errors, the deploy stops. If it is `stale` (the install
does not match `frontend/package-lock.json`), `npm --prefix frontend ci` runs
in this checkout after the prompt. The check can only see an outbound symlink.
A checkout whose real `node_modules` other worktrees link to (for example a
main checkout shared by agent worktrees) looks like a private install, so a
`stale` result there reinstalls the tree every linked worktree uses. Deploy
from a checkout that owns its `node_modules` and that nothing else links to,
or answer no at the prompt and refresh the shared install deliberately.

### Cotality source share

The silver lift reads the Cotality share by its canonical names: catalog
`cotality_mortgage_data`, schema `corelogic` and tables named
`entrada_eval_<stem>` (`backend/config/source_names.py`). A workspace whose
share is mounted under other names sets three deployment controls, in the
shell or in `.env.local` (a non-empty shell value wins, as for every control):

```bash
MIP_COTALITY_SOURCE_CATALOG="acme_cotality"
MIP_COTALITY_SOURCE_SCHEMA="mortgage"
# The share's table-name prefix; "-" when its tables carry none.
MIP_COTALITY_SOURCE_TABLE_PREFIX="-"
```

- Unset or blank means the canonical name. Blank cannot mean "no table
  prefix", because a blank value is unset in every layer, so that is `-`.
- The catalog and schema must be lowercase unquoted identifiers, and the
  prefix 1 to 64 lowercase letters, digits or `_`, or `-`. A template value
  (`<...>` or `00000000PLACEHOLDER`) is refused, not read as unset. The deploy
  preflight exits 2 on any of these before the first render, and
  `render_sql.py`, `bundle_env.py`, `provision_genie_space.py` and
  `ensure_asset_lineage_view.py`, run on their own, refuse the same values
  before any workspace call.
- One resolution feeds every reader: the rendered SQL (a closed-shape rewrite
  of the canonical names, then a check that refuses the whole render, naming
  the file and line, if a canonical token is left), the observed lineage
  view's share-catalog filter, the Genie space spec, and the
  `cotality_source_*` bundle variables. `databricks.yml` hands those to the
  feature pipeline's configuration. The pipeline has no default source: a run
  whose configuration does not name one fails before it reads a table.
- Locally they are local deploy controls in `.env.local`. They are client
  profile fields: public, so in CI they are variables of the client's GitHub
  Environment, which `deploy-dev.yml` reads whole through the
  resolve-client-profile action (M1-PR7); `nightly.yml` reads none of them
  (M1-PR8).
- The App reads the same three names as settings
  (`backend/config/cotality_source_settings.py`), canonical when unset, with
  the same refusals. `app_deploy_payload.py` sends a name to the App only when
  it is set and differs from the canonical one, so a canonical install's App
  env-name set does not change. The asset registry and the lineage manifest
  then name the configured share tables, each under its canonical
  `source_key` (the table's canonical three-part name, the same on every
  install), which the evidence drawer and the asset page key their links by.

`.env.example` cannot be read or edited from an agent session, so the owner
adds these lines to its settings section and, in the same change, empties
`SETTINGS_FIELDS_PENDING_TEMPLATE` in `tests/unit/test_settings_contract.py`;
`test_pending_settings_fields_stay_tracked_until_the_template_documents_them`
fails once the template has them and the set still lists them:

```bash
# Cotality source share catalog the App reads; unset or blank = the canonical share catalog.
MIP_COTALITY_SOURCE_CATALOG=
# Cotality source share schema the App reads; unset or blank = the canonical share schema.
MIP_COTALITY_SOURCE_SCHEMA=
# Cotality source share table-name prefix; unset or blank = canonical, "-" when the share's tables carry no prefix.
MIP_COTALITY_SOURCE_TABLE_PREFIX=
```
- The prompt guards keep their reviewed canonical patterns and add only the
  configured share's qualified names: `<catalog>.<schema>.` and each table's
  three-part name, also with quoted parts or spaces around the dots. A bare
  configured name is never a refusal, so a schema called `mortgage` leaves
  every mortgage question alone. The audit source-asset policy refuses the
  same qualified names, as it refuses the canonical share.

### Deploy interpreter

`deploy.sh` runs every repo tool with one interpreter. By default that is
`.venv/bin/python` in the checkout, or `python3` when there is no `.venv` (the
Makefile convention). `MIP_DEPLOY_PYTHON` selects another one, such as a venv
built only for deploys outside the checkout. Set it in the shell, not in
`.env.local`: `.env.local` is parsed with this interpreter. It must be an
absolute path to an executable that reports Python 3. A relative, missing,
directory or non-executable value exits 2 without running anything. An
absolute executable is run once, as
`<path> -c 'import sys; print(sys.version_info[0])'`, and exits 2 unless it
prints `3`.

Step 0 then runs `python -m tools.python_deps_check`. It compares the
interpreter with the `requirements.txt` pins (`-c uv.lock` plus
`-r requirements.in`):

- the interpreter must be the Python minor that `uv.lock` was compiled for
  (3.11);
- every requirement must be installed;
- every package that pip would install for this interpreter, and that
  `uv.lock` pins, must equal its pin exactly (PEP 440 equality, so a patch,
  post-release or local-label difference is drift).

A package that pip would install but that `uv.lock` does not pin is printed as
an `unpinned:` note and never fails the check, because it has no pin to equal.
`uv.lock` is resolved for one platform, the App's Linux x86_64 ("App Python
dependencies: the lock's platform" above). So the Linux x86_64 runner that
`deploy-dev.yml` uses prints no note, and an Apple Silicon Mac needs a subset
of the lock (everything but greenlet). A note names a dependency the lock's
platform does not need.

This covers mlflow (which registers the Gateway model), databricks-sdk and
every transitive pin, not only `import boto3, mlflow`. A dry run reports the
result (`python deps: pinned`, `drifted`, `error` or `skipped`) and continues. A real run refuses anything but `pinned`
with exit 2, before the first workspace call. On drift, the tool prints the
problems and this remedy:

```bash
python3.11 -m venv "$HOME/.venvs/mip-deploy"
"$HOME/.venvs/mip-deploy/bin/python" -m pip install -r <checkout>/requirements.txt
export MIP_DEPLOY_PYTHON="$HOME/.venvs/mip-deploy/bin/python"
./scripts/deploy.sh -t client
```

Keep the venv outside the checkout. The exact-source gate fails on any
untracked path that `.gitignore` does not cover: the default `.venv/` is
ignored and passes, but a new name such as `.venv-deploy/` would fail it.
Never re-sync a venv that other sessions share to make a deploy pass.

### Per-run M2M identities

Live app validation and agent ownership use seven service principals and store only their OAuth
client credentials. No bearer token is a GitHub secret or `.env.local` value:

One-shot OAuth secrets are written only to one GitHub Environment
(`--gh-env <client>`, required with `--set-gh-secrets`) of the repository
detected from the current `origin`, never to repository-level secrets. Every
`gh` call names `--env` and never `--repo`; the repository reaches it through
`GH_REPO`. Before any mint or SDK mutation the tool reads the environment
and refuses unless it exists with exactly its `MIP_REVIEW_MODE`'s
protections, with the signed-in `gh` user as its one required reviewer, no
admin bypass and a branch policy of exactly `{main}`; `<client>-validate`,
the validator's federation subject, is always refused. Customer automation
without a local git remote must set
`MIP_M2M_GITHUB_REPOSITORY=owner/repository`; an explicit `--gh-repo` must
match that reviewed sink. Grant-only reconciliation with `--no-mint-secret`
does not require this binding and can run from a customer fork unchanged.

| Role | Default service principal | GitHub Environment secrets |
|---|---|---|
| normal app user | `mip-nightly-ci-sp` | `DATABRICKS_CLIENT_ID`, `DATABRICKS_CLIENT_SECRET` |
| second app operator | `mip-nightly-operator2-ci-sp` | `DATABRICKS_OPERATOR2_CLIENT_ID`, `DATABRICKS_OPERATOR2_CLIENT_SECRET` |
| app admin | `mip-nightly-admin-ci-sp` | `DATABRICKS_ADMIN_CLIENT_ID`, `DATABRICKS_ADMIN_CLIENT_SECRET` |
| candidate release probe | `mip-release-probe-ci-sp` | `DATABRICKS_RELEASE_PROBE_CLIENT_ID`, `DATABRICKS_RELEASE_PROBE_CLIENT_SECRET` |
| AI Gateway verifier | `mip-ai-gateway-verifier-ci-sp` | `DATABRICKS_VERIFIER_CLIENT_ID`, `DATABRICKS_VERIFIER_CLIENT_SECRET` |
| agent resource runtime | `mip-agent-runtime-ci-sp` | `DATABRICKS_AGENT_RUNTIME_CLIENT_ID`, `DATABRICKS_AGENT_RUNTIME_CLIENT_SECRET` |
| Supervisor proxy caller | `mip-agent-supervisor-proxy-ci-sp` | `DATABRICKS_AGENT_PROXY_CREDENTIAL_BUNDLE` |

For a fresh workspace, create credentials **before** running deploy. The
credentials-only mode never lists or grants an App, Lakebase instance, Gateway
endpoint, or SQL warehouse; it creates/resolves the reserved service principal,
optionally creates/joins the reviewed admin group, and sends only that role's
client ID and one-shot client secret to the `--gh-env` environment of the
repository bound to `origin`.
Export the verifier proof signing key and its derived public verify key before
these commands. Each operation holds its outer signed App deployment lease and
also acquires the fixed global `mip-oauth-credential-mutations` lease before its
first provider inventory read. The global name is independent of App and
principal names, so alternate valid App names cannot create an overlapping
bootstrap, rotation, audit, or temporary identity probe.
Every standalone bootstrap or rotation also requires `HEAD` to equal the
configured source SHA (when set) and a completely clean tracked and untracked
worktree. A deploy-owned mutation instead borrows the already-acquired signed
outer lease and requires its explicit preflighted source SHA.
Bootstrap `agent_runtime` first: it is the only service-principal reader on the
shared signed-lease root and the delegated lease writer for every later
credential mutation.
The live workspace does not initially contain `mip-admin`, so creating that
group remains a separately reviewed, explicit action:

```bash
python tools/databricks/provision_m2m_oauth.py \
  --pre-app-bootstrap --identity-role agent_runtime --set-gh-secrets \
  --gh-repo skyler-myers-db/mortgage-intelligence-platform --gh-env <client>
python tools/databricks/provision_m2m_oauth.py \
  --pre-app-bootstrap --identity-role normal --set-gh-secrets \
  --gh-repo skyler-myers-db/mortgage-intelligence-platform --gh-env <client>
python tools/databricks/provision_m2m_oauth.py \
  --pre-app-bootstrap --identity-role operator2 --set-gh-secrets \
  --gh-repo skyler-myers-db/mortgage-intelligence-platform --gh-env <client>
python tools/databricks/provision_m2m_oauth.py \
  --pre-app-bootstrap --identity-role admin --create-group \
  --set-gh-secrets --gh-repo skyler-myers-db/mortgage-intelligence-platform \
  --gh-env <client>
python tools/databricks/provision_m2m_oauth.py \
  --pre-app-bootstrap --identity-role release_probe \
  --set-gh-secrets --gh-repo skyler-myers-db/mortgage-intelligence-platform \
  --gh-env <client>
python tools/databricks/provision_m2m_oauth.py \
  --pre-app-bootstrap --identity-role verifier \
  --set-gh-secrets --gh-repo skyler-myers-db/mortgage-intelligence-platform \
  --gh-env <client>
python tools/databricks/provision_m2m_oauth.py \
  --pre-app-bootstrap --identity-role agent_proxy \
  --set-gh-secrets --gh-repo skyler-myers-db/mortgage-intelligence-platform \
  --gh-env <client>
```

Pre-App bootstrap is creation-only and refuses every existing reserved
principal. If a failed bootstrap was fully compensated, prove that the
credentialless principal is removed before repeating the bootstrap command; do
not add `--rotate` to a bootstrap retry. After the App exists, a reviewed
rotation uses normal mode with that role's canonical
`--expected-application-id`, `--rotate`, `--set-gh-secrets`, and
`--gh-env <client>`; a mint without the environment is refused.
Before provider create, the provisioner writes and reads back a signed,
immutable intent containing the exact authority, principal, sink descriptor,
global lease generation, and prior credential IDs. It records the returned
credential ID before exposing the one-shot secret and arms the exact GitHub
sink before its first write. No journal phase contains the secret.
If exact restoration or delivery is unproven, an operation-bound quarantine
record under `/.mip-deployment-leases` blocks every later credential baseline,
even under another App name. A quarantine is cleared only by a signed terminal
resolution whose provider inventory and sink disposition validate against the
intent; lease expiry or deleting a record is never recovery proof.
Recovery is an explicit deploy mode, not a fresh-create retry. First inspect the
signed record with
`python -m tools.databricks.oauth_credential_recovery_cli inspect
--intent-path <workspace-path>`. After governance review, set all four
`MIP_OAUTH_CREDENTIAL_RECOVERY_INTENT_PATH`,
`MIP_OAUTH_CREDENTIAL_RECOVERY_PRINCIPAL_ID`,
`MIP_OAUTH_CREDENTIAL_RECOVERY_AUTHORITY_IDENTITY`, and
`MIP_OAUTH_CREDENTIAL_RECOVERY_PROVIDER_API`, then rerun `scripts/deploy.sh`.
The deploy takes over only the intent's signed recovery root after expiry,
re-proves the exact provider identity and full credential inventory, revokes
only the observed or sole attributable delta, deletes only the armed GitHub
secret names and proves their repeated absence, appends a signed
resolver-lease resolution, and releases the resolver lease. A zero-delta intent
without a durable observation remains quarantined: a bounded inventory read
cannot prove that a delayed provider create will never commit. It may be
resolved only with provider-specific durable non-commit evidence, which the
current Databricks credential APIs do not expose. Multiple deltas, missing
prior IDs, a delete exception, or mismatched operator confirmation likewise
remain quarantined.
If failure occurred after the global mutation lease was acquired but before an
intent was durably committed, inspect it with
`python -m tools.databricks.oauth_credential_recovery_cli
inspect-orphan-lease`. Only when `intent_present` is false may a reviewer set
both `MIP_OAUTH_CREDENTIAL_ORPHAN_LEASE_ID` and
`MIP_OAUTH_CREDENTIAL_ORPHAN_RECOVERY_ROOT_LEASE_ID` to those exact reviewed
coordinates and rerun `scripts/deploy.sh`. The deploy first acquires and
heartbeats a new signed outer App lease, then invokes
`recover-orphan-lease` with both confirmations before any later deployment
mutation. Directly invoking the recovery subcommand after the failed deploy is
unsupported because no live outer deployment lease exists. Orphan recovery and
intent recovery are mutually exclusive, and an authoritative intent always
requires the intent-recovery path.
Create the separate account-SCIM OAuth principal and store
`DATABRICKS_ACCOUNT_ID`, `DATABRICKS_ACCOUNT_CLIENT_ID`, and
`DATABRICKS_ACCOUNT_CLIENT_SECRET` independently; it must not reuse any of the
seven workspace client IDs. A first install with an approved group owner requires
account-admin authority because the bundle-created target App cannot be
delegated in advance. After that install, downscope the account principal to
Service Principal Manager on every normal, operator2, admin, release-probe,
verifier, agent-runtime, agent-proxy, and now-existing target-App principal so later deploys
can create and revoke each five-minute target-identity proof credential. Also
configure the two distinct Ed25519 private keys
`MIP_AI_GATEWAY_PROOF_SIGNING_KEY` and
`MIP_GATEWAY_MODEL_ATTESTATION_SIGNING_KEY`, plus the runtime HMAC/masking
secrets documented below. Store its derived public key separately as the
`MIP_GATEWAY_MODEL_ATTESTATION_VERIFY_KEY` GitHub Actions variable; nightly
read-only jobs never receive the private key. Only then run
`./scripts/deploy.sh -t client` for a client install (`deploy-dev.yml` in
CI; see "CI deploy (deploy-dev.yml): one client environment per dispatch" below). Bundle apply first
creates only non-App dependencies. Deploy then resolves their concrete IDs and
applies the App resource bindings without a source deployment: a new App is
created stopped with `--no-compute`, while an existing App update must preserve
its exact active/pending deployment and compute state. Only then does deploy
re-resolve the App service principal and grant `CAN_USE` to the exact normal,
operator2, and admin client IDs with `--no-mint-secret`. The release probe,
verifier, and runtime retain no persistent App access; verifier/runtime resource
reconciliation remains later, after their resources exist.

The deployment-wide App fence is an append-only signed generation chain under
`/.mip-deployment-leases`. Acquire, renewal, release, and authorized expired
takeover each contend on the one deterministic successor path with Workspace
Files create-without-overwrite; no authoritative generation is overwritten or
deleted. A signed head file is only a read accelerator, so stale, missing, or
corrupt hint state cannot select a winner. Legacy v2/v4 chains can be resolved
from their canonical base with an authenticated scan bounded by
`MAX_CANONICAL_GENERATIONS`; v5 publishes an immutable locator bound to a
canonical signed v5 generation (normally the first), with a distinct 64 KiB
envelope cap around its bounded 32 KiB holder map, so automatic fallback reads
remain bounded and fail closed instead of rescanning heartbeat history.
Protocol-v5
acquisition/takeover generations also bind an exact,
bounded holder map to immutable Ed25519-signed recovery checkpoints whose
Workspace Files paths contain the app name, chain UUID, and full checkpoint
digest. Each holder entry carries its durable root, newest checkpoint digest,
candidate count, last acquisition generation and lease ID, preceding
checkpoint digest, and signing-key epoch. A
checkpoint carries that holder's acquisition/takeover lease IDs newest-first
and the preceding checkpoint digest. Renewal and release preserve both the
holder map and current recovery digest byte-for-byte, so thousands of
heartbeats do not copy or rescan cumulative recovery history.

The first v4-to-v5 acquisition authenticates the retained legacy chain and
creates one linked checkpoint per historical acquisition before its new
checkpoint. Every later v5 recovery authenticates the canonical lease head,
the selected holder's current digest-addressed checkpoint, and its immediate
predecessor when one is named. The predecessor must carry the exact candidate
tail, root, count, holder, earlier generation sequence, and non-regressing key
epoch. Missing, malformed, oversized, cross-app,
cross-chain, signature-invalid, or head-divergent checkpoint state authorizes
nothing. Immutable checkpoint uploads use create-without-overwrite plus exact
readback after ambiguous timeouts. A losing acquisition may leave an
unreferenced immutable checkpoint, but cannot publish a lease successor or
alter another holder's map entry; checkpoint history is never deleted. Active
leases have a six-hour absolute lifetime. Keep every retired
public key, oldest first, in the public GitHub Actions variable
`MIP_AI_GATEWAY_PROOF_HISTORICAL_VERIFY_KEYS` (`deploy-dev.yml` does not carry
it yet: owner step 6 under "CI deploy (deploy-dev.yml)"); private keys are
still destroyed at retirement. Each successor records the public-key epoch and rejects epoch
regression. The signed chain retains its original recovery root through every
release by the same holder. A new holder with no signed history begins a
separate root, while a returning holder discovers its newest historical root
without inheriting another holder's authority. A killed deploy can therefore
present its exact root for an expired takeover even when no first-install
journal exists. A stranded legacy v2 fence is migrated append-only only after
its expiry plus the five-minute quiescence margin and that exact durable
recovery authority.

If a v5 chain has more than the automatic 64-successor fallback window and its
mutable `.head` or unsigned `.protocol-v5` locator is missing or corrupt, the
signed holder may run
`python -m tools.databricks.app_deployment_lease repair-head --app-name
"$MIP_APP_NAME"`. This exceptional command scans and authenticates the complete
immutable chain up to `MAX_CANONICAL_GENERATIONS`, re-proves the exact protected
root ACL, re-anchors a missing/corrupt unsigned locator to the authenticated
canonical head, publishes the non-authoritative `.head` hint, and requires exact
readback plus canonical download postflight. It never rewrites a signed
generation or checkpoint. The normal resolver remains bounded after repair.
If a v5 acquisition commits but locator publication fails, compensation must
authenticate or publish the immutable locator before appending a terminal
release; persistent locator failure leaves the signed acquisition active for
this governed repair path and never creates a markerless terminal v5 record.

Before the first Apps API create, deploy writes a signed, lease-bound ownership
journal under `/.mip-deployment-leases/<app>.first-install.json`. Journal v3
records the workspace, deployer identity, source revision, original lease and
bounded creation window, exact resolved resource bindings, and a random marker
embedded in the new App description. The successful Apps create response is
captured to a mode-`0600` file; before any bundle bind, deploy adds its
immutable App object ID plus service-principal client and SCIM IDs to the signed
journal and proves the live App still has those exact IDs. If the process is
lost after create but before that claim, the next deployment queries the
server-owned `system.access.audit` `apps/createApp` event. Recovery requires one
successful event in the signed workspace/actor/time window, an exact parsed
request payload, and an audited result App ID equal to the current live App ID;
only then are the live service-principal IDs signed. Missing or delayed audit
evidence is polled and authorizes no App mutation. The App-create authorization
lasts 15 minutes and the audit settlement interval lasts one additional hour,
keeping reconciliation inside the four-hour deploy job limit. An absent App
cannot make its intent disappear until both intervals have closed. During settlement, deploy
rechecks both live App inventory and the exact audit query every 30 seconds. A
zero-row query before that boundary is delayed evidence, never negative proof;
the journal is retired only after the final post-boundary query and App
recheck are both still absent.

If the runner is interrupted after either identity-claim path but before the
first signed last-good capture, an ordinary later deployment stops and
quarantines the App, quiesces its treatment authority, removes any partial
bundle binding, and deletes it only when the live creator, marker, resources,
and all three immutable App identity fields still match that signed journal.
The bundle-binding compensation is armed before the remote bind, and an
ambiguous unbind leaves both the stopped App and signed journal intact for a
later lease to reconcile. App deletion itself is performed only by the journal
verifier; a commit-then-timeout is resolved by re-reading exact App absence. An
App without the exact journal is never inferred to be owned by the failed run;
it remains on the explicit unsigned-App rebase path. Immediately after signed
last-good capture, deploy disarms first-install deletion and advances to the
signed-green compensation state before retiring the journal. A failed or
ambiguous journal retirement therefore preserves the signed customer App and is
retried by the next deployment. The journal is cleared only after audit-bound
proof that App creation did not commit or after signed rollback state is
verified, so a runner crash cannot strand an automatically trusted unsigned
App.

An explicit unsigned-App rebase stops the App and removes all direct non-manager
`CAN_USE` before any restart. After the green candidate deploy, the script first
proves the agent-runtime identity cannot reach the App, then temporarily grants
only the release probe `CAN_USE` for authenticated release checks. Normal,
second-operator, and admin access is restored only after the treatment authority
and exact last-good contract are durably captured. A pre-capture failure stops
the App and leaves operator access withheld.

Databricks Apps v2 accepts stop, start, resource update, snapshot deployment,
and App-permission mutations only by App name; it exposes no immutable-App-ID or
conditional-version parameter for those writes. App `CAN_MANAGE` principals and
workspace admins are therefore an explicit trusted control-plane boundary: they
must not delete/recreate or mutate the target App out of band while the signed
deployment lease is held. Deploy captures the App object/client/SCIM identity
triplet immediately after acquiring that lease, supplies it to every helper
that can accept an identity, and checks it before and after name-addressed
mutations. Any observed drift aborts the release and forbids a success claim,
but an authorized manager violating this boundary is a governance incident that
client-side code cannot atomically prevent. The supported operator path is
`scripts/deploy.sh`; manual App click-ops during a deployment are unsupported.

Without `--create-group`, admin provisioning fails closed when `mip-admin` is
missing. Re-running is idempotent: existing principals, group membership, and
the verifier Lakebase OAuth role are reused. Client ID and secret sink flags
are assertions for the reserved role-owned names; custom or cross-role sink
names are rejected. The release probe is admin-capable inside the product but
receives no persistent Databricks App permission; its temporary candidate access
is converged directly by the signed-release gate and removed after capture. The
verifier is never added to `mip-admin` and receives no
Databricks App `CAN_USE` permission by default. It receives only `CAN_USE`
on the named SQL warehouse, `CAN_QUERY` on the AI Gateway endpoint, read-only
access to the exact inference tables, and verifier-only Lakebase proof-ledger
privileges. Provisioning fails closed if the verifier has a direct App grant
or if any non-admin automation identity remains in `mip-admin`. A reused
Lakebase verifier role must be reported by the SDK with
`identity_type=SERVICE_PRINCIPAL`; `USER`, other identity types, and absent type
metadata are rejected before endpoint, warehouse, or verifier grants.
The agent-runtime identity is also excluded from the App and `mip-admin`; it
gets no Lakebase role, SQL warehouse grant, borrower-table access, or verifier
authority. Deployment re-audits those exclusions on every run. It grants
persistent `USE CATALOG`, `USE SCHEMA`, and `EXECUTE` only for the three
reviewed UC functions plus direct `CAN_RUN` on the one Genie space. `CREATE
MODEL` and `CREATE TABLE` on `mip.audit` exist only while it owns/updates the
exact registered proxy model and Gateway inference table; the deploy EXIT
compensation revokes both privileges on success or failure. Databricks makes
the endpoint creator the inference-table owner, so this runtime necessarily
retains owner capabilities on that exact payload table. That does not expand
the data it can observe: the same runtime already processes those request and
response payloads in flight, and it receives no access to any other audit or
borrower table.
The agent-proxy identity is independently excluded from every App, Lakebase
instance, SQL warehouse, and direct serving endpoint ACL. It receives
`CAN_QUERY` through exactly one endpoint-bound managed group whose sole member
is the proxy, direct `CAN_RUN` on one Genie
space, and direct `USE CATALOG`, `USE SCHEMA`, and `EXECUTE` only for the three
reviewed `mip.gold` functions. A target-authenticated dual-authority audit
enumerates every visible UC catalog, schema, function, table, volume, and
registered model before cutover and again after blue retirement; inherited,
foreign, ownership, or fourth-function authority fails deployment.
The proxy's own OAuth SCIM projection must expose the exact managed group before
the query gate runs. Deployment retries only `PermissionDenied` during bounded
provider propagation; deadline exhaustion fails closed. Removing the group's
sole member atomically revokes query authority before group retirement.
Before green activation, a principal-pinned workspace-admin global audit
enumerates every admin-visible Genie space and customer-created serving
endpoint. ID-less, creator-less Databricks foundation-model endpoints are
routed to the fixed `system.ai` Unity Catalog inventory instead of being
mistaken for customer ACL securables. The audit requires runtime direct
`CAN_MANAGE` only on the exact green Supervisor/Gateway pair (plus any pinned,
runtime-owned blue endpoint during side-by-side cutover) and direct `CAN_RUN`
only on the reviewed Genie space. Query-only identities, including verifier and
agent proxy, receive `CAN_QUERY` only through an endpoint-bound managed group
whose immutable name, external ID, group ID, endpoint ID, and sole member are
re-proven. Unrelated group-derived, inherited, or broader access and access to
any unrelated resource fail the deploy. The same endpoint audits run again
after blue retirement.
Isolation provisioning likewise enumerates every visible Databricks App and
every Lakebase instance rather than checking only the named deployment.
For an existing principal whose prior client secret is unavailable or being
replaced, add `--rotate`; without it, the existing secret remains unchanged.

For local `scripts/deploy.sh`, provide the normal, admin, release-probe,
verifier, agent-runtime, and agent-proxy client credential pairs plus the
second-operator client ID and active proxy credential ID through the process
environment or `.env.local`. The script rejects reused
client IDs, mints distinct normal/admin bearers at preflight, and uses only the
release-probe bearer while an unsigned rebase remains quarantined. It remints
the active automation bearers immediately before Agent Evaluation and the final
smoke sweep. GitHub workflows use `tools/oauth_m2m_mint.py` to append
fresh values directly to `$GITHUB_ENV`; minted values are not printed.
The second-operator credential pair is intentionally required only by the
on-demand `nightly.yml` isolation gate; deployment does not impersonate that
operator.

The deploy script's live smoke now requires agentic capability proof by
default (`MIP_EXPECT_AGENTIC_CAPABILITIES=1`). The admin proof uses the fresh
admin M2M bearer; it never falls back to the normal identity or deployment PAT.
Do not disable this gate for customer-release signoff. Only override it for an
explicitly documented partial validation where agentic capabilities are out of
scope.
AI Gateway claimability is stricter than endpoint/table configuration: deploy
runs `tools/databricks/verify_ai_gateway_exact_proof.py send --wait` under the
dedicated verifier M2M identity after agentic provisioning. It first reconciles
the verifier's Lakebase role and proof-ledger grants; the runtime app retains
read-only proof-ledger access. The capability row becomes available only when
Lakebase contains a fresh `mip_app.ai_gateway_proof_ledger` row proving an exact
inference-log row for the current `MIP_GIT_SHA`. Set
`MIP_REQUIRE_AI_GATEWAY_CLAIMABLE=1` for release signoff when the deploy must
fail if that exact proof has not landed yet. Proof and inference-row timestamps
are bounded below by the request/freshness window and above by a five-minute
clock tolerance. Future-dated evidence beyond that tolerance is rejected by the
verifier, runtime lookup, and Lakebase write trigger.
Without strict mode, a proof submission or verification failure is surfaced as
a deploy warning and the App remains honestly configured/unavailable; it never
becomes claimable without the exact signed row. This permits non-release
diagnostics and browser validation while preserving the release gate.
Inference-log tables are delivered asynchronously, so deploy waits up to
`MIP_AI_GATEWAY_GRANT_TIMEOUT_S` (1200 seconds by default, bounded at 3600)
before treating their table-level grants as pending. Non-strict deploys may
continue in configured/unavailable state; the manual live-validation workflow
reconciles the app and verifier grants again before exact proof verification.
Strict deploys fail if grant convergence is incomplete.

Product requests use the MIP-owned `mip-growth-agent-gateway` Agent Model
endpoint. Its MLflow `ResponsesAgent` delegates the unchanged bounded input to
the managed Mortgage Growth Agent Supervisor. Both the managed Supervisor and
outer Gateway endpoint are created by the stable `mip-agent-runtime-ci-sp`, not
a human PAT. The logged model declares the upstream serving endpoint, exact
Genie space, and all three reviewed UC functions as non-user-delegated MLflow
resources. Those resource names and the pinned runtime requirements participate
in the reviewed source hash. The App is
granted `CAN_QUERY` only on the outer Agent Model endpoint; it does not receive a
direct grant on the managed Supervisor endpoint. The runtime separately proves
the configured Supervisor ID-to-managed-endpoint mapping, the immutable creator
of both endpoints and the Supervisor, and the outer endpoint's
`agent/v1/responses` task before labeling a response as Supervisor-generated.

Every Supervisor or proxy-contract change is blue/green and resumable,
including the first migration from a human-owned Supervisor. Deployment derives
contract-hashed Supervisor and outer-Gateway candidate names, creates and proves
those runtime-owned resources without updating live endpoints in place, grants
the App only the green outer endpoint,
and deploys an authenticated-health-verified App snapshot on the green contract
while the old Supervisor remains available. Only then does it re-read the old
agent's pinned ID, endpoint, creator, and create time, revoke the old bypass,
delete the exact old agent and any pinned orphan endpoint, delete a replaced
outer Gateway only when its journaled ID and runtime creator still match, and
rename the Supervisor replacement. The durable journal also resumes
Gateway-only cleanup after interruption. Unexpected tools, examples,
instructions, duplicate names, creator drift, model/source/inference drift, or
a changed old identity stop the cutover before the destructive step.
The first migration from a Supervisor that a person (or any principal other
than the configured agent-runtime service principal) created deletes that
Supervisor and its serving endpoint permanently, so it runs only when the
operator names it: `export MIP_RETIRE_HUMAN_SUPERVISOR=<supervisor id>` (see
[Pre-stop probes](#pre-stop-probes-read-only-before-step-0a)).
Each immutable proxy model version also carries an Ed25519 contract
attestation binding its source hash, Supervisor object ID, managed Supervisor
endpoint ID, runtime application ID, upstream name, Genie space, catalog functions,
experiment family, inference-table family, model name, and immutable MLflow
logged-model URI/ID (`models:/m-...`). Run, registered-version, and alias URIs
are rejected because they do not prove the same logged-model identity.
The v3 signed payload is the first governed durable schema; the unshipped v2
draft is deliberately not accepted because it did not bind the immutable
managed-endpoint ID. Unity Catalog model-version metadata stores that payload as
16 fixed, underscore-only `mip_proxy_*` fields: the 13 contract values plus its
algorithm, signature, and public verification key. Every key and value is
validated against Unity Catalog's 256-character limits before registration.
The earlier dotted single-envelope transport never produced a model version and
is rejected; dotted tags remain only on the separate Serving Endpoint API.
Registration attaches the complete signed field set atomically to the newly
created version. Retained
historical versions are verified against their own signed source contract, not
against today's proxy bytes, so a reviewed source upgrade can allocate a new
blue/green family without making earlier evidence unverifiable. The model
attestation public key is itself part of the allocation hash. A key rotation
therefore creates a distinct current-key model, experiment, inference-table
family, and Gateway endpoint while leaving signed blue resources untouched.
Retained non-candidate models may remain signed by the bounded previous key
only when their allocation suffix recomputes from that attestation record key;
the exact release candidate must use the current key. Verification is
read-only: deploy never rewrites retained model-version tags, and unsigned,
source-drifted, or wrong-epoch versions fail closed.

The deployed proxy authenticates the signed resource envelope and re-reads the
immutable Supervisor ID, creator,
description, instructions, exact four-tool set, zero-example contract, and the
live Unity Catalog metadata/body hashes for all three reviewed SQL functions
before every inference, using only the dedicated proxy OAuth identity. The
model declares no automatic private Databricks resources, so Model Serving
creator credentials are not a second caller. A post-deploy mutation therefore
fails the product request closed instead of remaining a nominally verified
Supervisor path. The deployment verifier separately re-proves the immutable
private endpoint ID and the outer Gateway/model/experiment allocation.

App activation has a separate blue/green rollback contract. Before changing an
existing App, deploy requires a server-owned
`mip-app-rollback/app-last-good-v6-mip-app` secret whose Ed25519 signature and
digest bind the full environment payload, exact health SHA, App service-
principal client and SCIM IDs, Gateway binding, succeeded deployment ID,
immutable `/Workspace/Users/.../src/...` source artifact, and the complete live
Supervisor/Gateway/model/experiment/inference-table resource proof. The v6
record scope is deterministically App-bound: names ending in `-app` append
`-rollback`, while an environment suffix after `-app-` is preserved after the
inserted `-rollback-` segment (for example,
`mip-app-staging` → `mip-app-rollback-staging`). It contains an
App/deployer ownership marker, and is re-audited before every record read,
write, or legacy-key deletion. Its direct ACL must be exactly the original
deployer plus `admins` at `MANAGE`, and its only permitted non-marker keys are
that App's v5/v6 rollback records. An unmarked pre-existing scope, foreign ACL,
or unrelated key blocks deployment without adoption or cleanup. A v7 record
(M2-PR7, below) shares the v6 key, so one slot always holds the one current
record; the v5/v6 reader the release path calls refuses a v7 record by name
and reads a v7 retired tombstone as no record.
The only bounded adoption path is an unmarked, exact-current-deployer scope
containing solely that App's v5 key; deploy verifies the v5 Ed25519 signature,
schema, digests, and Lakebase binding before writing the ownership marker, then
the normal signed-blue verifier re-proves all live resources before any
rollback authority is exercised.
The v6
payload must also bind matching `MIP_LAKEBASE_INSTANCE` and
`LAKEBASE_INSTANCE_NAME` values to the current deployment target. An existing,
signed v5 record is accepted only through its original proxyless field set,
digest, signature algorithm, and exact live-resource verifier. The first
durable v6 capture deletes that legacy key. Older v4 records are intentionally
not rollback authority; an operator must use the explicit stopped-App rebase
path once before the next governed capture. Before any App start, endpoint ACL
grant, rollback deployment, or treatment restoration,
the rollback tool re-reads those immutable resource IDs, owners, exact endpoint
configuration, signed model envelope/source, runtime-owned experiment name/ID,
the experiment's normalized Workspace ACL (runtime `CAN_MANAGE` inherited only
from the independently resolved `/Users/<runtime-client-id>` directory object,
`admins` inherited only from the workspace root, and no other principal),
catalog, App resource target IDs, and Genie input and rejects any drift. Capture
additionally resolves the source-declared App bindings from
`databricks bundle summary` and rejects every
extra, missing, wrong-target, wrong-kind, or wrong-permission live binding
before it can become signed last-good authority. It repeats that proof after
health verification to close deployment races.

Green activation remains treatment-quiesced through hosted tool proof, exact
Gateway proof, evaluation, smoke, and authenticated health. Durable capture
first persists and verifies the signed last-good contract while treatment is
still quiesced, then restores treatment authority and repeats App health,
resource, and lease proof. Deployment acquires an atomic, signed workspace
lease under `/.mip-deployment-leases`; the root-level location avoids
`/Shared`'s inherited `users`-group management permission. Its directory ACL is
pinned to the exact workspace-admin deployer, with one direct `CAN_READ` grant
for the signed agent-runtime writer and only the inherited `admins` group also
retaining management. The writer identity is part of the signed lease record;
an unrelated identity cannot assert the lease, and the delegated writer cannot
release or replace it. Because Databricks restricts ACL reads to managers, the
deployer revalidates the exact directory ACL on every heartbeat and the
read-only runtime requires that signed manager attestation to be no more than
three heartbeat intervals old. Both identities revalidate the exact signed
lease before Lakebase Sync creation, Gateway logging, journal mutation, model
registration or cleanup, endpoint creation, App ACL convergence, Supervisor
rename, and blue-resource deletion. Its one-minute heartbeat renews the signed
fence. A losing contender reads the existing lease without changing that ACL,
and a heartbeat exits without renewal as soon as it is no longer a child of the
deployer that launched it. If process death follows the first signed generation
but precedes its ACL postflight, recovery discovery remains read-only. After the
signed lease expires (with renewal bounded by the six-hour cap), only the exact
signed holder and writer may present that durable root and contend on the one
deterministic non-overwriting successor path. Only the immutable successor
winner converges the exact new holder/writer ACL and then revalidates the
authoritative head; a losing historical holder never changes the shared ACL.
No journal, delete, or overwrite is involved, so an active, unrelated, or
changed fence cannot be replaced. The
lease UUID is injected into the App payload and authenticated
health response, and capture revalidates the live signed lease before and after
activation. Capture records the
`green_treatment_pending_capture` compensation state and advances to
`green_verified` only after the signed secret read-after-write matches and blue
cleanup/global ACL postflights finish. Normal
shell interruption in that window stops/quiesces green and restores signed blue
while still quiesced; every subsequent deploy also quiesces before trusting a
saved candidate. `--skip-smoke`, a missing smoke script, or
`ALLOW_SMOKE_FAILURE=1` never certifies green. With signed blue, rollback proves
blue while quiesced and restores authority only after exact blue health; on a
first install it leaves the unproven App stopped and quiesced and exits nonzero.

An existing legacy installation without this signed record cannot truthfully be
treated as exact blue state because Databricks redacts historical environment
values. Its one-time migration must set `MIP_REBASE_UNVERIFIED_APP=1`; deploy
first proves no last-good record already exists, then identity-pins and stops
that unverified App and uses first-install fail-closed compensation until the
rebased App passes the hosted-tool gate and is captured as the first signed
last-good contract. Remove the flag only after the first successful signed
capture; a failed attempt that remains unsigned requires another explicit
recovery invocation. Once a record exists, the exceptional path refuses to run
even if the flag is mistakenly selected. Routine CI must never set it. New
installations capture their first record automatically.
`MIP_APP_ROLLBACK_SECRET_SCOPE` is a reviewed non-secret setting, but its value
must match that deterministic transformation; arbitrary shared scopes are
rejected before workspace mutation.

Databricks Agent Model endpoints currently support AI Gateway payload logging,
but not Gateway rate limiting or usage tracking. The provisioner therefore
creates each immutable endpoint with its own contract-hashed inference-table
family under `<catalog>.audit.mip_agent_gateway_growth_agent_<resource-hash-12>`.
The resource hash binds source, model family, experiment family, schema, table
family, and the model-attestation public-key epoch. Databricks
does not support pointing two endpoint creations at an existing inference
table, so blue and green never reuse a table prefix. Historical table families
are retained for audit; App and verifier grants converge to the exact current
family. Request budgets remain enforced by the application's authenticated
backpressure middleware. Do not add unsupported Gateway rate-limit fields to
this Agent endpoint or describe them as live proof.

Interrupted model registration uses an append-only MLflow experiment journal.
Databricks exposes experiment-tag set/update but no delete operation, so the
runtime writes a SHA-256-addressed journal tag and later appends an exact
digest-bound retirement tag. The reader rejects malformed reserved tags,
orphans, multiple journals, reuse after retirement, and any journal whose
canonical JSON, signed model contract, model source, run, or experiment binding
does not verify—even when a matching retirement tag exists. Journal writes,
retirement, registration cleanup, and final endpoint creation are all fenced by
the signed deployment lease above.

Exact-row proof also requires an Ed25519 attestation from the verifier. Store
`MIP_AI_GATEWAY_PROOF_SIGNING_KEY` only in deploy/verifier automation. It signs
exact-row, App rollback, and cutover-journal evidence, but never model artifacts. The deploy
script derives `MIP_AI_GATEWAY_PROOF_VERIFY_KEY` and injects only that public key
into the App. Lakebase proof writers therefore cannot make AI Gateway claimable
by inserting or editing a proof row; the runtime verifies the signature over
the deployment SHA, request id, endpoint, inference table, and timestamps.
Rotate the key by setting the new signing key and temporarily setting
`MIP_AI_GATEWAY_PROOF_PREVIOUS_VERIFY_KEY` to the old public key. Early deploy
reconciliation verifies the signed last-good record with that bounded previous
key and immediately re-signs it with the new current key; the promoted App then
receives only the new public key and generates a fresh exact-row proof. Remove
the previous-key setting after that successful deploy, but first append its
public key oldest-first to `MIP_AI_GATEWAY_PROOF_HISTORICAL_VERIFY_KEYS` so the
append-only lease and first-install journal chains remain readable. Historical
keys are accepted only for those chain-verification paths; exact-row promotion
does not accept them, and proof rows signed by the prior key then fail closed.

Proxy-model provenance uses an independent Ed25519 authority:
`MIP_GATEWAY_MODEL_ATTESTATION_SIGNING_KEY`. The deploy preflight rejects a
model key whose derived public key equals the proof key. Only the bounded
runtime-owner registration command receives the model private key; exact
resource exporters and the deployed model receive
`MIP_GATEWAY_MODEL_ATTESTATION_VERIFY_KEY` instead. Rotate model attestations
with `MIP_GATEWAY_MODEL_ATTESTATION_PREVIOUS_VERIFY_KEY`, independently of the
proof/rollback rotation. The new current public key allocates a separate green
resource family; retained historical versions and blue endpoints are never
re-signed or updated in place. The ordinary UC postflight is public-key-only
and accepts the previous key only for a non-candidate model whose name suffix
is bound to that exact record key. Keep the previous public key configured
while any governed retained version still depends on it, or retire that
historical family through the reviewed retention process before removing it.

The App service principal does not receive direct Supervisor, registered-model,
or MLflow-experiment access. App-side capability and generation checks
authenticate the canonical signed resource envelope and re-read only the outer
Gateway endpoint the App is authorized to query. The runtime-owned served proxy
re-proves the private Supervisor definition, registered model/version,
experiment owner/ACL, and UC functions before every inference. This separation
keeps the product path functional without widening the App around the governed
Gateway boundary.

This attestation proves what the separated verifier observed at verification
time; it is not a tamper-independent audit of the inference-table owner.
Databricks makes the endpoint creator (`mip-agent-runtime-ci-sp`) the owner of
the endpoint's inference tables, so that identity can modify those tables. The
claimable row therefore means “a verifier-credentialed process observed and
signed a timely exact delivery row after a tool-bearing live response,” not
“the runtime owner was cryptographically unable to alter the source table.”
Customers requiring owner-independent retention must export Gateway logs to a
separately governed immutable sink; Module 0 does not claim that stronger
control.

The App payload binds `MIP_COTALITY_ID_MASK_SECRET` and
`MIP_GENIE_ACTION_SECRET_CURRENT` from the runtime secret scope for every
sandbox, staging, customer and production runtime; only `APP_ENV=local` and
`APP_ENV=test` may use compatibility keys. The prior action key
(`genie-action-previous`) is bound only when the public
`MIP_GENIE_ACTION_SECRET_PREVIOUS_KID` is configured for the deploy; no key
value is needed, because the scope already holds the key. A
`MIP_GENIE_ACTION_SECRET_PREVIOUS` value without its KID is rejected, by the
runtime secret plan before the App stop and again by the payload. The
bundle's static resource declarations still need every backing Databricks
Secret key to exist, so an
absent one gets a random disabled sentinel that the runtime ignores. Keep the
real prior key available for at least two hours so existing action
confirmations and one-hour generated-copy/cohort provenance tokens can
expire. The previous key is verification-only; new tokens always use the
current key.

The provisioner (`tools/databricks/provision_runtime_secrets.py`) is
presence-only. It decides every write before it makes one, and it never reads
or rewrites a key that already exists:

- **Existing keys are kept unread.** `cotality-id-mask-v1`,
  `genie-action-current` and `genie-action-previous` are kept whatever the
  deploy's inputs say, and the plan names an input that is set but unused.
  The tool only lists the scope's keys; it never calls `getSecret`, so no
  stored value reaches the runner, CI or a log, and the deployer needs no
  READ on a value. Writing a different mask would re-key every masked
  borrower id and orphan the `B-…` ids already stored in Lakebase campaigns,
  approvals, audit, watchlists and snoozes, so a deploy never does it. A
  deliberate re-key is an owner-run data migration.
- **Absent keys are written once.** On a first install the mask and the
  current action key are generated on the runner (`secrets.token_hex(32)`),
  unless the optional `MIP_COTALITY_ID_MASK_SECRET` or
  `MIP_GENIE_ACTION_SECRET_CURRENT` input (shell or `.env.local`) seeds them
  with a value the install already uses. `genie-action-previous` takes
  `MIP_GENIE_ACTION_SECRET_PREVIOUS` (with its KID) or the disabled sentinel.
  Each write is read back for presence (the key is listed), never for its
  value. No workflow carries these inputs
  (`tests/unit/test_runtime_secret_inputs_stay_out_of_ci.py`).
- **A deleted mask is never regenerated.** A run writes the mask before any
  other key, so a scope that lists `genie-action-current` or a
  `salesforce-*` key without `cotality-id-mask-v1` is an established install
  whose mask was deleted, not a first install. The plan refuses it with exit
  2, before the App stop, unless `MIP_COTALITY_ID_MASK_SECRET` (shell or
  `.env.local`, never a workflow) restores the value the install used.
  `genie-action-previous` alone does not count: `--retire-previous` may create
  it before the first deploy.
- **Salesforce credentials.** When a `SALESFORCE_*` input is unset, an
  existing `salesforce-*` key is kept. The disabled sentinel is written only
  when the key is absent, because the App's static resource binding needs a
  backing key. A configured value still replaces the key. The App payload
  binds the `salesforce-*` keys into the runtime only when the instance URL,
  client id, username, external-id field, client secret and password inputs
  are all set for that deploy (`settings.salesforce_configured` needs the
  same set at runtime), and it binds the security token only when its own
  input is set. For the three secret inputs, "set" means the same thing to
  the provisioner and to the payload: a placeholder (such as `<password>`
  or `changeme`) counts as unset in both. So a key kept because its input is
  unset or a placeholder is never bound and cannot re-enable the connector
  (`tests/unit/test_salesforce_secret_binding_parity.py`, through both the
  shell and `.env.local`). A real secret shaped like a placeholder (it starts
  with `<`, ends with `>`, or equals a placeholder word) is read the same way;
  when the four non-secret inputs are set, the payload prints a names-only
  `Salesforce notice:` naming each such input, never its value. Unsetting the
  inputs disables the connector but no longer
  clears the stored credentials. To revoke them deliberately, set the secret
  inputs to the replacement values, or have the owner delete the keys
  (`databricks secrets delete-secret mip-runtime <key>`); the next deploy then
  writes the disabled sentinel.

`--check --require-existing` (and the write run with `--require-existing`)
refuses with exit 2 when the plan would write anything, including a scope it
would create. An install that is already provisioned plans zero writes, so
run it before adopting an existing install: a misnamed
`MIP_RUNTIME_SECRET_SCOPE` (or a scope deleted and recreated empty) lists no
runtime key, so it would otherwise look like a first install and mint a new
mask. Run it with the `SALESFORCE_*` inputs unset: a configured one replaces
its key by design, which counts as a write.

Before its first Secrets API call, every mode of the tool (`--check`, the
write run, `--retire-previous`, `--rotate-genie-action`) proves which
workspace it is about to call.
Building the SDK client can already contact the resolved host for an OAuth
token, but no secret is listed or read before the proof.
`WorkspaceClient()` resolves the Databricks SDK's default chain: process
environment first, then the `~/.databrickscfg` DEFAULT profile, which may name
a different, shared workspace. The tool never binds the `.env.local` host or
token itself. Instead, the resolved host must equal every configured install
host: `DATABRICKS_HOST` in the shell or in `.env.local`, and
`MIP_DATABRICKS_WORKSPACE_HOST`, which `deploy.sh` exports once it has bound
the deployer. At least one of them must be set. Otherwise the tool exits 2,
names the resolved host and each disagreeing source, and reads nothing. Hosts
compare as `scheme://host[:port]`, lowercased. Every summary line names the
resolved host (`runtime secret plan for mip-runtime on https://…: …`).

`deploy.sh` runs the same plan read-only (`--check`: no scope creation, no
write) at the end of step 0. That is after it binds the deployer's workspace
identity, before the signed workspace lease, and before step 0a stops the
App, so a refusal leaves the App serving. To run the check by hand before a
deploy, pin the install's identity so the SDK cannot fall back to DEFAULT:

```bash
DATABRICKS_CONFIG_PROFILE=<install profile> \
  python tools/databricks/provision_runtime_secrets.py \
  --scope "${MIP_RUNTIME_SECRET_SCOPE:-mip-runtime}" --check
```

Confirm that the host on the printed plan line is the install's workspace
before you act on the result. Pin the profile the same way for
`--retire-previous` and `--rotate-genie-action`.

To rotate the Genie action key, an operator runs the human-only rotation (it
refuses under GitHub Actions, where a deploy only keeps keys). It is the one
mode that reads a stored value: it moves `genie-action-current` to
`genie-action-previous`, writes a newly generated `genie-action-current`, and
prints key names only. The App signs with the new key from its next
deployment:

```bash
DATABRICKS_CONFIG_PROFILE=<install profile> \
  python tools/databricks/provision_runtime_secrets.py \
  --scope "${MIP_RUNTIME_SECRET_SCOPE:-mip-runtime}" \
  --rotate-genie-action
./scripts/deploy.sh -t client
```

For that deploy, set `MIP_GENIE_ACTION_SECRET_KID` to the new key id and
`MIP_GENIE_ACTION_SECRET_PREVIOUS_KID` to the old one (in `.env.local`, or as
variables of the deploy's GitHub environment; they are public ids). The
payload then binds `genie-action-previous`, the old key the rotation moved
there, as the grace key, so outstanding action confirmations (two-hour TTL)
and provenance tokens keep verifying, and open tabs re-stamp to the new actor
key with no reset. No key value rides the workflow. Without the previous id
those tokens stop verifying at that deployment. If the rotation's second
write fails, both keys hold the old value: rerun `--rotate-genie-action`
before any deploy, because the runtime refuses a previous key equal to the
current one.

After a grace period, retire the previous key:

```bash
unset MIP_GENIE_ACTION_SECRET_PREVIOUS
unset MIP_GENIE_ACTION_SECRET_PREVIOUS_KID
DATABRICKS_CONFIG_PROFILE=<install profile> \
  python tools/databricks/provision_runtime_secrets.py \
  --scope "${MIP_RUNTIME_SECRET_SCOPE:-mip-runtime}" \
  --retire-previous
./scripts/deploy.sh -t client
```

Retirement is idempotent and does not require the current or Cotality secret
values. It replaces `genie-action-previous` with a fresh disabled sentinel,
which immediately invalidates old tokens while keeping the static Databricks
App resource valid. An ordinary deploy no longer does this: it keeps an
existing previous key, which stays unbound while
`MIP_GENIE_ACTION_SECRET_PREVIOUS_KID` is unset. An id left set after
retirement binds the sentinel, which verifies nothing.

`make bundle-validate` (`bundle_env.py validate -t client`) remains a
diagnostic that changes no resource; the CLI creates the root's empty `files`
folder when it is missing. `make bundle-plan` and the mutable
`make bundle-deploy` and `make bundle-deploy-dev` aliases are retired and fail
closed. The internal bundle wrapper accepts mutation only
with exact non-App selectors supplied by `scripts/deploy.sh`; it rejects
unrestricted or App selectors. Always rerun `./scripts/deploy.sh` for recovery
so complete resource bindings, durable secrets, the signed source contract,
refreshes, and smoke gates are preserved. A direct Apps promotion bypasses the
required full deployment payload and is unsupported.

For disaster-recovery rollback paths, including Lakebase point-in-time
restore, prior app snapshots, source-regression rollback via
`git checkout <prior-good-sha>`, Genie space re-provisioning, audit archival,
and governed-action HMAC key rotation, use
[`docs/disaster-recovery.md`](disaster-recovery.md).

### The agentic tier (`MIP_AGENTIC_TIER`, M2-PR3)

The client profile's `MIP_AGENTIC_TIER` decides which reserved M2M identities a
release uses (plan §4.15). `resolve_client_profile`, the first step of
`./scripts/deploy.sh`, reads it from the client-target view together with its
identity contract (`tools/databricks/m2m_identity_contract.py`): the client-id
name of the signed lease's writer and of every role the tier deploys with. A
view with no tier, another value than `off` or `on`, or a contract that
disagrees with its tier exits 2 before any other step; no step has a default
tier.

| Site | `on` (agentic, client #1 today) | `off` (core) |
|---|---|---|
| identities | normal, operator2, admin, release probe, verifier, agent runtime, Supervisor proxy | normal, operator2, admin, release probe |
| per-run credential checks | all seven | the four core ones |
| model-attestation key | required | not used |
| signed lease writer (`lease_writer`) | agent runtime | release probe |
| pre-stop probes | `--runtime-application-id` and `--verifier-application-id`; Supervisor and Genie clauses for App, runtime and verifier | `--agentic-tier off`, neither id; no Supervisor listing or clause, so the App may hold no Supervisor serving access; the Genie clause audits the App (on a first install, its future service principal as a `users` member) |
| runtime UC audit, verifier Lakebase role, runtime/verifier/proxy isolation audits | run | skipped |
| Lakebase bootstrap control identity (`run_with_lakebase_bootstrap_authority`) | the agent runtime, required up front | the release probe, required up front (it has no Lakebase role and is never a converged target) |
| UC grants and the reviewed-function reconcile | App, runtime and proxy | App only (`verify_reviewed_function_execute_grants --agentic-tier off`) |
| migrate job verifier flag (`var.require_ai_gateway_verifier`) | the bundle default `--require-ai-gateway-verifier` | `--ai-gateway-verifier-absent`, no verifier bound |
| quiesce window plan (`plan_quiesce_window`, M0-PR8) | App, verifier and jobs-reader role checks; the migration plan requires the verifier | App and jobs-reader role checks; `--verifier-role-check absent` (no WR2 from it, pinned in the digest); the migration plan asserts `--ai-gateway-verifier-absent` |
| App payload | unchanged | `MIP_AGENT_ORCHESTRATOR=0`, `MIP_AI_GATEWAY=0`; an operator value turning either on refuses; no other agentic name (agent model, Supervisor, Gateway, proxy, proof key or eval; M2-PR7b) |
| capability preflight (`tools/clients/capabilities.py`, in the live checks) | Apps, Lakebase, Genie, SQL warehouses, Model Serving, MLflow (the pre-stop probes' Supervisor listing proves the Supervisor API) | Apps, Lakebase, Genie, SQL warehouses |
| pre-stop record clause and step 0a (M2-PR7b) | v5/v6 `verified_signed_last_good_contract` and `app_deployment_rollback ensure`; a v7 record refuses | a v7 core record through the tier gate and the core resource proof (`app_core_rollback ensure`) |
| quiesce window over signed blue (M2-PR7b) | v5/v6, start and re-pin | a v7 core blue, start and re-pin onto the next v7 version (`app_core_rollback repin`, spike S7) |
| steps 10b-10c: agentic provisioning, cutover, Agent Evaluation and the retirement after capture | run (`deploy_step_agentic_*.sh`, `deploy_step_agent_eval_and_smoke.sh`) | skipped by name: never sourced, and each refuses the core tier (`deploy_step_core_activation.sh` runs instead) |
| activation (M2-PR7b) | the cutover's activation, then the Agent Evaluation redeploy | one activation rollover (a first install's is step 5's) |
| live smoke | `MIP_EXPECT_AGENTIC_CAPABILITIES=1` by default | `MIP_EXPECT_AGENTIC_CAPABILITIES=0`, `MIP_REQUIRE_AI_GATEWAY_CLAIMABLE=0`; Genie is still smoked |
| signed capture (M2-PR7b) | v6 `app_deployment_rollback capture` with the Gateway binding | a v7 core record, provenance captured (`app_core_rollback capture --agentic-tier off`) |
| signed-blue restore (M2-PR7b) | v5/v6 `app_deployment_rollback restore` | tier b of the v7 core record (`app_core_rollback restore`) |

The agentic tier's plan, bundle variables and App payload are byte-identical
to those before the tier was read (the PR's artifact-hash proof;
`tests/unit/test_agentic_tier_dry_run.py` pins its tier-aware lines). The
emergency recipe builds the agentic payload by default and keeps its 32 env
names.

**The core release (M2-PR7b).** #311 left a core release refusing before the
lease, and again at step 5, because publish, activation, signed capture and
the signed-blue restore still needed the agentic identities. Both refusals
are lifted now that every one of those sites has a core path; the core tier
runs exactly the four core identities and the deployer, and never the
verifier, the agent runtime or the Supervisor proxy:

- **Publish** (step 5) is tier-neutral: the governed create-only publish of
  the release folder.
- **Pre-stop and step 0a.** The pre-stop record clause and step 0a judge the
  rollback record with the tier gate (`tools/databricks/blue_tier_gate.py`)
  and the core resource proof (`tools/databricks/core_blue.py`): the App's
  service-principal identity and exactly the record's core bindings (a
  serving-endpoint binding refuses by name and is never repaired). Step 0a
  (`app_core_rollback ensure`) restores the record-owned bindings a killed
  run left, proves health against the record's commit with no Gateway
  binding, and restores blue (tier b) when another deployment is active or
  newest. Tier b needs blue's restore payload: an adopted blue no release
  has proven yet has none, so another deployment over it refuses
  (`no_restore_payload`), and the pre-stop clause predicts that before the
  lease (`rollback_record_drift`; the remedy is the owner break-glass or an
  adopt-live supersede onto the live deployment). A stopped core blue is
  started and re-pinned (tier a, spike S7): the start's
  new deployment is proven to be blue (its source folder, the record's
  full-tree digest and env names, health) and the record is re-signed onto
  it (`tools/databricks/core_blue_repin.py`). A stopped App whose newest
  deployment is blue but whose default source is not blue's folder (its next
  start would not rerun blue) refuses (`tier_a_refused`): the pre-stop clause
  predicts it before the lease (`rollback_record_drift`), and step 0a itself
  refuses it before any binding repair. A start snapshots the folder's
  CURRENT bytes, so before any start step 0a re-hashes the folder against the
  record's signed `source_tree_sha256` and checks blue's env names: a folder
  that is no longer blue's is tier b's (restored from the immutable copy, or
  refused before any start, `no_restore_payload`, when the record has no
  restore payload; the pre-stop clause predicts both). Databricks deploys
  only to a running App, so tier b over a stopped App starts it first: that
  start runs the folder's current bytes, as the App's service principal,
  until blue's immutable copy replaces them. A start that then fails its
  proof, its health or anything else before its re-pin never keeps serving:
  tier b replaces it, or, with no restore payload, it is stopped, pinned to
  the App's id and the record's identity, and the refusal raised. Such a
  failed start (or one a lost runner left behind) is the App's newest
  deployment, from blue's own folder with blue's env names, so the next run
  never strands on it: step 0a re-pins it in place when the running App
  serves it SUCCEEDED and its snapshot proves to be blue's signed tree, and
  otherwise stops it if running and starts blue's folder again
  (`core_blue_restore.stranded_start`), which the pre-stop clause predicts.
- **The tier gate.** A core release stands on a core blue. An agentic blue (a
  v5, v6 or v7 agentic record) under a core profile refuses
  (`blue_tier_mismatch`) and no ack lifts it: its restore needs the Gateway
  and agent-proxy proofs, so the agentic identities. A core blue under an
  agentic profile needs the exact `tier_transition` ack (this App, this
  blue, this signed record version and both tiers), and is then still
  verified and restored by the core proof; the agentic release path that
  would use it (client #1's run 2) is M4-PR1's, and today it refuses a v7
  blue before the lease as before. Client #1's run 1 needs no transition:
  blue was adopted as core and green is core.
- **The quiesce window.** The window plan reads a v7 core blue (its env-name
  set from the record) and plans no window when no reason exists. Spike S7 is
  recorded, so a v7 core blue that needs a window gets the one v5 and v6 get:
  the folder's full-tree digest is checked against the record's own
  `source_tree_sha256`, the start is proven with the record's env-name set,
  and the window library re-pins it with `app_core_rollback repin` (the
  dead-man's restart with `repin --from-deadman`) onto the record's next v7
  version (same provenance and ack, `predecessor_deployment_id` blue). A v7
  agentic record is still refused (M4-PR1).
- **Activation** (`scripts/lib/deploy_step_core_activation.sh`): one
  activation rollover over signed blue, with blue's treatment authority
  quiesced immediately before it, exactly as the agentic cutover activates.
  The pre-activation proof runs first, before that quiesce and before the
  quiesce window is superseded. For an adopted blue with no restore
  payload yet, the release rebuilds one with the payload builder
  (`app_core_rollback restore-payload`, read-only): Databricks redacts env
  values, so it is rebuilt from the profile the way the emergency recipe
  built blue (the immutable copy as its source, blue's commit as
  `MIP_GIT_SHA`, the treatment runtime off, and, as in every core payload, no
  agentic name), and handed to the proof only
  when its env-name set is exactly blue's; otherwise the proof needs the
  exact `no_rollback_for` ack. The profile's optional role names
  (`tools/clients/profile.py` `OPTIONAL_PAYLOAD_ENV_NAMES`: the auditor
  identities, the role emails and group names) that blue's set lacks are
  left out of the rebuild, since blue ran without them; any other difference
  still refuses. Its values are the profile's current values,
  not necessarily the adopted deployment's own. A blue whose `frontend/dist`
  is unverified is restorable only with its `restore_unverified_frontend`
  ack, so the proof requires that exact ack before activation too (export
  `MIP_ACK_RESTORE_UNVERIFIED_FRONTEND`, the digest the refusal prints, for
  the run; `unverified_frontend_unacked`); adopting with
  `MIP_ADOPT_BUILD_REFERENCE_DIST=1` avoids it. That ack covers this blue,
  this restore payload and the run's own commit: the activation gate and the
  same run's tier-b restore ask for the one digest, and a later run at
  another commit needs its own, which its refusal prints.
- **A refused pre-activation proof** (`unverified_frontend_unacked`,
  `no_rollback_proof`, a rebuild that fails) exits 2 at step 10b while signed
  blue still serves. Over signed blue the run reaches step 10b in the quiesce
  window's `blue_degraded` state, not step 0a's `blue_serving`: step 4c
  quiesced the App's synced-catalog access (in either tier), and the
  catalog bootstrap may have republished the reviewed functions. The EXIT
  trap compensates that state with the App up (`compensate_serving_blue`):
  it proves blue still serves the signed record, reconciles the reviewed
  function grants when they are unproven, converges the release access and
  treatment authority (quiesced since step 0a) back to runtime, and releases
  the lease. In the core tier it leaves the synced-catalog access quiesced,
  as a successful core release does (`MIP_LAKEBASE_SYNC`, below), so an
  estate with no Lakebase synced tables compensates the same way. It never
  restores blue over itself and never stops it. The refusal is not "nothing
  changed": steps 1 to 10 (grants, migrations, the data refresh) already
  ran, and the re-run with the printed digest repeats them.
- **The live smoke** expects no agentic capability; Genie is smoked as in the
  agentic tier (a Genie space is a core resource).
- **The signed capture** (`tools/databricks/core_blue_capture.py`): a v7 core
  record, provenance captured, over the core resource proof, with this
  release's folder as its restore source and the deployed payload as its
  restore payload. Health must name this commit and lease with no Gateway
  binding, the folder and the deployment's `/src/` snapshot must hash to the
  release this run staged (so `frontend/dist` is `verified`), and the live
  bindings must equal the profile's reviewed core bindings. Treatment is
  quiesced while the record is written and read back, then converged to
  runtime, as v6's capture does.
- **The restore** (tier b, `app_core_rollback restore`, from the EXIT-trap
  library): blue's restore payload from the immutable copy, re-hashed before
  a stopped App is started and again immediately before the deploy; the new
  deployment proven to be blue (active, newest, from the copy, its `/src/`
  snapshot hashing to the record's full-tree digest, with blue's env-name
  set, healthy on blue's commit) and the record re-pinned to it
  (`predecessor_deployment_id`, provenance and ack kept). When green's deploy
  never began (a failure between the activation quiesce and the deploy),
  blue is still the active and newest deployment, nothing is pending, the
  App runs and its health is proven: the restore then redeploys nothing,
  never re-pins, and only converges treatment back. Any doubt is tier b's.
- **Skipped by name in the core tier:** the agentic Lakebase Sync proof,
  verifier capture, historical endpoint retirement, Supervisor, proxy and
  Gateway provisioning and audits, the cutover journal, Agent Evaluation and
  the agentic retirement after capture. `scripts/deploy.sh` sources those
  step files only in the agentic tier, and each refuses the core tier.

- **The agentic env cache** (`.databricks/mip-agentic.env`, an agentic
  run's provisioner output, not keyed by client) is never carried into a
  core release: it would turn both core switches on, which the core payload
  refuses, and name another install's Supervisor and Gateway.
- **`MIP_LAKEBASE_SYNC`** is not an agentic name, so an operator value rides
  a core payload, but the core tier neither proves the Lakebase Sync nor
  converges the App's synced-table grants to runtime (both are
  `deploy_step_agentic_provisioning.sh`'s, agentic-only). Step 4c still
  quiesces the App's synced-catalog access in either tier (it also removes
  the historical direct grants on the Lakebase schemas), and a core release
  leaves it quiesced whichever way it ends: only the agentic step converges
  it back, and the EXIT trap never does in the core tier. A core install
  leaves the switch unset: no profile field sets it, and the cache that would
  is not carried. Set by hand, the App's capability row reports its live
  synced-table probe, which fails while that access stays quiesced.

The agentic tier's plan, bundle variables, payload and the emergency
recipe's 32 env names are unchanged by this (the PR's artifact-hash proof).
`tests/unit/test_core_tier_release_sandbox.py` runs a first install's core
release end to end in the sandbox with no agentic call (with and without a
leftover agentic env cache), and releases the lease after a green run, a
failed smoke and a failed capture. An upgrade over signed blue is pinned
below the sandbox: the activation block through the real EXIT trap
(`tests/unit/test_core_activation_exit_trap.py`) and the core tools over an
adopted blue (`tests/unit/test_core_blue_restore.py`).

**What the core payload turns off.** `MIP_AGENT_ORCHESTRATOR=0` and
`MIP_AI_GATEWAY=0` are the settings' own defaults, so for these two switches a
core App serves exactly what the emergency recipe's payload serves (it sets
neither): the
Growth co-pilot plans with its reviewed deterministic workflow rules, never
the Agent Responses Supervisor; composition and the Supervisor runtime
report the orchestrator disabled; no AI Gateway signal or chip renders, and
the capability page reports both rows not provisioned. `/ask-genie` and the
floating Genie panel are unchanged: they use the governed Genie space, a
core binding.

### PII salt (write-once)

The silver SQL and the Lakeflow feature pipeline hash PII with
`secret('mip', 'pii-salt-v1')`. Every masked join key in an install derives
from that value, so the salt is written once and never replaced. The Secrets
API overwrites on put and has no create-only form, so step 4d provisions the
salt with `tools/databricks/pii_salt_scope.py ensure`, which writes only after
a listing it can trust:

- It lists the secret scopes and, when the scope exists, its keys. Any list
  error (a permission error, a timeout, a lost page, a scope that vanished
  between the two calls) or a malformed listing refuses with exit 2 before
  any create or put. A failed listing is never read as "absent". The inline
  step it replaced did read it that way, so a failed `list-secrets` would
  have put a fresh salt over the live one.
- It creates the scope only when the scope listing lacks it, and then lists
  again. It puts a fresh random value (32 bytes, hex) only when a listing
  lacks the key, and then requires the key to be listed. An existing key is
  never written and its value is never read.
- Exit 1 means a put was attempted and its result could not be proven.
  Re-run the deploy; it lists again before any further write.

Right after the pre-stop probes below, before the signed lease and step 0a,
`deploy.sh` runs the same inventory read-only (`check`), so a listing the
deployer cannot complete refuses while the App still serves. Neither mode
prints the value. Both print scope and key names, and show an SDK error only by
its class. The one exception is the workspace-binding refusal, which runs
before any Secrets API call: it names the host the SDK resolved and the
install's configured host, so the operator can see which identity to pin.

`MIP_PII_SALT_SCOPE` (shell, then `.env.local`; default `mip`) names the
scope. The readers take the scope as a literal, so preflight refuses any other
value until they read the control. `--require-existing 1` refuses an absent
scope or key instead of creating it; `deploy.sh` passes `0`, so a first
install still creates the salt, until the install profile records that masked
keys already exist. To check by hand, pin the install's identity:

```bash
DATABRICKS_CONFIG_PROFILE=<install profile> \
  python -m tools.databricks.pii_salt_scope check --scope mip --require-existing 1
```

### The governed Genie space, pinned by id (M1-PR3)

Anyone who can create a Genie space can give it the governed title, and under
the live-first doctrine whoever controls the space controls the answers
(attack G12). So the deploy never adopts a space by its title alone, and
`GENIE_SPACE_ID` is no deploy input: an ambient value is unset at startup,
`bundle_env.py` ignores it in the shell, `.env.local` and `genie/space_id.txt`,
and `deploy-dev.yml` no longer carries it. The App's own `GENIE_SPACE_ID` is
unchanged: a `value_from: genie_space` resource reference whose binding
`databricks.yml` takes from `var.genie_space_id`, which `bundle_env.py` sets
from the pin. The App's env-name set is therefore exactly the one the
emergency recipe gates on.

`tools/databricks/genie_space_pin.py` resolves the space:

1. **A pin.** The signed pin record (`genie-space-pin-v1-<app>` in the App
   rollback secret scope, one of the keys that scope's ownership proof admits,
   signed with the deploy's proof key like the rollback record) or the App's
   signed rollback record names the space by
   id. When both exist they must agree, and the space must still exist; a
   pinned space that is gone refuses and never falls back to a title.
2. **No pin, no space with the title.** A first install: step 0a creates the
   space and pins its id at once, so a run interrupted before its capture
   still finds its own space next time.
3. **No pin, one space with the title.** Refused (exit 10): a space the deploy
   never created or pinned. The owner attestation is deferred, so the one way
   to adopt it is an exact digest ack. The read-only plan prints the candidate
   id, the principals holding a direct CAN_MANAGE on it (the Genie API reports
   no creator; the creator is among them), the deploying identity and the
   plan's sha256. Review it, then run with `MIP_GENIE_PIN_ACK=<sha256>` exported
   in the shell (never `.env.local`). Step 0a recomputes the plan immediately
   before it pins and refuses with nothing changed if the digest moved. In CI
   the plan's id, holders and deployer are runner masks, so the owner reviews
   it with `tools.clients.genie_pin_review` (read-only, counts and one digest
   only): it recomputes the plan's sha256 from the profile's
   `MIP_IDENTITY_IDS_JSON.deployer`, which must equal the CI log's, and counts
   the grantees the pre-stop owner-equivalent probe would refuse on that space
   (the cutover runbook's 10B-0).
4. **More than one.** Refused as ambiguous.

**The adopt phases (B4(c)).** Adopting an existing install (below) binds the
legacy state before anything pins its space: no pin record exists, and the
signed v7 record that names the space (`genie_space_id`) is what adopt-live
writes last. So the adopt phases' own bundle calls, `install_state_adopt`'s
plan, binds and unbinds and adopt-live's bundle summary, pass the wrapper-only
`bundle_env --adopt-acked-genie-pin`. With it, and only while no pin names a
space, bundle_env binds case 3's one title match when the shell's exact
`MIP_GENIE_PIN_ACK` (never `.env.local`) is that plan's sha256, through the
provisioner's own resolution (`bundle_env_adopt.acked_title_space_id`): a pin
still wins, a pin refusal still refuses, and no ack, another plan's ack, no
title match or two of them refuse (exit 2). bundle_env accepts the flag once,
on `plan`, `summary` and an adopt plan's `deployment bind|unbind` only, never
beside `--allow-unprovisioned-genie`, and never passes it to the CLI. It
writes no pin: adopt-live's signed v7 record pins the space, and its
`bindings_differ` check makes that the space the live App binds. bundle_env
builds a bound deployer profile's client with the provisioner's own builder
(`workspace_auth.deployer_profile_client`), so the deploying identity in the
digest bundle_env recomputes is the one the step-0 plan printed. Each call
re-resolves the match (the space list, its permissions, the deploying
identity), the undo's unbinds included: a direct CAN_MANAGE holder that
changes mid-run refuses every later call (`the ack names another plan`), and
when that lands among the binds the undo is unproven (exit 3, half-bound).
The recovery is `--phase unadopt` with a fresh ack: the step-0 pin check
prints the new plan's sha256.

The preflight runs the same resolution read-only
(`provision_genie_space --plan-pin`) before the signed lease and any App stop,
so a squatter or a stale pin refuses while the App still serves. Run it by hand
to read the plan:

```bash
python -m tools.databricks.provision_genie_space --plan-pin \
  --space-name "<genie-space-title>" --app-name <app-name> \
  --pin-scope <app-rollback-scope> --lakebase-instance <lakebase-instance>
```

Reading a pin needs `MIP_AI_GATEWAY_PROOF_VERIFY_KEY` exported; writing one
needs the signing key too, which the provisioner checks before any mutation.

Two operator-only modes; `scripts/deploy.sh` passes neither, and
`tests/unit/test_genie_space_pin_retire.py` pins that:

- **A deleted space (disaster recovery).** A pinned space that is gone refuses
  every deploy. `--retire-pin --plan-pin` proves it gone and prints the
  read-only retire plan and its sha256; `--retire-pin --ack-plan <sha256>`
  recomputes the plan right before its one write, a signed tombstone pin
  record that pins nothing and lists the gone id in `retired_space_ids`. The
  next deploy then creates and pins a new space. A rollback record naming the
  gone id is superseded until the next green capture (the pre-stop probes
  judge the space step 0a will use, not the record's gone one), and a retired
  id is never pinned again. A space that still exists is never unpinned. See
  `docs/disaster-recovery.md`, Scenario 5.
- **The legacy live install's Genie spec, until it is adopted (M2-PR6).** It
  has no pin and no signed rollback record, so its space is an unpinned title
  match, which the stand-alone provisioner no longer updates by title alone
  (before M1-PR3 it did). Run `--plan-pin` (read-only), review the plan, then
  run `--no-pin --ack-plan <sha256>`: the acked space is updated and no pin is
  written, so the live workspace gains no secret and no signing key is
  needed. Without `--no-pin`, the ack also pins the space, which needs the
  signing key pair and the App rollback scope this deployer owns. `--no-pin`
  never creates a space.

### Pre-stop probes (read-only, before step 0a)

Step 0a stops the existing App, and on the unsigned rebase path also
quarantines its access. Without a signed blue record any later failure leaves
the App stopped. Right after the per-run M2M credential checks, and before the
signed lease or any other workspace mutation, `./scripts/deploy.sh` runs
`tools/databricks/deploy_prestop_probes.py` as the workspace-admin deployer,
with the bounded account-SCIM credential (the owner policy resolves owners in
account SCIM) and never the proof-signing key. It performs only GET/list calls,
except the one Genie turn below, and lists every refusal in one pass (exit 2,
nothing changed, the App not stopped by this run) for these failures, which
would otherwise surface only after step 0a. Each refusal line reads
`[mip-prestop] REFUSED: <code>: <message>`, with one reviewed code
(`tools/databricks/prestop_refusals.py`). A read a probe cannot complete (an
unreadable Genie ACL, an App read error other than "not found", a SCIM or
Supervisor listing error) refuses as `probe_error`, never passes; a non-admin
deployer stops the probe with that error. Either way the deploy stops before
the signed lease. The `deploy-dev`
workflow runs the same probe and never sets `MIP_RETIRE_HUMAN_SUPERVISOR`, so
expect a CI deploy to refuse here, before step 0a, on any workspace whose Genie
spaces grant `users` (directly or through `/Workspace/Shared`) or that holds a
non-runtime canonical Supervisor. Those runs used to refuse at the same audits
after the stop.

- **Supervisor retirement.** If a Supervisor holds the canonical name
  (`MIP_AGENT_SUPERVISOR_NAME`, default `Mortgage Growth Agent`; the
  preflight reads it once, from the shell or the client profile in
  `.env.local`, and every probe, provisioning and cutover step uses that
  value) and its
  creator is not the configured agent-runtime service principal, a successful
  deploy replaces it and later deletes it and its serving endpoint. That cannot
  be undone and is not covered by any rollback step. The probe refuses unless
  `MIP_RETIRE_HUMAN_SUPERVISOR=<supervisor id>` names that exact Supervisor; a
  boolean or another id is refused. `pin_journal` re-checks the same
  acknowledgement before the destructive tuple is signed into the cutover
  journal, so it also holds if the canonical name changes hands after the
  probe, and the retirement step checks it once more before its first
  Supervisor or serving-endpoint mutation (normal, captured-cleanup and
  stale-journal paths), so a journal pinned by earlier code or under another
  canonical name is never retired unacknowledged. On all three paths the
  pinned old-Gateway step runs before that check: it revokes the App's and
  the verifier's access and deletes the Gateway only when the agent-runtime
  service principal created it. That last check also covers a run that finds the pinned
  Supervisor already gone but its serving endpoint still there: an endpoint the
  agent-runtime service principal did not create is never deleted
  unacknowledged. The probe cannot see that orphan case, so it refuses at the
  retirement step instead. Set it in the operator shell for the migrating run,
  and again for any run that resumes that retirement, including one that only
  deletes the orphan endpoint. It is not a `.env.local` setting and CI never
  sets it. To keep the Supervisor instead, rename it off the canonical name to a
  name outside the reserved runtime replacement names (a renamed Supervisor is
  neither pinned nor deleted) and remove the App service principal's access to
  its serving endpoint, which the global serving audit would otherwise refuse.
  A runtime-owned canonical Supervisor needs no acknowledgement. A non-runtime
  canonical Supervisor that lacks any part of the identity the cutover journal
  pins (id, serving endpoint, creator or create time) is refused even when
  acknowledged, because `pin_journal` would refuse it after step 0a; it needs
  manual governance review. A `MIP_AGENT_SUPERVISOR_NAME` with leading or
  trailing whitespace is refused too: the planner matches the name exactly
  while the cutover journal strips it, so the two could disagree on which
  Supervisor is replaced.
- **Reserved replacement names.** The Supervisor planner picks a Supervisor on
  a reserved runtime replacement name (`<canonical name> [mip-agent-runtime]`,
  or `<canonical name> [mip-agent-runtime-<12-hex contract hash>]` with an
  optional `-mq1` suffix) as the green candidate, and refuses it after step 0a
  when the agent-runtime service principal did not create it. The contract hash
  depends on the governed Genie space, which is provisioned later, so the probe
  refuses every non-runtime Supervisor on any reserved name. Rename it to a
  name outside the reserved names; a renamed Supervisor is never pinned or
  deleted.
- **Genie grants.** The global Genie audits refuse every Genie space,
  the governed one included, that grants any permission to a group the App,
  agent-runtime or verifier service principal belongs to. The probe applies the
  same group rule to every space visible to the admin and lists each offending
  space, group and permission with its source: direct on the space, or
  inherited from a parent object (the permission's `inherited_from_object`,
  named as `/Workspace/Shared` when it is that folder). It names each space by
  its id only, never by its title, because on a shared workspace the list
  includes other teams' spaces and the refusal reaches deploy and CI logs.
  Membership is the
  workspace-SCIM direct and nested groups plus the system `users` group, which
  every workspace principal joins (so a space granting `users`, directly or
  through its folder, is always refused). The fix depends on the source:
  - A direct grant: remove it on the space, with the space owner's consent.
  - An inherited grant cannot be removed on the space. With the space owner's
    consent, move the space out of that folder into one that does not grant the
    group (or let the owner trash a space nobody needs). Change the folder's own
    ACL only after that folder owner's governance review: `/Workspace/Shared`
    grants `users` by default, and changing it affects every workspace user's
    shared content. On a shared workspace, expect several spaces to inherit
    `users` from `/Workspace/Shared`.
  - For a group other than the system `users` group, removing the service
    principal from that group also works.

  A group grant the client profile accepts for the App or the agent-runtime
  (see "Accepted shared-estate exposure" below) is judged by that identity's
  matcher, exactly as the post-stop audit judges it; it never relaxes the
  verifier or the governed space. The governed space is the one the
  provisioner resolves by its pin (below), so an entry naming its id or its
  live title refuses here (`accepted_exposure`) even after a rename, and even
  when no group grant reaches the App or the agent-runtime.

  The verifier's audit (`--forbid-all-genie`) also refuses any grant to the
  verifier service principal itself on any Genie space, which needs no
  governed space id, so the probe lists and refuses those too. Remove a direct
  one on the space. An inherited one comes from the verifier's own grant on the
  named folder: remove that grant there with the folder owner's consent (it
  changes only the verifier's access) or move the space. Never add an exemption
  to the audits instead.
- **Unresolvable audited principals.** If the agent-runtime or verifier
  application ID does not match exactly one workspace service principal, or the
  existing App has no complete service principal identity, the probe cannot
  read that principal's groups, so it refuses it (add the missing service
  principal, or remove the duplicate) in the same pass as the other refusals.
  It still lists every `users` grant for it, since every principal joins that
  group once it exists.

Five more probes (M0-PR5) each call the very assertion of the step they
protect, pinned by identity in `tests/unit/test_prestop_probe_identity.py`, so a
refusal here is the refusal that step would raise with the App down:

- **Rollback record** (`prestop_rollback_record`). For an existing App on the
  signed-blue path the probe runs step 0a's verifier
  (`verified_signed_last_good_contract`): a missing record or rollback scope
  refuses as `rollback_record_absent`, a failed signature, digest or
  scope-ownership proof as `rollback_record_tampered`, and an App identity or
  Gateway resource that no longer matches the record, or an active deployment
  that is pending or still in progress, as `rollback_record_drift`. An active
  deployment that is simply not the record's is no refusal: step 0a's `ensure`
  restores the record's deployment over it, which is how a re-run recovers a
  run killed after green activation and before signed capture, so the probe
  only names it. The same restore replaces an emergency payload deploy, as it
  did before these probes.
  With `MIP_REBASE_UNVERIFIED_APP=1` and a first-install journal that is not
  signed, it runs the bootstrap gate's absence proof instead, moved here from
  step 0a: a record that exists refuses as `rollback_record_present`, and a
  rollback scope that does not exist yet holds none. Step 0a still runs the
  same proof once more under the lease, closing the window between this probe
  and the lease. No App, or an App its signed first-install journal owns
  (`recover` or `unclaimed`, which the recovery under the lease deletes), has
  no blue to protect.
- **Owner policy** (`prestop_owner_policy`, `owner_policy`).
  `ApprovedOwnerPolicy.assert_objects` on the governed catalog, its `audit`
  schema and the treatment table, and `MetastoreOwnerPolicy.assert_objects` on
  the metastore: the objects `converge_campaign_treatment_access` checks after
  step 0a. An owner outside `MIP_UC_APPROVED_OWNER_PRINCIPALS` plus the
  deployer, a metastore owner other than exactly `MIP_UC_METASTORE_OWNER`, or
  the App's own service principal, refuses, named with its object. For a group owner the
  post-stop converger proves the App is not a member by minting a short-lived
  credential; read-only, the probe answers from the App's workspace-visible
  groups (plus `users`), so an account-level nested membership is still caught
  only after the stop.
- **Owner-equivalent grantees** (`prestop_owner_equivalent`,
  `owner_equivalent`, attack G12). Every grantee of `MANAGE`, `ALL_PRIVILEGES`
  or `EXTERNAL_USE_SCHEMA` on the governed catalog or any of its schemas
  (direct grants, so each is named where it can be removed), and of `CAN_EDIT`
  or `CAN_MANAGE` on the governed Genie space (the signed record's, or without a
  record the one the Genie provisioner adopts by its exact title; an ambiguous
  title refuses, as the provisioner would after step 0a), must be approved,
  compared case-insensitively. On the catalog and its schemas that means
  `MIP_UC_APPROVED_OWNER_PRINCIPALS` or the deployer, the principals approved to
  own those objects. On the Genie space it also means
  `MIP_GENIE_MANAGER_PRINCIPALS`, which approves the space only: a Genie
  manager with `MANAGE` on the catalog still refuses. The workspace
  `admins` group's Genie `CAN_MANAGE`, which every workspace object carries and
  no one can revoke, is the one grant accepted by name. Each other grantee
  refuses with the exact `REVOKE ... FROM` statement or space-ACL removal, or
  the exact approval to add after the owner's review. The probe never revokes
  or grants, and a grants, schema-listing or permissions read error refuses: it
  is never read as "no grantees". So does a schema listing the deployer cannot
  prove complete: the schemas API lists every schema only to a metastore admin
  or the catalog's owner (directly or through the owning group), so any other
  deployer refuses here and must run as one of those. No post-stop step checks
  these grantees yet, so this probe owns the assertion.
- **Serving endpoints** (`prestop_serving_endpoints`, `serving_endpoint`). The
  cutover's App audit (`audit_global_serving_endpoint_access`) runs first, here,
  on the set that audit reviews: the App may query only the signed record's
  Gateway and the canonical Supervisor's endpoint (the only Supervisor the
  cutover journal can pin), each in its exact legacy query mode. The record's
  own Supervisor endpoint is in that set only while its Supervisor holds the
  canonical name. A `CAN_QUERY` on the legacy `mip-agent-gateway` that no
  record pins, or on the endpoint of a Supervisor renamed off the canonical
  name (even the one the record pins), refuses; remove that access with the
  endpoint owner's consent.
- **Genie budget** (`prestop_genie_budget`, `genie_budget`). One real turn,
  asked through the App's own Genie client (`GenieClient.ask`) on the governed
  space with the live smoke's question. A failed or unfinished turn, or one
  that returns only the question's echo, refuses, and a budget error is named
  as such: smoke and agent evaluation need Genie after step 0a. It is the
  probe's one call that is not a GET or a list (it opens one Genie
  conversation); with no governed space yet it is skipped. Tests never issue
  it.

The owner-equivalent approvals come from one deploy control, resolved like the
others (shell export, then `.env.local`):

```
# Principals (users, service principals or groups) approved to hold CAN_EDIT or
# CAN_MANAGE on the governed Genie space, besides MIP_UC_APPROVED_OWNER_PRINCIPALS
# and the deployer; comma-separated. Empty approves no one else.
MIP_GENIE_MANAGER_PRINCIPALS=
```

`MIP_REMEDIATE_FOREIGN_CATALOG_BINDINGS` is refused before any of this, before
any Databricks call and in a dry run too: any value other than `0`, from the
environment or a stale `.env.local`, exits 2. Each source is read on its own,
so an export of `0` does not mask a stale `.env.local` line. The stop branches
that ran the foreign-catalog remediation are gone (M0-PR5) and its tooling is
deleted (M4-PR2); `deploy-dev.yml` never names the flag. M4-PR2 adds no
`.env.example` line and removes none: if a run reports the refusal, delete the
flag's line from that `.env.local`.

The probe is an early refusal, not a replacement for the post-stop audits.
Those still read each target's own `/Me` groups, which can include
account-level nested groups that workspace SCIM does not show, and they still
check the App's and agent-runtime's direct grants against the governed space.
A resumed or stale cutover journal whose canonical name differs from
`MIP_AGENT_SUPERVISOR_NAME` is invisible to the probe: its retirement still
refuses without the acknowledgement, but at the retirement step rather than
before step 0a. When such a journal also pins an old Gateway, the retirement
step handles that Gateway first: it deletes it only when the agent-runtime
service principal created it, and otherwise only revokes the App's and
verifier's access. That step is the reviewed path whatever happens to the
Supervisor, so the unacknowledged refusal comes after it.

### Accepted shared-estate exposure (M2-PR4)

A client install shares its workspace and metastore with other teams, and the
no-revoke ruling means some of their grants stay: `users` holds `CAN_MANAGE`
on every Genie space under `/Workspace/Shared` by default, and a foreign
catalog may grant `account users` `SELECT`. The global audits refuse each one.
The client profile's `MIP_ACCEPTED_EXPOSURE_JSON` may accept one exactly
(`tools/databricks/estate/accepted_exposure.py`); nothing else changes.

- **Exact tuple.** kind; securable type, id and full name (a Genie space's
  title; a catalog's id is its name); grantee; the exact privilege set; source
  (`direct`, `inherited:/directories/<id>` for a Genie folder,
  `inherited:<TYPE>/<full name>` for a UC parent); binding (`OPEN` with no
  workspace ids, or `ISOLATED` with the bound ones; a Genie space is `ISOLATED`
  to its one workspace id); identities (`app`, `runtime`, `proxy`);
  privilege_class; reason; expires_at. Any change to an observed field, a
  wider or narrower grant included, refuses. A UC grant accepted on a catalog
  or schema also covers the same grantee's privileges inherited below it,
  while they are a subset.
- **Observed tuples only.** An entry loads only when an audit builds its tuple
  for each of its identities: a Genie space's `workspace_permission` for `app`
  or `runtime`, or a `uc_grant` on a catalog, schema, table, function (a
  registered model is one) or volume for `runtime` or `proxy`. Anything else
  (`uc_ownership`, another workspace object, an App UC grant, a proxy Genie
  grant) refuses at load instead of being acknowledged and never matching.
  `python -m tools.clients resolve` (deploy step 0) runs the same closed shape
  and ceiling, so such an entry fails there with the profile's other errors.
- **Ceiling.** The read class is Genie `CAN_VIEW`/`CAN_RUN` and UC `BROWSE`,
  `USE_CATALOG`, `USE_SCHEMA`, `SELECT`; everything else is elevated, and
  `privilege_class` must say which. Elevated is refused for
  `runtime` and `proxy`: the object's owner must narrow the grant, or the
  agentic tier stays off. It is accepted for `app` alone, for at most 30 days,
  and only while both compensating static controls hold
  (`tools/databricks/estate/genie_single_space.py` and `sql_trust_scope.py`,
  pinned by `tests/unit/test_genie_single_space_ast.py` and
  `test_sql_trust_scope_ast.py`): every backend Genie API call goes through the
  one client bound to the pinned governed space, and the SQL trust policy
  admits only FQNs in the install's catalog under `audit`, `gold`, `ref` or
  `semantics` (no configured Cotality source FQN is admitted today). An entry
  naming the verifier or the governed space refuses (before the stop, the
  space the provisioner resolves by its pin, by id or live title), as does an
  entry past its expiry (it parses, it never matches).
- **Matching.** One matcher per identity. The pre-stop Genie clause and the
  post-stop App and agent-runtime Genie audits (`audit_global_m2m_access
  --exposure-identity app|runtime`) use the same one, so they refuse alike; the
  foreign UC audit and both dual-authority proofs use the runtime's or the
  proxy's. The verifier's `--forbid-all-genie` never consults a matcher, so a
  grant reaching it (any `users` grant does) still refuses. Neither does the
  agent-proxy's Genie boundary (its managed-capability ACLs, converged or
  denied, and its deny-all audit), so a Genie entry naming `proxy` refuses at
  load.
- **Ack.** The owner attestation is deferred (`single_maintainer_audited`), so a
  non-empty policy is pinned by an exact digest instead.
  `python -m tools.databricks.estate.cli --plan` prints the plan's sha256, a
  public summary and, on its last line, the canonical plan whose sha256 the
  ack pins (each identifying value masked first under GitHub Actions), so the
  reviewer reads exactly what is acknowledged; a key repeated in the profile
  JSON refuses, since its last value would otherwise win unseen.
  `--check --ack-plan <sha256>` checks a digest. Export
  `MIP_ACK_ACCEPTED_EXPOSURE_PLAN=<sha256>` in the shell for the one run that
  should use the reviewed plan, never in `.env.local`: every consumer
  recomputes the plan from the profile right before it judges an exposure, and
  a missing or different ack refuses (`accepted_exposure` before the stop).
  Each consumer judges expiry when it loads, and the post-stop audits load
  long after the pre-stop probe, so an entry expiring within 24 hours of the
  probe refuses there (`accepted_exposure`): renew it before the run rather
  than have it expire with the App stopped.
- **Inventory.** `python -m tools.databricks.estate.cli
  --emit-exposure-inventory --app-name ... --runtime-application-id ...
  --verifier-application-id ... --expected-inventory-principal ...
  --genie-space-name ... [--catalog mip]` lists, read-only, the Genie grants
  reaching the App or the agent-runtime outside the governed space and (with
  `--catalog`) the agent-runtime's direct grants on each accessible foreign
  catalog, as candidate entries with placeholder `reason` and `expires_at`. It
  never applies them, and the policy refuses a candidate until the owner fills
  those in. Grants that can never be accepted (reaching the verifier, or
  elevated for the agent-runtime) are notes with their remedy. Under GitHub
  Actions every identifying value is registered as a mask before it prints.
  A core-tier install (`MIP_AGENTIC_TIER=off`) deploys no agent-runtime or
  verifier: run it with `--agentic-tier off` and neither id, and it lists the
  Genie grants reaching the App alone, as the core pre-stop probe reads them.
  It has no UC candidates, so `--catalog` refuses there.
- **Metastore owner.** `converge_campaign_treatment_access` and the pre-stop
  owner probe judge the metastore with `MetastoreOwnerPolicy`:
  `ApprovedOwnerPolicy` over exactly `MIP_UC_METASTORE_OWNER`, without the
  deployer or the approved owners, with the alias and group-membership
  exclusions kept. `users`, `account users` and `admins` refuse, and so does an
  owner the API does not return. The owner is read with `metastores.get`, which
  the SDK documents as metastore-admin only; whether a non-admin read
  (`metastores.summary`) returns it is spike S0(b), still owed, as are S0(a) (a
  service principal's own `get_effective`) and S0(c) (what a runtime service
  principal sees of its own objects without `USE_CATALOG`).

The deploy controls, resolved like the others (shell export, then `.env.local`):

```
# The metastore's exact owner principal (a user, service principal or group;
# never users, account users or admins). The treatment-access converger and the
# pre-stop owner probe require the metastore owner to be exactly this principal.
MIP_UC_METASTORE_OWNER=
# Accepted shared-estate exposure, one line of JSON (plan section 4.12); [] or
# empty accepts nothing. A non-empty value needs MIP_ACK_ACCEPTED_EXPOSURE_PLAN
# (shell only, one run) equal to `python -m tools.databricks.estate.cli --plan`.
MIP_ACCEPTED_EXPOSURE_JSON=
```

### Estate inventory (agentic tier, M4-PR1)

The agentic tier's foreign UC audit
(`tools/databricks/audit_agent_runtime_foreign_uc_access.py`) no longer needs
the deployer to own the metastore. What only a metastore owner can read comes
from the **estate inventory**, which the owner takes, and the agent identity's
own view of Unity Catalog checks it. The core tier runs no foreign UC audit and
needs no inventory: a core run reads none of the names below and its dry-run
transcript is byte-identical to the one before this change.

**Taking it (the owner, as the metastore's direct owner, read-only).** At most
29 days before the agentic run that relies on it (the inventory expires at 30
days, and the run's pre-lease check allows for the run's own 24 h):

```bash
python -m tools.clients.estate_inventory --out ~/mip-estate/<client>.json \
  [--catalog mip] [--bound-workspace-profile <cli-profile> ...]
```

The install workspace is the ambient Databricks auth, as the owner
(`DATABRICKS_CONFIG_PROFILE=<the owner's profile on the install workspace>`, or
`DATABRICKS_HOST` and its credentials); the tool refuses unless that caller
owns the metastore directly. It lists every catalog with `include_unbound`,
binding-denied ones included,
with its type, isolation mode, owner and workspace bindings, and below each
foreign catalog the owner of every schema, table, function, volume and
registered model (`information_schema` apart). A catalog bound only to other
workspaces is read only through a `--bound-workspace-profile` on one of them,
as the same owner: what the install workspace answers for it is never trusted,
because an empty answer there could be the binding itself. With no such
profile, or when a read is refused, the catalog is recorded as a gap. The
account plane (the deploy's `DATABRICKS_ACCOUNT_*` credential, exported for
the run) classifies every workspace assigned to the metastore, and a deleted
one is recorded with the exact sha256 of what its assignment listing returns
(or that it no longer lists). The document is validated by the deploy's own
parser before it is written, so a refused one never is; the file is written
mode 0600, atomically and never through a symlink. The tool prints counts, gap
counts and the `estate` digest, never a catalog, owner or id: a refusal of its
own prints its line, and any other error (a Databricks API, HTTP or file
error) only its class and its error code (an OS error's errno name, never its
free-text `strerror`), because its text can name another team's catalog. `--summarize <file>` recomputes the counts and digest. Its SDK
calls are pinned to a reviewed set of gets and lists, statically
(`tests/unit/test_bootstrap_account_safety.py`) and by a run-time spy on every
test path (`tests/unit/test_estate_inventory.py`). The static scan reads the
collector and the CLI under every name they give an owner client: an SDK API
is called where it is reached, never aliased, passed on or looked up by
reflection, and the one function outside them that is handed a client
(`authoritative_workspace_id`) may only read the workspace id. It is a review
gate on those files, not a sandbox. Exit 0: written, no gap; 3:
written with gaps (the deploy refuses it); 4: written, but printing its
summary failed (a closed stdout, as under `| head`): run `--summarize` on it;
2: refused, nothing written.

**Giving it to a run.** The inventory names other teams' catalogs and
principals, so it is identifying: store the file's content as the client's
`MIP_ESTATE_INVENTORY_JSON` GitHub Environment **secret** (GitHub's limit is
48 KB; the tool warns past it), or point `MIP_ESTATE_INVENTORY_FILE` at a
regular file the caller owns, mode 0600. Exactly one of the two, from the
process environment only, never `.env.local`. The owner hardware-key `estate`
attestation (plan 4.2) is deferred (M1-PR5), so the run must be handed the
exact digest instead, as a one-run shell export (or, in CI, a workflow input
mapped to it), never a `.env.local` line:

```bash
MIP_ESTATE_INVENTORY_FILE=~/mip-estate/<client>.json \
MIP_ACK_ESTATE_INVENTORY=<the estate sha256 the tool printed> \
  ./scripts/deploy.sh -t client
```

The digest covers the domain-separated canonical document, which names its
purpose (`estate`), so no other ack (`MIP_ACK_ACCEPTED_EXPOSURE_PLAN`, the
quiesce window, a handover plan) can satisfy it. A digest is not a signature:
it proves the operator acknowledged this exact inventory, not who took it, and
no check here pretends to verify a hardware-key signature or a trust root
(deferred, M1-PR5). The deploy-dev workflow is parked; the per-client
environment (M1-PR7) maps the secret into the deploy step's environment and
the ack from a dispatch input.

**What refuses.** In the agentic tier, before the lease and the `--phase plan`
exit, `tools/databricks/estate/inventory_check.py` loads the inventory exactly
as every audit will, judging its age at the end of the run's 24 h margin (so
it admits an inventory at most 29 days old, which cannot expire before the
run's last audit), and
prints its counts and digest. Every audit recomputes the digest right before it
relies on the inventory. Any of these refuses:

- the retired `MIP_UC_FOREIGN_CATALOG_BINDING_POLICY` is set (below);
- no inventory, both sources, or a file that is not a private regular file;
- no ack, a malformed one, or one that is not this inventory's estate digest;
- `issued_at` in the future, or more than 30 days before the check (the
  pre-lease check judges at the end of the run's margin, so it refuses one more
  than 29 days old);
- another metastore, install workspace or install catalog;
- any gap;
- a catalog the deployer or the agent identity can see that the inventory
  lacks, or lists with another owner, type or isolation mode (it is stale:
  take it again); a binding-denied catalog the identity can see;
- an object the identity can see whose owner the inventory does not list;
- foreign ownership, which is never acceptable: any owner of a binding-denied
  catalog or anything below it that is the runtime or proxy, one of their
  groups or `account users`, and any owner of an accessible foreign catalog or
  anything below it that `ApprovedOwnerPolicy` refuses for the identity;
- a workspace assigned to the metastore that the account plane no longer has,
  unless the inventory's exact defunct entry matches what its assignment
  listing returns now; whatever it still lists gets the same assignment audit
  as a live workspace, so an entry never admits the identity there;
- any API refusal, which is never read as absent (the account plane's
  `workspaces.get`, the identity's catalog, model or effective-permission
  reads).

**What is judged as of the inventory.** Only a metastore admin or a catalog's
owner can read its workspace bindings, and neither the deployer nor the agent
identity is either, so a catalog's bound workspace ids come from the
inventory, at most 30 days old (the pre-lease check admits at most 29 days,
so it cannot expire during the run); its owner, type and isolation mode are
re-read live and compared. Two things follow. The `binding` field of an
accepted-exposure entry (plan 4.12) is matched against the inventory's
bindings, so a catalog rebound while it stays `ISOLATED` still matches until
the inventory is taken again; and the proof's binding-denial evidence carries
the inventory's bindings. A catalog whose isolation mode changes refuses (the
inventory is stale). A catalog that is rebound into the install workspace and
becomes visible to the identity still refuses, and the identity's workspace
assignments, read live on the account plane, still confine it to the install
workspace. `tests/unit/test_estate_inventory_audit.py` pins all three.

**The self-view.** The audit reads the runtime's (or the proxy's) effective
privileges as that identity, with its own OAuth credential, on every catalog it
can browse and everything below each foreign one, through its accepted-exposure
matcher. Spikes owed: S0(a), whether an identity's own `get_effective` is
answered (a refusal refuses, so the agentic tier stays off until it is), and
S0(c), whether an identity sees what it owns without `USE_CATALOG`. The design
assumes the worst case, which is why ownership comes from the inventory: a
runtime-owned table behind a revoked `USE_CATALOG` is still refused. A grant
(not ownership) on an object whose catalog the identity cannot see is neither
in the inventory nor read by the audit: it is inert without `USE_CATALOG` on
that catalog. If another team later grants `USE_CATALOG` there (to the
identity, one of its groups or `account users`), the grant can become usable
in the running App with no deploy, and it is refused only at the next agentic
deploy, when the self-view can see it. The metastore-owner audit refused such a
grant while it was still inert; it accepted the same residual for
binding-denied catalogs, whose grants it never read.

**The retired binding policy.** `MIP_UC_FOREIGN_CATALOG_BINDING_POLICY` was an
exact contract on the binding-denied catalogs; the inventory now carries them,
taken by the owner at most 30 days before the run and acknowledged by its
exact `estate` digest on each run. Set to anything non-blank, it refuses in the
agentic tier (it could only have made the audit stricter than the inventory)
and is one notice in the core tier, which never audited with it. It is never
exported to a tool again, and the loader refuses it too. The binding
remediation tooling that also read it is deleted (M4-PR2).

**Owner patch: `.env.example`.** Agents cannot edit the template. Remove this
line (the inventory, its file path and its ack are environment-only and add no
line):

```diff
-MIP_UC_FOREIGN_CATALOG_BINDING_POLICY=
```

### Bundle plan gate (read-only, before the lease)

`./scripts/deploy.sh` renders the SQL and validates the bundle right after the
config gates, and after the pre-stop probes and the pii-salt check it plans the
bundle (`bundle_env plan -t client -o json`) and hands the plan to
`tools/databricks/bundle_plan_gate.py check --position pre-lease`. That is
before the signed lease and before any App stop. It runs the gate again under
the lease (`--position pre-apply`), after Genie provisioning and immediately
before the non-App bundle apply, so a plan that changed in between is judged
again. Either refusal exits 2 with a remedy per refusal; the pre-lease one
leaves the App serving and changes nothing. The gate refuses:

- a `delete` (a state-only delete of an object already gone included), a
  `recreate`, or an `update_id` rename of an identity field;
- a resource kind with no reviewed existence lookup (`KIND_LOOKUPS`, pinned
  equal to the kinds `databricks.yml` declares);
- a `create` whose name already exists, looked up with a read-only GET or list
  per kind (`collision`), or whose name cannot be proven free (`lookup_failed`:
  a read error, a name the plan has not resolved, or a caller that is not a
  workspace admin). A list or GET answers with what the caller may see, so a
  workspace-scoped absence counts only after `current_user.me` lists the
  caller in `admins` (the deploy's preflight already requires that); the
  metastore-scoped Lakebase catalog lookup is the one exception;
- an update or resize of the Lakebase database instance, unless
  `MIP_ACK_DATABASE_INSTANCE_UPDATE` equals the sha256 of what the update will
  do. The CLI sends the instance's whole configured state with
  `UpdateMask: "*"`, so the update also reverts a remote-only drift on an
  input-only field (`node_count`, `retention_window_in_days`, ...) that the
  plan lists as `skip`. The digest therefore covers the full `new_state`,
  every change skip included, the instance name and the state lineage. The
  refusal prints that digest and the full report's `instance_update` holds
  exactly what it covers. Review it, then export the digest for that run
  only. The pre-apply gate recomputes the digest, so an acknowledgement covers
  exactly the reviewed update of this install, and never another install's;
- **`not_adopted`**: when the deployer's state does not bind `apps.mip_app`
  (its entry plans a create), the App's own lookup finds it existing, and every
  refusal is a collision, the deployer is looking at an install it never
  adopted. The collisions fold into this one refusal and are listed as its
  detail. With no App there is no install: a first install that meets a stray
  same-name object gets the plain `collision` refusal and its remedy;
- **`app_acl`** (pre-lease only): the App ACL preview below found an ACL the
  release access converger cannot read or converge (an inherited non-manager
  grant, an unexpected level, or a release identity that also manages the
  App), which would fail it after step 0a, or could not read the ACL at all.

**App ACL preview** (pre-lease only, M0-PR5). The run ends by converging the
existing App's direct ACL to the runtime release phase
(`converge_app_release_access --mode runtime`): direct managers are kept, and
every other direct grant is replaced by `CAN_USE` for the normal, operator2 and
admin service principals, so a business user's direct `CAN_USE` is dropped.
The pre-lease gate previews that with the converger's own reader and plan
(`tools/databricks/bundle_plan_app_acl.py`). The printed line and the public
summary carry its code (`unchanged`, `changes`, `app_absent` or `refused`) and
the keep, add, remove and change counts; the full report names each grant. A
dry run without the four release identities skips it.

One collision is deferred instead, and only before the lease. An interrupted
first install can leave the App created but not bound into bundle state (a
lost create response, a crash between the journal claim and the bind, or a
cleanup that unbound the App and then failed to delete it); the deploy leaves
that App and its signed first-install journal for the next lease to
reconcile. On the retry the pre-lease gate reads that journal read-only
(`app_first_install_journal.recovery_state`, for the App named by
`MIP_APP_NAME`); when it reads `unclaimed` or `recover`, the App collision is
printed as `DEFERRED journal_owned apps.mip_app` and the run continues to the
lease, where the signed-journal recovery deletes the App. The pre-apply gate
never defers: a create that still collides there refuses. Any other journal
state, or a journal that cannot be read, keeps the strict refusal.

Printed lines carry refusal codes, counts, digests and symbolic `<kind>.<key>`
names only (`PlanReport.public_summary()` in
`tools/databricks/deploy_plan_report.py`). The full report, with the resolved
names and lookups, is written mode 0600 to a temporary file whose path the run
prints; keep it private. Before a first install provisions the governed Genie
space, the pre-lease plan binds the placeholder (`bundle_env plan
--allow-unprovisioned-genie`, accepted by `plan` only and only when no space has
the governed title); the pre-apply plan always resolves the governed space.

The plan JSON shape is derived from the Databricks CLI v1.10.0 source (cited in
`tools/databricks/bundle_plan_shape.py`), not from a live capture, which is
still owed at the first governed plan phase. Any field, action or key the
parser does not know refuses as `malformed_plan`.

`./scripts/deploy.sh -t client --phase plan` runs only the read-only steps
through the pre-lease gate, predicts each phase's budget, and exits before the
lease. A live plan phase is CI-only (`plan is CI-only; use --dry-run or make
bundle-validate`); locally, `--phase plan --dry-run` prints the same steps
without any Databricks call. `--phase data` runs no bundle plan at all (see
"Governed run phases" below).

### Keep blue serving: the one bounded quiesce window (M0-PR8)

A release keeps the signed last-good App (blue) serving. It stops the App
only for a **window reason** the read-only plan predicted
(`tools/databricks/quiesce_window_plan.py`):

- **WR1** binding diff: the App's live resource bindings differ from the
  bundle's, compared as a set keyed by binding name (an order-only difference
  is equal) on their access contract (kind, target, permission; a binding's
  own `description` is display text, reported apart and never a reason), or a
  binding id is unresolved. Every update that carries resources re-grants
  CREATE ON DATABASE to the App's Lakebase role (spike S2, below), so the
  release's binding step sends them only in a WR1 window (the signed-blue
  restores at step 0a and in the rollbacks send them outside any window; see
  below). A WR1 window always runs the migration job after its binding
  update, and its acked body names that re-grant
  (`app_role_regrant: ["binding_send"]`). An App description-only difference
  is no reason: the release applies it online while blue serves (below);
- **WR2** Lakebase role rotation: `converge_lakebase_oauth_role --check`
  drifts for the App role, the AI Gateway verifier role or the jobs
  identity's `jobs_reader` role (M2-PR5), or a `noop` migration plan reads
  the jobs reader's grants as drift, an absent role included (a rotation
  drops the role's grants, which only the migration job re-applies, and that
  job runs only inside a window). Blue never reads as the jobs reader, so its
  re-grant keeps blue restorable. The same holds for a `noop` migration plan
  that reads the App role's CREATE ON DATABASE as re-granted
  (`app_role_regrant: owed`, an earlier binding send outside a window, spike
  S2): the window's migration job revokes it, the schema is unchanged, and
  blue stays restorable. The first roll-forward that brings the
  jobs identity to an installed signed blue therefore plans a WR2 window to
  create and grant that role. The grants alone are no reason under an
  `apply` plan: its WR3 window runs the migration job, which re-grants the
  role, and a pending migration that adds or drops a `mip_app` table reads
  as grant drift even for exact grants, so a WR2 there would only open the
  window earlier, before the role converges. A `refuse` plan refuses the
  window (`window_migration_refused`) whatever the grants read, and an early
  refusal that never reached them records them as `unchecked`;
- **WR3** pending migration: the Lakebase migration plan decides `apply`;
- **WR4** acknowledged Lakebase instance update (the plan gate's digest).

The plan runs after each bundle plan gate: before the lease (`pre-lease`, so
`--phase plan` prints it) and again under the lease right before the non-App
apply (`pre-apply`, the time-of-check recheck). A plan dispatch may carry the
window's ack (`operation_acks` `quiesce_window=<sha256>`, read only by that
read-only pre-lease plan): the first plan refuses `window_unacked` and prints
the window's sha256, and the plan re-run with it completes, so it reaches the
active deployment's digest and the PlanReport. Its private report is written
mode 0600 and its public line carries codes, booleans, counts and digests
only. A window opens only when `MIP_ACK_QUIESCE_WINDOW` equals the plan's
sha256 at both positions; that sha256 covers the reasons, blue's deployment,
its restore source and that folder's full-tree digest, the env-name set, the
role and jobs-reader grant states and the migration's compatibility, so the
opened reasons are exactly the predicted ones. Export the digest for that run only. It is a shell-only, one-run value,
never a `.env.local` line. (The owner hardware-key operation attestation the
plan names is deferred; this exact digest ack stands in for it.) No reason
needs no ack, and a reason that appears only at the recheck refuses. Over a
stopped blue, step 0a's tier a starts it and re-pins the record onto the
start between the two positions, so the pre-apply body names the start where
the acknowledged pre-lease body named blue. The pre-apply recheck and the
open therefore also accept the digest of that body with
`blue_deployment_id` set back to the signed record's
`predecessor_deployment_id` (`acknowledged_digests`); every other fact must
be identical, so a re-pin from another folder or tree (a tier-b restore names
the immutable copy) still refuses, and the opened window journals the start.

**The window** (`scripts/lib/deploy_quiesce_window_lifecycle.sh`) opens at the
first mutation that needs it (WR4 before the non-App apply, WR1 before the
binding update, WR2 before the role convergence, WR3 before the migration):
it re-validates the plan and the ack, admits it against the job budget,
re-proves blue is the App's last deployment, arms the dead-man (M0-PR9,
below), admits and re-proves blue once more, journals `window_open`
(`<lease root>/<app>.quiesce-window.json`, signed) and then stops the App,
identity-pinned. It closes right after the
Lakebase migration: journal `window_starting`, then `apps start`; wait for
**the deployment the start created** (never "active changed"); require
SUCCEEDED; prove its source is blue's restore source, that folder's full-tree
digest is unchanged, its `/src/` snapshot has the same digest and its env-name
set is the signed record's; `app_deployment_rollback repin --to <id>` signs a
new record version with the same provenance and `predecessor_deployment_id`,
checking health against the re-pinned id; then the journal closes. After a
WR1 window blue restarted on the bundle's bindings, so the re-pin records the
App's live bindings when they are exactly the applied payload
(`--app-resource-payload`) or the record's own, and refuses anything else;
the next run's step 0a then has nothing to revert on the serving App. Any
failure after the start call (a failed proof, an unreadable pending id, a
vanished restore folder, an SDK error) stops the App again, identity-pinned,
journals `window_refused` and leaves the window for the dead-man (M0-PR9). A
start whose id was never journaled (`window_starting`) resumes from the App's
pending deployment; it is never started twice. A
contract migration (`blue_restorable` false) keeps the App stopped until the
green activation, and that outage is part of the acknowledged digest. The
activation's own start (an App must run to take a deploy) serves blue's last
deployment on the migrated schema, with treatment authority quiesced, until
green's snapshot deploy replaces it; that interval is part of the same
acknowledged outage. The journal stays open through the activation
(`contract_activating`) and closes only once green is the signed record and
the App's active deployment. Until then any failure, in the EXIT trap or the
final-proof rollback, stops the App and refuses the tier-b blue restore.

With no reason, nothing stops: the binding update is skipped (the bindings
are re-read under the lease and must still equal the bundle's as a set; a
description-only difference is applied online with
`tools/databricks/app_description_fixup.py`, a create-update masked to
`description`, identity-pinned, which restarts nothing and re-grants
nothing; a bundle that declares no description leaves the live one as it
is, and the plan owes no fix-up for it). A window opened for another reason
(WR4 opens before the binding step) skips the send the same way: the binding
step sends resources only in a WR1 window or an ungoverned run. It also
leaves a description-only difference to a later release, because the App is
stopped there and every create-update the spikes measured ran on a serving
App; the window plan's `online_fixups` names the fix-up only when no window
is open at the binding step (a WR1 window's update carries the description
itself). The App role
converger refuses any rotation the plan did not predict, the verifier and
jobs-reader roles are re-checked under the lease right before their converges
(drift exits 2, blue serving), and the migration job is skipped because the
pre-apply plan proved it a noop with a passing verify and the jobs reader's
grants exact.

**EXIT-trap states** (`stop_app_after_failed_deploy`): `blue_serving` never
stops the App and converges runtime access with it up; `blue_degraded` (blue
serving while its synced-catalog access or the reviewed functions are
mutated) also re-reconciles those (in the core tier the functions only: a
core release leaves the synced-catalog access quiesced); `window_open`
starts and re-pins blue when every proof holds, otherwise leaves the window
to the dead-man;
`contract_activating` stops the App, never restores blue and retains the
lease. The rollback's
`ensure` and `restore` re-pin the same way when they start a stopped blue
(tier a, only while blue is the App's last deployment); `restore` redeploys
the signed snapshot (tier b) otherwise, and `ensure` hands a start that fails
its proof (a changed folder's included: a start snapshots its CURRENT bytes)
to that same tier b, which starts a stopped App itself before its redeploy.

**Catalog bootstrap skip rule** (attack O12,
`tools/databricks/catalog_bootstrap_skip_rule.py`). `mip_init_catalog_schemas`
republishes the reviewed functions, an EXECUTE gap while blue serves. It is
skipped only when the live `information_schema` routines, parameters and DDL
contract columns and the treatment table's properties hash-equal the rendered
sources (read-only, under the lease, right before the job); the grants are
still reconciled and proven. Otherwise it runs and the PlanReport's
`execute_gap` predicts the interval and the republished functions. A function
clause the live side does not compare (a parameter `DEFAULT` or `COMMENT`,
`SQL SECURITY`, `LANGUAGE`, `READS SQL DATA`), a crashed rule or an unreadable
rendered source always means "run"; only the rule's own exit 0 skips.

**Stop contract** (attack O16). The window library and the EXIT-trap
compensation are the only shell files that stop or start the App (the binding
step's online description fix-up, a create-update masked to `description`,
is the one App update another step file runs; spike S2), and
`tools/databricks/stop_primitive_map.py` maps every SDK `apps.stop`, `start`,
`deploy` and `update` call in `tools/` (also through a name bound from
`.apps`) to the CLI modes that reach it; only those two files may call a
mutating mode, by `-m` or by its `tools/databricks/<module>.py` path (the mode
is read from that one command's own tokens), or import a stop module in a
heredoc or `-c`.

**Spike S7 is recorded** (a throwaway App, 2026-11-10). Every `apps start`
creates a new SNAPSHOT deployment from the current bytes of
`default_source_code_path`, with the newest deployment's env-name set. Neither
the start response nor a GET right after it shows that deployment: the
compute turns ACTIVE after 96-108 s (a trivial App), and only then does the
new id show, about 1 s as `pending_deployment`, then as `active_deployment`.
So `start` lists the App's deployment ids first, journals them with
`window_starting` (journal version 2), starts without waiting and polls, at
most every 5 s, for exactly one id outside that list (two refuse
`start_ambiguous`, none `start_timeout`); a resumed start finds it against the
journaled list. A STOPPED App's GET has no `active_deployment`, and a FAILED
deployment becomes active and moves the default source, so blue is current
(tier a, the plan's `blue_current`, the dead-man) only while nothing is
pending, blue is the newest deployment of ANY state and SUCCEEDED, the
default source is blue's folder and, on a running App, blue is active.
`default_source_code_path` is not settable. The live shape of
`information_schema.routines.routine_definition` is also derived, not
captured; a reformatted body only ever means "run".

**Spike S2 is recorded** (throwaway Apps, 2026-11-06 and 2026-11-10, with
S2b). An `apps update` or
`apps create-update` that carries resources, an identical array included,
grants CREATE ON DATABASE to the App's Lakebase role again (`=c` to `=Cc`,
TEMPORARY not), with no restart, no new deployment and no compute change.
Only the migration job's revoke takes it back. A PATCH (`apps update`)
without a description clears it to `""`. A create-update masked to
`description` changes the description and `update_time` only. So:

- the release's own sends (the binding step's `apps update`, only in a WR1
  window or an ungoverned run, and the first install's `apps create`) are
  followed by the migration job in the same run: the step files record them
  (`APP_ROLE_REGRANT_OWED`), a WR1 window runs the job after its binding
  update whatever the migration plan says (its budget counts it: every window
  without WR3 adds `migrate`), and a migration skip with a send recorded
  refuses;
- the signed-blue restore (`restore_signed_app_resource_contract`, step 0a
  and the rollbacks) sends a create-update masked to `resources`, so the App
  description survives, and re-sends each kept binding's live description
  (what the API does with a sent binding that omits one is unmeasured). No
  restore runs the migration job: step 0a restores before any window opens,
  and a rollback restores after a window's migration already ran. So the role
  keeps CREATE ON DATABASE until the NEXT release: the restore says so on
  stderr, that release's migration plan reports it as
  `app_role_regrant: owed`, and its window plan makes it a restorable WR2;
- the migration plan's verify sets that one privilege apart and reports it
  (`app_role_regrant`); `--mode verify` and apply's postflight still refuse it;
- the bindings compare as a set by name on their access contract, and the
  App description is an online fix-up, never a window. A binding's own
  description is display text: `app_resource_bindings diff` and the
  post-update verifier name the bindings whose text differs and pass; only a
  resources send carries the bundle's text (no online fix exists).

S2b also proved an online `REVOKE CREATE, TEMPORARY ON DATABASE` by the
database owner safe while the App serves. The release does not use it: the
grantor it needs after the ownership handover is inferred, not tested, so the
revoke stays the migration job's, inside a window.

Cutover run 1 on client #1, on the shape S2 read live (an empty description,
the eleven bindings, the scheduler binding in another position, and three
secret bindings, `cotality_id_mask_secret`, `genie_action_current_secret`
and `genie_action_previous_secret`, with no description of their own while
the bundle declares one for each), now plans no WR1 window and one online
description fix-up (`tests/unit/test_cutover_run1_binding_plan.py`). The
three bindings keep reading undescribed: the plan names them and nothing
repairs them until a later WR1 send carries the bundle's text. Whether run 1
needs another window depends on facts the plan reads and the spike did not:
the live App role's CREATE ON DATABASE (a WR2 if a past restore left it
re-granted) and WR2-WR4's other inputs.

### The job budget and the dead-man (M0-PR9)

A runner that dies inside the window (a cancelled or timed-out job sends
SIGKILL about 10 s after SIGINT, and a lost runner sends nothing) would leave
the App stopped. Two controls bound that (threat T5).

**Budget admission** (`tools/databricks/quiesce_window_budget.py`). A window
opens only while the job's remaining budget covers it:

    MIP_JOB_DEADLINE_EPOCH - now >= predicted window + predicted restore + 15 min

The predicted window is the plan's `predicted_seconds` (stop, each reason's
step, the migration job's `migrate` budget unless a WR3 already budgets it,
since every window runs the job (spike S2), then start and re-pin when blue is
restorable; a contract window, which stays open with the App stopped until
green activation, adds that tail, the reviewed `green_activation` budget).
Step 0a's start and re-pin of a stopped blue (spike S7) is not part of it: it
runs before any window opens, and the release phase budgets it as
`step0a_start` (below). The predicted restore is
`predicted_restore_seconds`: the EXIT trap's tier-a start and re-pin after a
failure at the window's last mutation (start + re-pin budgets), and nothing for
a contract window, which restores nothing. Both, the 15-minute margin and their
sum are in the window's canonical body, so `MIP_ACK_QUIESCE_WINDOW` covers
them; a plan with no reason has no budget, and its body and digest are M0-PR8's.
The admission itself depends on the clock and is never in the digest: the plan
prints it (`admission <state>: <n> s left, <m> s required`) before the lease
and again under it, refusing `window_budget_refused`. `--phase plan` forecasts
it instead (`--admission-forecast release`, pre-lease only): judged against a
release dispatched now, `quiesce_window_budget.RELEASE_JOB_SECONDS` (the
release job's 330 minutes), since the plan job's own 30 minutes admit no window
(each needs about an hour) and a plan opens none (`open` takes a pre-apply
plan only); the line reads `admission (forecast for a release dispatched now)
<state>`, and the release decides it again against its own deadline. `quiesce_window open`
decides it again before its dead-man submit and once more after the arm,
right before the journal write and the stop. A missing,
unparseable, past or more than six hours away deadline refuses (fail closed). A
run with no window needs no deadline, and a dry run prints the admission rule
and the deadline's state without a Databricks call.

`MIP_JOB_DEADLINE_EPOCH` is the job's hard deadline in epoch seconds:
`deploy-dev.yml` sets it in its first step from its own `timeout-minutes`, and
a local run passes `./scripts/deploy.sh -t client --deadline <epoch>` (or
exports it for that run). It is a shell-only, one-run value like the window
ack, never a `.env.local` line, so `.env.example` gets no line for it.

**The run's own deadline** (`tools/databricks/quiesce_window_watchdog.py`). In
CI the job's timeout kills the runner by the deadline; a local `--deadline`
is enforced by the run itself. Once a restorable window is open, a watchdog
runs beside the deploy: a minute before the start guard it sends the run
USR1 (`deploy.sh` traps it: `exit 143` once the current command ends) and
TERMs every process the run started except the lease heartbeat, so no
in-window mutation keeps running while the dead-man may start blue. The EXIT
trap disarms it first and, still outside the guard, starts and re-pins blue
itself, or leaves the window to the dead-man. It fires only while the run is
its ancestor (a reused pid is never signalled); the close disarms it, and a
contract window has none (the dead-man never starts one).

**The dead-man** (`jobs/window_deadman.py`). `quiesce_window open` arms it
after the budget admission and the blue re-check, before the journal write and
the stop:

1. It publishes this run's staged release with `app_release_source` (create
   only, an exact listing and the sha256 of every file): the window opens
   before step 5's publish, so the folder is published here, the same way and
   to the same path, and step 5 then uploads nothing. `jobs/` is in the App's
   staged upload set, so the dead-man ships in every release folder and the
   App's runtime closure is unchanged (one more file in `jobs/`, which the App
   never imports; the payload's env names are untouched).
2. Its source is `<bundle root>/app-src/<release id>/jobs/window_deadman.py`:
   the manifest must name it, the folder must be a governed release folder
   (never the bundle files root or any mutable path), and its bytes are read
   again and compared with the manifest right before the submit.
3. One `jobs submit`: a one-time run as the submitting deployer (no `run_as`
   or ACL, so only the deployer and workspace admins see or manage it; the App
   has no permission on it), serverless environment version 5 like the
   bundle's jobs with `databricks-sdk` pinned to the published release's own
   `uv.lock` (its bytes the manifest's, exactly one pin; never the deployer's
   installed version), no retries, `timeout_seconds` = (deadline - now) + the
   start budget (the start step's 600 s plus 300 s), and the idempotency
   token sha256(window digest, lease id): one run per window, a retried
   submit gets the same run.
4. **Armed.** A submit returns a run id before the run starts, and a run can
   still fail to start (its environment, the workspace file, its auth). The
   dead-man's first act is a create-only armed marker beside the journal
   (`<app>.quiesce-window.armed-<token>.json`), and the arm waits, at most
   `ARM_WAIT_S` (600 s, before the stop, so no outage), until the run is
   RUNNING and that marker names the window. A run that ends first or never
   arms is cancelled and the window refused.

After the arm the open admits the budget again and re-checks that blue is
still the App's last deployment (a deployment that landed during the publish
refuses the window). An arm or a check that fails journals and stops nothing;
any failure after the arm cancels the armed run. Until the deadline the
dead-man polls the journal and exits once the journal names its window (the
token) in any state but `window_open` or `window_starting`; another window's
journal, or none yet, is not its window. At the deadline, with the journal
still exactly its `window_open`, or its `window_starting` (the run's start
intent, which the run never journals inside the start guard, so at the
deadline the runner died before or during its start): a contract window stays
stopped (marker `deadman_left_stopped`); otherwise, only when the App is still
the window's stopped blue (its id, STOPPED, nothing pending, blue active, the
default source blue's restore source: no start of the run's happened, or it
failed), it runs `apps start`, waits for the deployment that start created to
be terminal and records `deadman_restarted` (or `deadman_start_failed`) with
that id. A dead runner therefore leaves the App restarted by the deadline plus
the start budget. It never deploys, updates, stops or changes a grant: its
only calls are `apps get`, `get_deployment` and `start`, the journal read and
its two create-only marker writes (`tests/unit/test_window_deadman.py`, which
also fails on a second SDK client; `tools/databricks/stop_primitive_map.py`
maps its start as a site no shell file reaches, and follows every alias,
method reference and raw REST path of an App or job-run primitive).

The run and the dead-man never both start the App: the run journals no start
intent within the start guard of the deadline (the start and re-pin budgets
plus two minutes, so a start the run begins can finish before the job's kill)
and leaves that start to the dead-man. Every path that closes the journal (the re-pin close, the EXIT
trap's resumed closes, a contract window's close after green's capture, an
open refused before its journal) cancels the run with
`quiesce_window cancel-deadman` (cancel only; nothing is deleted; it refuses a
run that is not exactly this App's dead-man: the whole run name, the task's
`--app` and a governed release file). A path that leaves the window open never
cancels it, and a cancel that fails only warns: the dead-man reads the closed
journal at its next poll and exits.

**Why a marker, not a signature.** The journal is signed with the proof-signing
key, which never leaves the deployer's process. The dead-man signs nothing: it
writes one create-only, unsigned marker beside the journal
(`<lease root>/<app>.quiesce-window.deadman-<token>.json`). The marker is a
pointer, never proof. The next governed run's pre-lease plan lets exactly an
open (or `window_starting`) window whose marker reads `deadman_restarted`
through (the public line says `deadman_recovery`); under its own lease, first
in step 0a (before the fail-closed arm and the treatment quiesce),
`app_deployment_rollback repin --from-deadman` takes the start, blue and the
tree digest from the signed journal, requires the marker to name that window
and blue, the App to serve exactly that deployment with compute ACTIVE and
blue's restore folder to keep the journal's digest, then proves source,
snapshot digest, env names, bindings (the record's or exactly this run's
bundle payload) and health, signs a new record version under this lease and
closes the journal (`closed_by: deadman_repin`). The re-pin changes blue's
deployment id but nothing else the window digests, and its signed
`predecessor_deployment_id` is the blue the pre-lease plan named, so a
recovering run that also needs a window keeps its pre-lease ack at the
pre-apply recheck (see the exact digest ack above); any other fact that moved
still refuses it (`window_unacked`), and the next run plans and acks it
afresh. A refusal exits 2 with nothing else run: the App stays exactly
as the dead-man left it, the journal open and the lease released, so the next
run retries a transient failure (a re-pin already signed resumes at the
journal close). A WR1 window restarted on the dead runner's applied bindings
re-pins only from that same commit, whose bundle payload is exactly those
bindings: re-run the dead window's commit, then roll forward. An App no longer
serving the dead-man's start (stopped or restarted by hand) needs the owner
break-glass (under the emergency recipe). Any other open journal still refuses before the lease
(`window_journal_open`): a contract window the dead-man left stopped, a start
it could not prove, or a dead-man that has not reached its deadline. That is
the owner break-glass case: a raw `apps start` with the owner's U2M profile
("Owner break-glass: a raw `apps start`", under the emergency recipe, which
also says what the next governed run does after one today).

**The workflow.** `deploy-dev.yml`'s deploy job (M1-PR7) first records
`MIP_JOB_DEADLINE_EPOCH` from its phase's own timeout, and a step after the
run, `if: always()`,
runs the read-only `quiesce_window status --unverified` and records the
journal's and the dead-man's states as codes only. That step has no key
registry (deploy.sh derives the verify key inside its own process), so it
reads the raw journal, labelled unverified; `--unverified` refuses
`--out-env`, no deploy path passes it, and every decision reads the verified
journal. The emergency recipe never runs
`scripts/deploy.sh`, so it opens no window and arms no dead-man.

**Owed live confirmations.** Spike S7 recorded the start the dead-man makes
(above): it judges the stopped App by its newest deployment and default source
and finds its start's deployment against the ids listed before it, within the
one `--start-timeout-seconds` budget. Still owed: a one-time serverless run
from a `/Workspace/Users/.../app-src/<id>/jobs/` file with these parameters,
how long it takes to reach RUNNING and arm (`ARM_WAIT_S` assumes at most
600 s), its idempotency token and timeout, `apps start` as the deployer, and
the cancel of a sleeping run. Nothing is trusted without the re-proof above.

### Governed run phases: plan, release and data (M1-PR6)

CI runs one phase per job (`./scripts/deploy.sh -t client --phase <phase>`,
M1-PR7), each with its own deadline in `MIP_JOB_DEADLINE_EPOCH` (plan 30,
release 330 and data 330 minutes). **A run with no `--phase` is unchanged**: it
is still the complete deploy, release then data in one process, and nothing in
this section gates it: its dry-run transcript is the one before this change,
line for line (only the commit sha and mktemp names differ between any two
runs). A live phase is CI-only; locally every phase runs with `--dry-run`.

| Phase | Lease | What it does |
|---|---|---|
| `plan` | none | The read-only steps through the pre-lease bundle plan gate and the window plan, then each phase's predicted budget (`tools/databricks/phase_budget.py describe`), kept as local files. No governed resource is changed: its pre-stop probes run with `--genie-turn off`, so the one Genie turn they otherwise ask (the probes' only call that is not a GET or a list; it opens a conversation) is the release's alone. It prints the sha256 of the App's active deployment id (`tools/databricks/active_deployment_pin.py digest`), and under GitHub Actions publishes the PlanReport in its two halves (M1-PR7): `PlanReport.public_summary()` to the step summary and `PlanReport.full()` create-only to `<deployer root>/plan-reports/<run id>-<attempt>.json` in the client workspace, whether the gate passed or refused. That create-only file is the plan phase's one workspace write. `tests/unit/test_plan_phase_live_sandbox.py` runs the real `--phase plan` live (`DRY_RUN=0`) from its first line, preflight included, in the offline release sandbox (core tier) and pins every Databricks CLI call (the version check alone) and every tool it runs, with the arguments that make each read-only; its local `-c` and heredoc programs are recorded by kind. `tests/unit/test_plan_phase_live_shape.py` pins the automation step's commands after the preflight in the agentic tier. |
| `release` | yes | The run with no `--phase` minus exactly the data jobs of steps 6-9b (silver, gold and its grant reconcile, the stale-rate guard, the lifecycle sync and the KPI snapshot), which it never runs, submits or triggers. It prints `data jobs deferred to --phase data`. In the core tier it is the core release path (M2-PR7b) with the same data jobs deferred. |
| `data` | yes, its own | Silver (unless `--skip-silver`), then `mip_scheduled_refresh` (FRED, gold, lifecycle mirror, KPI snapshot and its outcome gate), each followed by the reviewed-function grant reconcile and verify when its graph touches gold, then the data smoke. No pre-stop probe, no bundle plan or apply, and no Apps write: its Apps calls are reads only, the preflight's (`apps list`, `apps get`) and the App identity pinned to its signed rollback record. |

**Budgets** (`tools/databricks/phase_budget.py`). Each phase's prediction is a
reviewed sum of per-step budgets, calibrated to the 240-minute envelope the
parked workflow held for a whole first install. It must be at most 330
minutes, and `MIP_JOB_DEADLINE_EPOCH - now` must cover it plus 15 minutes,
decided by the window's own admission rule under its own schema
(`quiesce_window_budget.PHASE_BUDGET_SCHEMA`). A release's prediction adds the
one-use Lakebase role bootstraps when the window plan reads `not_applicable` (a
first install, or one resumed after the owner bootstrap: the App's role, and the
verifier's in the agentic tier) and the window's own length and restore when it
reads `window`. Over a signed blue (`none` or `window`) it also adds
`step0a_start`, the window's stop, start and re-pin budgets: step 0a starts and
re-pins a stopped blue (tier a, spike S7), stopping first an earlier failed
start a lost runner left active, and the plan cannot tell a stopped blue from
a serving one. A release refuses before its lease; a data phase refuses in its
read-only data plan, also before its lease. The numbers are not measurements:
the first governed run owes the live timings.

**The data job runner** (`tools/databricks/job_phase_runner.py`, with
`job_phase_definition.py` and `job_phase_ledger.py`):

- `plan` (read-only, before the lease): each job is the one this deployer's
  bundle state binds, every job of its `run_job_task` closure and every
  pipeline it starts is bound there too, and each was deployed by this
  deployer's bundle (`deployment.kind` BUNDLE, its metadata file under the
  client target's root). The definition digest is the sha256 of the closure's
  bound ids, live job settings and pipeline specs: a job recreated under a new
  id is another definition, as is any settings or spec change. It does not
  cover the deployed SQL and Python a task names by path, so a release that
  changes only that content keeps the digest (see the rerun ack below).
  `touches_gold` fails closed: only a silver, ref or catalog-DDL SQL file or
  the FRED or KPI script is gold-free. The live digest is compared with
  itself (plan, run, ledger), never with a digest of the bundle's own config:
  a job edited out of band after its bundle deploy, its deployment metadata
  intact, is digested and run as it is. That is an explicit, unspiked
  assumption: no section 7 spike covers the Jobs API's `settings.deployment`
  shape, the first governed data phase owes its live capture, and any other
  shape refuses.
- `begin`, `run`, `close` (under the lease): a signed ledger beside the lease
  (`<lease root>/<app>.data-phase-ledger.json`, the proof key's Ed25519 scheme
  the quiesce journal uses) holds one generation per data phase. Each run's
  idempotency token, `mip-data-<sha256(client, job, definition, generation,
  attempt)>`, is journaled before the run-now. A rerun re-attaches to a run
  still going, skips one that succeeded, and runs only a terminal-failed one
  (TERMINATED other than SUCCESS, SKIPPED, INTERNAL_ERROR) as the next attempt,
  with a new token. Every run it re-attaches to, or that a token resend
  answers, must be a run of the bound job id. One job runs at a time: no
  run-now, close or retirement while another entry of the generation still
  runs, planned in this phase or not, and no resume while an entry outside
  this phase's plan still runs (a planned entry still running is re-attached
  by its `run`). A run still going at the deadline
  exits 4: re-run `--phase data` and it re-attaches. Nothing is ever
  cancelled.
- **Stale definition.** An open generation whose entry ran under another
  definition or job id (a release changed the job's settings, or the job was
  recreated, between a failed attempt and this phase) refuses with the sha256
  of its reset plan (purpose `data_ledger_supersede`). Review it, then export
  `MIP_ACK_DATA_LEDGER_SUPERSEDE=<that sha256>` for one run (shell only, never
  `.env.local`). The runner recomputes the plan, refuses on any difference, and
  never supersedes while any run of the generation is still active.
- **A deleted bound job.** The Jobs API answers `runs/list` of a job deleted
  out of band with `InvalidParameterValue` ("Job N does not exist."). Such an
  entry (`tools/databricks/job_phase_activity.py`) is `absent`, not running,
  only when `jobs/get` answers `NotFound` and no run of its job id is active
  anywhere in the workspace; any other read error still refuses. The reset
  plan records `ledger_job_present: false` for it, so the supersede ack names
  the absence. Recovery is in band: the release that rebinds the job is not
  refused by the ledger (the bundle plan gate still judges the rebind), then
  `--phase data` refuses with the supersede ack, and that ack retires the
  generation. A `--skip-silver` phase resumes, reruns and closes past an absent
  silver. Never delete the signed ledger file.
- **Rerun.** An open generation with nothing stale can still need every job
  again: a data smoke that fails after both jobs succeeded (gold scored before
  a later FRED refresh), a release that changed only the deployed SQL, or a
  run the Jobs API no longer returns. Each resume prints the sha256 of the
  generation's rerun plan (purpose `data_ledger_rerun`); review it, then
  export `MIP_ACK_DATA_LEDGER_RERUN=<that sha256>` for one run (shell only,
  never `.env.local`). The runner recomputes it and refuses on any difference,
  never retires while a run is active (an unreadable run is judged by its
  job's active runs), and neither ack ever satisfies the other's purpose.
- **Attack O11.** After every job whose graph touches gold or the UC
  functions, the data phase runs `reconcile_reviewed_function_execute_grants`
  (moved verbatim to `scripts/lib/deploy_reviewed_function_grants_lifecycle.sh`
  so both phases run the same function) and its read-only
  `verify_reviewed_function_execute_grants`, whatever the job did but a run
  still going (exit 4, re-attached later): a failed or refused job is repaired
  too, as the release's governed job boundary repairs, and the phase exits
  with the job's own code. A failure of either refuses success: no later job
  runs and the generation stays open. The grantee is the App's service
  principal only while it is the one its signed last-good rollback record pins
  (`tools/databricks/data_phase_app_identity.py`; v5/v6 or v7, so adopt-live's
  and the core release's records pin it, and a retired tombstone pins nothing)
  and is not the account credential's client; otherwise the phase refuses
  before its ledger.
- **No overlap.** A data phase that exits 4 releases the lease with its job
  still running. A release, or a run with no `--phase`, reads the ledger right
  after taking its lease (`job_phase_runner quiet`, read-only) and refuses
  while any entry of an open generation still runs. An entry whose job was
  deleted is not running: `quiet` names it and the supersede ack remedy, and
  passes.

The runner's run-now is the one reviewed run-now site
(`tools/databricks/stop_primitive_map.JOB_RUN_SITES`), its mutating modes are
the data-phase library's alone, every shell `databricks bundle run <job>` is
a reviewed site (`SHELL_JOB_RUN_SITES`), and no tool, job source or shell file
starts or stops a pipeline update (`PIPELINE_RUN_SITES` is empty): silver
refreshes only as `mip_refresh_silver`.

**First install: awaiting owner bootstrap** (`tools/databricks/first_install_bootstrap.py`).
The account principal manages only the service principals it was enumerated
for, so a new App's identity needs the owner's human-run
`python -m tools.clients.bootstrap_account --post-first-install` before any
App-identity proof. A `--phase release` that created the App (stopped,
source-free, bound, its signed first-install journal claimed) checks read-only
whether the account principal holds Service Principal Manager on it. Until it
does, the release writes a signed awaiting marker
(`<lease root>/<app>.first-install-bootstrap.json`, bound to the journal's
claim), keeps the App, and stops with exit 3 and
`awaiting owner bootstrap --post-first-install`. The owner then runs:

```bash
python -m tools.clients.bootstrap_account --plan --for post-first-install
python -m tools.clients.bootstrap_account --post-first-install --ack-plan <sha256>
```

The next `--phase release` resumes at the same point: step 0a reads the marker
in place of deleting the journal-owned App, the bundle apply proves the App is
still bound exactly as its journal recorded, and the gate consumes the marker
once the check passes. From there it is an ordinary first install, whose
failure is compensated as before. A run with no `--phase` never reaches the
gate, never resumes the kept App and never deletes it silently: its step 0a
refuses (exit 2, the App kept) with the sha256 of the marker's retire plan
(purpose `first_install_bootstrap_retire`). To abandon that first install,
review it and export `MIP_ACK_FIRST_INSTALL_BOOTSTRAP_RETIRE=<that sha256>` for
one run (shell only, never `.env.local`): the marker is retired and that run's
recovery deletes the App, with any owner grant on its identity, as any
interrupted first install. A marker naming a claim the journal no longer holds
(its first install was deleted since) is stale: it protects nothing, reads as
absent and is replaced by the next marker, so no manual step clears it. Owed
live: what the account principal may read of a rule set it does not manage yet
(a refusal reads as "not held"), and whether a step before the gate in the
resumed run needs that grant.

**Data phase on a first install.** The release's gold-dependent tail (the
Genie trusted assets, the agentic tier's Lakebase synced tables, Agent
Evaluation and the smoke) reads gold. A routine release reads the gold an
earlier data phase wrote; a first install in phase mode has none yet. Until
M1-PR7 decides the dispatch order, the no-phase run (release then data in one
process) stays the first-install path; see the PR's open questions.

### Adopting an existing install (M2-PR6, bind only)

An install another identity deployed keeps its direct-engine bundle state in
that identity's root, so the client deployer's own state binds nothing and
every plan refuses it as `not_adopted`. `--phase adopt` binds exactly the
legacy keys into the deployer's state; `--phase unadopt` is its exact inverse.
Neither stops, starts or deploys the App, changes a grant or deletes anything:
the bind's only mutation is the deployer's own bundle state, through
`databricks bundle deployment bind|unbind`, under the signed lease. Each phase
then runs `adopt-live` (next section), which signs the live deployment as an
adopted blue or retires that record.

**Profile.** `MIP_LEGACY_BUNDLE_ROOT` (identifying: a GitHub Environment secret
in CI, `.env.local` locally) names the legacy root,
`/Workspace/Users/<owner>/.bundle/mortgage-intelligence-platform/<leaf>`. The
legacy map's ids and paths are read from that root's `state/resources.json` at
run time; they are never committed. The profile then requires
`MIP_DASHBOARD_PARENT_PATH` inside the legacy root (see the dashboards below).

**The run.** Both phases run the same read-only steps as `--phase plan`, with
the step-0 Genie pin check (no phase guard) and the pre-stop probes in their
adopt mode, so an adopt or unadopt dispatch carries the `genie_pin` and
`accepted_exposure_plan` acks those read (`operation_acks`; see "CI deploy
(deploy-dev.yml)" below). Until a signed record names the governed Genie space, the
adopt phases' bundle plan, binds and summary bind the one title match that
same `genie_pin` names (`bundle_env --adopt-acked-genie-pin`; "The governed
Genie space, pinned by id", "The adopt phases (B4(c))"), and refuse
`no pin names the governed Genie space yet` only without the flag. They run with
the live checks in their adopt mode (`tools.clients.live_checks --phase adopt`
or `--phase unadopt` accepts an App the deployer's state does not bind, and its
unmarked catalog: adopt binds that App, unadopt unbinds it, and the half-bound
state an unproven undo can leave, legacy keys bound without the App, must
still reach unadopt; a catalog marked for another install still refuses). In
place of the pre-lease gate,
`tools/databricks/install_state_adopt.py plan` computes the canonical plan
(tools/databricks/adopt_legacy_map.py) from the pre-bind bundle plan, both
states and one GET by id per legacy object, and refuses, before the lease:

- `missing`: the id no longer resolves (a trashed dashboard or a deleted
  experiment included);
- `renamed`: the live name is neither the bundle's name for the key nor its
  development-mode form (`[dev <user>] <name>`, on an experiment path's
  basename); an App, a Lakebase instance or catalog must keep its name, since
  it is named by its id;
- `foreign_bound`: a job or pipeline whose bundle deployment metadata names
  another root than the legacy root or the deployer's;
  `bound_elsewhere` (the deployer's state binds the key to another object, or
  the object under another key) and `duplicate_id` (two legacy keys bind one
  object);
- `deployer_state_orphan`, in either phase: a key the deployer's state binds
  and the bundle no longer declares (the pre-bind plan carries it as a delete,
  which names no bundle key);
- `legacy_key_unknown`, `dashboard_parent_moved`, `unresolved_name`,
  `state_mismatch`, `malformed_state` and `read_failed` (a read error is never
  read as absent).

It prints the plan's sha256. That digest is the exact acknowledgement: the
owner attestation is deferred (review mode `single_maintainer_audited`), so a
mutating run needs `MIP_ADOPT_PLAN_ACK=<sha256>` exported in the shell (never
`.env.local`). Without it the run ends there, read-only. With it the run takes
the signed lease and runs `install_state_adopt apply`, which recomputes the
plan immediately before the first state edit, refuses with nothing changed
unless the digest is the acknowledged one, asserts the lease before every
edit and then:

- **adopt** binds exactly the plan's keys
  (`bundle_env deployment bind <key> <id> -t client --auto-approve`), proves
  the state binds each to its id, plans again and judges that plan with the
  bundle plan gate's adopt mode (position `post-bind`): no delete, recreate or
  `update_id`; no Lakebase instance update or resize (`instance_change`; the
  release acknowledgement does not apply); creates exactly the bundle's keys
  minus the bound keys (`create_set_mismatch`, a grant create included); no
  dashboard recreate or move (`dashboard_recreate`, `dashboard_move`); and
  every rename's new name looked up with the create's own read-only lookup
  before anything is renamed (`rename_collision`). On any failure after the
  first bind it unbinds each key the state shows this run bound, in reverse
  (the App last), and reads the state again to prove it (exit 2); if that
  cannot be proven it exits 3 and the private report names what the state may
  still bind;
- **unadopt** unbinds each legacy key the deployer's state binds to its legacy
  id, and nothing else: a key the deployer created itself stays bound. It
  unbinds in the reverse of adopt's order, so the App's binding is the last one
  removed, and stops at the first failure (exit 3).

After an exit 3 from either phase, run `--phase unadopt` (again): its plan
unbinds whatever legacy keys the state still binds, and its live checks accept
that half-bound state.

`bundle_env deployment` admits a non-App bind or unbind only as one of the
edits an adopt plan names, never with `--force-lock`
(tools/databricks/bundle_env_adopt.py). The caller passes the plan file, its
digest and the held lease's evidence as wrapper-only flags that never reach the
CLI (`--adopt-plan`, `--adopt-plan-sha256`, `--adopt-lease-id`,
`--adopt-source-git-sha`). Those prove nothing alone, since anyone can digest a
document they wrote, so bundle_env also requires the digest to be the
`MIP_ADOPT_PLAN_ACK` in its own environment, the plan to name the resolved
profile's client, deployer root and legacy root, and the profile App's signed
lease to be held, which only the deploy's signing authority can take. The CLI
never inherits the proof-signing keys, and its output (which names ids) stays
out of the log; every id read from either state is a runner
mask. A live adopt or unadopt phase is CI-only, like the plan phase; locally,
`--phase adopt --dry-run` (with or without `MIP_ADOPT_PLAN_ACK`; with it,
adopt-live's own ack must be exported too) prints the steps and stops before
the lease. The expected run on client #1 binds 16 keys and
plans exactly 2 creates (the two jobs the legacy install never had).

**Owed live confirmations.** No adoption has run. The direct engine's `bind`
of every declared kind (App, Lakebase instance and catalog, warehouse, jobs,
pipeline, dashboards, experiment) on CLI v1.10.0 is unconfirmed: an
unsupported kind fails its bind, and the run undoes the binds before it.

**Dashboards (attack O9).** Spike S4 measured a dashboard `parent_path` move
on throwaway resources (CLI v1.10.0, direct engine): `parent_path` is
`spec:immutable`, so a changed folder plans `recreate` (a new dashboard id,
the old one trashed, its published revision and URL gone), and the Lakeview
API has no in-place move (an update carrying only `parent_path` is accepted
and ignored). A `display_name` change, such as the development-mode prefix
dropping, is an in-place update. So `MIP_DASHBOARD_PARENT_PATH` stays pinned
at the legacy dashboards' live folder, permanently for this install (the
profile requires it inside `MIP_LEGACY_BUNDLE_ROOT`; the adopt plan refuses a
legacy dashboard outside it and records `pinned_permanent`; the post-bind
gate refuses a dashboard recreate or move). Moving them into the deployer's
root is a recreate, so it is only ever an owner decision, never a deploy.

`lakeview.get` answers a dashboard's folder as `/Users/<owner>/...`, while
`MIP_DASHBOARD_PARENT_PATH` must be `/Workspace/...` (spike S4 read both
live client-#1 dashboards that way), and the CLI plans no change between the
two spellings of one folder. The adopt map compares the two through
`adopt_legacy_map.canonical_folder`, which aliases exactly `/Users` and
`/Shared` to their `/Workspace` spelling and drops a trailing slash; any
other difference is another folder and refuses `dashboard_parent_moved`.

**Archiving the legacy root (cutover step 16).**
`python -m tools.clients.archive_legacy_root` is read-only by default. It plans
copying the legacy root's top-level children (`--only <name>` narrows them)
into `<deployer root>/legacy-archive/<legacy leaf>/` and refuses (`bound_path`)
any move whose path set intersects a path a bound resource still uses (a
dashboard's file, an experiment, the App's default and deployed source
folders, every workspace path a bound job's settings or the pipeline's spec
names) or the legacy `state` folder, which unadopt reads. It refuses unless the
deployer's state binds the App. The Workspace API has no move call, so
`--apply --ack-plan <sha256>` recomputes the plan, refuses on any difference
and copies each file and notebook create-only and byte-verified. It never
deletes: removing the legacy originals stays the owner's cutover step, from
the plan's list.

### Signing the live deployment as an adopted blue (`adopt-live`, M2-PR7)

`tools/databricks/adopt_live.py` runs in the adopt phases after the bind (adopt)
or the unbind (unadopt): `plan` before the signed lease, `apply` under it. It
never stops, starts, deploys or updates the App (a spy test of every SDK App
mutation and the stop-primitive AST map both prove it). Its writes are a
create-only copy of the live source folder, the rollback scope when absent,
and one signed record version.

**Preconditions** (`tools/databricks/adopt_live_facts.py`), each with its own
refusal, all before the lease: the active deployment is the newest SUCCEEDED
one, nothing is pending and the compute is ACTIVE (`deployment_unstable`; a
stopped App has no active deployment, spike S7, so it is never adopted: step
0a's tier a starts only an already-adopted blue); its
env-name set (names only; Databricks redacts the values) carries no agent
model, serving, Gateway, Supervisor, eval, Gateway-attestation or proxy name
(`env_names_not_proxyless`; the two agentic switches `MIP_AI_GATEWAY` and
`MIP_AGENT_ORCHESTRATOR` are core names whose restore value must be `0`); the live
bindings are core kinds only (`bindings_invalid`) and, after the bind, equal
the profile's reviewed bindings from the bundle summary (`bindings_differ`);
the App ACL is snapshotted (`acl_unreadable`); the campaign-treatment boundary
is exactly converged, read-only, in the runtime or the quiesce mode
(`treatment_unverified`); the App's authenticated health names a commit that
resolves in the checkout and is an ancestor of main, where main is
`refs/remotes/origin/main`, never the run's own HEAD, and the run's source
commit is itself on that main (`health_unreadable`, `main_unresolved`,
`source_not_on_main`, `health_sha_not_on_main`; a CI checkout needs the full
history and origin/main fetched), so a run from a branch whose HEAD carries an
unmerged commit refuses; the full-tree digest of the deployment's source folder (every file,
`app.yaml` and `frontend/dist` included) equals that of its `/src/` snapshot
(`tree_drift`); `app.yaml` is byte-equal to the repo's at the health sha
(`app_yaml_differs`); and every other file outside `frontend/dist` is the App
runtime closure git tracks at that sha (`closure_differs`). A folder staged by
an older closure definition therefore refuses, and the remedy is a fresh
emergency deploy of a current commit before the adoption. `frontend/dist` is
compared with a dist adopt-live builds itself from the health sha when
`MIP_ADOPT_BUILD_REFERENCE_DIST=1` is exported for the run: `git archive` of
that sha into a private temporary tree, then `npm ci --ignore-scripts` and
`npm run build` there, with an allowlisted environment that holds no
Databricks, proof or npm credential: the tools, an allowlist of npm's
registry, cache and proxy settings (never an auth token or password, and no
registry or proxy URL that carries userinfo), the reviewed tenant appearance,
and npm's user and global config pointed at an empty private file, so
`~/.npmrc` is never read. It is no sandbox: the build runs as the operator's
OS user, like the deploy's own frontend build. No CI
job keeps a dist artifact, and a directory an operator names proves nothing (a
copy of the live dist itself compares equal), so no such input exists. Equal
bytes are `verified`, and the plan and the record carry the digest of the dist
they matched; anything else (no build, a failed one, another Node, a lender
mark the build cannot stage) is `unverified`, and any restore to an unverified
record needs the `restore_unverified_frontend` ack.

**The record slot and the modes.** adopt signs only when the slot holds no
record, a retired tombstone, or this deployment's own adopted record (an
idempotent re-run); any other record refuses (`record_present`). It signs the
core tier only (`tier_agentic`): the adopted deployment carries no Gateway, so
its record cannot carry the Gateway resource proof, and the agentic tier waits
for the estate inventory (M4-PR1). `MIP_ADOPT_LIVE_SUPERSEDE=<sha256 of the
record's deployment id>` in an adopt phase re-signs an adopted record onto a
newer active deployment after the same proofs (`predecessor_deployment_id`
names the old one). unadopt writes the tombstone version (`provenance:
retired`, every other field kept), so a later adopt starts clean; with no
adopted record it retires nothing, so the bind's unadopt recovery still runs
in either tier, and a captured blue refuses (`record_present`). Only adopt and
supersede refuse the agentic tier. Over a tombstone, the v5/v6 reader's
remedy (the same text in either tier) is a re-adoption in the core tier:
`--phase adopt` from a profile whose `MIP_AGENTIC_TIER` is off. adopt-live
refuses the agentic tier (`tier_agentic`) and the one-time unsigned rebase's
bootstrap gate refuses over any signed record, a tombstone included, so an
agentic-tier profile has no route over a tombstone until the agentic tier can
adopt.

**The acks.** The owner attestation is deferred (review mode
`single_maintainer_audited`), so each mode prints the sha256 of its plan's
purpose-named canonical body (`tools/databricks/operation_ack.py`):
`MIP_ACK_ADOPT_LIVE`, `MIP_ACK_SUPERSEDE` or `MIP_ACK_UNADOPT`, a one-run shell
export and never a `.env.local` line. Through `deploy-dev.yml` each is an
`operation_acks` pair: `adopt_live=<sha256>` (phase adopt),
`supersede=<sha256>` with the selector `supersedes_deployment=<sha256 of the
adopted record's deployment id>` (phase adopt; the selector alone runs the
read-only supersede plan, which prints the supersede ack), and
`unadopt=<sha256>` (phase unadopt), beside the bind's `adopt_plan=<sha256>`.
`record_present` over this install's own adopted record of another
deployment, and the pre-stop probe's `rollback_record_drift` remedy, print the
selector's value (a digest, never the id). The bind's `MIP_ADOPT_PLAN_ACK` without
it refuses before the lease. The body names its purpose, so an ack for one
purpose never satisfies another, and whenever the slot holds a record the plan
names that exact signed version's sha256 (`record_sha256`), so an ack covers
one record version: the first unadopt's ack is refused after a re-adoption.
`apply` recomputes the plan immediately before its first write and refuses on
any difference. The `override_freeze` ack goes to the publisher as
`--override-freeze-ack`, and the `restore_unverified_frontend` ack is the
one-run export `MIP_ACK_RESTORE_UNVERIFIED_FRONTEND`, which the core release
hands to its pre-activation proof and its tier-b restore (M2-PR7b); a refusal
names the route each really takes.

**The copy.** `apply` publishes the downloaded bytes create-only into the
deployer's `app-src/adopted-<id12>-<digest12>` (`id12` from the sha256 of the
deployment id, `digest12` from the full-tree digest) with
`app_release_source`'s own stage checks, listing and sha256 proof, re-hashes the
copy remotely and refuses on any difference, with no record written. Prune
never deletes it: its name is no release id, and every record that names it is
read before any delete.

**The record (v7).** `tools/databricks/app_rollback_record_v7.py`: `tier`
(`core`, whose resource proof is exactly the core bindings and refuses any
serving-endpoint, Gateway or proxy binding, never consulting the Gateway
resource proof; or `agentic`, which keeps v6's Gateway contract unchanged),
`provenance` (`adopted`, `captured`, `retired`), `ack_purpose` and
`ack_sha256` (the exact ack that authorised the version, in place of the
plan's `profile_attestation_digest`, which needs the deferred attestation),
`source_code_path`, `source_tree_sha256`, `restore_source_path`,
`frontend_provenance` and `frontend_reference_sha256`, `env_names`,
`restore_payload` and `restore_payload_sha256` (null until a release proves
them), `predecessor_deployment_id`, the App ACL snapshot and the treatment
mode. A core record's restore payload carries `MIP_AI_GATEWAY` and
`MIP_AGENT_ORCHESTRATOR` as exactly a literal `0` or not at all (the core
tier's own invariant), so a core record never restores an agentic surface.
Golden v5 and v6 records verify byte-identically.

Where the tier is checked today: adopt-live signs the core tier only; the
pre-activation proof and `tier_b_payload` (before it re-hashes the copy)
refuse a v7 blue whose tier is not the profile's `MIP_AGENTIC_TIER`
(`blue_tier_mismatch`), the proof's tier gate admitting a core blue under an
agentic profile only with the exact `tier_transition` ack (the core restore,
whose own load runs that gate, then hands `tier_b_payload` the core tier);
and `app_deployment_rollback
inspect` re-proves the record's own tier (core: the App identity and exactly
the record's core bindings, never the Gateway; agentic: v6 inspect's own
Gateway resource proof, its reviewed-function owner the signed payload's).
The core release path (M2-PR7b, "The agentic tier" above) adds the capture
writer (core only, the profile's tier), the restore that deploys
`tier_b_payload`'s payload with the restore-owned resource repair, and the
release's rebuild of an adopted blue's restore payload, handed to the proof
only with blue's exact env-name set (D0's names carry neither agentic
switch, so the rebuild leaves both out).

**Spike S7 is recorded: the default source is not settable.**
`apps create-update` rejects the `default_source_code_path` mask, and
`apps update` succeeds but ignores the field; only a deployment moves it.
`S7_DEFAULT_SOURCE_SETTABLE` in `adopt_live.py` stays False and the switch
refuses for good (`default_source_not_settable`; the adopt plan records
`s7_default_source: not_settable`). The default source stays on D0's own
folder, and any later start snapshots that folder's current bytes, which the
start proofs' full-tree digests check.

**The pre-activation rollback proof (attack O2).** The first activation of a
run (`deploy_app_snapshot`) first runs
`blue_restore_proof prepare-activation`. A v5 or v6 record's own payload is
blue's tier-b restore payload, so nothing changes for the agentic release. A
v7 record must carry `restore_payload_sha256`, re-proven now (the copy
re-hashed), or be handed blue's restore payload (env-name set equal to blue's,
source the immutable copy), which is checked and recorded under the lease;
otherwise activation refuses unless `MIP_ACK_NO_ROLLBACK_FOR` is the exact
`no_rollback_for` digest the refusal prints. Its plan covers this blue and
this green only: sha256(blue's deployment id), the release's source commit
and its release id (`--source-git-sha`, `--release-id`), so an ack left
exported in a long-lived shell never carries to another release. Tier a (start-only) refuses once blue is no longer the App's last
deployment. The core release rebuilds an adopted blue's restore payload
before this proof, and an unverified blue frontend needs its
`restore_unverified_frontend` ack here too (M2-PR7b).

**Freeze.** The emergency recipe's publisher obeys a signed freeze marker
(the emergency recipe, above).

### Ownership handover (M2-PR8, cutover steps 7 and 8)

An install deployed by hand is owned by that person: its Unity Catalog
objects, its Lakebase database and every `mip_app` object. Two owner-run
tools hand them to the install's identities, both plan-first. The owner
hardware-key attestation is deferred (review mode `single_maintainer_audited`),
so each mutating run needs an **exact digest ack**: the read-only plan prints
the sha256 of its canonical document, `--ack-plan <sha256>` passes it for that
one run (a flag, never a `.env.local` line), and the tool recomputes the plan
immediately before its first change and refuses, with nothing changed, when
the two differ. Each keeps a per-object journal at an explicit `--journal`
path (a new file, mode 0600, never through a symlink, rewritten atomically
after every state change), and `--revert --journal <path>` is the exact
inverse from it, also after a partial failure: every object that may have
moved goes back to its journaled prior owner, and only while its live owner
is still the one the handover gave it. A revert prints its own digest first
and mutates only with `--ack-plan <that digest>`. Printed lines carry codes,
counts and digests; names and owners go only to `--report-out` (mode 0600)
and the journal. Neither tool has run live.

**Unity Catalog (step 7).** Run as the legacy owner:

```bash
python -m tools.clients.handover_uc_ownership --plan --report-out <private report>
python -m tools.clients.handover_uc_ownership --apply --ack-plan <sha256> --journal <new path>
python -m tools.clients.handover_uc_ownership --verify
# after the release (cutover step 12):
python -m tools.clients.handover_uc_ownership --verify --released
```

The target is `<MIP_IDENTITY_PREFIX>-data-owners`. The plan enumerates the
governed catalog (`MIP_DEFAULT_CATALOG`): the catalog, its schemas, tables,
views, volumes, functions and registered models the legacy owner owns, leaves
first and the catalog last; the Lakebase sync catalog
(`MIP_LAKEBASE_SYNC_CATALOG`) at catalog level only (what a managed-online
catalog holds follows its database and sync pipelines, which spike S3 has not
rehearsed). Each change is one typed Unity Catalog update with only `owner`
set (the plan shows the `PATCH` and the equivalent `ALTER ... OWNER TO`),
after a compare-and-swap read of the live owner. It never touches, and
refuses explicitly: the metastore (`metastore`), a Delta Sharing catalog
(`share_catalog`), a foreign catalog or table (`foreign_catalog`,
`foreign_securable`), a system catalog (`system_catalog`), an object the
System user owns (never a change), and the users, account users or admins
vocabulary as the target. The first failure stops the apply (exit 3).
`--verify` is read-only: nothing left to move, every pipeline dataset the
legacy owner still owns paired exactly, and every pipeline dataset owned by
its own pipeline's live `run_as_user_name` and by an approved principal: the
jobs identity (`MIP_IDENTITY_IDS_JSON.jobs`), or the legacy owner until the
release (`tools/clients/handover_pipeline_owner.py`: `pipeline_unresolved`,
`pipeline_run_as_unreadable`, `pipeline_owner_not_run_as`,
`pipeline_owner_unapproved`). The platform keeps a dataset's owner equal to
its pipeline's run_as, so the approved-owner rule is what refuses: another
identity running a pipeline into the governed catalog, or a run_as moved off
the jobs identity. `--verify --released`, after the release, no longer admits
the legacy owner, pair or not. `--interim-days N` (1 to 14, default 14) sets
the suggested pairs' expiry.

**Pipeline-managed datasets.** Spike S4 measured that a pipeline-published
streaming table or materialized view can never change owner by itself: the
REST owner PATCH and every `ALTER ... OWNER TO` form were refused, for every
actor tried, metastore owner included
(`CHANGE_OWNER_NOT_ALLOWED_FOR_PIPELINE_TABLE`). Its owner is always the
pipeline's run_as identity, and it flips the moment the pipeline's run_as
setting changes, before any run; grants on the datasets survive the flip. So
the plan excludes them for good. Until the release they are the legacy
identity's, which the pipeline runs as; the release's bundle apply moves the
pipeline's run_as to the jobs identity and with it every dataset, at once,
while blue serves. From then on the jobs identity (`MIP_IDENTITY_IDS_JSON.jobs`)
owns them permanently, never `<MIP_IDENTITY_PREFIX>-data-owners`: a group
cannot be a run_as. `--verify --released` after the release proves that end
state: every pipeline dataset in the governed catalog is owned by the jobs
identity, and its pipeline runs live as it. The release postflight's live
run_as check (below, "Workspace ACLs") already refuses the release when the
governed pipeline does not. The private
report (`--report-out`; the printed lines carry counts only) suggests one
interim owner pair per dataset, `(full_name, owner, expires_at)`, at most 14
days ahead, as rows (`suggested_interim_pairs`) and as the single-line value
the profile field takes (`interim_owner_pairs_json`). The reviewed pairs are per-client
configuration, `MIP_UC_INTERIM_OWNER_PAIRS_JSON` (identifying: a GitHub
Environment secret, or `.env.local`), never committed.
`ApprovedOwnerPolicy` admits a pair's owner for exactly that object, character
for character; an expired pair refuses its object, and the handover plan
refuses once a configured pair has expired (`interim_pair_expired`). A pair
never approves a grantee (the owner-equivalent probe does not read pairs) or
the metastore. The deploy's pre-stop probe set holds no pipeline dataset, so
it passes none: no deploy probe judges a pipeline dataset today, so no
production caller hands pairs to the policy yet, and `--verify` matches each
dataset with the same `build_pairs` output from the same profile field.
A pair covers the window from this handover to the release's bundle apply;
no journaled re-own exists. Unset the field once `--verify --released`
passes after the release; the pairs then match nothing.

**Lakebase (step 8).** Spike S3 rehearsed this on a throwaway of the install's
type (Provisioned, PostgreSQL 16) and chose L-A role setup with this tool's
per-object `ALTER ... OWNER TO` at `lock_timeout = '3s'` and
`statement_timeout = '30s'`: the worst App wait was about the lock timeout,
with no App error from lock contention. Not `REASSIGN OWNED` (all or nothing
across every table, and it also takes the instance's other databases and
extensions) and not L-B (it deletes the legacy role, which stale jobs still
run as). First print the L-A setup, read-only, as the legacy owner connected
to the App database:

```bash
python -m tools.clients.lakebase_handover --preconditions [--report-out <private file>]
```

It prints, in order and quoted, only the steps that do not hold yet, with the
role names from the profile and the database (so the output names roles: keep
it in your terminal or the mode-0600 report, never in a shared log):

1. `CREATE EXTENSION IF NOT EXISTS databricks_auth;` and
   `SELECT databricks_create_role('<deployer application id>', 'SERVICE_PRINCIPAL');`,
   run BY THE LEGACY OWNER, which leaves the legacy owner an ADMIN edge on the
   new role. Never create the deployer's role through the REST API or the UI
   first: that leaves no ADMIN edge, so step 2 fails; `--preconditions` then
   refuses `target_role_without_admin_edge` (the tool never drops a role);
2. `GRANT "<deployer>" TO "<legacy owner>" WITH INHERIT TRUE, SET TRUE;`
   (SET for the apply, INHERIT for the exact revert);
3. `GRANT CREATE ON SCHEMA "mip_app" TO "<deployer>";`
4. `GRANT USAGE ON SCHEMA "<schema>" TO "<deployer>";` for every other
   non-system schema the deployer cannot use yet (a synced-table or leftover
   schema, for example). `public` is never printed: the deployer uses it as
   the database owner. The grants job closes `public` as `pg_database_owner`
   (it refuses "public-schema closure authority mismatch" unless the role
   running it may `SET ROLE` to it) and its exact public-schema gate refuses
   any extra ACL row there, so a USAGE grant on `public` breaks every later
   release. When the App database belongs to neither the legacy owner (the
   handover then moves it) nor the deployer, and the deployer holds no
   `SET` and `INHERIT` membership in its owner, the mode prints no SQL and
   refuses `target_will_not_own_database`: the database's owner gives it to
   the legacy owner, then re-run this mode. SET alone fails the grants job's
   revokes in `public`, and INHERIT alone its closure (PostgreSQL 16.15, PR
   #336 review round 2). USAGE lets the grants job look up the objects in a
   schema, but it is not enough on its own:
5. `GRANT <one privilege> ON <object> TO "<deployer>";` for every table,
   view, sequence and routine the handover leaves to another owner (anything
   outside `mip_app`, and a `mip_app` object another role owns) that the
   deployer holds no privilege on that its revoke accepts: `SELECT` on a
   table, view or sequence, `EXECUTE` on a routine. The grants job run as the
   deployer revokes the App roles' privileges on every one of them, and
   PostgreSQL refuses a revoke outright from a role that holds nothing on the
   object: with step 4 alone, a legacy-owned table in a usable schema
   planned clean and the grants job failed "permission denied for table"
   after the apply (PostgreSQL 16.15, PR #336 review). Its `REVOKE ALL` on a
   table, view or sequence also revokes each column, where only a column
   privilege counts: the deployer's `SELECT`, `INSERT`, `UPDATE` or
   `REFERENCES` on a table or view, or `SELECT` or `UPDATE` on a sequence,
   is enough, and `TRIGGER`, `DELETE` or `TRUNCATE` alone on a table, or
   `USAGE` alone on a sequence, failed "permission denied for column
   tableoid" (round 2). The step prints a grant only where the legacy owner can
   give one (it owns the object, or holds its owner's privileges); each other
   object refuses `target_needs_object_privilege` until its owner grants the
   deployer a privilege on it. Who owns a synced-table schema's tables on the
   install has not been measured: this step reads it. Column grants and
   default-privilege rows are not predicted (the grants job revokes only the
   App roles' entries there, which a grants run before the handover leaves
   empty); one that appears later fails the grants job closed.

Run them in `psql` as the legacy owner and re-run `--preconditions` until it
prints `-- Every precondition holds.`. Names in its comments are JSON strings,
and a statement whose name holds a control character is not printed: it
refuses `name_has_control_character`, and the owner grants that one by hand. Before the apply, check read-only that
no App session holds a long transaction: one older than the per-object budget
(10 attempts, about 120 s) exhausts it into the WR5 window or a revert
(`SELECT state, now() - xact_start FROM pg_stat_activity WHERE datname =
current_database() AND xact_start IS NOT NULL ORDER BY 2 DESC;`). Then, as the
legacy owner:

```bash
python -m tools.clients.lakebase_handover --plan
python -m tools.clients.lakebase_handover --apply --ack-plan <sha256> --journal <new path>
```

The plan reads in a server-enforced read-only session; it refuses a missing
target role (`target_role_missing`), one the session cannot `SET ROLE` to
(`target_role_not_settable`), one whose privileges the session does not hold
(`caller_cannot_revert`: after the apply the target owns every object and only
an owner may change an owner, so a SET-only membership applies and then
cannot revert; PostgreSQL 16 refuses that revert with 42501), an unreviewed
object kind, and each other
precondition PostgreSQL sets for `ALTER ... OWNER TO`: the target's CREATE on
`mip_app` for the schema's objects (`target_needs_schema_create`), the
session's CREATE on the database for the schema
(`caller_needs_database_create`) and CREATEDB for the database
(`caller_needs_createdb`). It also refuses an App database the target will
neither own nor act for as owner (`target_will_not_own_database`, step 4);
once per schema and by count only, a non-system schema the target cannot use
(`target_needs_schema_usage`): the grants job the deployer runs after the
handover revokes on every table in every non-system schema and failed
"permission denied for schema" without it (spike S3); each object the
handover leaves to another owner that the target holds no privilege on that
its revoke accepts (`target_needs_object_privilege`, step 5); and an entry
naming the target that the revert could not restore exactly
(`target_grant_unrevertible`, below). The rehearsal reads the same
preconditions first. The apply moves
the App database and each `mip_app` object the legacy role owns, one
`ALTER ... OWNER TO` per transaction under `SET LOCAL lock_timeout = '3s'`
and `SET LOCAL statement_timeout = '30s'`, in this order: types and domains,
tables (a column's own sequence moves with its table) and standalone
sequences, invoker routines, views and materialized views, the schema,
SECURITY DEFINER routines, the database. A view or a SECURITY DEFINER routine
reads with its owner's privileges, and under L-A the legacy role holds the
deployer's privileges but not the reverse, so what is read moves first (S3:
with views first, every App read through a view failed 42501 for as long as
the handover ran). A definer routine also resolves the names in its body as
its owner, which needs USAGE on `mip_app`; step 3 gives the deployer only
CREATE there, so definer routines move after the schema (moved before it,
each failed "permission denied for schema mip_app" until the schema
followed, in the apply and in the revert). `ALTER OWNER` takes an ACCESS
EXCLUSIVE lock, and App queries queue behind a waiting one, so the lock
timeout is what bounds an App query's wait. A lock timeout rolls back and
retries after a backoff, at most 10 times per object and 10 minutes in all.
It never creates, drops or reassigns a role, and the apply emits no DROP,
DELETE, TRUNCATE, GRANT or REVOKE. When the budget runs out it stops with nothing
half-applied (exit 4) and offers exactly two exits:

- **WR5**, an announced window: `./scripts/lakebase_handover_window.sh
  --journal <path>` plans it read-only (`quiesce_window plan-wr5`) and prints
  its sha256; announce it, then re-run with `MIP_ACK_QUIESCE_WINDOW=<sha256>`
  exported for that run. The window library
  (`run_lakebase_handover_window`) journals `window_open` in the handover
  journal, stops the App identity-pinned, applies the remainder in one bounded
  pass (300 s), starts the App, and proves that the start created a new
  deployment that SUCCEEDED from the planned folder (`await-wr5`); its EXIT
  trap starts an App the window left stopped. The digest pins the App's
  identity, its serving deployment, the folder a start snapshots and the
  journal's remainder. Neither Python window mode takes the caller's word for
  the window: `--window-open` and `--in-window` each read the private plan
  (`--window-plan`, written by `plan-wr5`) and refuse unless it passed, is
  intact, is the one `MIP_ACK_QUIESCE_WINDOW` names for this run and pins this
  journal's remainder; `--in-window` also refuses unless the planned App reads
  STOPPED (read-only) and the plan's PostgreSQL preconditions still hold.
  The entry selects the deploy's interpreter (`MIP_DEPLOY_PYTHON`, else
  `.venv/bin/python`, else `python3`) and refuses, before planning, one that
  cannot import the window's modules (psycopg, databricks-sdk). A window that
  opened is never planned again (`plan-wr5` then refuses
  `window_handover_not_exhausted`): after a failed or exhausted window the
  exit is `--revert --journal <path>`.
  WR5 is for an install no signed blue governs yet (the
  handover runs before adoption, which then signs the deployment the start
  created); a signed install refuses (`window_wr5_signed_install`), since its
  re-pin needs the deployment lease. Predicted: 1020 s, #307's conservative
  stop (120 s) and start (600 s) budgets plus the 300 s apply bound; announce
  that figure. The plan's "5 minutes or less" is not met by those budgets and
  is not a gate (a 300 s refusal would refuse every WR5 window: the start
  budget alone is 600 s). Spike S7 measured a trivial App's start (96-108 s
  to compute ACTIVE before its deployment even begins) and stop (about
  40-45 s); the real App installs its whole tree on top, so no budget is
  shrunk. The release
  window (WR1-WR4) never plans WR5, and its digests are unchanged.
  `--remainder --journal <path>` prints, read-only, the exhausted journal's
  remainder count and the digest the window plan pins.
- **Revert**: `--revert --journal <path>`, the exact inverse by the same lock
  bounds. `ALTER OWNER` merges an entry the legacy owner granted the
  deployer (step 3's CREATE, say) into the deployer's owner entry, and the
  inverse leaves the deployer nothing, so the plan journals the GRANT that
  restores each such entry (`revert_grants`) and the revert runs it, as the
  legacy owner, in the same transaction as that object's inverse. Any other
  entry naming the deployer (granted by another role, with a grant option,
  granted by the deployer, of a privilege the legacy owner's own entry lacks,
  which the owner would come back holding, a column grant, or on a column's
  own sequence) could not come back exactly, so the plan refuses it
  (`target_grant_unrevertible`). Apply, revert and a new plan give the
  original plan digest.

`--retire-check [--legacy-identity <name>]` (the default identity is the
caller's) is the read-only gate before the owner removes the legacy role by
hand: it refuses while any job or pipeline still runs as the legacy identity,
judged by its effective run_as (each job read by id; never its creator), or
while one's run_as cannot be read (`python -m tools.clients.handover_verify
retire-check` checks this half alone); and, from the App database, while the
legacy Postgres role still owns an extension, owns a default-ACL row, owns any
other object, holds a privilege, or granted another role a membership, on any
database of the instance (`legacy_role_residue`, counts by category; names go
only to `--report-out`); and when no Postgres role has the identity's exact
name (`legacy_role_not_found`: a typo, or a case variant, which the jobs half
would still match, would otherwise read as no residue). The handover leaves two of those by design (spike
S3): the extensions (`pgcrypto` stays the legacy owner's) and the legacy
owner's own `pg_default_acl` row. Neither affects a deploy; both must be
cleared before the role can be dropped. `--rehearse --rehearse-instance <throwaway> --rehearse-database
<db>` is spike S3's mode (owner-run, refused on the install's own instance):
it creates one schema and table on the throwaway, grants the deployer CREATE
on that schema it owns when the deployer lacks it (and revokes exactly that
grant at the end, also after a failure; a pass needs the revoke to land, and
when a failure's revoke fails too it prints the `REVOKE` to run on the
throwaway), holds a concurrent long transaction on
it past `lock_timeout`, probes it, runs the handover's own lock-bounded change
and its inverse, and passes only if the change needed a retry, the inverse
restored the owner, at least one probe query arrived while the change waited
(`probe_samples_during_change`), and no probe query waited longer than
`lock_timeout` plus a second; a session that dies voids the run. It refuses a
database that already holds `mip_app`, and, like every mode of the tool, a
`LAKEBASE_HOST`, `LAKEBASE_USER`, `LAKEBASE_PASSWORD` or `LAKEBASE_PORT`
override (the operator runbooks export them for migrations; unset them first).
On a local PostgreSQL 16.15 it passed with 12 of 12 probe queries during the
change and a worst wait of 3.0 s. Only the L-A mechanism is rehearsed: L-B
deletes a role.

**Verify without running the stale jobs.**
`python -m tools.clients.handover_verify privileges --principal <legacy owner>`
probes each named principal's effective privileges, or ownership (directly or
through a workspace-visible membership of the owning group), on the
App-triggered lifecycle job's targets; an invisible membership refuses.
`--target KIND:FULL_NAME:PRIV+PRIV` (repeatable; KIND is `CATALOG`, `SCHEMA`
or `TABLE`, and `{catalog}` renders `MIP_DEFAULT_CATALOG`) replaces those
targets. `watch-next-run --job-id <id> [--timeout-seconds S] [--interval-seconds S]`
waits, bounded (default 7200 s, at most 12 hours; polled every 30 s by
default, one request per poll), for the next run that starts on its own and
reports its result; it never starts or cancels a run. Each mode takes
`--report-out <private file>` for the names.

**Owner patch: `.env.example`.** Agents cannot edit the template, so these
are the lines the owner adds under the identifying section:

```bash
# Adopted installs, during the ownership handover only (M2-PR8): one exact
# (full_name, owner, expires_at) pair per pipeline dataset the handover leaves
# with its owner, at most 14 days ahead. Unset it afterwards.
MIP_UC_INTERIM_OWNER_PAIRS_JSON=
```

## Presenter mode (demo workspaces only)

`MIP_PRESENTER_MODE` (default off) shows demo-only affordances to every user
of a deployment: the PROTOTYPE "Preview borrower view" on the Offer page and
the M1-M4 roadmap rail slots, neither of whose modules is downloaded when the
flag is off. It
gates no guard, approval, audit or data path, and it is never an
authorization input. The deploy payload accepts `0`/`false`/`no`/`off`/empty
(off: the variable is omitted) or `1`/`true`/`yes`/`on` (on: the App receives
`MIP_PRESENTER_MODE=1` and the deploy prints a notice); any other value fails
the deploy, and a truthy value is refused for a customer install. At runtime an
unparseable value reads as off instead of failing boot. Admin -> Deployment
readiness and the admin health body (`presenter_mode`) show the current
state.
The deploy preflight parses the same values at step 0, before any bundle, UC
or Lakebase mutation, so an unparseable value, or a truthy one for a customer install,
exits 2 there instead of at App promotion.

`MIP_PREVIEW_MIRROR` is a different flag: it gates the roadmap rows of the
Admin capability panel (`backend/services/capabilities.py`), and it is not
carried by the deploy payload, so deployed Apps run with it off.

## Customer lender identity: a digest-pinned record

The source-controlled registry in `backend/schemas/lender_identity.py` holds
only the Summit Mortgage demo pair. A customer lender onboards without a code
change, as one record and its pin (`backend/schemas/lender_identity_record.py`):

| Name | Class | Value |
| --- | --- | --- |
| `MIP_LENDER_IDENTITY_JSON` | identifying (GitHub Environment secret, or `.env.local`) | `{"version":1,"legal_name":…,"nmls_id":…,"tenant_id":…,"servicer_aliases":[…],"reviewed_by":…,"reviewed_at":…}`, plus an optional `"mark"` (below). |
| `MIP_LENDER_IDENTITY_SHA256` | public (GitHub Environment variable) | The sha256 of the record's canonical JSON: the normalized record with sorted keys and no whitespace. |

The record passes the code registry's own validators (organization-shaped
legal name, 4-12 digit NMLS id, lowercase tenant id, the 160-character SMS
disclosure budget), lists 1-32 distinct servicer aliases (the raw Cotality
servicer strings that name the tenant itself), and is never any part of the
Summit demo identity. Print the record's pin (read-only) with:

```bash
MIP_LENDER_IDENTITY_JSON='<the record>' python -m backend.schemas.lender_identity_record
```

**What the pin is, and is not.** The pin is an exact-digest acknowledgement,
the review mode `single_maintainer_audited` uses in place of the deferred
owner attestation: the deploying maintainer reviews the record, prints its
digest and sets that digest beside it, and every reader recomputes it before
it acts. Any change to the record after that, one character included, is
refused everywhere, the deploy, the App and the Lakebase migration can never
serve two different records, and every verified App boot writes a
`LENDER_IDENTITY_VERIFIED` audit row naming the digest. The pin is not an
independent approval: it travels in the same configuration as the record, so
whoever can set a deploy's configuration can acknowledge a record. Since
M1-PR7 `deploy-dev.yml` carries it as a secret of the client's GitHub
Environment, behind that environment's approval, which in
`single_maintainer_audited` mode is an audited self-approval, not an
independent review.
A trust anchor outside that configuration is the owner attestation, deferred
with the rest of plan M1-PR5.

**Where it is verified.** Every lender call site resolves through
`reviewed_lender_identity_from_env()`:

- `scripts/deploy.sh` preflight, before any workspace mutation; it prints only
  the first 12 characters of the pin, never the record.
- The App payload build (`tools/databricks/app_deploy_payload.py`) exits 2 on
  an unverifiable record, and adds `MIP_LENDER_IDENTITY_JSON` and
  `MIP_LENDER_IDENTITY_SHA256` to the payload only for a verified one. With no
  record, the payload's env-name set is unchanged.
- `tools/databricks/bundle_env.py`, the SQL render, the Lakebase migration
  job (bundle variables `lender_identity_json` / `lender_identity_sha256`, empty
  by default) and the lender-mark tool.
- App startup: `backend/runtime.py` refuses the boot, with one line that names
  no value, before settings load. A verified boot writes exactly one
  `LENDER_IDENTITY_VERIFIED` audit row carrying only the digest. If that write
  fails, the App keeps serving and logs one `lender_identity_audit_write_failed`
  ERROR (outcome `degraded`); the write is not retried, because a retry after
  an ambiguous commit could record the same boot twice.

`MIP_LENDER_NAME`, `MIP_LENDER_NMLS_ID` and `MIP_TENANT_ID` are optional beside
a record and, when set, must equal it.

**Tenant aliases.** The SQL render adds one `is_competitor = FALSE` row per
alias to the lender dictionary seed, display name the record's legal name,
and drops a competitor row whose raw key the record claims. The seed `MERGE`
never deletes, so each demo tenant row (`SUMMIT MTG` and its variants) the
record does not claim is retired instead: it stays in the seed as an
`is_competitor = TRUE` row displayed as `Competitor Other`, and the `MERGE`'s
update turns the demo row an existing `ref.lender_dictionary` holds into a
competitor. Gold then counts none of those loans as the tenant's and names
the record's legal name as the tenant. The App's in-process lender fallback
mirrors the same rows.

**Demo and legacy installs** set neither name and behave exactly as before.

The record and its pin travel like every other deployment control: in the
shell or in `.env.local`, a non-blank shell value winning, at every reader
(the deploy preflight, `bundle_env.py`, the App payload build, settings and
`backend/runtime.py`). A blank value is one Python's `str.strip()` empties: a
whitespace-only shell value is unset, and `.env.local`'s is read instead. (The
preflight's other controls, read through `deployment_control_value`, let any
non-empty shell value win.) A local App reads them from the same `.env` /
`.env.local` files as its settings.

Under `./scripts/deploy.sh -t client` both are fields of the client profile
(`deploy/clients/example.env`), which resolves, from the same sources in the
same order, before any other step: a customer install's profile requires the
record and refuses it without its pin or with a pin that is not its digest,
and a demo install's profile refuses either name. `bundle_env.py -t client`
resolves the profile the same way; `-t ci` validates with the environment's
values alone.

`.env.example` cannot be read or edited from an agent session, so the owner
adds these lines to its "Direct os.environ reads" section and, in the same
change, moves both names from `DIRECT_ENV_READS_PENDING_TEMPLATE` to
`DIRECT_ENV_READS` in `tests/unit/test_settings_contract.py`;
`test_pending_direct_reads_stay_tracked_until_the_template_documents_them`
fails once the template has them and the move is not made:

```bash
# optional customer installs: the lender record (identifying) and the sha256 of its canonical JSON the deploying maintainer acknowledged (public); docs/deployment.md "Customer lender identity"
MIP_LENDER_IDENTITY_JSON=
MIP_LENDER_IDENTITY_SHA256=
```

## Demo seed operator and other required inputs (M1-PR4)

No tracked file names a client value any more:
`tests/unit/test_no_client_identifiers_committed.py` holds the tree at zero
workspace hosts and their bare `dbc-`/`adb-` labels, App URLs, `?o=`
workspace ids, non-example emails (plain, URL-encoded or a URL's userinfo;
`.local` is not an example domain), bare 16-digit ids, 16-hex and 32-hex ids
in either case and UUIDs, outside synthetic values, pinned test digests and
its reviewed `ALLOWED` entries. No file is exempt by name: lockfiles and
manifests are scanned too.
Docs use symbolic placeholders (`<workspace-host>`, `<app-host>`,
`<workspace-id>`, `<genie-space-id>`, `<warehouse-id>`, `<deployment-id>`,
`<operator-email>`). Git history still holds the values the purge removed.

Three values that were committed defaults are now inputs:

- **The demo seed's operator.** `lakebase/seed_campaigns.sql` names its demo
  operator (the `sales_team` admin row, the archived campaigns' owner and the
  sample approvals' actor) by the token `{{mip_seed_operator_email}}`. The
  migrate job renders it from `--seed-operator-email`, which the
  `seed_operator_email` bundle variable carries; `bundle_env.py -t client`
  sets that variable from the profile's demo-only `MIP_SEED_OPERATOR_EMAIL`
  and never from an ambient export, and the deploy's migration plan passes
  the same value (`nonblank_control_value MIP_SEED_OPERATOR_EMAIL`: a shell
  value `str.strip()` empties is unset and `.env.local`'s is read, as the
  profile reads it), so plan and apply digest one text. Unset, the seed names the synthetic
  `operator@summit.example`. An install that seeded an operator before keeps
  its seed, and its schema-input digest, byte for byte by setting the
  address it seeded. A customer profile refuses the field.
- **The App URL of the live checks.** `tools/verify_live.py` takes
  `--base-url https://<app-host>` (or `MIP_APP_URL`) and `scripts/e2e_live.sh`
  takes `MIP_APP_URL`; both refuse without it. `databricks apps get
  <app-name>` prints it as `url`. The verifier's report names the URL, so it
  is written to a new private (0700) `mip-verify-live-*` directory in the
  system temp directory, never a fixed path there, and an `--out` inside the
  checkout that git does not ignore, or cannot answer for, is refused. So is
  the default when `TMPDIR` puts that directory inside the checkout and git
  does not ignore the report there: the path mkdtemp picks is checked again
  before it is opened.
- **The load-test target** recorded in `tools/load_test/baseline.json` reads
  `https://<app-host>/api/v1`. It is a label, never compared:
  `tools/load_test/baseline.py --target` keeps the scheme and path and
  writes the `<app-host>` placeholder for the host, so a regenerated
  baseline never names an App.

The emergency recipe above takes none of these: it ships App source and
never runs the migrate job or a live check, so its commands are unchanged.

`.env.example` cannot be read or edited from an agent session, so the owner
adds this line to its "Direct os.environ reads" section and, in the same
change, moves the name from `DIRECT_ENV_READS_PENDING_TEMPLATE` to
`DIRECT_ENV_READS` in `tests/unit/test_settings_contract.py`:

```bash
# demo installs only: the demo seed's operator (lakebase/seed_campaigns.sql's {{mip_seed_operator_email}}); unset seeds operator@summit.example; a customer profile refuses it; docs/deployment.md "Demo seed operator and other required inputs"
MIP_SEED_OPERATOR_EMAIL=
```

## Lender co-branding (optional)

A deployment may draw its lender's reviewed mark beside the tenant name (the
topbar tenant pill and the Console "Configured tenant" chip) and may set the
theme and accent a user sees before choosing their own. Three optional
`.env.local` controls, read only by `scripts/deploy.sh` preflight and the
frontend build (no App env var, no API or Lakebase change):

| Control | Default | Meaning |
| --- | --- | --- |
| `MIP_LENDER_MARK_FILE` | none | Absolute path to the lender-supplied mark (PNG or WebP). Empty means no mark. |
| `MIP_DEFAULT_THEME` | `dark` | Tenant-default theme for users who have not chosen: `dark`, `light` or `system`. |
| `MIP_DEFAULT_ACCENT` | `bright` | Tenant-default accent for users who have not chosen: `bright`, `teal`, `navy` or `red`. |

Values equal to the defaults emit nothing, so a deployment that sets none of
them renders exactly as before. A user's own Console choice always wins over
a tenant default.

The mark is accepted only when it matches a reviewed entry: for the
code-registry identity, a source-controlled entry in
`backend/schemas/lender_branding.py` bound to the reviewed lender name and
NMLS id in `backend/schemas/lender_identity.py`; for a digest-pinned customer
record, the record's own `"mark"` object (`sha256`, `media_type`, `width`,
`height`, `authorization_ref`, `reviewed_on`), checked by the same rules. In
both cases: same sha256, media type and pixel size, PNG or WebP without metadata, colour profile or
animation, at most 64 KiB, 32-512 px tall and 1-2 times as wide as tall.
Preflight checks it before any workspace mutation and stops the deploy on any
mismatch; the log shows only the first 12 characters of its sha256, never the
path. Step 1 copies the validated file into the ignored
`frontend/.branding-stage/` right before the build, which emits it as
`dist/branding/lender-mark.png` (or `.webp`). The Summit Mortgage sample
lender has no mark.

Keep the mark file outside the checkout (an absolute path) or in an ignored
path: an untracked, non-ignored file inside the repo fails the exact-source
gate before any mutation. Never commit the image.

Reviewer checklist for adding a registry entry:

1. View the lender-supplied file out of band.
2. Confirm it matches the lender's current mark and the cited brand-use
   authorisation.
3. Record only its sha256, media type, dimensions, the `authorization_ref`
   and the `reviewed_on` date beside the (name, NMLS) identity, in the same
   reviewed change as any identity edit. Never commit the image.

Fail-safe: a plain `npm run build` without the preflight exports ships the
product defaults and no mark, and the app also hides a mark that was built
for a different lender than the one the session reports. The mark's hash
(`MIP_LENDER_MARK_SHA256`) is derived by preflight, never set by an operator.

## Browser telemetry (RUM) default

Browser RUM is ON for every `./scripts/deploy.sh` deploy: the deploy payload
sets `MIP_RUM_ENABLED=1` when `.env.local` does not set it
(`tools/databricks/app_deploy_payload.py` `SAFE_RUNTIME_DEFAULTS`). The App
stores only day-grain, identifier-free aggregates in Lakebase
`mip_app.rum_daily` (created by `mip_lakebase_migrate` at deploy step 4b),
and administrators read them in Administration -> Field performance. The
envelope is in
[security-and-compliance.md](security-and-compliance.md#browser-telemetry-rum).

- Opt out with `MIP_RUM_ENABLED=0` in `.env.local` and redeploy; the value is
  forwarded as-is.
- Upgrade note: an existing `.env.local` that already carries
  `MIP_RUM_ENABLED=0` (copied from the old `.env.example`) keeps RUM off, and
  `deploy.sh` never rewrites it. Delete the line, or set it to `1`, to take
  the new default.
- A redeploy through the Databricks Apps UI Deploy button drops the operator
  environment, so RUM is off (the code default) until the next `deploy.sh`
  run. `GET /api/v1/admin/health` reports the effective state as
  `browser_telemetry`.
- A piecewise deploy that runs `mip_lakebase_migrate` by hand must stop
  `mip-app` first: the migrate job fails closed while an App-role session is
  live.

## Follow-ups due and snooze (W5d)

The rail's Follow-ups due item and tray need two Lakebase blocks that
`mip_lakebase_migrate` applies at deploy step 4b
(`scripts/lib/deploy_step_lakebase_and_uc_grants.sh`): the partial index
`idx_approvals_follow_up_open` and the table `mip_app.lead_snoozes` (the App
role gets `SELECT`, `INSERT` and `UPDATE` through the reviewed privilege
matrix). Both are idempotent. Migrate BEFORE the App: the follow-ups CTE
reads `lead_snoozes`, so a piecewise deploy stops `mip-app`, runs the
migrate job, starts the App and then deploys its source. The snooze wake
ships in the bundle job file (`jobs/sync_lifecycle_state.py`, synced by
`deploy.sh`) and in the App source, and runs at deploy step 9 through
`tools.sync_lifecycle_warehouse`. There is no new environment variable,
grant step, job or task.

## Resources

Databricks App resources expected by `app.yaml`:

- `sql_warehouse`: SQL warehouse resource, `CAN_USE`.
- `genie_space`: Genie space resource, `CAN_RUN`.
- `database`: Lakebase database resource, `CAN_CONNECT_AND_CREATE` for binding
  compatibility; `mip_lakebase_migrate` immediately revokes effective
  database `CREATE` and fails unless postflight proves connect-only access plus
  the exact reviewed `mip_app` table/sequence matrix in the internal security
  grant runbook.

## Observed lineage view (optional evidence)

The asset-detail "Observed lineage" section reads
`<catalog>.audit.asset_lineage`, never `system.access.table_lineage`. The
system table holds lineage for every table on a metastore other teams share,
and App SQL reads gold, ref and audit only. So the view
(`sql/ddl/audit_asset_lineage_view.sql`) exposes four columns
(`source_table_full_name`, `target_table_full_name`, `event_time`,
`event_date`) and only edges whose target is in the install catalog and whose
source is in the install catalog or the Cotality share catalog, the two
catalogs the App's asset registry renders. An edge from a product table into
another team's catalog, or from another catalog into this one, would carry a
table name the App never shows, so the view does not expose it. The App's
query keeps its 90-day `event_date` window. Unity Catalog evaluates a view
with its owner's privileges, so the App receives `SELECT` on this one view and
no privilege on the `system` catalog.

Deploy step 4c publishes it after the base UC grants
(`tools/databricks/ensure_asset_lineage_view.py`, run as the deploying
identity, between the `observed lineage view: publish` markers in
`scripts/lib/deploy_step_lakebase_and_uc_grants.sh`):

1. Probe `system.access.table_lineage` with a zero-row `SELECT`.
2. `CREATE OR REPLACE VIEW` from the file rendered for `MIP_DEFAULT_CATALOG`
   (`tools/render_sql.py` rewrites the view name and the two
   `*_catalog = '<catalog>'` literals, and the share catalog literal to
   `MIP_COTALITY_SOURCE_CATALOG` when that is set: "Cotality source share").
3. Check with `DESCRIBE TABLE` that the view exposes exactly those four
   columns.
4. `GRANT SELECT ON TABLE <catalog>.audit.asset_lineage` to the App service
   principal.

Failure semantics follow the convention for optional evidence that the
pending AI Gateway inference-table grants already use:

- **The deploying identity cannot read the system table.** It lacks
  `USE CATALOG` on `system`, `USE SCHEMA` on `system.access` or `SELECT` on
  `system.access.table_lineage`, or the `access` system schema is not
  enabled. The deploy prints a warning that names those privileges and
  continues. It creates, replaces, verifies and grants nothing. A view and
  App grant that an earlier owner left stay as they were and this deploy has
  not verified them; their owner answers for their definition. A new App
  service principal (after an App recreate) is not granted on such a view, so
  lineage stays degraded until a deploy that can read the system table runs.
  Without a view and grant the App keeps its explicit "Observed lineage
  unavailable" gap; it never shows substitute lineage. A dry run cannot
  probe, so its plan prints the grant as conditional on the probe.
- **Any other failure is fatal**, like every UC grant in step 4c: an
  unexplained probe error, a publish or `DESCRIBE` failure after the probe
  passed, a projection other than the four columns, or the grant itself.
  Replacing a view needs its ownership. If an operator published the view by
  hand and a later deploy identity can read the system table, either hand the
  view to that identity
  (`ALTER VIEW <catalog>.audit.asset_lineage OWNER TO ...`) or keep deploying
  with an identity that skips.

The AI Gateway convention is matched only for the missing-access class. The
pending inference-table grants warn on any grant-tool failure; this step
fails an ownership conflict instead, because a deployer that can read the
system table could publish the reviewed view, and another identity's view
is in the way of it.

**Ownership and grants.** The view's owner decides what the App can read
through it, since Unity Catalog evaluates the view with the owner's access.
After a deploy publishes the view, the deploying identity owns it, because
replacing a view requires ownership. A view published by hand belongs to
that operator. Transfer it only to an identity that holds the system-table
privileges; under an owner without them the view stops resolving and the
App shows its gap. `CREATE OR REPLACE VIEW` does not keep the privileges
granted on the replaced view. Every publish therefore drops the App's
`SELECT` and re-grants it in the next statement. A failure between the two
leaves the App ungranted, which shows as the explicit gap, until the next
successful deploy. Do not grant the view to anyone else by hand: the next
publish drops that grant.

Give the system-table privileges only to the identity that should own the
view. A `SELECT` on `system.access.table_lineage` for a CI deploy principal
is a metastore-wide grant, and the metastore owner decides it. The view and
its grant are listed in the internal security grant runbook (§4).

## Optional Salesforce activation

Salesforce delivery is disabled unless the full connector is configured. In
addition to the OAuth values, the customer must create a unique External ID
text field on the configured sObject and set
`SALESFORCE_EXTERNAL_ID_FIELD` (for example `MIP_Activation_Id__c`). MIP uses
the immutable activation UUID as that field's value and performs a Salesforce
External-ID `PATCH` upsert; ordinary non-idempotent `POST` delivery is not
used. This makes exact request retries and recovery after an ambiguous local
commit reuse one remote record.

The dev deploy workflow accepts the non-secret connector values as GitHub
environment variables and the client secret, integration-user password, and
optional security token as GitHub environment secrets. `deploy.sh` writes
those secret values directly to the `mip-runtime` Databricks secret scope and
the App payload uses `value_from` bindings. An unset value never replaces a
key that already exists in the scope; only an absent key gets the disabled
sentinel that the static App binding needs. A kept key is never bound: the
payload binds a secret only when every required connector input is set for
that deploy, and a placeholder secret input counts as unset. Revoking stored
credentials is a deliberate replacement or an owner-approved key delete (see
the `provision_runtime_secrets.py` notes under "Databricks App"). A partial connector
remains honestly staged and cannot claim delivery.

**The three Salesforce bindings are the profile's to switch on** (spike S2b).
`databricks.yml` declares them (`salesforce_client_secret`,
`salesforce_password`, `salesforce_security_token`) beside the App's other
eleven, and stays their one definition. The release applies them only when
the client profile's `MIP_SALESFORCE_BINDINGS` is `on`; unset or `off` (the
default) keeps the eleven, client #1's live set
(`tools/databricks/app_optional_bindings.py`). The binding payload
(`app_resource_bindings build --salesforce-bindings`), the window plan, the
adopt phase's reviewed bindings and the App payload all read that one value.
With the switch off the App payload names no Salesforce secret, even with the
connector's inputs set: a `valueFrom` naming an unbound binding does not fail
a deployment, it starts the App with the env var unset (S2b), so the payload
prints a names-only `Salesforce notice:` instead. The default never turns a
configured connector off: with every connector input set (env or
`.env.local`) and the switch unset, the preflight
(`resolve_salesforce_bindings_switch`) refuses before any mutation, so the
owner says `on` or `off`. Switching it on adds three
bindings: a resources send, so a WR1 window with its migration job. Each key
must exist in the runtime scope first (a missing key refuses the whole
update, S2b); `provision_runtime_secrets.py` writes an absent one as a
disabled sentinel.

**Owner patch: `.env.example`.** Agents cannot edit the template, so this is
the line the owner adds under the public profile fields:

```bash
# Optional (client profile): bind the App's three Salesforce connector secrets
# (salesforce_client_secret, salesforce_password, salesforce_security_token).
# off when unset; on adds the three bindings in a WR1 window. Set it (on or off)
# whenever every SALESFORCE_* connector input is set: unset then refuses
# (docs/deployment.md, "Optional Salesforce activation").
MIP_SALESFORCE_BINDINGS=
```

## Optional OTLP log export

The shipped default is stdout-safe JSON only. To prove durable
off-platform logs, configure the collector endpoint before promoting the
snapshot, plus headers when the collector requires auth:

```bash
MIP_OTEL_ENDPOINT=https://<otlp-collector>/v1/logs
# MIP_OTEL_HEADERS is stored in Databricks Secrets and referenced by
# the Databricks App resource named otel_headers. Do not paste it into
# app.yaml, bundle JSON, screenshots, or shell history.
```

The endpoint must be a customer-owned collector reachable from the
Databricks App container. Without an endpoint, `/api/v1/admin/health` should honestly
report `"log_export": "stdout-only"`. With them configured and the
OpenTelemetry exporter wheels present in `requirements.txt`, it should
report `"log_export": "otlp"`; then verify the collector has a fresh
deployed app `correlation_id`.

Current Entrada dev status (2026-05-15): external OTLP is not configured
on `mip-app`. CLI/API evidence shows active deployment
`<deployment-id>` is `SUCCEEDED`, the app is `RUNNING`,
and only the warehouse, Genie, Lakebase, and lifecycle job resources are
attached; no app secret resource is attached for collector headers.
`/api/v1/admin/health` returns
`log_export: stdout-only`. The Databricks CLI `apps deploy` command in
use here (`0.299.1`) has no top-level `--env` flag, but the underlying
API accepts deployment `env_vars` via `--json`. That list replaces the
app.yaml env list. The historical proof used that API directly; current
operators must use `scripts/deploy.sh`, which attaches the `otel_headers` App
secret resource and emits the full runtime contract plus
`value_from: otel_headers`. Do not paste collector tokens into `app.yaml`, a
bundle var, process environment, or shell history.

App-side proof was completed on 2026-05-14 with temporary deployment
`<deployment-id>`: `/api/v1/admin/health` reported
`log_export: otlp`, a sanitized RUM request returned correlation id
`<correlation-id>`, and the external collector received
the matching OTLP HTTP protobuf payload. The sandbox was then restored to
stdout-only so it is not left exporting logs to the temporary collector.

Secret-backed header proof was completed on 2026-05-14/15 with temporary
deployment `<deployment-id>`: `MIP_OTEL_HEADERS` was
resolved through Databricks App resource `value_from: otel_headers`,
backed by a temporary non-sensitive `mip/otel-headers` secret. The
external collector received request `<request-id>`
with proof header `x-mip-otel-proof=proof-20260515T000246Z` and a
matching `/api/v1/telemetry/rum` log body for correlation id
`<correlation-id>`. The sandbox was restored to
deployment `<deployment-id>` with no OTLP env vars, the
temporary app secret resource removed, and the temporary Databricks
Secret deleted.

For customer production, store the collector headers in a dedicated Databricks
Secret, then set `MIP_OTEL_ENDPOINT`, `MIP_OTEL_HEADERS_SECRET_SCOPE`, and
`MIP_OTEL_HEADERS_SECRET_KEY` for the ordinary governed `prod` deployment.
`scripts/deploy.sh` adds the `otel_headers` resource to the existing App target
and overlays `MIP_OTEL_ENDPOINT` plus `value_from: otel_headers` onto the same
complete signed payload used by every release. It rejects partial settings,
plaintext `MIP_OTEL_HEADERS`, and credential-bearing endpoints before mutation.
Do not add collector values to `app.yaml` or deploy the App separately.

All MIP refresh and automation schedules deploy paused by default, including
`prod`. The app's Admin **Data operations** panel is the
normal customer-facing refresh surface. The Growth Agent monitor scheduler is
also paused by default; when an operator deliberately runs or unpauses it, it
only refreshes saved reviewed watchlists and creates Slack/Teams review drafts.
It does not send messages or activate connectors. Only unpause FRED,
lifecycle, or Growth Agent schedules after the customer approves a recurring
cadence and confirms the target writes to an isolated catalog; otherwise
multiple bundle targets can contend on the same Unity Catalog tables and burn
avoidable compute.

The one exception: the weekly `mip_scheduled_refresh` (FRED → gold →
lifecycle → KPI, Fridays 06:00 America/Chicago) runs when the client profile's
own `MIP_SCHEDULED_REFRESH` is `on` (M1-PR3, `tools/clients/policy.py`), for a
demo or a customer install alike, and its failure email then goes to the
profile's `MIP_SCHEDULED_REFRESH_RECIPIENTS` (required with `on`; an
application id is refused). `off` ships it paused, mailing nobody. The schedule
reaches the workspace only through the bundle apply of a GO governed
`./scripts/deploy.sh -t client` run; the live install is not adopted yet, and
its last governed run was NO-GO on 2026-10-27. See `docs/runbook.md`, "Weekly scheduled
refresh (dev live install)", for the GO conditions, the risks (static-share
decay, the treatment-table DDL race, silent re-pause, the piecewise-render trap,
a shared catalog) and the read-only checks.

The App binds the Growth Agent scheduler job read-only (App resource
`growth_agent_scheduler_job`, permission `CAN_VIEW`, env
`MIP_GROWTH_AGENT_SCHEDULER_JOB_ID`), so Home and Saved monitors say whether
scheduled runs are on. The App only reads the job's schedule state (cached,
served stale while one background refresh runs); it never runs the job, and
the schedule stays `PAUSED` until an operator unpauses it. The signed
rollback contract admits exactly this one `CAN_VIEW` job binding; every other
job binding keeps `CAN_MANAGE_RUN`.

Adding OTLP to an existing App uses the same target that already owns its
Lakebase and warehouse resources. Re-run `scripts/deploy.sh -t client`; its
source-free resource reconciliation preserves the active signed deployment,
adds `otel_headers`, and only then runs the normal migration, cutover, signed
rollback, and smoke gates.

## Release checklist

The app runs on live Unity Catalog + Lakebase in every environment — there is no mock-mode runtime toggle (see [CLAUDE.md](../CLAUDE.md) "Negative prompting"). Flakiness is handled by the resilience layer (retry, warehouse warm-start, SWR cache, circuit breaker, degraded-state banner), never by silent mock fallback.

- The governed Genie space is pinned (`provision_genie_space --plan-pin` resolves it read-only), and no warehouse or Genie id is set by hand: `bundle_env.py` binds `BUNDLE_VAR_genie_space_id` from the pin, and every resource uses the bundle's own SQL warehouse, whose id the deploy reads from bundle state.
- App name, Lakebase instance/catalog/database, Genie title, and runtime/rollback
  secret scopes are unique to the intended workspace; the deployer's
  authenticated host is the client profile's `DATABRICKS_HOST`, and the live
  checks find its workspace id, metastore id and deployer.
- Lakebase schema + `mip_app.action_audit` table exist.
- Genie space is curated against `mip.semantics.*` metric views only.
- `/api/v1/health` returns `status: ok`; `/api/v1/admin/health` reports
  `warehouse: up`, `genie: up`, `lakebase: up`, all circuits `closed`,
  and the current `log_export` posture.
- Resilience is observable: degraded banner renders when a dependency drops; Approve writes a real row to `mip_app.action_audit`.
- Live outreach smoke persists an email draft, then approves that exact draft
  with its generation ID, response hash, source-freshness timestamp, subject,
  body, and canonical borrower evidence IDs.
- `./scripts/deploy.sh --verify-source-only` passes for the exact committed SHA.
- `tools/databricks/bundle_env.py validate -t client` passes.
- The governed deploy's plan step shows the expected direct deployment
  changes; a first isolated-workspace plan has zero changes and zero
  deletions.
- Optional production observability gate: if external log durability is
  required, `MIP_OTEL_ENDPOINT` and any required `MIP_OTEL_HEADERS` are
  configured, `/api/v1/admin/health` reports `log_export: otlp`, and the
  collector has a fresh deployed-app log line. Validate the customer
  evidence packet with
  `python tools/databricks/otlp_customer_retention_gate.py <evidence.json>`
  before claiming durable customer retention. If any customer-owned
  collector, real secret reference, retention/ACL proof, or fresh
  collector query proof input is missing, record the gate as bounded
  stdout-only or transport-only instead of treating local mocked OTLP
  tests as external collector proof.
- Frontend build passes (`npm --prefix frontend run build`).
- The deploy's App release lines are present: `app runtime closure: current`
  in preflight; a stage of at most 1,300 files with every `.br` checked; the
  publish line (`... uploaded, ... already present with equal bytes; listing
  and sha256 verified`); `[static-app-source] release <id> matches its
  manifest` before each activation; and an `[app-release-prune]` report (or
  its warning) at the end.
- `databricks apps get <app>` shows `active_deployment.source_code_path` under
  `<bundle root>/app-src/` and its `deployment_artifacts.source_code_path` as a
  `/src/` snapshot ("Governed per-release App source").
- Python tests pass (`pytest -q`).
- Talk track rehearsed.
- Live evidence is certified, never asserted. A customer release is the
  `scripts/package_source.sh` zip plus `dist/release-readiness.json`
  generated on the release sha (`python tools/release_readiness.py
  --release-sha <sha> --client <client>`) with an empty `cannot_claim`.
  Its six live checks (SQL/Python parity, Lakebase round trip, Genie live,
  Playwright live, the simulated resilience drill and the non-admin proof)
  reach `passed` only from a green `live validation (on demand)` run of that
  client certified by `tools/live_validation_gate.py`: the newest completed
  run on `main` whose code is runtime-equivalent to the release
  (deny-by-default: only docs,
  repo-root `*.md`, tests and workflow files are non-runtime), 14 days old
  or newer, with its three release jobs green. An operator `passed` without
  such a run is recorded `unknown`. The `prod-readiness-gate` workflow
  (`deploy-prod.yml`) runs the same gate as a convenience, for the client
  its dispatch names (`--client`); neither `scripts/deploy.sh` nor
  `scripts/package_source.sh` calls GitHub.

## Client account bootstrap (human-run)

The client's account admin sets up a new client's identities once, by hand,
with `python -m tools.clients.bootstrap_account`. These are the deployer,
jobs, validator, data-owners group, reserved M2M identities and account
principal, each pinned by application id.

The loop is plan, review, ack, apply:

1. `--plan` is read-only. It prints a public-safe canonical plan and its
   sha256.
2. `--apply --ack-plan <sha256>` recomputes the plan and refuses unless the
   digest is identical.
3. A second `--apply` makes zero mutations. `--verify` re-checks read-only.

`--rotate-adopted` and `--post-first-install` follow the same plan and ack
loop. The mutating actions refuse under CI or without a terminal. Every run
logs in fresh in a throwaway config and token cache, and never reads
`~/.databrickscfg`.

The full operator guide, including what the tool never does, is
[docs/clients/bootstrap.md](clients/bootstrap.md). Unless the GitHub
credential sink is selected with `--sink github-environment`, an apply that
would create, adopt or mint refuses before its first mutation.

`python -m tools.clients.github_environment` creates the client's GitHub
Environments, `<client>` and `<client>-validate`, with the review mode's
protections, and fills `<client>` from the local profile: public fields as
variables, identifying fields as secrets, freshly generated Ed25519 signing
keys, and the profile digest `MIP_CLIENT_PROFILE_SHA256`. It uses the same
plan, review, ack and apply loop, in a throwaway `gh` login. The guide is
[docs/clients/github-environment.md](clients/github-environment.md).

The order of a new client's onboarding, from profile to the per-client
nightly, is [docs/clients/onboarding.md](clients/onboarding.md); adopting the
first client's existing install is
[docs/clients/first-client-cutover.md](clients/first-client-cutover.md); and
what the single-maintainer environment approval does and does not prove is
[docs/clients/review-modes.md](clients/review-modes.md).

## Live validation per client

`nightly.yml` (`live validation (on demand)`) validates one client per
manual dispatch (M1-PR8): `gh workflow run nightly.yml --ref main -f
client=<client>`. Its run-name is `nightly client=<client> sha=<sha>`, and
`python -m tools.live_validation_gate --client <client>` accepts only a
green run whose run-name names that client and the run's own head sha: a
run of another client (cross-client) or naming another sha (cross-sha) is
refused, and so is a run from before M1-PR8 (no such run-name).
`deploy-prod.yml` passes its required `client` input as `--client`;
`tools/release_readiness.py` takes `--client` too. Without it any client's
titled run counts, and the gate's verdict and every live check's evidence in
`release-readiness.json` and `.md` say `client: any`; a customer release
always passes the customer's `--client`. It is never scheduled.

**Identities.** No job authenticates as the deployer or holds a workspace
PAT, and every identity a job uses is proven, before any test, not to be in
the workspace `admins` group. That is the only admin power checked:
metastore admin, account admin and MODIFY, MANAGE or ALL PRIVILEGES grants
are kept out by the owner's grants (owner action 3), not by a check here.
nightly writes product rows only through the App API, with the App's
service principal performing every Lakebase transaction, and the
validator, read-only, opens Genie conversations directly (the Genie suites
call the Genie Conversation API as the validator, and Genie runs their SQL
under its grants).

| Job | Environment | ID token | Identity |
|---|---|---|---|
| `check-dispatch` | none | no | none: refuses step debug logging, a malformed client key and any profile name set at repository or organization level, exactly as `deploy-dev.yml` does |
| `check-validate-environment` | none | no | `GITHUB_TOKEN` (`actions: read`): `<client>` and `<client>-validate` must exist with their review mode's protections before any job names them |
| `parity-live`, `kill-drill-simulated`, `genie-fuzz-standard`, `genie-fuzz-deep` | `<client>-validate` | yes | the client's `<prefix>-validator`, through GitHub OIDC with the subject `repo:<org>/<repo>:environment:<client>-validate`; `python -m tools.clients.validation_session session` refuses it in `admins` (`me_not_in_admins`) or as any other identity (`me_is_the_validator`) before any test |
| `playwright-e2e-live` | `<client>` | no | per-run App bearers of the reserved operator identities `normal`, `operator2` and `admin` (the App's admin role, a workspace USER), minted from their M2M secrets; each bearer must be its role's identity and not in `admins` (`<role>_bearer_is_its_identity`, `<role>_not_in_admins`) |

The browser job runs in `<client>` because the credential sink writes the
operator M2M secrets there only (tools/databricks/m2m_oauth_github.py
refuses a `-validate` environment); it requests no ID token, so the
deployer's federation subject `environment:<client>` is unreachable. The
secrets are bound only in the env of the steps that mint
(`tools/clients/validation_profile.collect_env("operators")`), never written
to disk and never in an npm or Playwright step's env. npm runs in this job
only, as `npm ci --ignore-scripts`, and only after the first mint, the App
API suites and their archive. That is not isolation: the remint before the
browser suites and the `always()` reconcile run after npm package code
(`npx playwright install`, the specs) has run in the same job, and that
code could tamper with the checkout they execute; the lockfile limits this
and does not remove it. Each job reads its names one by one
(`tools/clients/validation_profile.py`): `MIP_CLIENT`, `MIP_APP_NAME` and
`MIP_DEFAULT_CATALOG` (variables), `DATABRICKS_HOST` and
`MIP_IDENTITY_IDS_JSON` (secrets), and in the minting steps
`DATABRICKS_CLIENT_SECRET`, `DATABRICKS_OPERATOR2_CLIENT_SECRET` and
`DATABRICKS_ADMIN_CLIENT_SECRET`. The App's URL, SQL warehouse and Genie
space come from the App itself (`apps.get`), never from a stored id.
Because `<client>-validate` is filled by hand, the browser job refuses to
mint unless `<client>` holds the same client, host, App, catalog and
identity map: `parity-live` exports a per-run digest of them
(`validation_profile.profile_digest`, salted with the run id) and every
minting step recomputes it. The validator session refuses any catalog but
`mip`: the live suites read `mip.` literally, so a client whose catalog is
another name cannot be validated until they read `MIP_DEFAULT_CATALOG`. When
`GITHUB_TOKEN` cannot read an environment's protections (spike S8(a)), the
dispatch input `protections_unverified_ack` must hold, for each unreadable
environment, the exact ack the refusal prints (the `protections_unverified`
digest over the client, that environment and the run's sha). A missing or
drifted environment refuses whatever the ack.

**What it writes, and what stays.** Through the App API only:

- campaigns under the per-run marker (`MIP_LIVE_CAMPAIGN_RUN_MARKER`), from
  the App API suites and the browser specs' Genie campaign action, archived
  again at the end (`tools/cleanup_live_campaign_fixtures.py`);
- outreach drafts and approvals of real leads (the two-operator bulk
  approvals, and approvals bound to those campaigns) with their audit rows,
  which stay: an approval is a governed decision record, nothing is ever
  sent, and the audit trail is append-only;
- Genie turns and their audit rows (the browser specs, the scorecard);
- the other App actions the browser specs take, with their audit rows,
  which stay: Genie answer feedback and open-cohort actions; Growth Agent
  workflow runs, a saved watchlist and its Slack/Teams notification drafts
  (drafted, never sent); and the App admin's force-degraded proof.

The App API mutation suites (`App API workflow contracts`) run only when the
App's `/api/v1/admin/health` reports `app_env` `dev` or `sandbox`
(`validation_session install-class`): their own guard refuses any other App,
so a `prod` App skips them and its run can still be green, and an unknown
`app_env` refuses. The skip is evidence, not silence: the gate reads that
step's conclusion and its verdict, like `tools/release_readiness.py`'s
Playwright evidence, says `App API mutation suites: skipped`.

Never written, on any install: lead outcomes, call dispositions, snoozes and
loan-officer assignments. They carry no run marker and no cleanup undoes
them, so the tests that write them are deselected (listed under gaps): the
suite test
`test_live_duplicate_outcome_and_disposition_replay_without_breaker_trip`
(`--deselect`) and four browser tests (the real-UC step's `--grep-invert`).
`tests/unit/test_nightly_workflow_writes.py` scans what nightly runs for
those write paths. It reads every string in `nightly.yml` (inline
`python - <<'PY'` scripts included) and splits each step's `run:` into
commands, in the step's shell and `working-directory` and following `cd`.
From those commands it reads each pytest suite with its imports,
`pytest_plugins` and conftests; each Playwright spec with its relative
imports; the `package.json` scripts an npm command runs; and each repo
script or module a command runs (by path, with `-m`, sourced, or as inline
`-c` or here-document code), with what those import, source or run in turn.
It fails on a write that a running test can reach, that no single test
holds, or that pytest runs by name. It also fails on what a step runs that
it cannot read: a pytest or Playwright command that names no suite or spec
file (a directory, a marker, `testpaths`), a script or import that resolves
to no file, a program outside the repo or named by a variable, inline code
it cannot parse, `make`, another package manager, or a shell other than
bash, sh or python. It does not read a program that builds its own command
line (`xargs`, `find -exec`), an installed tool's config file, or an
environment one step writes to `$GITHUB_ENV` for a later one.

**What left nightly, and its home.** Each runs under the deployer in
`./scripts/deploy.sh`, at deploy time only: between deploys nothing re-runs
it, and nightly checks the deployed App's git sha only
(`validation_session app-sha`).

| Protection | Home (`scripts/lib/`) |
|---|---|
| `tools.verify_deployed_app_contract` (the deployed commit, its Gateway binding and the deployment lease) | `deploy_step_agentic_cutover.sh` (release phase, agentic tier) |
| `tools.databricks.export_gateway_runtime_contract` (the source-bound Gateway runtime contract) | `deploy_step_agentic_cutover.sh` (release phase, agentic tier) |
| `tools.databricks.verify_verifier_identity_boundary` | `deploy_verifier_gateway_lifecycle.sh` (release phase, agentic tier) |
| `tools.databricks.run_agent_eval` (the Growth Agent evaluation) | `deploy_step_agent_eval_and_smoke.sh` (release phase, agentic tier) |
| `reconcile_reviewed_function_execute_grants` (grant reconciliation) | `deploy_governed_phases_lifecycle.sh` (data phase, after every gold-touching job) |

The silver, gold and FRED refresh runs and the bundle validate left before
M1-PR8 (M0-PR3); the data phase runs the refreshes.

**Gaps: no home yet.** These need more than the validator's reads or the
App API, so nightly no longer runs them, and nothing else does yet. Until a
reviewed home exists, the two AI Gateway items converge only at the next
deploy, and the rest are an operator's to run by hand against a non-live
install, never the live one:

- the delayed AI Gateway inference-table grant retry
  (`tools.databricks.grant_ai_gateway_inference_table`): the deploy's
  agentic cutover makes one attempt, and a non-strict deploy that finds the
  table still pending continues without it, so the capability stays
  honestly configured/unavailable until the next deploy (a strict deploy,
  `MIP_REQUIRE_AI_GATEWAY_CLAIMABLE=1`, fails instead);
- the rolling exact-proof refresh (`tools.databricks.verify_ai_gateway_exact_proof
  verify-pending`, then `send --wait`): the deploy proves once, so the
  capability reports configured/unavailable once that proof is older than
  the 26-hour `AI_GATEWAY_PROOF_FRESHNESS_MAX_S`, until the next deploy
  (docs/ai-gateway-exact-proof-workorder.md, §1d);
- `tests/integration/test_lifecycle_delta_replay_live.py` and
  `tests/integration/test_campaign_treatment_at_cap_live.py` create, merge
  into and drop scratch Delta tables in the governed `audit` schema;
- `tests/integration/test_lineage_manifest_live.py` reads
  `system.access.table_lineage`;
- `test_live_saved_holdout_and_household_dedup_exclude_non_treatment_borrowers`
  (in `test_lakebase_idempotency_live.py`, deselected) reads the
  `audit.campaign_treatment_snapshot` table;
- `test_live_duplicate_outcome_and_disposition_replay_without_breaker_trip`
  (in `test_lakebase_idempotency_live.py`, deselected) writes an
  assignment, a funded-loan outcome and a disposition on a real lead;
- four browser tests (deselected by `--grep-invert`) write on a real lead:
  in `movement_closed_loop_regressions.spec.ts`, "closed-loop outcome labels
  reject PII/raw names and summarize only governed aliases" (a
  lost-to-competitor outcome; its three refusals write nothing), "duplicate
  disposition request ids replay without opening the Lakebase breaker" (a
  connected disposition) and "simultaneous outcome idempotency race produces
  one durable outcome without a breaker cascade" (an application-submitted
  outcome); in `real_data.spec.ts`, "sales outcomes: live manual import writes
  Lakebase ledger and Lead Queue explains status" (an application-submitted
  outcome, counted in the Sales ops KPIs);
- on a `prod` App, the whole App API mutation suite (skipped there);
- the real-infra kill drills, `tools/kill_drill/run_drill.sh` with
  `--target warehouse-real` and `--target lakebase-real`, stop and start the
  warehouse and the Lakebase instance (CAN_MANAGE).

**Supply chain.** Every `pip install` in nightly is `--only-binary=:all:`,
so no package runs code at install time, but `requirements-dev.txt` is
version-pinned, not hash-pinned: a validator job (`id-token: write`) imports
what it installs, and that code can mint the validator's token. There is no
hashed dev lock yet; it is a blocker before a non-sandbox client's first
dispatch (`check-validate-environment` already installs the hashed deploy
lock).

**Owner actions before the first dispatch (spike S8(c)).**

1. Create the `<prefix>-validator` service principal with
   `python -m tools.clients.bootstrap_account` (no secret, USER assignment,
   never in `admins` or the data-owners group) and its one federation policy
   with the subject `repo:<org>/<repo>:environment:<client>-validate`.
2. Create `<client>-validate` with the review mode's protections
   (`python -m tools.clients.github_environment --apply` creates both
   environments) and fill it with the five names above: `MIP_CLIENT`,
   `MIP_APP_NAME` and `MIP_DEFAULT_CATALOG` as variables, `DATABRICKS_HOST`
   and `MIP_IDENTITY_IDS_JSON` as secrets, the same values `<client>` holds.
   It holds no M2M secret. The tool fills `<client>` only today, so this
   step is by hand until it fills the validation subset too.
3. Grant the validator read-only access, nothing more: CAN_USE on the App
   and on its SQL warehouse; CAN_RUN on the governed Genie space; USE
   CATALOG, USE SCHEMA and SELECT on the schemas the parity suites read
   (`gold`, the metric views, `silver`, `raw` and `audit`, and the raw
   Cotality source through its owner); EXECUTE on the scoring functions they
   call (`fn_in_the_money`, `fn_lead_score`, `fn_next_best_offer`,
   `fn_rate_spread`, `fn_estimated_upb`, `fn_bounded_mortgage_rate`). Never
   MODIFY, MANAGE, ALL PRIVILEGES or CAN_MANAGE. The plan's "SELECT on gold
   and metric views" undercounts what the parity suites read.
4. The spike: a second environment-subject federation policy works for the
   validator; CAN_USE lets the validator, and the normal operator, read the
   App's URL and resources; and the read grants survive the deploy's
   reconciliations (the reviewed-function EXECUTE verify covers
   `fn_build_cohort`, `fn_segment_counts` and `fn_lead_queue_url` only, and
   the Genie space ACL checks).

A dispatch before these exist refuses in `check-validate-environment` (no
environment) or in the validator session (no validation profile, no
federation policy), before any test.

## Second-client portability proof (CI, offline, M3-PR4)

The ci.yml job `client-portability-proof` runs
`tests/unit/test_client_portability_proof.py` and
`tests/unit/test_client_portability_integrity.py` on every push. It proves that a
second client onboards with zero code edits. The client is `acme`
(`tests/fixtures/clients/acme.env`), a synthetic customer install: prod App
env, agentic tier off, weekly refresh off, and the Cotality share mounted as
`cotality_prod.mortgage` with unprefixed tables.

Its secrets and its lender record's pin exist only inside the sandbox. The
proof runs offline against a stub `databricks` CLI and a stub account API.
Both record the authority each call needs (`tests/fixtures/role_ledger.py`).
It runs, in order:

1. `python -m tools.clients check`;
2. `python -m tools.clients.bootstrap_account --plan`;
3. a deploy-dev.yml `phase: plan` dispatch and an acked `phase: release`
   dispatch, each replayed with `--dry-run` in a sandbox clone with the profile
   removed from the tree;
4. the `bundle_env validate -t client` those dispatches plan.

The proof fails in any of these cases:

- the real checkout changes;
- a bundle call leaves `-t client` or acme's variables and root leaf;
- a canonical Cotality token stays in acme's rendered SQL;
- the pipeline configuration or the App's booted settings are not acme's;
- an agentic step runs (held to a committed list of the agentic-only tools,
  and to a reviewed list of the tools a core run may use), or the refresh is
  not PAUSED;
- a call the dry runs plan or run needs account-admin authority, or is one
  the reviewed role table (`tests/fixtures/role_ledger.py`) does not class:
  a tool (by `-m` or by path, behind any interpreter flag), a CLI command
  group or flag, or a `databricks api` path other than the reviewed SQL
  statements path (any `accounts` path segment is an account call). An
  unclassified call counts as an account-admin need. A tool reviewed as
  workspace-only may not reach an account API through its imports, and inline
  Python the interpreter runs (`-c` or a heredoc) is held to that same rule
  directly: it may not name an account API or import a module that reaches
  one, and everything else it names must be on a reviewed allowlist
  (`tests/fixtures/inline_code_review.py`, read from the inline code the dry
  runs execute). The allowlist lists the modules it may import, outside the
  repository and in it, the attributes it may use of each and the builtins
  it may name, and it refuses a `match` statement and any private, dunder or
  frame attribute, so every module name and builtin the code reaches, by any
  import form or alias, is on that list. It does not model what an object a
  reviewed call returns does through its own public attributes, nor what the
  code writes for a later process to load. Such a write can land outside the
  sandbox: the sandbox clone's `.venv` links to the interpreter prefix that
  runs the tests;
- a bundle call addresses another target than `client`, whether it is
  planned, run through the CLI or run through the `bundle_env` wrapper, or a
  bundle variable databricks.yml declares resolves to a client-#1 default;
- an identifying value reaches the public log or the step summary in any
  letter case;
- a value of the client-#1-shaped install reaches acme's run (a client
  literal);
- the analytics battery stops being allowed with acme's names;
- a one-character tamper of the lender record or the accepted exposure is not
  refused, whatever ack the dispatch carries.

Its client-#1-shaped case (placeholders only) renders SQL byte for byte as
the sql/ sources with only the demo feeds switched on, a reference read from
the sources rather than from the renderer. A dispatch with planted defects
proves that each check can fail.

The proof covers what the `plan` and `release` dry runs plan or run. A tool
that only a live run (`DRY_RUN -eq 0`) or another phase reaches is outside
it. `tests/unit/test_client_portability_integrity.py` therefore holds every
tool a core-tier deploy file names to a list: the reviewed role table, the
human-only bootstrap, or `OUTSIDE_PROOF_MODULES`. It also pins the tools on
that last list that reach an account API through their imports
(`OUTSIDE_PROOF_ACCOUNT_REACH`), so a new one cannot join unnoticed. Their
live authority is not proven here.

The owner attestation layer is deferred (M1-PR5). So the integrity it
exercises is main's: the record's exact-digest pin and the dispatch's exact
digest acks. Nothing signs anything. backend-tests ignores both modules, so
they run once per push. `tests/unit/test_ci_client_portability_job.py` pins
both sides and the proof job's steps whole. It refuses a job or step that is
switched off (`if`, `continue-on-error`, `needs`), a step that writes
`$GITHUB_ENV` or checks out another ref, a pytest command that deselects or
swallows a failure, a trigger filter that skips the workflow, and a
workflow-wide shell or variable that changes what the shell or pytest runs.
It reads the triggers as GitHub does: a push must trigger on `main` itself,
and no negation that could match `main` may follow it. The shell's variables
are matched by exact name. So a `run-name`, an explicit pull_request `types`
list that keeps the three defaults, push `tags`, or an unrelated variable
such as `ENVIRONMENT_LABEL` all pass. The job is advisory until the owner
makes it a required check.

## Jobs identity and pinned job parameters (M2-PR5)

The App holds `CAN_MANAGE_RUN` on four bundle jobs (`mip_sync_lifecycle_state`,
`mip_fred_rates_ingest`, `mip_refresh_silver`, `mip_refresh_scores`), and
`run_now` lets the caller replace a Python task's parameters. So a
compromised App must be able to start only bundle-defined runs, under an
identity that holds no workspace admin.

**Two run_as identities.**

* The non-admin jobs service principal (`MIP_IDENTITY_IDS_JSON.jobs`, the
  `jobs_principal` bundle variable, which has no default) runs those four
  jobs, `mip_scheduled_refresh`, `mip_kpi_snapshot` and
  `mip_feature_pipeline`.
* `mip_kpi_snapshot` moves with them because the weekly refresh starts it.
  Nothing a jobs-identity job starts runs as the deployer, so the non-admin
  identity never needs run permission on an admin-identity job.
* The deployer keeps the deploy-time jobs: `mip_init_catalog_schemas`,
  `mip_ref_seed`, `mip_lakebase_migrate` and the PAUSED
  `mip_growth_agent_monitor_scheduler`.
* The CLI v1.10.0 bundle schema declares `run_as` on `resources.Pipeline`,
  so the pipeline does not need spike S4's fallback (staying on the
  deployer). S4's live half: no ALTER can re-own the pipeline's published
  datasets; they follow its run_as, so they are the jobs identity's for good
  ("Pipeline-managed datasets" above).
* `tests/unit/test_jobs_identity_shape.py` pins the split.

**Workspace ACLs.** The jobs identity holds no workspace admin and only a
USER assignment, so its runs can reach a workspace object only by grant.
Deploy step 4, right after the jobs-reader Lakebase role and before the first
job runs as it, runs `tools/databricks/jobs_identity_acl.py apply`:

* It derives the grants from databricks.yml:
  * CAN_READ on the bundle files folder (`workspace.file_path`). Every task's
    Python and SQL file, `jobs/install_pins.json` and the pipeline's library
    sit under the deployer's own home folder.
  * CAN_USE on the warehouse the SQL tasks name.
  * CAN_RUN on `mip_feature_pipeline`, which `mip_refresh_silver` starts.
  * CAN_MANAGE_RUN on the four jobs `mip_scheduled_refresh` starts. Each of
    them must run as the jobs identity itself.
  * CAN_USE on the Lakebase instance a `--lakebase-instance` parameter names.
* Any other resource reference or task kind in a jobs-identity job refuses, so
  a new dependency cannot ship without its grant.
* It resolves each object from the bundle summary, and refuses unless the
  summary's jobs-identity jobs run as the profile's jobs principal.
* Before any grant it reads the **live** `run_as_user_name` of every
  jobs-identity job and pipeline by id (`jobs.get`, `pipelines.get`; a
  pipeline's spec never carries run_as) and refuses, exit 4 and nothing
  granted, unless each runs as the jobs principal: `live_run_as_drift` for one
  that runs as anyone else, `live_run_as_unreadable` for a blank answer or a
  missing object. The summary is configuration, and spike S4 measured that
  the direct-engine plan cannot see a pipeline's run_as (`input_only`): a
  drifted pipeline planned `skip` and a deploy left it drifted. A job's drift
  the plan does see and the apply repairs. A pipeline's needs a reviewed
  change by the owner, then a re-run.
* It adds only what the identity lacks, by the permissions API's PATCH, then
  re-reads every object. It never removes or lowers a grant, so the App's
  `CAN_MANAGE_RUN` bindings, the verifier's warehouse `CAN_USE` and other
  teams' grants stay. Bundle `permissions:` blocks would set each object's
  whole ACL on every deploy and drop those, which is why this is a deploy
  step. For the same reason no tool sets (PUT) a job's or pipeline's ACL:
  spike S4 measured that `jobs set-permissions` replaces the whole ACL
  (setting IS_OWNER dropped another principal's `CAN_MANAGE_RUN`).
  `tests/unit/test_job_pipeline_acl_writes.py` refuses any such call in the
  tree.
* `plan` reads only. Output names bundle keys, never object ids or
  principals. `tests/unit/test_jobs_identity_acl.py` pins all of this.

**Pinned parameters.**

* Before every bundle call, `tools/databricks/bundle_env.py` renders
  `jobs/install_pins.json` from the profile: the catalog, the Lakebase
  instance and database, and the data-owner group
  (`<MIP_IDENTITY_PREFIX>-data-owners`). These are public profile values.
  The file is gitignored, and `sync.include` uploads it into the
  deployer-owned bundle root, where the App cannot write.
* `jobs/_install_pins.py` reads the file from its own folder. Each Python
  entrypoint a jobs-identity job runs (`jobs/fred_rates_ingest.py`,
  `jobs/sync_lifecycle_state_job.py`, `jobs/converge_governed_owner.py` and
  `jobs/kpi_snapshot.py`) calls it first. A run exits 5 before any Spark session, Lakebase credential
  or connection when:
  * a pinned option (`--catalog`, `--lakebase-instance`, `--lakebase-database`,
    or the FRED `--table`) differs from the pin;
  * an option the bundle does not pass is set (`--seed-path`,
    `--prune-legacy-defaults`);
  * the pins file is missing or malformed.
* The lifecycle task runs `jobs/sync_lifecycle_state_job.py`. That file loads
  the pins and `jobs/sync_lifecycle_state.py` beside it by exact path.
  `jobs/sync_lifecycle_state.py` itself is unchanged, because the App imports
  it.
* The SQL and pipeline tasks take no parameters a `run_now` can redirect. The
  rendered SQL declares no parameter markers, and `pipeline_params` carries
  only `full_refresh`.

**Owner convergence.**

* `mip_refresh_silver`, `mip_refresh_scores` and `mip_fred_rates_ingest` run
  `converge_governed_owner` after every refresh task, on `run_if: ALL_DONE`,
  so a failed run still hands over what it created.
* A run is judged by its leaf tasks. With the ALL_DONE handover as the only
  leaf, a run whose refresh task failed would end `SUCCESS_WITH_FAILURES`,
  and CLI v1.10.0 `bundle run` exits 0 for that state, so deploy.sh would
  carry on past a failed refresh. Each of the three jobs therefore ends in
  `assert_refresh_succeeded`: a compute-free condition task on
  `run_if: ALL_SUCCESS` after the handover and every refresh leaf. Any failed
  task leaves it `UPSTREAM_FAILED` and the run `FAILED`.
  `tests/unit/test_job_run_outcome.py` fails each task of every bundle job in
  turn and requires a `FAILED` run every time.
* It hands every table, view and function that the run's own identity owns
  in the pinned catalog's governed schemas to the pinned group.
* It never touches:
  * an object someone else owns (the deployer's reviewed Growth Agent
    functions included);
  * another catalog, the catalog itself or a schema;
  * a pipeline dataset.

**Lakebase `jobs_reader`.**

* `mip_lakebase_migrate` gets `--jobs-reader-client-id=${var.jobs_principal}`
  and grants that role USAGE on `mip_app`, SELECT on `approvals` and
  `call_dispositions` (the lifecycle sync's reads), and SELECT, INSERT and
  UPDATE on `kpi_snapshots` (the KPI snapshot's daily upsert, which the
  deployer, the database owner, made before). The postflight refuses any
  other privilege.
* Deploy step 4 first converges the role LOGIN-only
  (`converge_lakebase_oauth_role --role-contract jobs_reader`).
* With the option unset, the grant reconciliation is byte-identical to before
  (`tests/unit/test_lakebase_jobs_reader.py`).
* It grants no other write, so the lifecycle sync's fail-soft snooze wake
  (an UPDATE of `lead_snoozes`) is skipped under it.

**Monitor scheduler held PAUSED.** Until a non-approver scheduler role exists,
`tools/databricks/monitor_scheduler_gate.py` refuses an enabled
`mip_growth_agent_monitor_scheduler` (a schedule, trigger or continuous block
that is not PAUSED) in three places:

* in databricks.yml or a target override (`bundle_env`, every call);
* in the bundle plan's `new_state`;
* live: by name, a dev-prefixed copy included, or by its bound id. This runs
  beside the plan gate at both positions.

**Live effect.** None until a governed release deploys this bundle. The live
jobs keep their deployed run_as and sources until then.

## CI deploy (deploy-dev.yml): one client environment per dispatch, and the release-gate bootstrap

Decision D-platform-process-b step 7 (audit quality-03, report 12.4 #8). The
release gate above needs one green `live validation (on demand)` run on
`main`; its 120-day lookback from the last green run (2026-06-16) ended on
2026-10-14, so the gate reads `no_green_run` until a CI install is green
again.

**Status: one client environment per manual dispatch (M1-PR7).** The CI
staging install this section bootstrapped lived in its own workspace, which
was deleted on 2026-10-29. `deploy-dev.yml` now deploys one client per
dispatch: `client` names the client's GitHub Environment, which holds the
whole client profile (public fields as variables, identifying and secret
fields as secrets; tools/clients/schema.py), and `phase` is the
`scripts/deploy.sh --phase` the run executes. Nothing client-specific lives in
the workflow, and only a manual `workflow_dispatch` reaches it. `nightly.yml`
validates one client per dispatch under a non-admin validator identity
(M1-PR8, "Live validation per client"). M1-PR2 replaced the bundle's `dev` and `prod` targets with the one
host-less `client` target, rooted at the client profile's deployer and client
key.

**The jobs.**

- `refuse-unacknowledged` and `check-dispatch` run first. Without
  `acknowledge_single_maintainer_break_glass=true` the run ends in the first.
  The second refuses step debug logging (it traces every expression with its
  operands, a variable's value included), a client key that is not
  `MIP_CLIENT`-shaped or names an environment that is not a client's (`dev`,
  `copilot`, `github-pages`), and a phase outside the five, before any job
  names an environment: GitHub creates an environment, unprotected, the first
  time a job references a name it does not know. It also refuses while any
  profile field or alias name is set as a repository- or organization-level
  variable or secret, and prints those names (never a value): it runs in no
  environment, so it sees only those scopes, and GitHub merges them into
  every client environment's contexts, silently filling a field the
  environment omits (tools/clients/workflow_contexts.py).
- `build` has no ID token, no environment and no secret. It runs `npm ci` and
  the vite build, stages the App runtime closure
  (`app_runtime_closure stage --require-brotli-check`), downloads a wheelhouse
  from `requirements-deploy.hashes.txt` with `pip download --require-hashes
  --only-binary=:all:`, seals all three under `SHA256SUMS.json`
  (tools/databricks/prebuilt_release.py) and uploads them as the
  `prebuilt-release` artifact, one day's retention. The manifest's sha256 is
  the job's output.
- `deploy` runs in the client's environment (`environment: ${{ inputs.client
  }}`), in a non-cancelling lane per client (`mip-client-<client>`), with
  `id-token: write` and `actions: read`. It sets up no Node and runs no npm.
  Its timeout is the phase's: plan 30, release 330, data 330, adopt and
  unadopt 60 minutes, and its first step turns that into
  `MIP_JOB_DEADLINE_EPOCH`; the next refuses step debug logging. It re-hashes
  the artifact against the build job's
  manifest digest, and checks its staged App source against its own checkout
  of the commit, before it installs anything, installs with
  `pip install --no-index --find-links <wheelhouse> --require-hashes`, runs the
  collect step, the resolve-client-profile action, the OIDC proof and the
  dispatch preflight,
  then `./scripts/deploy.sh -t client --phase <phase> --no-confirm
  --prebuilt-release <dir>` with `MIP_REDACT_FOREIGN_NAMES=1`, records the
  quiesce window journal and deletes the profile files. Under this workflow's
  ref `scripts/deploy.sh` refuses any run that names no `--phase` or no
  `--prebuilt-release`, before it prints anything.

**The collect step** is the one step that reads `vars` or `secrets`. The
runner prints a step's `with:` and `env:` values in its log header before the
step runs, and only secrets are masked there: a variable prints in clear, and
the run logs are public. So no step holds `toJSON(vars)` or
`toJSON(secrets)` (that would print every repository-, organization- and
environment-level variable and carry every repository secret into the job).
The collect step binds each public profile name from `vars` by value, each
profile name from `secrets` by value (the runner masks each), and each
identifying or secret name only by whether `vars` holds it, so a misfiled one
prints its name and refuses; no other variable or secret reaches the job. It
writes the two objects to `$RUNNER_TEMP`, mode 0600 and create-only, and
prints counts only. Its env is generated from tools/clients/schema.py (`python
-m tools.clients.workflow_contexts print`), and a test holds the workflow
equal to it and parses every other expression in it.

**The resolve-client-profile action** (`.github/actions/resolve-client-profile`)
takes the collect step's two files. Its first step registers
every identifying leaf, and each spelling the deploy derives from one (lower
case, the host origin), as a runner mask (`python -m tools.clients mask`),
before anything else prints. Then `tools/clients/ci_profile.py` writes
`.env.local` (the whole profile, as an operator's: `scripts/deploy.sh`
un-exports every credential, so each child that resolves the profile reads the
secret fields there) and the github-oidc `DEFAULT` profile, both create-only
and mode 0600, and outputs public values only. Identifying fields come from
secrets only: the resolver refuses an identifying or secret field found in a
variable, and a public one found in a secret.

**The dispatch preflight** (`tools/clients/dispatch_preflight.py`) prints codes,
counts, digests, field names and tracked paths only, and appends them to the
step summary. The owner hardware-key attestation layer (plan M1-PR5) is
deferred, so every attestation is an exact digest ack over a purpose-named
body (`tools/clients/dispatch_acks.py`); an ack for one purpose never
satisfies another:

- the workflow is `deploy-dev.yml` on `main`, dispatched by hand;
- `single_maintainer_audited` needs the break-glass acknowledgement; approval
  provenance is "not applicable (single_maintainer_audited)", and the
  `independent` mode is refused by the profile schema until M1-PR5. The
  preflight also refuses `approval_provenance` for every mode but
  `single_maintainer_audited` on its own, and the workflow's
  `refuse-unacknowledged` job needs the break-glass for every dispatch, so an
  independent mode needs that gate changed as well;
- the trust-root chain is reported "deferred (M1-PR5)": nothing verifies a
  hardware-key signature or a chain;
- `owner_attestation` must be the exact `profile` ack (purpose, client and
  profile digest) for every mutating phase; a plan prints it;
- `operation_acks` carries `purpose=<sha256>` pairs from a closed map
  (`tools/clients/dispatch_acks.OPERATION_ACKS`), each admitted only in the
  phases whose run reads it and exported for this job under the name its own
  tool recomputes it from: `adopt_plan` (adopt, unadopt), `adopt_live` and
  `supersede` (adopt), `unadopt` (unadopt), `quiesce_window` (plan, release;
  a plan reads it at its pre-lease window plan), `database_instance_update`
  (plan, release), `genie_pin` and `accepted_exposure_plan` (plan, release,
  adopt, unadopt; never data, which never pins and skips the probes),
  `data_ledger_supersede` and `data_ledger_rerun` (data), and the one
  selector, `supersedes_deployment` (adopt): the sha256 of the adopted
  record's deployment id that picks adopt-live's supersede mode, exported as
  `MIP_ADOPT_LIVE_SUPERSEDE`. A selector is a digest, never a switch (`yes`,
  `true` or `1` fail the entry shape). Every value is 64 lowercase hex, each
  purpose at most once, and a refusal names purposes and phases, never a
  value. The carrier pairs no entries: half an adoption (the bind's
  `adopt_plan` without adopt-live's own ack, which the read-only adopt run
  printed beside it) refuses one layer later, in `run_adopt_live_plan`,
  still before the lease, and a selector that names no adopted record
  refuses in adopt-live (`supersede_mismatch`);
- the environment's protection rules are read with the job's token and must
  be the review mode's (the maintainer as the one required reviewer,
  `can_admins_bypass=false`, a branch policy of exactly `{main}`). Spike S8 is
  owed: if the token cannot read them, a plan records
  `environment_protections_unreadable`, and every mutating phase refuses
  unless `protections_unverified_ack` is the exact ack over the client, the
  environment and the run's head commit, which the refusal prints;
- no tracked file holds a resolved identifying value (the tree scan names the
  field and the file, never the value). A cloud's default account-console
  host is no needle. Only the closed set of group names the repository itself
  defines is reported and never refused, each in its own field:
  `<prefix>-admin` as `MIP_ADMIN_GROUP_NAME`, `<prefix>-approver` as
  `MIP_APPROVER_GROUP_NAME` and `<prefix>-data-owners` in
  `MIP_UC_APPROVED_OWNER_PRINCIPALS`, with `<prefix>` the public
  `MIP_IDENTITY_PREFIX`. Any other value, `<prefix>-` spellings and every
  email, host, id or share included, is refused by a mutating phase;
- `expected_active_deployment_sha256`, when given, reaches `--phase release`
  as the one-run export `MIP_EXPECTED_ACTIVE_DEPLOYMENT_SHA256` (never a
  `.env.local` line; a local release may export it the same way),
  which refuses before the lease unless the App's active deployment still has
  that digest (a plan prints it).

A plan records drift, an unreadable rule set or a tree-scan hit; a mutating
phase refuses them. **The PlanReport** goes out in two halves: the plan
phase's step summary carries `PlanReport.public_summary()` only, and the full
report goes create-only to the deployer's own
`<root>/plan-reports/<run id>-<attempt>.json` in the client workspace. No
artifact carries either.

**What a client environment holds** (names only; every value lives in the
environment). Variables, the public fields: `MIP_CLIENT`,
`MIP_PROFILE_VERSION`, `MIP_CLOUD`, `MIP_INSTALL_CLASS`, `MIP_APP_ENV`,
`MIP_AGENTIC_TIER`, `MIP_SCHEDULED_REFRESH`, `MIP_REVIEW_MODE`,
`MIP_DEFAULT_CATALOG`, `MIP_LAKEBASE_SYNC_CATALOG`, `MIP_LAKEBASE_SYNC_SCHEMA`,
`MIP_APP_NAME`, `MIP_LAKEBASE_INSTANCE`, `LAKEBASE_DATABASE`,
`MIP_RUNTIME_SECRET_SCOPE`, `MIP_APP_ROLLBACK_SECRET_SCOPE`,
`MIP_AGENT_PROXY_SECRET_SCOPE`, `MIP_PII_SALT_SCOPE`, `MIP_GENIE_SPACE_NAME`,
`MIP_AGENT_SUPERVISOR_NAME`, `MIP_AI_GATEWAY_TABLE_PREFIX` and
`MIP_AI_GATEWAY_AGENT_EXPERIMENT_BASE`, plus, when they apply,
`MIP_IDENTITY_PREFIX`, `MIP_ENABLE_DEMO_FIRST_PARTY_FEEDS`,
`MIP_PRESENTER_MODE`, `MIP_LAKEBASE_READABLE_SECONDARIES`,
`MIP_SALESFORCE_BINDINGS`, `MIP_COTALITY_SOURCE_CATALOG`, `MIP_COTALITY_SOURCE_SCHEMA`,
`MIP_COTALITY_SOURCE_TABLE_PREFIX` and `MIP_LENDER_IDENTITY_SHA256`. Secrets,
the identifying fields: `DATABRICKS_HOST`, `MIP_EXPECT_WORKSPACE_ID`,
`MIP_EXPECT_METASTORE_ID`, `DATABRICKS_ACCOUNT_ID`, `MIP_IDENTITY_IDS_JSON`,
`MIP_UC_METASTORE_OWNER` and `MIP_UC_APPROVED_OWNER_PRINCIPALS`, plus, when
they apply, `DATABRICKS_ACCOUNT_HOST`, `MIP_GENIE_MANAGER_PRINCIPALS`,
`MIP_SCHEDULED_REFRESH_RECIPIENTS`, `MIP_ADMIN_EMAILS`, `MIP_APPROVER_EMAILS`,
`MIP_AUDITOR_EMAILS`, `MIP_ADMIN_IDENTITIES`, `MIP_APPROVER_IDENTITIES`,
`MIP_AUDITOR_IDENTITIES`, `MIP_ADMIN_GROUP_NAME`, `MIP_APPROVER_GROUP_NAME`,
`MIP_COTALITY_SHARE_PROVIDER`, `MIP_COTALITY_SHARE_NAME`,
`MIP_UC_CATALOG_STORAGE_ROOT`, `MIP_DASHBOARD_PARENT_PATH`,
`MIP_LEGACY_BUNDLE_ROOT`, `MIP_LENDER_IDENTITY_JSON`,
`MIP_ACCEPTED_EXPOSURE_JSON`, `MIP_UC_INTERIM_OWNER_PAIRS_JSON` and
`MIP_SEED_OPERATOR_EMAIL`. Secrets, the secret fields:
`DATABRICKS_CLIENT_SECRET`, `DATABRICKS_OPERATOR2_CLIENT_SECRET`,
`DATABRICKS_ADMIN_CLIENT_SECRET`, `DATABRICKS_RELEASE_PROBE_CLIENT_SECRET`,
`DATABRICKS_ACCOUNT_CLIENT_SECRET` and `MIP_AI_GATEWAY_PROOF_SIGNING_KEY`,
plus, in the agentic tier, `DATABRICKS_VERIFIER_CLIENT_SECRET`,
`DATABRICKS_AGENT_RUNTIME_CLIENT_SECRET`,
`DATABRICKS_AGENT_PROXY_CREDENTIAL_BUNDLE` and
`MIP_GATEWAY_MODEL_ATTESTATION_SIGNING_KEY`. No repository- or
organization-level secret or variable may carry any of these names (owner
step 3). The per-client workflow carries no name outside the profile: the
Genie action key ids, the refusal-capture switch, the proof-key rotation
inputs, the foreign-catalog binding policy, the Salesforce connector settings
and a first install's bootstrap warehouse are no profile field, so a client
that needs one needs it in the schema first.

**The live install.** Until it is adopted, a dispatch of a mutating phase for
the live install's environment ends in the client live checks, before the
lease, with `adoption: existing install not adopted`, and `phase: plan` there
reports the same refusal; nothing runs without a manual dispatch.

Retired with that workspace (M0-PR3):

- its bundle target and that target's own workspace-host anchor;
- the CI isolation rule table, its `scripts/deploy.sh` preflight block and
  its `tools/databricks/bundle_env.py` hooks, with the bundle-root leaf and
  display-name suffix variables it resolved (the dev root is
  `~/.bundle/<bundle>/dev` again, and the three workspace-unique names are
  literals, exactly as the defaults rendered);
- the install-scope probes, which ran only behind a non-empty suffix;
- `deploy-dev.yml`'s normal-credential repair step, its input and its
  secrets-write token, and its foreign-catalog remediation input: the
  remediation has no CI command of record, and since M4-PR2 deleted its
  tooling the workflow no longer names its flag;
- `nightly.yml`'s personal-access-token profile, its bundle validate and its
  three refresh-job runs. They were removed rather than re-aimed: the
  remaining `dev` target is the live install, whose deployed jobs CI must
  never run (docs/runbook.md, "Interim refresh (governed deploy NO-GO)").

**Owner follow-up: `.env.example`.** One line of the operator `.env.local`
template still describes the retired CI staging install. Agents cannot read
that template under this repository's permission settings, so the owner
deletes the line and, in the same change, drops the template's
`ALLOWED_FILES` entry from the retired-install ratchet and this paragraph.
Until then the ratchet caps the template at that one line;
`test_every_allowlist_entry_is_still_needed_and_tracked` fails once the line
is gone and the entry is not.

The metastore ownership transfer and the staging-catalog ownership handover
that two former owner steps described were cancelled; neither is part of any
step below, and no step revokes another team's grant.

**The live install is outside this bootstrap, and its governed deploy is
NO-GO.** Nothing in this section deploys, recovers or restarts the live
`mip-app`. Its install predates the client target: until it is adopted
(M2-PR6) it ships only through the emergency recipe ("The emergency recipe,
command by command"), and a governed `./scripts/deploy.sh -t client` cannot
reach it. A read-only readiness check on 2026-10-26 also found that the
governed deploy cannot deploy the live App by any supported path today:

- The live workspace holds none of the seven reserved principals, and
  `scripts/deploy.sh` never creates one: it always passes
  `--expected-application-id`, and `provision_m2m_oauth` then refuses a
  missing principal.
- The only path that mints a usable durable credential cannot serve the
  live install from this repository (owner step 1).
- The live App has no signed rollback record, so a first governed run stops
  it at step 0a and leaves it stopped if any later gate fails; on 2026-10-26
  two gates would have failed after that point.

Deploying or recovering the live App is therefore an owner-only decision.
Never start `./scripts/deploy.sh -t client` against the live workspace during
an incident; escalate to the owner (docs/runbook.md, section 1.2).

**The CI deployer: GitHub OIDC, no stored credential.** `deploy-dev.yml`
deploys as the client's dedicated account service principal (its profile's
`MIP_IDENTITY_IDS_JSON.deployer`), never a person and never one of the
reserved app-facing identities. It has no OAuth secret and no personal access
token: it authenticates only through GitHub Actions OIDC workload identity
federation. Its federation policy trusts issuer
`https://token.actions.githubusercontent.com` and subject
`repo:<owner>/<repo>:environment:<client>`, the default GitHub OIDC subject of
a job bound to the client's environment, whose required reviewer and branch
policy gate every run; never widen it to a branch, a ref or
`repo:<owner>/<repo>:*`. The owner creates and federates that service
principal outside this repository until M2-PR1's client bootstrap does it.

- The `deploy` job alone holds `id-token: write` (the workflow default stays
  `contents: read`, and the `build` job holds nothing more). The
  resolve-client-profile action writes the `DEFAULT` profile as `host`,
  `auth_type = github-oidc` and `client_id` from the profile; the profile's
  `MIP_IDENTITY_IDS_JSON` refuses a deployer id equal to any other role, the
  account-SCIM client included. The SDK and the CLI exchange the job's GitHub
  ID token for a short-lived Databricks token as that service principal;
  nothing is stored.
- "Prove the GitHub OIDC deployer identity" runs next, read-only, before the
  dispatch preflight and `scripts/deploy.sh` (tools/clients/oidc_proof.py).
  Its profile and host checks run first and stop the run before any network
  call. The profile must hold only those three keys, with no ambient
  `DATABRICKS_HOST`, `_TOKEN`, `_AUTH_TYPE`, `_CLIENT_ID`, `_CLIENT_SECRET` or
  `_CONFIG_FILE` override, its host must equal the profile's
  `DATABRICKS_HOST`, and the bundle's `client` target must commit no host.
  The step then proves, through the Python SDK and the Go CLI, that the
  profile authenticates as the deployer, that this principal is in `admins`,
  and that the workspace is `MIP_EXPECT_WORKSPACE_ID` and its current
  metastore `MIP_EXPECT_METASTORE_ID`. The metastore-owner check, which
  presumed the cancelled metastore transfer, and the warehouse check (the
  bundle state binds the warehouse since M1-PR3) are gone. The step prints
  booleans only (a failed call adds its exception class or the CLI's exit
  code, never a message or a value) and exits 2 on any mismatch.
- Under `github-oidc` an environment `DATABRICKS_CLIENT_ID` overrides the
  profile's `client_id`, so no deployer step carries one: every client id is
  in `.env.local`, and `scripts/deploy.sh` un-exports every App client pair
  before its first child process.
- The run step sets `MIP_TEMP_CREDENTIAL_PLANE=workspace`. The deploy's
  300-second identity-probe secrets are then minted, listed and deleted
  through the deployer's workspace `service_principal_secrets_proxy`. The
  2026-08-04 `invalid_client` for `mip-agent-runtime-ci-sp` came from an
  account-plane M2M `service_principal_secrets.create()` that reported
  success from the runner, but the secret never appeared in the inventory;
  the workspace plane is the one durable rotations from a runner have
  persisted on. This is expected, not yet proven. Unset, the plane stays
  `account`, so operator runs are unchanged.
- At run time the deployer's SCIM `userName` is its application id. That id
  is the bundle's current user and `run_as`, the deployment-lease holder,
  the release folder's principal and the reviewed functions' owner. The
  account-SCIM authority is unchanged: it is still its own
  `DATABRICKS_ACCOUNT_CLIENT_ID`/`_SECRET` pair, the profile's
  `account_principal` role and its secret.

**Workspace-global steps.** Bundle roots, names and variables do not isolate
identities. These steps act on a whole workspace or the whole metastore, so a
second install that shared one of these identities with the live install
would cut the live App off, or fail closed:

| Step | Scope | What a shared identity does |
|---|---|---|
| `warehouse_acl.converge_exact_can_use` (verifier `CAN_USE`) | removes the identity's direct access on every other warehouse | a verifier shared with the live install loses the live warehouse grant |
| `agent_proxy_access` converge (Supervisor and Genie ACLs, then obsolete-endpoint revokes) | deactivates the proxy's membership on every non-reviewed Supervisor agent, converges its ACL on every Genie space, revokes it on every endpoint outside the reviewed set | a proxy shared with the live install loses the live Supervisor and endpoint access |
| `agent_proxy_access --mode deny-all` (the failure compensation) | revokes the proxy on every endpoint, Supervisor and Genie space | revokes a shared proxy everywhere, the live install included |
| `m2m_access_policy.revoke_can_query_on_obsolete_endpoint` (verifier) | endpoints outside the reviewed set | endpoints carry no install-scoped name, so a shared verifier loses the live Gateway |
| `reconcile_historical_agent_endpoints cleanup` (step 10b, "attest and retire every unrelated historical runtime endpoint") | every serving endpoint and Supervisor agent the agent-runtime identity created | deletes each one outside the preserved pins, with its query groups, but only after attesting the whole inventory. An endpoint whose signed Gateway contract names another catalog or Genie space fails that attestation (`signed identity or scope drifted`), so a runtime shared with the live install fails closed here before any delete |
| `audit_global_m2m_access --genie-space-id` (`agent_runtime_access.audit_global_genie_access`, the runtime's re-audits) | every Genie space in the workspace, read-only | fails closed unless the agent runtime holds `CAN_RUN` on exactly this install's space and nothing else; a runtime shared with the live install holds the live space |
| `assert_lakebase_role_scope`, `assert_no_warehouse_access`, the other global M2M audits | the whole workspace, read-only | fail closed if a shared identity holds access elsewhere |
| `audit_agent_runtime_foreign_uc_access` (agentic tier only: step 4 before the Lakebase bootstrap, step 10b before runtime-owned UC mutations) | every catalog in the metastore except this install's, read-only: the owner's estate inventory plus the runtime's own view ("Estate inventory") | fails closed on any agent-runtime privilege there (a runtime shared with the live install holds the live catalog's grants), on any foreign catalog granting to `account users` the profile does not accept (owner step 7), and on any foreign ownership |

So each install needs its own seven identities (owner step 1), and a second
install in the live install's metastore still meets the metastore-wide UC
audit: every `OPEN` catalog, the live install's included, must grant the
runtime nothing, and every catalog `ISOLATED` away from the install's
workspace must be in the owner's estate inventory, with no runtime, proxy,
group or `account users` ownership below it (owner step 7; "Estate
inventory").

**The App release folder on a CI install.** The release lives at
`/Workspace/Users/<deployer-application-id>/.bundle/mortgage-intelligence-platform/<target>/app-src/<release id>`
("Governed per-release App source"). `deploy-dev.yml`'s `build` job stages it
with Node 22, which the build and the `.br` check use, and the `deploy` job
takes it as a prebuilt release ("A prebuilt release (M1-PR7)"); a shallow
checkout is enough for `git archive`. Publish, converge and prune each validate the
principal, the target and the root leaf. The stage root goes away with the
runner, and the first run is a first install, so prune has nothing to delete.
First-run checklist for the owner-triggered run:

- the publish line counts at most 1,300 files, with the upload and verify
  wall time at the default 4 workers and the number of `[app-release]` rate
  limit retry lines (none is the goal; a retry is not a failure);
- every activation SUCCEEDED and no `[BUILD]` log shows an npm step; the
  first activation's `[BUILD]` log shows a pip install (a first install has
  no previous `requirements.txt`), later activations of the same release are
  expected to print `Requirements have not changed. Skipping installation.`
  ("App Python dependencies"), and the boot `dependency_lock_check` is ok
  after each;
- `active_deployment.source_code_path` ends in `/<target>/app-src/<release id>`
  and `deployment_artifacts.source_code_path` is a `/src/` snapshot;
- `verify-deployment` matches the run's manifest. The runner's stage root is
  gone after the run, so re-stage the same commit from a fresh build with the
  runner's Node major and use that manifest only if its release id equals the
  one in the run's publish line (equal ids mean equal bytes; a local rebuild
  with one Node version reproduced the id on 2026-10-28);
- step 5 printed the bundle files root, and the converge before each later
  activation passed (the Apps deployment wrote nothing into the release
  folder);
- the signed capture passes and the prune report is present and says what it
  kept (and deleted), not `skipped: the deployment lease is not proven held`
  (prune runs under plain `run`, not the proof-signing authority);
- `default_source_code_path` is recorded before and after the resource update
  and after activation;
- a second run of the same commit uploads nothing.

**Owner steps (never agent work).** They apply to the CI install M1-PR7
brings back. Each is a credential, identity, GitHub-settings or
audited-consent action against that install's workspace, the Databricks
account or this repository. The Unity Catalog checks in step 7 read
metastore-wide state that other teams share. Read-only checks of the current
GitHub state (names and timestamps only):

```bash
R=skyler-myers-db/mortgage-intelligence-platform
export GH_TOKEN="$(gh auth token --user skyler-myers-db)"
gh api "repos/$R/environments/dev" --jq '{name, updated_at, rules: [.protection_rules[].type]}'
gh api "repos/$R/environments/dev/deployment-branch-policies" --jq '.branch_policies[] | {name, type}'
gh api "repos/$R/environments/dev/secrets" --jq '.secrets[] | {name, updated_at}'
gh api "repos/$R/environments/dev/variables" --jq '.variables[] | {name, updated_at}'
gh api "repos/$R/actions/secrets" --jq '.secrets[] | {name, updated_at}'
gh api "repos/$R/actions/variables" --jq '.variables[] | {name, updated_at}'
gh api "repos/$R/actions/workflows/deploy-dev.yml/runs?per_page=5" --jq '.workflow_runs[] | {id, head_branch, conclusion, updated_at}'
```

1. **The reserved-identity contract.** Each M2M role is bound to one
   reserved, workspace-wide service-principal display name: the seven names
   in "Per-run M2M identities" above. The code enforces it:
   - `tools/databricks/m2m_identity_contract.py` `IDENTITY_DEFAULTS` binds
     each role to its name (`--sp-name may not select another identity`);
   - `provision_m2m_oauth._find_existing_sp` resolves the role's principal
     by that name and refuses a duplicate, and the caller then refuses a
     principal whose application id differs from the configured client id;
   - `agent_runtime_access.AGENT_RUNTIME_DISPLAY_NAME` pins the runtime's
     name.

   `scripts/deploy.sh` reconciles all seven roles by name on every deploy,
   the live install's included. So in one workspace a CI staging install
   runs as that workspace's seven identities, and CI-only identities cannot
   coexist with them there: a CI principal under another name is never
   resolved, and the deploy refuses the reserved-name principal it finds on
   the application-id mismatch.

   **Never create a second service principal with a reserved display
   name.** Every `scripts/deploy.sh` in that workspace, the live install's
   included, would then exit at identity reconciliation (`Multiple service
   principals use reserved display name`) and fail closed through its EXIT
   trap.

   Resolution: one workspace per install, each with its own seven reserved
   principals. The CI target that implemented it was retired with its
   workspace; M1-PR7's client environments carry the same rule. Owner action,
   in the CI install's workspace: make each of the seven reserved principals
   exist exactly once and its GitHub client id/secret pair current. Create
   only a missing role, with the `--pre-app-bootstrap` commands in "Per-run
   M2M identities" authenticated to that workspace (creation-only: it refuses
   an existing reserved principal), and the account-SCIM client
   (`DATABRICKS_ACCOUNT_*`) if absent.

   **Owner invariant: each workspace keeps its own reserved principals.**
   Service principals are account-level objects, so separate workspaces
   separate identities only while this holds; no code checks it. Never assign
   one install's seven reserved principals to another install's workspace,
   and never configure an install (`.env.local`, an operator shell) with
   another install's client ids.

   The live install has no seven of its own: on 2026-10-27 the live workspace
   held none of the seven names, only the App's own `app-<id> mip-app`
   principal. None can be created for it under this design.
   `scripts/deploy.sh` never creates a reserved principal: it always passes
   `--expected-application-id`, and `provision_m2m_oauth` then refuses a
   missing one. (The temporary Lakebase bootstrap principals a deploy
   creates and deletes are not reserved identities.) Outside the deploy, a
   `provision_m2m_oauth` run without `--expected-application-id` can create
   a reserved principal, but with `--no-mint-secret` that principal has no
   secret. The only path that mints a usable durable credential, for a new
   principal or a rotation, is `--set-gh-secrets` with a reviewed `--gh-repo`;
   the governed creation run is
   `provision_m2m_oauth --pre-app-bootstrap --set-gh-secrets`. (A deploy's
   identity probes, `campaign_treatment_identity_probe` and
   `identity_boundary_probes`, mint 300-second secrets for existing
   principals and delete them after the probe; none is durable.) The
   `--set-gh-secrets` sink sends each role's client id and one-shot secret
   only to one GitHub Environment's secrets (`gh secret set --env`, never
   repository-level ones; `--gh-env` names the environment) of the reviewed
   `origin`, or of the repository `MIP_M2M_GITHUB_REPOSITORY` names. The secret
   names are fixed per role (`IDENTITY_DEFAULTS`). Before M2-PR2 they were
   repository-level, and in this repository they belong to the CI install:
   the parked `nightly.yml` read them until M1-PR8 (it now reads the three
   operator secrets from the client's environment only); `deploy-dev.yml`
   reads none, and its
   `check-dispatch` refuses every dispatch while one is set (owner step 3). A
   live identity bootstrap therefore needs its own reviewed sink: another
   repository named by `MIP_M2M_GITHUB_REPOSITORY`, plus a reviewed way to
   hand the values to the operator, who cannot read GitHub secrets back. It
   stays out of scope until that design is reviewed. Never run the bootstrap
   for the live workspace against this repository: it would overwrite the CI
   install's secrets.

   Check the invariant read-only before each dispatch. Run
   `databricks service-principals list --filter 'displayName sw "mip-"'` once
   per workspace credential. No `applicationId` in one install's listing may
   equal the `applicationId` of any of the seven reserved principals in the
   other's. Compare the listings, never a stored `*_CLIENT_ID`: GitHub
   secrets cannot be read back.
2. Set the CI environment's install names to that install's own values,
   never the live install's: `MIP_APP_NAME`, `MIP_DEFAULT_CATALOG`,
   `MIP_LAKEBASE_INSTANCE`, `MIP_LAKEBASE_SYNC_CATALOG`,
   `MIP_RUNTIME_SECRET_SCOPE`, `MIP_APP_ROLLBACK_SECRET_SCOPE` (derived from
   the App name, as the preflight requires), `MIP_GENIE_SPACE_NAME`,
   `MIP_AI_GATEWAY_TABLE_PREFIX` and `MIP_AI_GATEWAY_AGENT_EXPERIMENT_BASE`;
   leave `MIP_AI_GATEWAY_AGENT_MODEL_FAMILY` unset or name a family in that
   install's catalog. `MIP_AGENT_PROXY_SECRET_SCOPE` may stay unset: the
   preflight derives it as `<MIP_APP_NAME>-agent-proxy` and refuses any other
   value. An install in the live install's metastore shares it, so neither
   catalog name may name a live catalog (`mip`, `mip_app_state`). Nothing
   enforces that since the CI rule table was retired; M1-PR7's client
   environment carries the names.
3. Delete every repository- and organization-level secret or variable that
   carries a client profile field or alias name, the identity aliases
   (`DATABRICKS_CLIENT_ID` and the other `*_CLIENT_ID` names) included:
   GitHub merges those scopes into the job's contexts with no sign of where
   an entry came from, so a field the client's environment omits would be
   filled from them
   (tools/clients/environment.py, "Scope precondition"). `check-dispatch`
   refuses every dispatch until they are gone and names each one it finds;
   `nightly.yml`'s `check-dispatch` refuses them the same way (M1-PR8). Delete the retired `dev` environment's variables too: no dispatch
   may name it, and its identifying values must not stay variables. The old
   environment-level `DATABRICKS_CLIENT_ID` (2026-07-20) and the
   repository-level pairs (2026-07-26) are such entries: the client ids now
   live in the environment secret `MIP_IDENTITY_IDS_JSON`, and the collect
   step binds no identity alias, so an alias in a client environment is
   ignored (delete it too). Re-mint the account-SCIM credential yourself. Create
   a new OAuth secret for the service principal `DATABRICKS_ACCOUNT_CLIENT_ID`
   names and pipe it straight into `gh secret set --env <client>
   DATABRICKS_ACCOUNT_CLIENT_SECRET` (the client's environment, never the
   repository); agents never handle the value. Then
   retire its older secrets unless something local still uses them. The
   deployer never takes this role: the account plane stays on
   `DATABRICKS_ACCOUNT_*`.
4. The SQL warehouse is the one the install's bundle state binds (M1-PR3).
   A first install's bootstrap warehouse (`DATABRICKS_WAREHOUSE_ID`) is no
   client profile field, so `deploy-dev.yml` carries none and a first install
   through CI refuses at the preflight until that field exists (a follow-up).
   (`GENIE_SPACE_ID` is no deploy input since M1-PR3; since M1-PR8
   `nightly.yml` reads the App's Genie space from the App itself.)
5. Set no repository-level profile name. Every profile name lives in the
   client's environments only (`gh variable set --env <client>` for a public
   field, `gh secret set --env <client>` for an identifying or secret one, as
   in step 3). Since M1-PR8 `nightly.yml` reads the client's `<client>` and
   `<client>-validate` environments only ("Live validation per client"), and
   its `check-dispatch` refuses a repository- or organization-level profile
   name exactly as `deploy-dev.yml`'s does. The names the parked workflow
   read that are no profile names (such as `DATABRICKS_WAREHOUSE_ID`,
   `GENIE_SPACE_ID`, `MIP_APP_URL` and `MIP_AI_GATEWAY_AGENT_MODEL_FAMILY`)
   are read by nothing now; delete them. The repository-level
   `DATABRICKS_TOKEN` is dead with the deleted CI workspace; do not mint
   another one. After the first green `deploy-dev.yml` run, set the
   repository variable `MIP_REVIEWED_FUNCTION_OWNER` (no profile name) to
   `<deployer-application-id>`, the reviewed functions' new owner (the
   deploy's agentic tier reads it; `nightly.yml` no longer does).
6. The AI Gateway proof-key registry (the proof-key rotation in "Per-run M2M
   identities"). The registry (`app_deployment_lease_support.key_registry`)
   is `MIP_AI_GATEWAY_PROOF_HISTORICAL_VERIFY_KEYS` (comma-separated, oldest
   first), then `MIP_AI_GATEWAY_PROOF_PREVIOUS_VERIFY_KEY`, then the current
   key derived from `MIP_AI_GATEWAY_PROOF_SIGNING_KEY`. It must end at the
   current key, and each signed lease record's `key_epoch` must be its
   signing key's index, so a retired key can never become current again.
   `deploy-dev.yml` carries only the signing key, a secret of the client's
   environment (`gh secret set --env <client>
   MIP_AI_GATEWAY_PROOF_SIGNING_KEY`, never repository level). Neither the
   historical keys nor the previous key is a client profile field, so it
   carries neither: its registry is the current key alone, and a lease record
   signed by a retired key, or under a longer registry (`key_epoch` above 0),
   fails closed until a reviewed profile field carries the retired public
   keys (a follow-up). Never reuse a retired private key; a rotation
   generates a new Ed25519 signing key, and you do it yourself.
7. Clear the step-4 and step-10b blockers. First confirm the metastore:
   `databricks metastores current` on a credential for the CI install's
   workspace returns that workspace's `workspace_id` and the `metastore_id`
   it runs on; compare it with the same read on a live-workspace credential
   to know whether the two installs share a metastore. If they do, the UC
   audit reads the live install's catalogs too.
   - **Unity Catalog grants, metastore-wide.** The step-4 audit fails on any
     privilege the runtime holds on an `OPEN` catalog, BROWSE included and
     inherited through `account users` included. Never revoke another team's
     grant and never isolate or re-bind another team's catalog: such a
     catalog stays a blocker for this install until a reviewed exposure
     decision covers it. Find them and their owners at run time with this
     read-only inventory on the CI install's credential; every row it prints
     outside `samples` and `system` is a blocker:

     ```bash
     databricks catalogs list -o json \
       | jq -r '.[] | select(.isolation_mode == "OPEN") | [.name, .owner] | @tsv' \
       | while IFS=$'\t' read -r catalog owner; do
           databricks grants get catalog "$catalog" -o json \
             | jq -r --arg c "$catalog" --arg o "$owner" \
                 '.privilege_assignments[]? | select(.principal == "account users")
                  | [$c, $o, (.privileges | join(","))] | @tsv'
         done
     ```
   - **Estate inventory (an owner action, no UC change).** The audit
     accepts a catalog `ISOLATED` away from the CI install's workspace only
     as the owner's estate inventory records it (owner, catalog type,
     isolation mode, bound workspace ids, binding types, and who owns what
     below it), taken read-only as the metastore owner at most 30 days
     before the run ("Estate inventory"). The retired
     `MIP_UC_FOREIGN_CATALOG_BINDING_POLICY` refuses when set.
   - **Workspace-scoped, in the CI install's workspace only:** Genie spaces
     that grant a group the CI identities belong to, such as workspace
     `users` (the runtime, verifier and App Genie audits fail on any group
     grant).

   None of this is remediated by the deploy. The foreign-catalog binding
   remediation, which set catalogs ISOLATED and rewrote their workspace
   bindings metastore-wide (between its isolate and re-bind calls, or after a
   killed run, every other workspace lost those catalogs, the live install's
   share catalogs included), is deleted (M4-PR2), and `scripts/deploy.sh`
   refuses its flag.
8. Dispatch `deploy-dev.yml` on `main` with `client=<client>`, `phase=plan`
   and `acknowledge_single_maintainer_break_glass=true` (or approve that exact
   dispatch in chat), then approve the pending deployment and read the step
   summary: the refusals it collected, the profile ack and the active
   deployment digest. Then dispatch `phase=release` with
   `owner_attestation=<that profile ack>` (and any operation ack a tool
   printed), and `phase=data` after it. If an App with `MIP_APP_NAME` already
   exists in that workspace with no signed rollback record, set
   `rebase_unverified_app=true` on that release only; without it the run fails
   closed after the treatment quiesce and leaves that App stopped.

**Integrator steps.**

1. Freeze merges and pause piecewise deploys.
2. Read-only render: a plain `python -m tools.databricks.bundle_env validate
   -t client` with the client profile shows the client root and today's
   resource names (`tests/unit/test_client_install_dry_run.py` pins both for a
   client-#1-shaped profile).
3. Dispatch `deploy-prod.yml` on `main` with `client=<client>` and record
   the expected FAIL (`no_green_run`).

Steps 4-6 run only once a client environment exists and owner steps 1-8
hold for it, never against the live install.

4. After the owner's dispatch and approval (owner step 8), watch
   `deploy-dev.yml` to green.
5. Dispatch `nightly.yml` with `client=<client>` on the same sha and watch
   it. It needs the validator's owner actions first ("Live validation per
   client"); until then `check-validate-environment` or the validator session
   refuses, before any test.
6. Repair stale `frontend/tests/e2e/real_data.spec.ts` assertions in a PR if
   it is red, then repeat 4-5; record the green run URL in the audit
   report; re-dispatch `deploy-prod.yml` with the same `client` and expect
   PASS.
