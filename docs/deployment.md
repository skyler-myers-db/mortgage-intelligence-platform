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
(emergency recovery only — the command of record is `./scripts/deploy.sh`):

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
where `<leaf>` is the target (or the CI install's `dev-ci`). `app-src/` is a
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
deployment, the folder the App's `default_source_code_path` names,
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

## Emergency recovery: a slim per-release App source folder

**Emergency recovery only. The command of record is `./scripts/deploy.sh -t
dev`, which now stages, publishes and promotes a per-release folder itself
(previous section).** A hand-made slim folder is for the case that path cannot
run at all. A deploy from it bypasses three governed steps, and `stage`
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
2 on any refusal. It makes no Databricks call. Stage with a manifest and
read the governed release id from it:

```bash
python -m tools.databricks.app_runtime_closure stage --ref <sha> \
  --dist frontend/dist --out <new empty directory> \
  --manifest-out <new file beside it> --require-brotli-check
python -m tools.databricks.app_release_contract release-id \
  --manifest <that file> --expected-commit <sha>
```

Upload the folder under that governed name,
`<bundle root>/app-src/<release id>` ("Governed per-release App source"), with
`python -m tools.databricks.app_release_source publish` and the flags
`scripts/deploy.sh` step 5 passes (create-only, byte-verified), never with
`workspace import-dir` and never into a folder that is already used: a folder
under the governed name whose bytes differ makes the governed publish of that
commit refuse at step 5. A publish that stops part way, for example on a rate
limit (HTTP 429) that outlasts the retry budget, leaves a partial folder:
run the same command again, which byte-compares what is already there and
uploads only what is missing. The piecewise payload deploy then names that
folder as the deployment's `source_code_path`.

A later governed run of the same commit and dist reuses a folder published
this way, and governed prune retires it once no retained deployment
references it. Legacy `app-src/<short sha>` folders are not release ids, so
prune never touches them: they stay owner cleanup.

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
   Actions variables). The exact lender-name/NMLS pair must already exist in
   the source-controlled reviewed identity registry in
   `backend/schemas/lender_identity.py`; customer onboarding adds that pair in
   an independently reviewed PR before these deployment variables are set:

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
2. For a customer fork, rebind the bundle's single workspace-host anchor
   before any deploy:

```bash
./scripts/configure-workspace.sh https://<customer-workspace>.cloud.databricks.com
```

   The `databricks.yml` host anchor and `DATABRICKS_HOST` must identify that
   same workspace. `deploy-dev.yml` and `nightly.yml` are Entrada CI only:
   both address the `pr105-host` target, whose `&ci_staging_host` anchor
   names Entrada's CI staging workspace and is never rebound by
   `scripts/configure-workspace.sh`, so a fork's `deploy-dev.yml` run fails
   closed at its host check. A fork deploys with `./scripts/deploy.sh -t dev`.
   Before the first deployment into an isolated workspace, run:

```bash
python tools/databricks/bundle_env.py validate -t dev
python tools/databricks/bundle_env.py plan -t dev
```

   The first plan must report only additions (`0 to change`, `0 to delete`). A
   change or deletion means the local bundle state or resource namespace still
   points at another installation; stop before deployment and reconcile it.
   `MIP_LAKEBASE_INSTANCE`/`LAKEBASE_INSTANCE_NAME` and
   `LAKEBASE_DATABASE`/`MIP_LAKEBASE_DATABASE_NAME` are compatibility alias
   pairs and must match when both names are present.

3. Run the deployment script. It builds the frontend, provisions Genie if
   needed, validates/plans/deploys the direct bundle through the env-aware wrapper,
   publishes the staged per-release App source folder and promotes it to the
   running Databricks App ("Governed per-release App source"), and runs the
   refresh/smoke steps.

```bash
./scripts/deploy.sh -t dev
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
./scripts/deploy.sh -t dev
```

Keep the venv outside the checkout. The exact-source gate fails on any
untracked path that `.gitignore` does not cover: the default `.venv/` is
ignored and passes, but a new name such as `.venv-deploy/` would fail it.
Never re-sync a venv that other sessions share to make a deploy pass.

### Per-run M2M identities

Live app validation and agent ownership use seven service principals and store only their OAuth
client credentials. No bearer token is a GitHub secret or `.env.local` value:

One-shot OAuth secrets are written only to the repository detected from the
current `origin`. Customer automation without a local git remote must set
`MIP_M2M_GITHUB_REPOSITORY=owner/repository`; an explicit `--gh-repo` must
match that reviewed sink. Grant-only reconciliation with `--no-mint-secret`
does not require this binding and can run from a customer fork unchanged.

| Role | Default service principal | GitHub Actions secrets |
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
client ID and one-shot client secret to the repository bound to `origin`.
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
  --gh-repo skyler-myers-db/mortgage-intelligence-platform
python tools/databricks/provision_m2m_oauth.py \
  --pre-app-bootstrap --identity-role normal --set-gh-secrets \
  --gh-repo skyler-myers-db/mortgage-intelligence-platform
python tools/databricks/provision_m2m_oauth.py \
  --pre-app-bootstrap --identity-role operator2 --set-gh-secrets \
  --gh-repo skyler-myers-db/mortgage-intelligence-platform
python tools/databricks/provision_m2m_oauth.py \
  --pre-app-bootstrap --identity-role admin --create-group \
  --set-gh-secrets --gh-repo skyler-myers-db/mortgage-intelligence-platform
python tools/databricks/provision_m2m_oauth.py \
  --pre-app-bootstrap --identity-role release_probe \
  --set-gh-secrets --gh-repo skyler-myers-db/mortgage-intelligence-platform
python tools/databricks/provision_m2m_oauth.py \
  --pre-app-bootstrap --identity-role verifier \
  --set-gh-secrets --gh-repo skyler-myers-db/mortgage-intelligence-platform
python tools/databricks/provision_m2m_oauth.py \
  --pre-app-bootstrap --identity-role agent_proxy \
  --set-gh-secrets --gh-repo skyler-myers-db/mortgage-intelligence-platform
```

Pre-App bootstrap is creation-only and refuses every existing reserved
principal. If a failed bootstrap was fully compensated, prove that the
credentialless principal is removed before repeating the bootstrap command; do
not add `--rotate` to a bootstrap retry. After the App exists, a reviewed
rotation uses normal mode with that role's canonical
`--expected-application-id`, `--rotate`, and `--set-gh-secrets`.
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
`./scripts/deploy.sh -t dev` for an operator install, or dispatch
`deploy-dev.yml` for the CI staging install, which deploys only the
`pr105-host` target (see "CI staging install" below). Bundle apply first
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
`MIP_AI_GATEWAY_PROOF_HISTORICAL_VERIFY_KEYS`; private keys are still destroyed
at retirement. Each successor records the public-key epoch and rejects epoch
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
`mip-app-pr105-staging` → `mip-app-rollback-pr105-staging`). It contains an
App/deployer ownership marker, and is re-audited before every record read,
write, or legacy-key deletion. Its direct ACL must be exactly the original
deployer plus `admins` at `MANAGE`, and its only permitted non-marker keys are
that App's v5/v6 rollback records. An unmarked pre-existing scope, foreign ACL,
or unrelated key blocks deployment without adoption or cleanup.
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
./scripts/deploy.sh -t dev
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
./scripts/deploy.sh -t dev
```

