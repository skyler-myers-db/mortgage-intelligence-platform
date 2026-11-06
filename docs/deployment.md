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

## Emergency recovery: a slim per-release App source folder

**Emergency recovery only. The command of record is `./scripts/deploy.sh -t
client`, which stages, publishes and promotes a per-release folder itself
(previous section).** A hand-made slim folder is for the case that path cannot
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
MIP_GIT_SHA=<sha> python -m tools.databricks.app_deploy_payload \
  --source-code-path /Workspace/Users/<owner>/.bundle/mortgage-intelligence-platform/dev/app-src/<release-id> \
  --install-class demo --app-env sandbox --catalog mip --schema gold \
  --lakebase-instance <lakebase-instance> > <payload>
databricks apps deploy <app-name> --json @<payload> -p <profile>
```

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
   `deploy-dev.yml` and `nightly.yml` are Entrada CI only, and both are parked
   until a client environment is selected (M1-PR7): their first job refuses
   every dispatch. The command of record is `./scripts/deploy.sh -t client`.
   Before the first deployment into an isolated workspace, run:

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
   pairs and must match when both names are present.

3. Run the deployment script. It builds the frontend, provisions Genie if
   needed, validates/plans/deploys the direct bundle through the env-aware wrapper,
   publishes the staged per-release App source folder and promotes it to the
   running Databricks App ("Governed per-release App source"), and runs the
   refresh/smoke steps.

```bash
./scripts/deploy.sh -t client
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
- These are local deploy controls for now. `deploy-dev.yml` and `nightly.yml`
  are parked and do not forward them; per-client CI reads them from the
  client profile once M1-PR7 lands.
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
`./scripts/deploy.sh -t client` for a client install (`deploy-dev.yml` is
parked; see "CI deploy (deploy-dev.yml): parked" below). Bundle apply first
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
| App payload | unchanged | `MIP_AGENT_ORCHESTRATOR=0`, `MIP_AI_GATEWAY=0`; an operator value turning either on refuses |
| capability preflight (`tools/clients/capabilities.py`, in the live checks) | Apps, Lakebase, Genie, SQL warehouses, Model Serving, MLflow (the pre-stop probes' Supervisor listing proves the Supervisor API) | Apps, Lakebase, Genie, SQL warehouses |

The agentic tier's plan, bundle variables and App payload are byte-identical
to those before the tier was read (the PR's artifact-hash proof;
`tests/unit/test_agentic_tier_dry_run.py` pins its tier-aware lines). The
emergency recipe builds the agentic payload by default and keeps its 32 env
names.

**A core release refuses before the lease.** The core tier is tier-aware
through the Lakebase migration and the grant and reconcile steps, but publish,
activation, signed capture, the signed-blue restore and the agentic
provisioning, cutover and evaluation still need the agentic identities (the
signed rollback record pins the Gateway resource contract). A core release
would therefore mutate the install through the grants and then stop at step 5
on every attempt, so it refuses instead, after every read-only check (the live
checks, the pre-stop probes in their core mode and the bundle plan gate) and
before the lease, in a dry run too. `--phase plan` and the adopt phases run in
the core tier, over an existing App as well. The step-5 stop stays as that
refusal's defence in depth. `tests/unit/test_core_tier_release_sandbox.py`
pins the refusal, and, with the refusal excised from a sandbox clone, runs the
sites behind it for real with the four core identities. Building the core
activation path, and with it a core release, is a follow-up.

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
   before it pins and refuses with nothing changed if the digest moved.
4. **More than one.** Refused as ambiguous.

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
environment or `.env.local`, exits 2. The stop branches that ran the
foreign-catalog remediation are gone; its tooling is deleted with M4-PR2.

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
through the pre-lease gate and exits before the lease. A live plan phase is
CI-only (`plan is CI-only; use --dry-run or make bundle-validate`); locally,
`--phase plan --dry-run` prints the same steps without any Databricks call.

### Keep blue serving: the one bounded quiesce window (M0-PR8)

A release keeps the signed last-good App (blue) serving. It stops the App
only for a **window reason** the read-only plan predicted
(`tools/databricks/quiesce_window_plan.py`):

- **WR1** binding diff: the App's live resource bindings differ from the
  bundle's (any diff, until spike S2 proves an unchanged database binding
  leaves a running App and its role alone), or a binding id is unresolved;
