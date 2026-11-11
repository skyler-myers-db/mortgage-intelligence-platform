"""adopt-live's health-sha check in the checkouts actions/checkout v6 makes (B5), with real git.

The real ``adopt_live_facts.resolved_health_sha`` runs in clones of a small
file:// origin that copy checkout v6's two fetch paths (input-helper.ts,
git-source-provider.ts, ref-helper.ts, git-command-manager.ts):

* depth > 0: ``fetch --no-tags --prune --depth=<n> origin +<run commit>:refs/remotes/origin/main``.
  At depth 1 (every phase but adopt) the clone holds one commit, so any older
  health sha refuses, and even a targeted fetch of it cannot admit it: a
  shallow graft only removes parent links. At 2147483647 (git's
  INFINITE_DEPTH, the adopt phase) the clone is not shallow and holds exactly
  main's history, one ref and no tag.
* depth 0: ``+refs/heads/*`` and ``+refs/tags/*``, every branch and tag, what
  a full local clone holds too. It admits nothing main-only does not, prints
  every branch name, and lets a tag named like a short sha shadow that commit.

A sha main does not hold (a side branch, a squash-merged branch head, an
unknown sha) refuses ``health_sha_not_on_main`` in every checkout.
"""

from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

from tools.databricks import adopted_source_copy as source
from tools.databricks.adopt_live_facts import AdoptLiveRefused, Readers, resolved_health_sha

INFINITE_DEPTH = 2**31 - 1
MAIN_REF = "refs/remotes/origin/main"
NOT_IN_MAIN = "the health sha resolves to no single commit in this checkout's history of main"
NOT_AN_ANCESTOR = "the health sha is not an ancestor of main"
SHALLOW = "this checkout is shallow"