Retirement is idempotent and does not require the current or Cotality secret
values. It replaces `genie-action-previous` with a fresh disabled sentinel,
which immediately invalidates old tokens while keeping the static Databricks
App resource valid. An ordinary deploy no longer does this: it keeps an
existing previous key, which stays unbound while
`MIP_GENIE_ACTION_SECRET_PREVIOUS_KID` is unset. An id left set after
retirement binds the sentinel, which verifies nothing.

`make bundle-validate` and `make bundle-plan` remain read-only diagnostics.
The mutable `make bundle-deploy` and `make bundle-deploy-dev` aliases are
retired and fail closed. The internal bundle wrapper accepts mutation only
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

### Pre-stop probes (read-only, before step 0a)

Step 0a stops the existing App, and on the unsigned rebase path also
quarantines its access. Without a signed blue record any later failure leaves
the App stopped. Right after the per-run M2M credential checks, and before the
signed lease or any other workspace mutation, `./scripts/deploy.sh` runs
`tools/databricks/deploy_prestop_probes.py` as the workspace-admin deployer. It
performs only GET/list calls and lists every refusal in one pass (exit 2,
nothing changed, the App not stopped by this run) for these failures, which
would otherwise surface only after step 0a. A read the probe cannot complete
(an unreadable Genie ACL, an App read error other than "not found", a SCIM or
Supervisor listing error, a non-admin deployer) exits non-zero with that error
instead; either way the deploy stops before the signed lease. The `deploy-dev`
workflow runs the same probe and never sets `MIP_RETIRE_HUMAN_SUPERVISOR`, so
expect a CI deploy to refuse here, before step 0a, on any workspace whose Genie
spaces grant `users` (directly or through `/Workspace/Shared`) or that holds a
non-runtime canonical Supervisor. Those runs used to refuse at the same audits
after the stop.

- **Supervisor retirement.** If a Supervisor holds the canonical name
  (`MIP_AGENT_SUPERVISOR_NAME`, default `Mortgage Growth Agent`) and its
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

## Presenter mode (demo workspaces only)

`MIP_PRESENTER_MODE` (default off) shows demo-only affordances to every user
of a deployment: the PROTOTYPE "Preview borrower view" on the Offer page and
the M1-M4 roadmap rail slots, neither of whose modules is downloaded when the
flag is off. It
gates no guard, approval, audit or data path, and it is never an
authorization input. The deploy payload accepts `0`/`false`/`no`/`off`/empty
(off: the variable is omitted) or `1`/`true`/`yes`/`on` (on: the App receives
`MIP_PRESENTER_MODE=1` and the deploy prints a notice); any other value fails
the deploy, and a truthy value is refused for `-t prod`. At runtime an
unparseable value reads as off instead of failing boot. Admin -> Deployment
readiness and the admin health body (`presenter_mode`) show the current
state.
The deploy preflight parses the same values at step 0, before any bundle, UC
or Lakebase mutation, so an unparseable value, or a truthy one with `-t prod`,
exits 2 there instead of at App promotion.

`MIP_PREVIEW_MIRROR` is a different flag: it gates the roadmap rows of the
Admin capability panel (`backend/services/capabilities.py`), and it is not
carried by the deploy payload, so deployed Apps run with it off.

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

The mark is accepted only when it matches a source-controlled entry in
`backend/schemas/lender_branding.py` that is bound to the reviewed lender
name and NMLS id in `backend/schemas/lender_identity.py`: same sha256, media
type and pixel size, PNG or WebP without metadata, colour profile or
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
   `*_catalog = '<catalog>'` literals; the share catalog literal is fixed).
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
`01f14ff2c4cd10b99ebad8f8785c307f` is `SUCCEEDED`, the app is `RUNNING`,
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
`01f14fe0164a1b6388f9d240679492db`: `/api/v1/admin/health` reported
`log_export: otlp`, a sanitized RUM request returned correlation id
`b07b2f0825a043188fda96e041a14d19`, and the external collector received
the matching OTLP HTTP protobuf payload. The sandbox was then restored to
stdout-only so it is not left exporting logs to the temporary collector.

Secret-backed header proof was completed on 2026-05-14/15 with temporary
deployment `01f14ff1cb5513a9824b1e040701141d`: `MIP_OTEL_HEADERS` was
resolved through Databricks App resource `value_from: otel_headers`,
backed by a temporary non-sensitive `mip/otel-headers` secret. The
external collector received request `d64260ce-232a-498e-bb9a-4bf65306c090`
with proof header `x-mip-otel-proof=proof-20260515T000246Z` and a
matching `/api/v1/telemetry/rum` log body for correlation id
`c864cd1bbbf44779874fdb235ae7c6bf`. The sandbox was restored to
deployment `01f14ff2c4cd10b99ebad8f8785c307f` with no OTLP env vars, the
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

The one exception: the dev target's live install runs the weekly
`mip_scheduled_refresh` (FRED → gold → lifecycle → KPI, Fridays 06:00
America/Chicago). `MIP_SCHEDULED_REFRESH=off` pauses it. The CI staging install
and `prod` always ship it paused, and `-t prod` and `-t pr105-host` refuse
`MIP_SCHEDULED_REFRESH=on`. Its failure email goes to the deploying user on the
live install only. The schedule reaches the workspace only through the bundle
apply of a GO governed `./scripts/deploy.sh -t dev` run, and that run was NO-GO
on the dev workspace on 2026-10-27. See `docs/runbook.md`, "Weekly scheduled
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
Lakebase and warehouse resources. Re-run `scripts/deploy.sh -t prod`; its
source-free resource reconciliation preserves the active signed deployment,
adds `otel_headers`, and only then runs the normal migration, cutover, signed
rollback, and smoke gates.

## Release checklist

The app runs on live Unity Catalog + Lakebase in every environment — there is no mock-mode runtime toggle (see [CLAUDE.md](../CLAUDE.md) "Negative prompting"). Flakiness is handled by the resilience layer (retry, warehouse warm-start, SWR cache, circuit breaker, degraded-state banner), never by silent mock fallback.

