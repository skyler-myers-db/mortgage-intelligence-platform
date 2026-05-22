# Release Hygiene

`make zip` creates `dist/mortgage-intelligence-platform.zip` from git-tracked
source using `git archive`. The package step removes local configuration,
workspace metadata, generated evidence, caches, rendered SQL, and test output,
then runs the release hygiene checker before returning success.

The source zip must not contain `.env.local`, `.databricks`, `.git`,
`.playwright-mcp`, `test-results`, `playwright-report`, `sql/_rendered`,
screenshots, traces, caches, or workspace metadata. `.env.example` is the only
env file allowed in the artifact.

Validate a package locally:

```bash
make zip
python tools/release_hygiene.py dist/mortgage-intelligence-platform.zip
```

Secret scanning is additive. CI scans the unpacked source artifact with
`gitleaks --no-git` when gitleaks is available in the workflow. Local operators
can run the same check after unzipping the artifact; missing local gitleaks
should not block source packaging validation.