- **WR2** Lakebase role rotation: `converge_lakebase_oauth_role --check`
  drifts for the App role, the AI Gateway verifier role or the jobs
  identity's `jobs_reader` role (M2-PR5), or a `noop` migration plan reads
  the jobs reader's grants as drift, an absent role included (a rotation
  drops the role's grants, which only the migration job re-applies, and that
  job runs only inside a window). Blue never reads as the jobs reader, so its
  re-grant keeps blue restorable. The first roll-forward that brings the
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
apply (`pre-apply`, the time-of-check recheck). Its private report is written
mode 0600 and its public line carries codes, booleans, counts and digests
only. A window opens only when `MIP_ACK_QUIESCE_WINDOW` equals the plan's
sha256 at both positions; that sha256 covers the reasons, blue's deployment,
its restore source and that folder's full-tree digest, the env-name set, the
role and jobs-reader grant states and the migration's compatibility, so the
opened reasons are exactly the predicted ones. Export the digest for that run only. It is a shell-only, one-run value,
never a `.env.local` line. (The owner hardware-key operation attestation the
plan names is deferred; this exact digest ack stands in for it.) No reason
needs no ack, and a reason that appears only at the recheck refuses.

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
are re-read under the lease and must still equal the bundle's), the App role
converger refuses any rotation the plan did not predict, the verifier and
jobs-reader roles are re-checked under the lease right before their converges
(drift exits 2, blue serving), and the migration job is skipped because the
pre-apply plan proved it a noop with a passing verify and the jobs reader's
grants exact.