- Warehouse ID + Genie space ID are set (`BUNDLE_VAR_sql_warehouse_id`, `BUNDLE_VAR_genie_space_id`).
- App name, Lakebase instance/catalog/database, Genie title, and runtime/rollback
  secret scopes are unique to the intended workspace; the configured bundle
  host and `DATABRICKS_HOST` identify that same workspace.
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
- `tools/databricks/bundle_env.py validate -t dev` passes.
- `tools/databricks/bundle_env.py plan -t dev` shows the expected direct
  deployment changes; a first isolated-workspace plan has zero changes and
  zero deletions.
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
  --release-sha <sha>`) with an empty `cannot_claim`. Its six live checks
  (SQL/Python parity, Lakebase round trip, Genie live, Playwright live,
  the simulated resilience drill and the non-admin proof) reach `passed`
  only from a green `live validation (on demand)` run certified by
  `tools/live_validation_gate.py`: the newest completed run on `main` whose
  code is runtime-equivalent to the release (deny-by-default: only docs,
  repo-root `*.md`, tests and workflow files are non-runtime), 14 days old
  or newer, with its three release jobs green. An operator `passed` without
  such a run is recorded `unknown`. The `prod-readiness-gate` workflow
  (`deploy-prod.yml`) runs the same gate as a convenience; neither
  `scripts/deploy.sh` nor `scripts/package_source.sh` calls GitHub.

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
[docs/clients/bootstrap.md](clients/bootstrap.md). Until the GitHub
credential sink (M2-PR2) lands, an apply that would create, adopt or mint
refuses before its first mutation.

## CI staging install (deploy-dev.yml) and the release-gate bootstrap

Decision D-platform-process-b step 7 (audit quality-03, report 12.4 #8),
prepared in W5e `w5-deps-report` and completed by the post-W5
forked-anchor lane. The release gate above needs one green
`live validation (on demand)` run on `main`; its 120-day lookback from the
last green run (2026-06-16) ended on 2026-10-14, so the gate reads
`no_green_run` until this bootstrap completes.

**Status: the forked-anchor `pr105-host` target is built; the bootstrap now
waits on the owner steps below, then integrator steps 1-6.** The
`pr105-host` render in integrator step 2 has never run: it needs a credential
for that workspace, so the target itself is pinned by static tests only. In W5e the
bootstrap was blocked in the anchor workspace: the reserved-identity contract
(owner step 1) meant a CI staging install there would resolve the same seven
reserved principals as any full `scripts/deploy.sh -t dev` of the live
install, so the two installs could never hold separate identities there (the
anchor workspace holds none of the seven today: a read-only listing found
none on 2026-10-27). The guards below keep that fail-closed but can never let
it reach green. Since post-W5, `deploy-dev.yml` deploys only to the
`pr105-host` target, which has its own workspace host. That separates the
workspace-scoped objects and the workspace the identities are resolved in; it
does not separate Unity Catalog or the account (see "What the forked anchor
separates, and what it shares" below).

**The live install is outside this bootstrap, and its governed deploy is
NO-GO.** `deploy-dev.yml` deploys and recovers only the CI staging install on
`pr105-host`; nothing in this section deploys, recovers or restarts the live
`mip-app` in the anchor workspace. Its command of record is an operator
`./scripts/deploy.sh -t dev`, but a read-only readiness check on 2026-10-26
found that it cannot deploy the live App by any supported path today:

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
Never start `./scripts/deploy.sh -t dev` against the live workspace during an
incident; escalate to the owner (docs/runbook.md, section 1.2).

**Why the CI install needs isolating.** `databricks.yml` anchors
`workspace.host` to the live workspace, and the dev target's bundle root was
`~/.bundle/<bundle>/dev`, the live `mip-app`'s own root. A `deploy-dev.yml`
run would have uploaded into, and shared bundle state with, the live install
(and `converge_static_app_source` deletes the root npm manifests there), and
with the dev environment's variables unset its fallbacks name the live
Lakebase instance `mip-app-state`, sync catalog `mip_app_state` and secret
scope `mip-runtime`. Root, name and variable isolation cannot separate the
identities, because the seven reserved display names are workspace-wide; only
another workspace can, and only while each workspace keeps its own reserved
principals (the owner invariant in owner step 1).

**The forked-anchor target (built, post-W5).**

- `databricks.yml` target `pr105-host`: development mode, `run_as` the
  deploying service principal (`service_principal_name:
  ${workspace.current_user.userName}`, the CI deployer below; only
  `deploy-dev.yml` deploys this target), root
  `~/.bundle/<bundle>/${var.bundle_root_leaf}`, no Genie
  pin (the governed space is resolved by `MIP_GENIE_SPACE_NAME` at deploy
  time), and its own YAML anchor `&ci_staging_host` =
  `https://dbc-149e560d-e056.cloud.databricks.com`, the retired staging
  workspace the CI install used before the 2026-08-08 rebind. `dev`, `prod`
  and `ci` keep `*default_host`, so `scripts/configure-workspace.sh` and
  `make check-workspace-host` stay single-anchor for customer forks.
- Identities are resolved in the host's workspace. `scripts/deploy.sh`'s
  workspace-auth bind refuses any authenticated host other than the target's
  `workspace.host` before any mutation, and the deploy reconciles the seven
  reserved roles by display name in that authenticated workspace, refusing a
  duplicate reserved name or a principal whose application id differs from
  the configured client id. That is not a structural separation: service
  principals are account-level, so one principal can be assigned to both
  workspaces. The two installs could share an identity only if an owner
  assigned one workspace's reserved principal to the other AND configured the
  other install with its client id; the owner invariant in owner step 1
  forbids both, and no code checks the account-level assignments.
- `scripts/deploy.sh` accepts `-t pr105-host` as a dev-class target
  (`TARGET_CLASS=dev`: sandbox `APP_ENV`, Summit demo feeds, the resolved root
  leaf). `deploy-dev.yml` runs `scripts/deploy.sh -t pr105-host --no-confirm`;
  `nightly.yml`'s bundle validate and three bundle runs use `-t pr105-host`.
- Why this and not install-scoped reserved names: those would rework the
  identity contract in `m2m_identity_contract.py`, `provision_m2m_oauth.py`
  and the runtime display-name pin the global audits read, and the CI install
  would still share the live workspace and its `users` group, so the
  workspace-scoped audits below would still fail it on the live workspace's
  Genie `users` grants. Both designs
  share the Unity Catalog metastore, so neither changes the metastore-wide UC
  audit's blockers (owner step 8). The forked anchor changes one bundle target
  and the rule table, separates every workspace-scoped object, and keeps every
  guard.
- The dev target's Genie pin is `01f13d4968af1b249dc388fd5b18b195`, the live
  "Mortgage Lead Intelligence" space. It was `01f18188d7a41311abe3d99932b5aa9a`
  from 441588f6, which moved the host anchor to the staging workspace and
  swapped the pin in the same diff, until this lane; ace044d6 had rebound the
  anchor but not the pin, so the isolation guard compared CI's
  `GENIE_SPACE_ID` against a space that does not exist on the live host.

**The CI deployer: GitHub OIDC, no stored credential.** `deploy-dev.yml`
deploys as a dedicated account service principal, never a person and never
one of the seven reserved identities. It has no OAuth secret and no personal
access token: it authenticates only through GitHub Actions OIDC workload
identity federation (owner step 3).

- The `deploy` job alone holds `id-token: write` (the workflow default stays
  `contents: read`). The Configure step writes the `DEFAULT` profile as
  `host`, `auth_type = github-oidc` and `client_id` from the `dev`
  environment variable `MIP_CI_DEPLOYER_CLIENT_ID`. It refuses a deployer id
  equal to any app-facing client id or to the account-SCIM client. The SDK
  and the CLI exchange the job's GitHub ID token for a short-lived Databricks
  token as that service principal; nothing is stored.