@pytest.fixture(autouse=True)
def _hermetic_git(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    """No user or system git config reaches these clones, nor adopt-live's own git calls."""

    monkeypatch.setenv("GIT_CONFIG_GLOBAL", "/dev/null")
    monkeypatch.setenv("GIT_CONFIG_NOSYSTEM", "1")
    monkeypatch.setenv("HOME", str(tmp_path))
    for name in ("AUTHOR", "COMMITTER"):
        monkeypatch.setenv(f"GIT_{name}_NAME", "t")
        monkeypatch.setenv(f"GIT_{name}_EMAIL", "t@example.invalid")


def _git(repo: Path, *args: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        ["git", "-C", str(repo), *args], check=True, capture_output=True, text=True
    )


def _out(repo: Path, *args: str) -> str:
    return _git(repo, *args).stdout.strip()


def _commit(repo: Path, name: str, text: str) -> str:
    (repo / name).write_text(text, encoding="utf-8")
    _git(repo, "add", "-A")
    _git(repo, "commit", "-q", "-m", text)
    return _out(repo, "rev-parse", "HEAD")


class Origin:
    """main c1-c2-c3-c4 (squash of sq's f1)-c5 (merge of mg's m1); s1 off c2; tag c1[:12] at c3."""

    def __init__(self, root: Path) -> None:
        repo = root / "origin"
        repo.mkdir()
        _git(repo, "init", "-q", "-b", "main")
        _git(repo, "config", "uploadpack.allowReachableSHA1InWant", "true")
        self.c1 = _commit(repo, "app.yaml", "c1")
        self.c2 = _commit(repo, "app.yaml", "c2")
        _git(repo, "checkout", "-q", "-b", "side")
        self.s1 = _commit(repo, "side.txt", "s1")
        _git(repo, "checkout", "-q", "main")
        self.c3 = _commit(repo, "app.yaml", "c3")
        _git(repo, "checkout", "-q", "-b", "sq")
        self.f1 = _commit(repo, "sq.txt", "f1")
        _git(repo, "checkout", "-q", "main")
        _git(repo, "merge", "-q", "--squash", "sq")
        _git(repo, "commit", "-q", "-m", "squash sq (#1)")
        self.c4 = _out(repo, "rev-parse", "HEAD")
        _git(repo, "checkout", "-q", "-b", "mg")
        self.m1 = _commit(repo, "mg.txt", "m1")
        _git(repo, "checkout", "-q", "main")
        _git(repo, "merge", "-q", "--no-ff", "-m", "merge mg (#2)", "mg")
        self.c5 = _out(repo, "rev-parse", "HEAD")
        _git(repo, "tag", self.c1[:12], self.c3)
        self.url = repo.as_uri()
        self.root = root

    def checkout(self, name: str, depth: int) -> tuple[Path, str]:
        """actions/checkout v6 of ``main`` at c5, the run's commit: (clone, the fetch's log)."""

        clone = self.root / name
        clone.mkdir()
        _git(clone, "init", "-q")
        _git(clone, "remote", "add", "origin", self.url)
        fetch = [
            "-c",
            "protocol.version=2",
            "fetch",
            "--no-tags",
            "--prune",
            "--no-recurse-submodules",
        ]
        if depth > 0:
            log = _git(clone, *fetch, f"--depth={depth}", "origin", f"+{self.c5}:{MAIN_REF}").stderr
        else:
            log = _git(
                clone,
                *fetch,
                "origin",
                "+refs/heads/*:refs/remotes/origin/*",
                "+refs/tags/*:refs/tags/*",
            ).stderr
            assert (
                _out(clone, "rev-parse", MAIN_REF) == self.c5
            ), "checkout re-pins a moved ref; here none moved"
        _git(clone, "checkout", "-q", "--force", "-B", "main", MAIN_REF)
        return clone, log


@pytest.fixture
def origin(tmp_path: Path) -> Origin:
    return Origin(tmp_path)


def _readers(clone: Path, health: str, *, source_sha: str) -> Readers:
    return Readers(
        health_sha=lambda: health,
        treatment=lambda *_: [],
        repo=clone,
        main_ref=MAIN_REF,
        source_sha=source_sha,
    )


def _resolve(clone: Path, health: str, *, source_sha: str) -> str:
    return resolved_health_sha(_readers(clone, health, source_sha=source_sha))


def _refused(clone: Path, health: str, *, source_sha: str) -> AdoptLiveRefused:
    with pytest.raises(AdoptLiveRefused) as refused:
        _resolve(clone, health, source_sha=source_sha)
    return refused.value


def test_a_depth_1_checkout_refuses_every_older_main_commit_and_says_it_is_shallow(
    origin: Origin,
) -> None:
    clone, _log = origin.checkout("depth-1", 1)

    assert _out(clone, "rev-parse", "--is-shallow-repository") == "true"
    assert (
        _resolve(clone, origin.c5[:12], source_sha=origin.c5) == origin.c5
    ), "only the run's own commit"
    for health in (origin.c1, origin.c1[:12], origin.m1):
        refused = _refused(clone, health, source_sha=origin.c5)
        assert refused.code == "health_sha_not_on_main"
        assert str(refused).startswith(SHALLOW) and "full clone" in str(refused)


def test_fetching_the_health_commit_into_a_shallow_checkout_still_refuses(origin: Origin) -> None:
    """A shallow graft only removes parent links: the object resolves, its ancestry does not."""

    clone, _log = origin.checkout("depth-1-plus-health", 1)
    _git(clone, "fetch", "-q", "--no-tags", "--depth=1", "origin", origin.c1)

    assert _out(clone, "cat-file", "-t", origin.c1) == "commit"
    refused = _refused(clone, origin.c1, source_sha=origin.c5)
    assert refused.code == "health_sha_not_on_main" and str(refused).startswith(SHALLOW)


def test_the_adopt_phases_main_only_full_history_admits_exactly_mains_commits(
    origin: Origin,
) -> None:
    clone, log = origin.checkout("main-full-history", INFINITE_DEPTH)

    assert _out(clone, "rev-parse", "--is-shallow-repository") == "false"
    assert _out(clone, "for-each-ref", "--format=%(refname)").splitlines() == [
        "refs/heads/main",
        MAIN_REF,
    ]
    assert not any(
        f"origin/{branch}" in log for branch in ("side", "sq", "mg")
    ), "no other branch is named"
    for health, commit in (
        (origin.c1, origin.c1),
        (origin.c1[:12], origin.c1),  # no tag was fetched to shadow it
        (origin.c3, origin.c3),
        (origin.m1, origin.m1),  # a branch head main merged with a merge commit
        (origin.c5, origin.c5),
    ):
        assert _resolve(clone, health, source_sha=origin.c5) == commit
    assert source.app_yaml_equal(
        {"app.yaml": b"c1"}, repo=clone, commit=origin.c1
    ), "the tree at the sha reads"
    for health in (
        origin.s1,
        origin.f1,
        "f" * 40,
    ):  # a side branch, a squash-merged branch head, unknown
        refused = _refused(clone, health, source_sha=origin.c5)
        assert (refused.code, str(refused)) == ("health_sha_not_on_main", NOT_IN_MAIN)


def test_a_full_clone_of_every_branch_and_tag_admits_nothing_more_and_a_tag_can_shadow(
    origin: Origin,
) -> None:
    """Depth 0, or an operator's full local clone: the reason the adopt phase is main-only."""

    clone, log = origin.checkout("all-heads-and-tags", 0)

    assert _out(clone, "rev-parse", "--is-shallow-repository") == "false"
    assert "origin/side" in log, "depth 0 names every branch in the job's log"
    assert _resolve(clone, origin.c1, source_sha=origin.c5) == origin.c1
    assert _resolve(clone, origin.m1, source_sha=origin.c5) == origin.m1
    for health in (origin.s1, origin.f1):  # present now, but still off main
        refused = _refused(clone, health, source_sha=origin.c5)
        assert (refused.code, str(refused)) == ("health_sha_not_on_main", NOT_AN_ANCESTOR)
    shadowed = _refused(clone, origin.c1[:12], source_sha=origin.c5)
    assert (shadowed.code, str(shadowed)) == (
        "health_sha_not_on_main",
        NOT_IN_MAIN,
    ), "the tag names c3"
    unknown = _refused(clone, "f" * 40, source_sha=origin.c5)
    assert (unknown.code, str(unknown)) == ("health_sha_not_on_main", NOT_IN_MAIN)
    assert _refused(clone, origin.c1, source_sha=origin.s1).code == "source_not_on_main"