**EXIT-trap states** (`stop_app_after_failed_deploy`): `blue_serving` never
stops the App and converges runtime access with it up; `blue_degraded` (blue
serving while its synced-catalog access or the reviewed functions are
mutated) also re-reconciles those; `window_open` starts and re-pins blue when
every proof holds, otherwise leaves the window to the dead-man;
`contract_activating` stops the App, never restores blue and retains the
lease. The rollback's
`ensure` and `restore` re-pin the same way when they start a stopped blue
(tier a, only while blue is the App's last deployment); `restore` redeploys
the signed snapshot (tier b) otherwise.

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
compensation are the only shell files that stop or start the App, and
`tools/databricks/stop_primitive_map.py` maps every SDK `apps.stop`, `start`,
`deploy` and `update` call in `tools/` (also through a name bound from
`.apps`) to the CLI modes that reach it; only those two files may call a
mutating mode, by `-m` or by its `tools/databricks/<module>.py` path (the mode
is read from that one command's own tokens), or import a stop module in a
heredoc or `-c`.

**Owed live confirmations.** Spike S7 (a throwaway App): that `apps start`
creates a new deployment from `default_source_code_path`, whether the env
config carries over, and the start of a FAILED-active App. Spike S2: whether
`apps update` with an unchanged database binding touches a running App or its
role. Until then the code assumes the worst case for both. The live shape of
`information_schema.routines.routine_definition` is also derived, not
captured; a reformatted body only ever means "run".

### The job budget and the dead-man (M0-PR9)

A runner that dies inside the window (a cancelled or timed-out job sends
SIGKILL about 10 s after SIGINT, and a lost runner sends nothing) would leave
the App stopped. Two controls bound that (threat T5).

**Budget admission** (`tools/databricks/quiesce_window_budget.py`). A window
opens only while the job's remaining budget covers it:

    MIP_JOB_DEADLINE_EPOCH - now >= predicted window + predicted restore + 15 min

The predicted window is the plan's `predicted_seconds` (stop, each reason's
step, then start and re-pin when blue is restorable; a contract window, which
stays open with the App stopped until green activation, adds that tail, the
reviewed `green_activation` budget). The predicted restore is
`predicted_restore_seconds`: the EXIT trap's tier-a start and re-pin after a
failure at the window's last mutation (start + re-pin budgets), and nothing for
a contract window, which restores nothing. Both, the 15-minute margin and their
sum are in the window's canonical body, so `MIP_ACK_QUIESCE_WINDOW` covers
them; a plan with no reason has no budget, and its body and digest are M0-PR8's.
The admission itself depends on the clock and is never in the digest: the plan
prints it (`admission <state>: <n> s left, <m> s required`) before the lease
and again under it, refusing `window_budget_refused`, and `quiesce_window open`
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
deployment id, so a recovering run that also needs a window refuses it at the
pre-apply recheck (`window_unacked`: the digest moved); the next run plans and
acks it afresh. A refusal exits 2 with nothing else run: the App stays exactly
as the dead-man left it, the journal open and the lease released, so the next
run retries a transient failure (a re-pin already signed resumes at the
journal close). A WR1 window restarted on the dead runner's applied bindings
re-pins only from that same commit, whose bundle payload is exactly those
bindings: re-run the dead window's commit, then roll forward. An App no longer
serving the dead-man's start (stopped or restarted by hand) needs the owner
break-glass below. Any other open journal still refuses before the lease
(`window_journal_open`): a contract window the dead-man left stopped, a start
it could not prove, or a dead-man that has not reached its deadline. That is
the owner break-glass case: a raw `apps start` with the owner's U2M profile,
then the next governed run re-pins (M2-PR9 documents it).

**The workflow.** `deploy-dev.yml` stays parked (M1-PR7). Its deploy job's
first step records `MIP_JOB_DEADLINE_EPOCH`, and its last step, `if: always()`,
runs the read-only `quiesce_window status --unverified` and records the
journal's and the dead-man's states as codes only. That step has no key
registry (deploy.sh derives the verify key inside its own process), so it
reads the raw journal, labelled unverified; `--unverified` refuses
`--out-env`, no deploy path passes it, and every decision reads the verified
journal. The emergency recipe never runs
`scripts/deploy.sh`, so it opens no window and arms no dead-man.

**Owed live confirmations.** Spike S7 (a throwaway App only the owner can
delete) also owes the dead-man's live facts: a one-time serverless run from a
`/Workspace/Users/.../app-src/<id>/jobs/` file with these parameters, how long
it takes to reach RUNNING and arm (`ARM_WAIT_S` assumes at most 600 s), its
idempotency token and timeout, `apps start` as the deployer on a stopped App,
and the cancel of a sleeping run. Until then the worst case is assumed: a start
creates a new deployment, and nothing is trusted without the re-proof above.

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

**Dashboards (attack O9).** Spike S4 (a dashboard `parent_path` move on
throwaway resources: update in place, or recreate?) has not run, because it
needs resources only the owner can delete. The worst case is assumed: a move
recreates the dashboard, losing its id, history and shares. So
`MIP_DASHBOARD_PARENT_PATH` stays pinned at the legacy dashboards' live folder
and is permanent for this install (the profile requires it inside
`MIP_LEGACY_BUNDLE_ROOT`; the adopt plan refuses a legacy dashboard outside it
and records `pinned_permanent`; the post-bind gate refuses a dashboard
recreate or move). The other branch, moving both dashboards into the
deployer's root and updating the pin with the state together, stays unbuilt
until S4 shows the move is an in-place update.

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
one, nothing is pending and the compute is ACTIVE (`deployment_unstable`); its
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
export and never a `.env.local` line. The bind's `MIP_ADOPT_PLAN_ACK` without
it refuses before the lease. The body names its purpose, so an ack for one
purpose never satisfies another, and whenever the slot holds a record the plan
names that exact signed version's sha256 (`record_sha256`), so an ack covers
one record version: the first unadopt's ack is refused after a re-adoption.
`apply` recomputes the plan immediately before its first write and refuses on
any difference. The `override_freeze` ack goes to the publisher as
`--override-freeze-ack`, and the `restore_unverified_frontend` ack to the one
restore that needs it; neither is an environment variable, and a refusal names
the route each really takes.

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
(`blue_tier_mismatch`); and `app_deployment_rollback
inspect` re-proves the record's own tier (core: the App identity and exactly
the record's core bindings, never the Gateway; agentic: v6 inspect's own
Gateway resource proof, its reviewed-function owner the signed payload's).
Owed to M2-PR7b, the core release path, and not claimed here: a capture
writer (whose tier must equal the profile's), the restore that deploys
`tier_b_payload`'s payload (with the restore-owned resource repair and, for
an agentic record, the Gateway proof), and a release that builds blue's
restore payload: `scripts/deploy.sh` passes no `--restore-payload` yet, so
over a v7 blue the only way through activation is the exact `no_rollback_for`
ack, and today's core builder adds the two switches as `0`, which D0's
env-name set lacks.

**Spike S7 is owed** (whether the Apps update API can set
`default_source_code_path`, and how an App start snapshots source). The worst
case holds: the App's default source is never pointed at the copy.
`S7_DEFAULT_SOURCE_SETTABLE` in `adopt_live.py` is the reviewed switch a later
PR flips with the recorded S7 result (a code constant, never an environment
variable); until then the switch refuses.

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
deployment. Building the restore payload in the release and the core release
path itself are M2-PR7b's; the core release still refuses before the lease.

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
`--verify` is read-only: nothing left to move, and every pipeline dataset
paired exactly. `--interim-days N` (1 to 14, default 14) sets the suggested
pairs' expiry.

**Pipeline-managed datasets.** Spike S4 (does `ALTER OWNER` on a
pipeline-published streaming table or materialized view hold, and survive the
pipeline's run_as change?) has not run, so they are excluded. The private
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
Unset the field after the pipeline update or a journaled re-own moves the
datasets.

**Lakebase (step 8).** After the deployer's Postgres role exists (by the
mechanism the S3 rehearsal picks), run as the legacy owner:

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
(`caller_needs_createdb`). Which mechanism grants them is spike S3's; the
rehearsal reads the same preconditions first. The apply moves
the App database and each `mip_app` object the legacy role owns, one
`ALTER ... OWNER TO` per transaction under `SET LOCAL lock_timeout = '3s'`
and `SET LOCAL statement_timeout = '30s'`. `ALTER OWNER` takes an ACCESS
EXCLUSIVE lock, and App queries queue behind a waiting one, so the lock
timeout is what bounds an App query's wait. A lock timeout rolls back and
retries after a backoff, at most 10 times per object and 10 minutes in all.
It never creates, drops or reassigns a role and emits no DROP, DELETE,
TRUNCATE, GRANT or REVOKE. When the budget runs out it stops with nothing
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
  budget alone is 600 s); spikes S2 and S7 owe the live timings. The release
  window (WR1-WR4) never plans WR5, and its digests are unchanged.
  `--remainder --journal <path>` prints, read-only, the exhausted journal's
  remainder count and the digest the window plan pins.