- "Prove the GitHub OIDC deployer identity" runs next, read-only, before the
  credential repair and before `scripts/deploy.sh`. Its profile and host
  checks run first and stop the run before any network call. The profile must
  hold only those three keys, with no ambient `DATABRICKS_HOST`, `_TOKEN`,
  `_AUTH_TYPE`, `_CLIENT_ID`, `_CLIENT_SECRET` or `_CONFIG_FILE` override,
  and its host must equal both `DATABRICKS_HOST` and the `pr105-host`
  target's `workspace.host`. The step then proves, through the Python SDK and
  the Go CLI, that the profile authenticates as `MIP_CI_DEPLOYER_CLIENT_ID`,
  that this principal is in `admins` and owns the current metastore, and that
  `DATABRICKS_WAREHOUSE_ID` and `GENIE_SPACE_ID` resolve. It prints booleans
  only (a failed call adds its exception class or the CLI's exit code, never
  a message or a value) and exits 2 on any mismatch.
- Under `github-oidc` an environment `DATABRICKS_CLIENT_ID` overrides the
  profile's `client_id`, so no deployer step lets one reach a Databricks
  client. The repair step carries the normal identity's id as
  `MIP_NORMAL_CLIENT_ID`. The deploy step carries the App's client pairs,
  which `scripts/deploy.sh` un-exports before its first child process.
- The deploy step sets `MIP_TEMP_CREDENTIAL_PLANE=workspace`. The deploy's
  300-second identity-probe secrets are then minted, listed and deleted
  through the deployer's workspace `service_principal_secrets_proxy` (owner
  step 9). Unset, the plane stays `account`, so operator runs are unchanged.
- At run time the deployer's SCIM `userName` is its application id. That id
  is the bundle's current user and `run_as`, the deployment-lease holder,
  the release folder's principal and the reviewed functions' owner. The
  account-SCIM authority is unchanged: it is still its own
  `DATABRICKS_ACCOUNT_CLIENT_ID`/`_SECRET` pair.

**What the forked anchor separates, and what it shares.** Read-only reads
from the live workspace on 2026-10-27 (agents never call `pr105-host`):

- Shared: the live workspace's Unity Catalog metastore and the Databricks
  account. The
  live workspace (id `2543889327043640`) is on it, and so are the pr105-era CI
  catalog `mip_pr105_staging` and another team's catalog whose step-4
  blocker grant was revoked on 2026-08-03 through a pr105 credential. The live `mip` and
  `mip_app_state` and the share catalog `cotality_mortgage_data` are `OPEN`,
  so the `pr105-host` workspace sees them. Catalogs, schemas, tables, grants,
  isolation modes, workspace bindings, `account users` and service principals
  are therefore shared. The owner confirms the metastore with `databricks
  metastores current` on a `pr105-host` credential (owner step 8).
- Separated: every workspace-scoped object, i.e. the App, SQL warehouses,
  serving endpoints, Genie spaces, secret scopes, Lakebase instances, jobs,
  pipelines, MLflow experiments, bundle state, the workspace `users` group and
  service-principal workspace assignments, plus every catalog `ISOLATED` away
  from the `pr105-host` workspace. Of the metastore's 46 catalogs, 34 are
  `OPEN` and 12 are `ISOLATED`, and none of the 12 is bound to workspace
  `7474645995341779`, the `pr105-host` id on record.
- Consequence: the rule table's catalog-scoped names are the CI install's
  primary Unity Catalog isolation, never defence in depth, and must never be
  relaxed: `MIP_DEFAULT_CATALOG` and `MIP_LAKEBASE_SYNC_CATALOG` (neither may
  name `mip` or `mip_app_state` in any UC spelling), and the AI Gateway table
  prefix and model family, which live in `MIP_DEFAULT_CATALOG`. The same holds
  for the metastore-wide UC audit (`audit_agent_runtime_foreign_uc_access`)
  and for owner step 8: its grant and binding changes are metastore-wide.

**Isolation rules (built).**

- `databricks.yml`: variables `bundle_root_leaf` (default `dev`) and
  `install_suffix` (default empty). The dev-class targets' root is
  `~/.bundle/${bundle.name}/${var.bundle_root_leaf}`, and the three
  workspace-unique display names (the SQL warehouse, the feature pipeline,
  the MLflow experiment) end in `${var.install_suffix}`. With the defaults
  the live install renders exactly as before; the prod and ci targets are
  unchanged. The experiment is `/Shared/mip-lead-scoring${var.install_suffix}`.
  The CLI rewrites every experiment name as `dir + "/" + name_prefix + base`
  in every target, so a workspace-root name rendered as `//…`. One level under
  `/Shared` renders `/Shared/[dev <user>] mip-lead-scoring` for dev and
  `/Shared/mip-lead-scoring` for prod and ci
  (`tests/unit/test_bundle_experiment_path.py`). On paychex the bundle state
  holds the experiment at `/Shared/mip/[dev <user>] lead-scoring`,
  so the first governed bundle apply is expected to rename it to the new name.
  That apply has not been run or planned. A read-only `experiments
  get-by-name` on 2026-10-27 found nothing at the new dev name, so the rename
  has no collision to fail on. For rollback, record the rename as a change
  the listed rollback steps do not undo. It keeps the experiment id, runs and
  models, and no repo code reads the experiment by name. Reversing it is an
  owner-run rename by id (`databricks experiments update-experiment
  <experiment id> --new-name '/Shared/mip/[dev <user>] lead-scoring'`), and
  the next bundle apply from this tree renames it again. At the new path the
  experiment inherits the ACL of `/Shared` itself rather than of
  `/Shared/mip`; whether that ACL is acceptable is an owner decision.
- `deploy-dev.yml` sets `MIP_BUNDLE_ROOT_LEAF: dev-ci` as a literal in the
  deploy step, so the CI install lives under `~/.bundle/<bundle>/dev-ci` and
  its names end in `_ci`, as defence in depth inside its own workspace.
  `nightly.yml` sets `BUNDLE_VAR_bundle_root_leaf: dev-ci` and
  `BUNDLE_VAR_install_suffix: _ci`, so its `bundle validate/run -t pr105-host`
  read that install's state.