- **Revert**: `--revert --journal <path>`, the exact inverse by the same lock
  bounds.

`--retire-check [--legacy-identity <name>]` (also `python -m
tools.clients.handover_verify retire-check`; the default identity is the
caller's) is the read-only gate before the owner removes the legacy role by
hand: it refuses while any job or pipeline still runs as the legacy identity,
judged by its effective run_as (each job read by id; never its creator), or
while one's run_as cannot be read. `--rehearse --rehearse-instance <throwaway> --rehearse-database
<db>` is spike S3's mode (owner-run, refused on the install's own instance):
it creates one table on the throwaway, holds a concurrent long transaction on
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
whoever can set a deploy's configuration can acknowledge a record, and no
workflow carries it through a GitHub Environment approval yet
(`deploy-dev.yml` is parked until a client environment is selected, M1-PR7).
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
  deployer). S4's live half, ALTER OWNER on the pipeline's published
  datasets after a run_as change, stays with the cutover.
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
* It adds only what the identity lacks, by the permissions API's PATCH, then
  re-reads every object. It never removes or lowers a grant, so the App's
  `CAN_MANAGE_RUN` bindings, the verifier's warehouse `CAN_USE` and other
  teams' grants stay. Bundle `permissions:` blocks would set each object's
  whole ACL on every deploy and drop those, which is why this is a deploy
  step.
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

## CI deploy (deploy-dev.yml): parked, and the release-gate bootstrap

Decision D-platform-process-b step 7 (audit quality-03, report 12.4 #8). The
release gate above needs one green `live validation (on demand)` run on
`main`; its 120-day lookback from the last green run (2026-06-16) ended on
2026-10-14, so the gate reads `no_green_run` until a CI install is green
again.

**Status: parked until a client environment is selected (M1-PR7).** The CI
staging install this section bootstrapped lived in its own workspace, which
was deleted on 2026-10-29. Both CI workflows now refuse up front: the first
job of `deploy-dev.yml` and of `nightly.yml`, `parked`, fails every dispatch,
and every other job needs it, so no job requests the environment approval or
an ID token, or reaches a workspace. `scripts/deploy.sh` repeats the refusal
for the `deploy-dev.yml` workflow ref under GitHub Actions, before any
Databricks call and in a dry run too; an operator run and CI's own pytest are
unchanged. M1-PR7 rebuilds `deploy-dev.yml` per client environment, and
M1-PR8 rebuilds `nightly.yml` per client under a non-admin validator
identity. M1-PR2 replaced the bundle's `dev` and `prod` targets with the one
host-less `client` target, rooted at the client profile's deployer and client
key.

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
  workflow now passes `MIP_REMEDIATE_FOREIGN_CATALOG_BINDINGS=0` as a
  literal, and the remediation has no CI command of record;
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
deploys as a dedicated account service principal, never a person and never
one of the seven reserved identities. It has no OAuth secret and no personal
access token: it authenticates only through GitHub Actions OIDC workload
identity federation. Its federation policy trusts issuer
`https://token.actions.githubusercontent.com` and subject
`repo:<owner>/<repo>:environment:dev`, the default GitHub OIDC subject of a
job bound to this repository's `dev` environment, whose required reviewer and
branch policy gate every run; never widen it to a branch, a ref or
`repo:<owner>/<repo>:*`. The owner creates and federates that service
principal outside this repository until M2-PR1's client bootstrap does it.

- The `deploy` job alone holds `id-token: write` (the workflow default stays
  `contents: read`). The Configure step writes the `DEFAULT` profile as
  `host`, `auth_type = github-oidc` and `client_id` from the `dev`
  environment variable `MIP_CI_DEPLOYER_CLIENT_ID`. It refuses a deployer id
  equal to any app-facing client id or to the account-SCIM client. The SDK
  and the CLI exchange the job's GitHub ID token for a short-lived Databricks
  token as that service principal; nothing is stored.
- "Prove the GitHub OIDC deployer identity" runs next, read-only, before
  `scripts/deploy.sh`. Its profile and host checks run first and stop the run
  before any network call. The profile must hold only those three keys, with
  no ambient `DATABRICKS_HOST`, `_TOKEN`, `_AUTH_TYPE`, `_CLIENT_ID`,
  `_CLIENT_SECRET` or `_CONFIG_FILE` override, and its host must equal both
  `DATABRICKS_HOST` and the bundle target's `workspace.host` (the `dev`
  target's until M1-PR7). The step then proves, through the Python SDK and
  the Go CLI, that the profile authenticates as `MIP_CI_DEPLOYER_CLIENT_ID`,
  that this principal is in `admins` and owns the current metastore, and that
  `DATABRICKS_WAREHOUSE_ID` resolves (the Genie space is no input since
  M1-PR3; the deploy resolves it by its pin). The metastore-owner
  check presumed the cancelled metastore transfer; M1-PR7 replaces it. The
  step prints booleans only (a failed call adds its exception class or the
  CLI's exit code, never a message or a value) and exits 2 on any mismatch.
- Under `github-oidc` an environment `DATABRICKS_CLIENT_ID` overrides the
  profile's `client_id`, so no deployer step lets one reach a Databricks
  client. The deploy step carries the App's client pairs, which
  `scripts/deploy.sh` un-exports before its first child process.
- The deploy step sets `MIP_TEMP_CREDENTIAL_PLANE=workspace`. The deploy's
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
  `DATABRICKS_ACCOUNT_CLIENT_ID`/`_SECRET` pair.

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
| `audit_agent_runtime_foreign_uc_access` (step 4 before the Lakebase bootstrap, step 10b before runtime-owned UC mutations, and after any foreign-catalog remediation) | every catalog in the metastore except this install's, read-only | fails closed on any agent-runtime privilege there (a runtime shared with the live install holds the live catalog's grants) and on any foreign catalog granting to `account users` (owner step 7) |

So each install needs its own seven identities (owner step 1), and a second
install in the live install's metastore still meets the metastore-wide UC
audit: every `OPEN` catalog, the live install's included, must grant the
runtime nothing, and every catalog `ISOLATED` away from the install's
workspace must match `MIP_UC_FOREIGN_CATALOG_BINDING_POLICY` exactly (owner
step 7).

**The App release folder on a CI install.** The release lives at
`/Workspace/Users/<deployer-application-id>/.bundle/mortgage-intelligence-platform/<target>/app-src/<release id>`
("Governed per-release App source"). `deploy-dev.yml` needs no change for it:
it already sets up Node 22, which the build and the `.br` check use, and
installs `requirements.txt`, which brings the SDK; a shallow checkout is
enough for `git archive`. Publish, converge and prune each validate the
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
   `nightly.yml` reads them, and `deploy-dev.yml` reads them wherever its
   `dev` environment does not shadow them (owner step 3). A live identity bootstrap therefore needs its own reviewed sink: another
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
3. Remove or re-set the environment-level `DATABRICKS_CLIENT_ID`
   (2026-07-20), which shadows the rotated repository-level pair
   (2026-07-26), and check that every environment-level `*_CLIENT_ID` pairs
   with its own secret. Re-mint the account-SCIM credential yourself. Create
   a new OAuth secret for the service principal `DATABRICKS_ACCOUNT_CLIENT_ID`
   names and pipe it straight into `gh secret set
   DATABRICKS_ACCOUNT_CLIENT_SECRET`; agents never handle the value. Then
   retire its older secrets unless something local still uses them. The
   deployer never takes this role: the account plane stays on
   `DATABRICKS_ACCOUNT_*`.
4. Set the CI install's `DATABRICKS_WAREHOUSE_ID` secret for its own
   workspace, never the live one. The proof step refuses it unless it
   resolves for the deployer. (`GENIE_SPACE_ID` is no deploy input since
   M1-PR3; `nightly.yml` still names it for its live checks.)
5. Point the REPOSITORY-level names `nightly.yml` reads (it has no
   `environment:`) at the same CI install as the CI environment: the
   variables `MIP_APP_NAME`, `MIP_DEFAULT_CATALOG`, `MIP_LAKEBASE_INSTANCE`,
   `MIP_LAKEBASE_SYNC_CATALOG`, `MIP_RUNTIME_SECRET_SCOPE`,
   `MIP_APP_ROLLBACK_SECRET_SCOPE`, `MIP_AGENT_PROXY_SECRET_SCOPE`,
   `MIP_GENIE_SPACE_NAME`, `MIP_AI_GATEWAY_TABLE_PREFIX` and
   `MIP_AI_GATEWAY_AGENT_EXPERIMENT_BASE` (and
   `MIP_AI_GATEWAY_AGENT_MODEL_FAMILY` unset or in that install's catalog),
   and the secrets `DATABRICKS_HOST`, `DATABRICKS_WAREHOUSE_ID` and
   `GENIE_SPACE_ID`. Only `deploy-dev.yml` (whose dev environment shadows the
   repository level) and `nightly.yml` read these names. Compare names and
   `updated_at` in the repository-level listings above against the dev
   environment's. If the repository-level values must keep naming the live
   install, nightly cannot certify the CI install: stop and decide before
   integrator step 5. The repository-level `DATABRICKS_TOKEN` is dead with
   the deleted CI workspace; do not mint another one. M1-PR8 gives nightly
   its own validator identity and federation subject. After the first green
   `deploy-dev.yml` run, set the repository variable
   `MIP_REVIEWED_FUNCTION_OWNER` to `<deployer-application-id>`, the reviewed
   functions' new owner, or nightly reports owner drift.
6. Keep `main` in the dev environment's deployment branch policy. A
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
   keys to `scripts/deploy.sh`.
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
   - **Binding policy (a GitHub-settings action, no UC change).** The audit
     accepts a catalog `ISOLATED` away from the CI install's workspace only
     if it matches the CI environment's `MIP_UC_FOREIGN_CATALOG_BINDING_POLICY`
     exactly (owner, catalog type, isolation mode, bound workspace ids,
     binding types). Set the policy from a read-only inventory of their
     current state, never the other way round.
   - **Workspace-scoped, in the CI install's workspace only:** Genie spaces
     that grant a group the CI identities belong to, such as workspace
     `users` (the runtime, verifier and App Genie audits fail on any group
     grant).

   The foreign-catalog binding remediation sets catalogs ISOLATED and
   rewrites their workspace bindings metastore-wide, and between its isolate
   and re-bind calls (or after a killed run) every other workspace loses
   those catalogs, the live install's share catalogs included.
   `deploy-dev.yml` has no input that requests it.
8. Once M1-PR7 lifts the parked refusal, dispatch `deploy-dev.yml` on `main`
   with `acknowledge_single_maintainer_break_glass=true` (or approve that
   exact dispatch in chat), then approve the pending deployment. If an App
   with `MIP_APP_NAME` already exists in that workspace with no signed
   rollback record, set `rebase_unverified_app=true` on this first dispatch
   only; without it the run fails closed after the treatment quiesce and
   leaves that App stopped.

**Integrator steps.**

1. Freeze merges and pause piecewise deploys.
2. Read-only render: a plain `python -m tools.databricks.bundle_env validate
   -t client` with the client profile shows the client root and today's
   resource names (`tests/unit/test_client_install_dry_run.py` pins both for a
   client-#1-shaped profile).
3. Dispatch `deploy-prod.yml` on `main` and record the expected FAIL
   (`no_green_run`).

Steps 4-6 run only after M1-PR7 selects a client environment and owner steps
1-8 hold for it, never against the live install.

4. After the owner's dispatch and approval (owner step 8), watch
   `deploy-dev.yml` to green.
5. Confirm owner step 5 (names and `updated_at` only: the repository-level
   variables and secrets `nightly.yml` reads name the CI install), then
   dispatch `nightly.yml` on the same sha and watch it. It needs M1-PR8
   first: until then its `parked` job refuses.
6. Repair stale `frontend/tests/e2e/real_data.spec.ts` assertions in a PR if
   it is red, then repeat 4-5; record the green run URL in the audit
   report; re-dispatch `deploy-prod.yml` and expect PASS.