- `scripts/deploy.sh` preflight (before the prompt and any mutation, also in
  `--dry-run`) runs `tools/databricks/ci_install_isolation.py`. It refuses,
  with exit 2 and the variable name only: `deploy-dev.yml` on any target but
  `pr105-host`, or without a non-default leaf; `pr105-host` outside
  `deploy-dev.yml`; a non-default leaf outside GitHub Actions or on any target
  but `pr105-host`; a `pr105-host` whose resolved hostname is empty,
  unparseable or the dev target's (a scheme, port, path, query or trailing
  dot never makes a second workspace; an alias DNS name for the live
  workspace is beyond a static check, and owner step 8's `metastores current`
  workspace id is that proof); a malformed leaf; and, with a non-default leaf, any of
  `MIP_APP_NAME`, `MIP_DEFAULT_CATALOG`, `MIP_LAKEBASE_INSTANCE`,
  `MIP_LAKEBASE_SYNC_CATALOG`, `MIP_RUNTIME_SECRET_SCOPE`,
  `MIP_APP_ROLLBACK_SECRET_SCOPE`, `MIP_GENIE_SPACE_NAME`,
  `MIP_AI_GATEWAY_TABLE_PREFIX` or `MIP_AI_GATEWAY_AGENT_EXPERIMENT_BASE`
  that is unset or equal to its live default, a `MIP_DEFAULT_CATALOG` or
  `MIP_LAKEBASE_SYNC_CATALOG` that names either live catalog (`mip`,
  `mip_app_state`) in any spelling Unity Catalog resolves to it (case,
  backticks; the crossed pair included, because the metastore is shared), a
  `GENIE_SPACE_ID` equal to the dev target's pinned live space, a set
  `MIP_AI_GATEWAY_AGENT_MODEL_FAMILY` outside `MIP_DEFAULT_CATALOG` (unset,
  the deploy derives it from that catalog), or a
  `MIP_REMEDIATE_FOREIGN_CATALOG_BINDINGS` other than unset or `0` (the
  workflow's `remediate_foreign_catalog_bindings` input: the remediation
  rewrites bindings metastore-wide). An operator run (no leaf, not
  `pr105-host`) is unchanged.
  The CI rule is keyed on the `deploy-dev.yml` workflow ref, a present leaf
  or the `pr105-host` target, never on `GITHUB_ACTIONS` alone, so CI's own
  pytest is unaffected.
- `tools/databricks/bundle_env.py` runs the same rule table before its
  mutating subcommands (`deploy` and the App `deployment bind/unbind`): once
  before any workspace call and again on the governed Genie id it would bind.
  So through `bundle_env` the `pr105-host` target is deployable and bindable
  only from `deploy-dev.yml`, and `deploy-dev.yml` can deploy or bind no other
  target. `validate`, `plan` and `summary` are unchanged. It does not gate
  `databricks bundle run` (`nightly.yml` runs `pr105-host` jobs directly) or
  a raw CLI bundle deployment, which the deploy contract retires.
- `deploy-dev.yml`'s `repair_normal_credential` step rotates the normal
  identity's OAuth secret into GitHub before, and outside,
  `scripts/deploy.sh` and its preflight. It first runs
  `python -m tools.databricks.ci_install_isolation repair-credential`, which
  refuses (exit 2, before any workspace call) outside `deploy-dev.yml`, a
  `DATABRICKS_HOST` whose hostname is not the `pr105-host` target's, and a
  `MIP_APP_NAME` that is unset or the live `mip-app`; the step's
  `MIP_APP_NAME` has no `mip-app` fallback.
- `converge_static_app_source --expected-root-leaf` checks the App source
  path against the resolved leaf (default: the target, i.e. as before).
- **Workspace-global steps** (the root and names do not isolate identities).
  The workspace-scoped rows are defence in depth now that the CI install runs
  in its own workspace. The `audit_agent_runtime_foreign_uc_access` row is
  metastore-wide, and the metastore is shared, so it stays primary
  protection:

  | Step | Scope | With a non-empty install suffix |
  |---|---|---|
  | `install_scope check-proxy` and `check-warehouse` (`tools/databricks/install_scope.py`, read-only probes in step 10b) | the proxy's serving-endpoint ACLs; the verifier's warehouse ACLs | run before `AGENT_PROXY_ACCESS_MUTATED` or `VERIFIER_GATEWAY_CUTOVER_MUTATED` is armed and refuse (exit 2) when the identity holds access this install does not own. The deploy then exits through its EXIT trap with no workspace-global compensation armed: the trap stops and cleans up only this install's App and temporary grants, then releases the lease |
  | `warehouse_acl.converge_exact_can_use` (verifier `CAN_USE`) | removes the identity's direct access on every other warehouse | backstop: repeats the warehouse check before the command's first mutation. A refusal this late exits after both compensations are armed: the proxy's deny-all is guarded (below), and the verifier's compensation revokes only this run's green Gateway and otherwise audits read-only |
  | `agent_proxy_access` converge (Supervisor and Genie ACLs, then obsolete-endpoint revokes) | deactivates the proxy's membership on every non-reviewed Supervisor agent, converges its ACL on every Genie space, revokes it on every endpoint outside the reviewed set | backstop: repeats the endpoint check before its first convergence. The check reads serving endpoints only, so it covers the Supervisor and Genie steps indirectly: a proxy shared with the live install holds the live Supervisor endpoint |
  | `agent_proxy_access --mode deny-all` (the failure compensation) | revokes the proxy on every endpoint, Supervisor and Genie space | refuses (exit 2, before any revoke) unless the proxy holds no endpoint access outside this install's own Supervisor endpoints (`--install-owned-endpoint`, passed by `deny_all_agent_proxy_access`); the compensation then fails loudly (exit 90, lease retained) |
  | `m2m_access_policy.revoke_can_query_on_obsolete_endpoint` (verifier) | endpoints outside the reviewed set | backstop: before the Gateway convergence's first grant, and again before each revoke, repeats the `check-warehouse` proof (`install_scope.assert_verifier_revokes_owned`) with the call's `--warehouse-id`, and refuses (exit 2, counts only) without one. Endpoints carry no install-scoped name and a shared runtime creates both installs' endpoints, so the verifier's warehouse ACL is the witness: a verifier shared with the live install holds the live, unsuffixed warehouse grant. The forked-anchor `pr105-host` target gives the CI install its own verifier in the first place, while the owner invariant in owner step 1 holds |
  | `reconcile_historical_agent_endpoints cleanup` (step 10b, "attest and retire every unrelated historical runtime endpoint") | every serving endpoint and Supervisor agent the agent-runtime identity created | deletes each one outside the preserved pins, with its query groups, but only after attesting the whole inventory. An endpoint whose signed Gateway contract names another catalog or Genie space fails that attestation (`signed identity or scope drifted`), so a runtime shared with the live install fails closed here before any delete. Not suffix-guarded: the scope is keyed on the identity |
  | `audit_global_m2m_access --genie-space-id` (`agent_runtime_access.audit_global_genie_access`, the runtime's re-audits) | every Genie space in the workspace, read-only | fails closed unless the agent runtime holds `CAN_RUN` on exactly this install's space and nothing else; a runtime shared with the live install holds the live space |
  | `assert_lakebase_role_scope`, `assert_no_warehouse_access`, the other global M2M audits | the whole workspace, read-only | fail closed if a shared identity holds access elsewhere |
  | `audit_agent_runtime_foreign_uc_access` (step 4 before the Lakebase bootstrap, step 10b before runtime-owned UC mutations, and after any foreign-catalog remediation) | every catalog in the metastore except this install's, read-only | fails closed on any agent-runtime privilege there (a runtime shared with the live install holds the live catalog's grants) and on any foreign catalog granting to `account users` (owner step 8) |

  With the shared identities of the anchor workspace these guards fail a CI
  deploy closed at the step-4 agent-runtime foreign-UC audit, at the
  historical Gateway attestation and at the `install_scope` probes; they
  cannot make it pass. That is why the CI install now runs in its own
  workspace, and why the rule table refuses `deploy-dev.yml` on any other
  target: no dispatch can target the anchor workspace. In the `pr105-host`
  workspace the workspace-scoped audits read that workspace's own Genie
  spaces, endpoints and warehouses. The UC audit reads the shared metastore:
  every `OPEN` catalog, the live install's included, must grant the runtime
  nothing, and every catalog `ISOLATED` away from that workspace must match
  `MIP_UC_FOREIGN_CATALOG_BINDING_POLICY` exactly (owner step 8).
- `nightly.yml` declares no `environment:`, so it reads REPOSITORY-level
  variables and secrets while `deploy-dev.yml` reads the `dev` environment's.
  Its `bundle validate/run -t pr105-host` address the dev-ci root through its
  `BUNDLE_VAR_*` pins, but its `databricks apps get` `--git-sha` verify,
  `BUNDLE_VAR_uc_catalog` and parity tests use the repository-level names, so
  those must name the same CI staging install (owner step 6). Its DEFAULT
  profile is written from the repository-level `DATABRICKS_HOST`, which must
  be the `pr105-host` host: the CLI refuses a profile whose host differs from
  the target's. Otherwise the verify step fails fast against a different App.
  Its first job, `ci-install-isolation`, runs the rule table's UC-catalog row
  (`python -m tools.databricks.ci_install_isolation uc-catalogs`) on the
  workflow-level `MIP_DEFAULT_CATALOG` and `MIP_LAKEBASE_SYNC_CATALOG`, and
  every other job needs it. Those names fall back to the live `mip` and
  `mip_app_state` when the repository variables are unset, so an unset or
  live catalog name (in any UC spelling) refuses the run before any job reads
  or writes a catalog through the shared metastore.

**The App release folder on the CI install.** With `MIP_BUNDLE_ROOT_LEAF=dev-ci`
the release lives at
`/Workspace/Users/<deployer-application-id>/.bundle/mortgage-intelligence-platform/dev-ci/app-src/<release id>`
("Governed per-release App source"). `deploy-dev.yml` needs no change: it
already sets up Node 22, which the build and the `.br` check use, and installs
`requirements.txt`, which brings the SDK; a shallow checkout is enough for
`git archive`. Publish, converge and prune each validate the principal, the
target and the `dev-ci` leaf. The stage root goes away with the runner, and
the first run is a first install, so prune has nothing to delete. First-run
checklist for the owner-triggered run:

- the publish line counts at most 1,300 files, with the upload and verify
  wall time at the default 4 workers and the number of `[app-release]` rate
  limit retry lines (none is the goal; a retry is not a failure);
- every activation SUCCEEDED and no `[BUILD]` log shows an npm step; the
  first activation's `[BUILD]` log shows a pip install (a first install has
  no previous `requirements.txt`), later activations of the same release are
  expected to print `Requirements have not changed. Skipping installation.`
  ("App Python dependencies"), and the boot `dependency_lock_check` is ok
  after each;
- `active_deployment.source_code_path` ends in `/dev-ci/app-src/<release id>`
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

**Owner steps (never agent work).** These are the exact remaining steps. Each
is a credential, identity, GitHub-settings or audited-consent action against
the `pr105-host` workspace
(`https://dbc-149e560d-e056.cloud.databricks.com`), the Databricks account or
this repository. None mutates a live-workspace object, but the Unity Catalog
changes in step 8 are metastore-wide, so they also change what the live
workspace and every other team on that metastore see, and so are step 3's
metastore grants and ownership transfer. Agents never call the
`pr105-host` workspace, so nothing below is pre-verified there. Read-only
checks of the current GitHub state (names and timestamps only):

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

   Resolution (built): the forked-anchor (`pr105-host`) target, chosen over
   a change that makes the reserved names install-scoped (see "The
   forked-anchor target" above). The CI install's identities are the seven
   reserved principals of the `pr105-host` workspace. Owner action: in that
   workspace, make each of the seven reserved principals exist exactly once
   and its GitHub client id/secret pair current. The repository and `dev`
   environment secrets date from 2026-07-15..26, the staging-workspace era, so
   they are expected to be that workspace's principals; confirm each
   `*_CLIENT_ID` resolves there under its reserved name. Create only a
   missing role, with the `--pre-app-bootstrap` commands in "Per-run M2M
   identities" authenticated to that workspace (creation-only: it refuses an
   existing reserved principal), and the account-SCIM client
   (`DATABRICKS_ACCOUNT_*`) if absent.

   **Owner invariant: each workspace keeps its own reserved principals.**
   Service principals are account-level objects, so the forked anchor
   separates identities only while this holds; no code checks it. Never
   assign the `pr105-host` workspace's seven reserved principals to the live
   workspace (`2543889327043640`), and never configure the live install
   (`.env.local`, an operator shell) with their client ids.

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
   only to a GitHub repository's
   repository-level Actions secrets (`gh secret set --repo`): the reviewed
   `origin`, or the repository `MIP_M2M_GITHUB_REPOSITORY` names. The secret
   names are fixed per role (`IDENTITY_DEFAULTS`), and in this repository they
   belong to the CI install: `nightly.yml` reads them, and `deploy-dev.yml`
   reads them wherever its `dev` environment does not shadow them (owner step
   4). A live identity bootstrap therefore needs its own reviewed sink: another
   repository named by `MIP_M2M_GITHUB_REPOSITORY`, plus a reviewed way to
   hand the values to the operator, who cannot read GitHub secrets back. It
   stays out of scope until that design is reviewed. Never run the bootstrap
   for the live workspace against this repository: it would overwrite the CI
   install's secrets.

   Check the invariant read-only before each dispatch. Run
   `databricks service-principals list --filter 'displayName sw "mip-"'` once
   with a live-workspace credential and once with a `pr105-host` credential.
   No `applicationId` in the live listing may equal the `applicationId` of any
   of the seven reserved principals in the `pr105-host` listing. Compare the
   two listings, never a stored `*_CLIENT_ID`: GitHub secrets cannot be read
   back.
2. Set the dev environment variables to staging names: `MIP_APP_NAME`,
   `MIP_DEFAULT_CATALOG`, `MIP_LAKEBASE_INSTANCE`,
   `MIP_LAKEBASE_SYNC_CATALOG`, `MIP_RUNTIME_SECRET_SCOPE`,
   `MIP_APP_ROLLBACK_SECRET_SCOPE` (derived from the App name, as the
   preflight requires), `MIP_GENIE_SPACE_NAME`, `MIP_AI_GATEWAY_TABLE_PREFIX`
   and `MIP_AI_GATEWAY_AGENT_EXPERIMENT_BASE`; leave
   `MIP_AI_GATEWAY_AGENT_MODEL_FAMILY` unset or name a family in the staging
   catalog. `MIP_AGENT_PROXY_SECRET_SCOPE` may stay unset: the preflight
   derives it as `<MIP_APP_NAME>-agent-proxy` and refuses any other value.
   The two catalog names must name neither live catalog (`mip`,
   `mip_app_state`): the metastore is shared, and the rule table refuses them
   otherwise. The 2026-10-26 readiness check found the staging App, catalog,
   Lakebase, runtime-scope and Genie names already set; confirm the
   rollback scope and the two AI Gateway names, which the rule table refuses
   when unset.
3. **The CI deployer service principal and its authority.** This replaces
   the retired deployer PAT; `deploy-dev.yml` reads no `DATABRICKS_TOKEN`.
   - Create a dedicated account service principal for the deployer. Record
     its account id as `<deployer-sp-id>` and its application id as
     `<deployer-application-id>`. Never create an OAuth secret or a token
     for it, and never reuse a reserved identity, the account-SCIM client or
     another team's automation principal.
   - Assign it to the `pr105-host` workspace (`7474645995341779`) with the
     `ADMIN` permission, so `admins` is in its `/Me` groups, as the global
     inventory audits require. Assign it to no other workspace, and never to
     the live workspace (`2543889327043640`).
   - Create its federation policy (`databricks account
     service-principal-federation-policy create <deployer-sp-id>`). Issuer:
     `https://token.actions.githubusercontent.com`. Subject:
     `repo:<owner>/<repo>:environment:dev`, the default GitHub OIDC subject
     of a job bound to this repository's `dev` environment, whose required
     reviewer and branch policy still gate every run. Never widen it to a
     branch, a ref or `repo:<owner>/<repo>:*`. Both audiences are required:
     the workspace token endpoint
     `https://dbc-149e560d-e056.cloud.databricks.com/oidc/v1/token` (the
     SDK's default) and the account id `<account-id>` (for a CLI that
     requests the account audience).
   - Set the `dev` environment variable `MIP_CI_DEPLOYER_CLIENT_ID` to
     `<deployer-application-id>`. It is public: a variable, never a secret.
   - Point the environment's `DATABRICKS_HOST` (set 2026-07-17,
     staging-workspace era) at the `pr105-host` workspace: the host
     must equal `https://dbc-149e560d-e056.cloud.databricks.com`, or the
     proof step and `scripts/deploy.sh` refuse the run at their host checks,
     before any mutation. Then delete the dead `dev` environment secret
     `DATABRICKS_TOKEN`. Keep the repository-level one until `nightly.yml`
     moves to GitHub OIDC (step 6).
   - Unity Catalog (metastore-wide): run these as the current metastore
     owner, in this order. Grant `CREATE_CATALOG` on the metastore (the bundle creates
     the Lakebase database catalog). Grant `USE_CATALOG`, `USE_SCHEMA` and
     `SELECT` on the share catalog `cotality_mortgage_data`, which the silver
     pipeline and the gold SQL read as the deployer (`run_as`). Optionally,
     with consent, grant `USE_SCHEMA` on `system.access` and `SELECT` on
     `system.access.audit`, read only by first-install and Supervisor audit
     recovery; these expose account-wide audit logs, and without them an
     interrupted first install needs owner-run recovery. Last, transfer the
     metastore's ownership to `<deployer-application-id>` (`databricks
     metastores update <metastore id> --owner <deployer-application-id>`).
     The UC control-plane audit requires its caller to own the current
     metastore directly, and a group owner would weaken that fail-closed
     check. This is an owner decision on a shared object: afterwards only the
     deployer passes the deploy's step-4 and step-10b UC audits on this
     metastore, and the previous owner loses metastore-admin powers. An
     account admin can reverse it.
4. Remove or re-set the environment-level `DATABRICKS_CLIENT_ID`
   (2026-07-20), which shadows the rotated repository-level pair
   (2026-07-26), and check that every environment-level `*_CLIENT_ID` pairs
   with its own secret. Re-mint the account-SCIM credential yourself. Create
   a new OAuth secret for the service principal `DATABRICKS_ACCOUNT_CLIENT_ID`
   names and pipe it straight into `gh secret set
   DATABRICKS_ACCOUNT_CLIENT_SECRET`; agents never handle the value. Then
   retire its older secrets unless something local still uses them. The
   deployer never takes this role: the account plane stays on
   `DATABRICKS_ACCOUNT_*`.
5. Set staging `GENIE_SPACE_ID` and `DATABRICKS_WAREHOUSE_ID` secrets for
   the `pr105-host` workspace, never the live ones. `GENIE_SPACE_ID` must
   differ from the dev target's pin `01f13d4968af1b249dc388fd5b18b195`; the
   rule table refuses it otherwise. The proof step refuses either one unless
   it resolves for the deployer.
6. Point the REPOSITORY-level names `nightly.yml` reads (it has no
   `environment:`) at the same CI staging install as the dev environment:
   the variables `MIP_APP_NAME`, `MIP_DEFAULT_CATALOG`,
   `MIP_LAKEBASE_INSTANCE`, `MIP_LAKEBASE_SYNC_CATALOG`,
   `MIP_RUNTIME_SECRET_SCOPE`, `MIP_APP_ROLLBACK_SECRET_SCOPE`,
   `MIP_AGENT_PROXY_SECRET_SCOPE`, `MIP_GENIE_SPACE_NAME`,
   `MIP_AI_GATEWAY_TABLE_PREFIX` and `MIP_AI_GATEWAY_AGENT_EXPERIMENT_BASE`
   (and `MIP_AI_GATEWAY_AGENT_MODEL_FAMILY` unset or in the staging catalog),
   and the secrets `DATABRICKS_HOST` (the `pr105-host` host),
   `DATABRICKS_TOKEN`, `DATABRICKS_WAREHOUSE_ID` and `GENIE_SPACE_ID`. Only
   `deploy-dev.yml` (whose dev environment shadows the repository level) and
   `nightly.yml` read these names. Compare names and `updated_at` in the
   repository-level listings above against the dev environment's. If the
   repository-level values must keep naming the live install, nightly cannot
   certify the CI install: stop and decide before integrator step 5.
   `nightly.yml` still authenticates with that repository-level
   `DATABRICKS_TOKEN` until its own GitHub OIDC migration, a follow-up. The
   CI install's bundle state lives under the deployer's home (`~` is the
   authenticated principal's), so nightly must then run as the same deployer
   service principal, through a second federation policy bound to
   `nightly.yml` on `main` (subject claim `job_workflow_ref`). After the
   first green `deploy-dev.yml` run, set the repository variable
   `MIP_REVIEWED_FUNCTION_OWNER` to `<deployer-application-id>`, the reviewed
   functions' new owner, or nightly reports owner drift.
7. Keep `main` in the dev environment's deployment branch policy. A
   read-only check on 2026-10-28 found it next to
   `codex/intelligence-trust-ux`. Then make the AI Gateway proof-key registry
   verify every signed record on the CI install (the proof-key rotation in
   "Per-run M2M identities"). The registry
   (`app_deployment_lease_support.key_registry`) is
   `MIP_AI_GATEWAY_PROOF_HISTORICAL_VERIFY_KEYS` (comma-separated, oldest
   first), then `MIP_AI_GATEWAY_PROOF_PREVIOUS_VERIFY_KEY`, then the current
   key derived from `MIP_AI_GATEWAY_PROOF_SIGNING_KEY`. It must end at the
   current key, and each signed lease record's `key_epoch` must be its
   signing key's index, so a retired key can never become current again. If
   the install's records carry more than one epoch, generate a new Ed25519
   signing key yourself and store it with `gh secret set
   MIP_AI_GATEWAY_PROOF_SIGNING_KEY`; never reuse a retired private key.
   Set the repository variable `MIP_AI_GATEWAY_PROOF_HISTORICAL_VERIFY_KEYS`
   to every retired public key that signed one of those records, oldest
   first, and the repository variable `MIP_AI_GATEWAY_PROOF_VERIFY_KEY` to
   the new public key for nightly. `deploy-dev.yml` passes the historical
   keys to the repair step and to `scripts/deploy.sh`.
8. Clear the step-4 and step-10b blockers. First confirm the shared
   metastore: `databricks metastores current` on a `pr105-host` credential
   must return the live workspace's `metastore_id` (compare it with the
   `metastore_id` that `databricks metastores current` returns on a
   live-workspace credential) and `workspace_id` `7474645995341779` (if not,
   the facts above are wrong: stop and re-read this section).
   - **Unity Catalog grants, metastore-wide.** A grant revoke here also
     changes what the live workspace and every other team on the metastore
     see, so each needs the catalog owner's approval and is a narrow grant
     revoke, never an isolation or re-binding. The step-4 audit fails on any
     privilege the runtime holds on an `OPEN` catalog, BROWSE included and
     inherited through `account users` included. On 2026-10-27, 15 catalogs
     granted `account users`: 11 `ISOLATED` ones, bound to the live
     workspace and not to `pr105-host` (they need the binding policy below,
     not a revoke); the platform catalogs `samples` and `system`, which the
     audit skips; and the two known blockers, two `OPEN` catalogs owned by
     other teams that grant `account users` `ALL_PRIVILEGES` and `BROWSE`
     respectively. Find them and their owners at run time with this
     read-only inventory on a `pr105-host` credential; every row it prints
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
   - **Binding policy (a GitHub-settings action, no UC change).** The audit
     accepts a catalog `ISOLATED` away from the `pr105-host` workspace only
     if it matches the dev environment's
     `MIP_UC_FOREIGN_CATALOG_BINDING_POLICY` exactly (owner, catalog type,
     isolation mode, bound workspace ids, binding types). On 2026-10-27 that
     is all 12 `ISOLATED` catalogs. Set the policy from a read-only inventory
     of their current state, never the other way round.
   - **Workspace-scoped, in the `pr105-host` workspace only:** Genie spaces
     that grant a group the CI identities belong to, such as workspace
     `users` (the runtime, verifier and App Genie audits fail on any group
     grant); and any direct grant the staging verifier still holds on an
     unsuffixed warehouse, or the proxy on a serving endpoint outside its
     reviewed set, from the staging-workspace era (the `install_scope` probes
     refuse both).

   Never dispatch with `remediate_foreign_catalog_bindings=true`: it sets
   catalogs ISOLATED and rewrites their workspace bindings metastore-wide,
   and between its isolate and re-bind calls (or after a killed run) every
   other workspace loses those catalogs, the live install's share catalogs
   included. The rule table refuses it on
   `pr105-host` (exit 2, before any mutation).
9. Hand the staging install over to the deployer, then clear the known
   step-4 blocker.
   - A previous user deployer created the staging objects, and the deploy's
     owner, creator and ACL checks accept only the deployer. In the
     `pr105-host` workspace, with consent (staging state is lost), delete
     the previous deployer's development-mode jobs (`[dev <user>] mip_*`) and
     its feature pipeline, which manages the silver datasets. Delete the
     staging Lakebase synced tables, then the database catalog, then the
     instance. Delete the staging rollback and runtime secret scopes. The
     deploy recreates each one as the deployer.
   - In the staging catalog, transfer to `<deployer-application-id>` every
     securable the previous deployer owns: the catalog, its schemas, tables,
     views, metric views, functions and volumes. Leave the agent runtime's
     own models and payload tables alone: step 10b attests the historical
     runtime artifacts against them, live, before it retires those
     artifacts. Never widen `MIP_UC_APPROVED_OWNER_PRINCIPALS` instead; a
     missed object must fail closed. Verify read-only that the previous
     deployer owns nothing left in the staging catalog.
   - The 2026-08-04 `invalid_client` for `mip-agent-runtime-ci-sp` came from
     an account-plane M2M `service_principal_secrets.create()` that reported
     success from the runner, but the secret never appeared in the
     inventory. `deploy-dev.yml` now sets `MIP_TEMP_CREDENTIAL_PLANE=workspace`,
     so the deploy's 300-second probe secrets go through the deployer's
     workspace `service_principal_secrets_proxy`, the plane that durable
     rotations from a runner have persisted on. This is expected, not yet
     proven: if a probe still fails, re-mint or assign that service
     principal in the `pr105-host` workspace, or take the captured
     `error_id`s to Databricks support.
10. Dispatch `deploy-dev.yml` on `main` with
    `acknowledge_single_maintainer_break_glass=true` (or approve that exact
    dispatch in chat), then approve the pending deployment. The workflow can
    only deploy `-t pr105-host`, so it never runs against the anchor
    workspace. If a staging App with `MIP_APP_NAME` already exists in that
    workspace with no signed rollback record (the staging-workspace-era App
    likely does), set `rebase_unverified_app=true` on this first dispatch only;
    without it the run fails closed after the treatment quiesce and leaves that
    staging App stopped.

**Integrator steps.**

1. Freeze merges and pause piecewise deploys.
2. Read-only render: a plain `python -m tools.databricks.bundle_env validate
   -t dev` shows the live root and names unchanged (a bare `databricks bundle
   validate -t dev` with no `BUNDLE_VAR_genie_space_id` now binds the pin
   `01f13d4968af1b249dc388fd5b18b195`). The `pr105-host` render
   (`MIP_BUNDLE_ROOT_LEAF=dev-ci ... validate -t pr105-host`, root
   `.../dev-ci`, the `_ci` names) needs a credential for that workspace, so it
   is an owner or CI check, never an agent one.
3. Dispatch `deploy-prod.yml` on `main` and record the expected FAIL
   (`no_green_run`).

Steps 4-6 run only on the forked-anchor target (owner steps 1-10), never
against the anchor workspace; the rule table now refuses any other target.

4. After the owner's dispatch and approval (owner step 10), watch
   `deploy-dev.yml` to green.
5. Confirm owner step 6 (names and `updated_at` only: the repository-level
   variables and secrets `nightly.yml` reads were set to the CI staging
   install), then dispatch `nightly.yml` on the same sha and watch it. Its
   live jobs need the `nightly.yml` GitHub OIDC follow-up in owner step 6
   first: they still read the retired repository-level PAT.
6. Repair stale `frontend/tests/e2e/real_data.spec.ts` assertions in a PR if
   it is red, then repeat 4-5; record the green run URL in the audit
   report; re-dispatch `deploy-prod.yml` and expect PASS.
