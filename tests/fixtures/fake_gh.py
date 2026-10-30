"""A fake ``gh`` on PATH for the GitHub-environment tool and sink tests.

``install_fake_gh(tmp_path)`` writes an executable ``gh`` (a small Python
program, ``_PROGRAM``) into ``tmp_path/bin`` and a JSON "GitHub" state file
beside it. The program emulates exactly the gh surface the tools use:

* ``auth login`` (writes ``hosts.yml`` into ``GH_CONFIG_DIR``) and ``auth status``;
* ``api --method {GET,PUT,POST,DELETE}`` on ``user``, the repository's
  environments and their deployment branch policies, a body on stdin
  (``--input -``);
* ``secret list|set|delete --env`` and ``variable list|set --env``, a value on stdin.

**The recorder.** Every call appends one JSON line to ``calls.jsonl``: argv,
the sha256 and length of stdin (never stdin itself), the GitHub names in the
child environment, and whether the call mutates. A secret is stored as the
sha256 of its value, so no value the tools write lands on disk here.

**Refusals the fake enforces** (exit 97, recorded as a violation): ``--repo``
or ``-R`` in argv, any GitHub token variable in the child environment, a
missing ``GH_CONFIG_DIR``, a ``GH_REPO`` other than the repository, and a
call before the login.
"""

from __future__ import annotations

import json
import os
import stat
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from tests.fixtures.fake_databricks_account import REPOSITORY

MAINTAINER = {"login": "acme-maintainer", "id": 4242}
SESSION_TOKEN = "gho_fake-session-token-canary-0000"

_PROGRAM = r'''
import base64, hashlib, json, os, sys

STATE_PATH = os.environ.get("FAKEGH_STATE", "")
LOG_PATH = os.environ.get("FAKEGH_LOG", "")
TOKENS = ("GH_TOKEN", "GITHUB_TOKEN", "GH_ENTERPRISE_TOKEN", "GITHUB_ENTERPRISE_TOKEN")
MUTATING_METHODS = {"PUT", "POST", "DELETE", "PATCH"}


def load():
    with open(STATE_PATH, encoding="utf-8") as handle:
        return json.load(handle)


def save(state):
    with open(STATE_PATH, "w", encoding="utf-8") as handle:
        json.dump(state, handle, indent=1, sort_keys=True)


def flag(argv, name):
    return argv[argv.index(name) + 1] if name in argv else None


def tick(state):
    state["clock"] += 1
    return "2026-10-30T%02d:%02d:%02dZ" % (state["clock"] // 3600, state["clock"] // 60 % 60, state["clock"] % 60)


def public(env):
    return {key: value for key, value in env.items() if key not in {"branch_policies", "secrets", "variables"}}


def is_ed25519(value):
    try:
        return len(base64.urlsafe_b64decode(value + b"=" * (-len(value) % 4))) == 32 and b"=" not in value
    except Exception:
        return False


def main():
    argv = sys.argv[1:]
    state = load()
    config_dir = os.environ.get("GH_CONFIG_DIR", "")
    command = argv[:2]
    takes_stdin = command in (["secret", "set"], ["variable", "set"]) or "--input" in argv
    stdin = sys.stdin.buffer.read() if takes_stdin else b""
    mutating = (command in (["secret", "set"], ["secret", "delete"], ["variable", "set"])) or (
        command[:1] == ["api"] and flag(argv, "--method") in MUTATING_METHODS
    )
    record = {
        "argv": argv,
        "stdin_sha256": hashlib.sha256(stdin).hexdigest() if stdin else None,
        "stdin_len": len(stdin),
        "tokens": sorted(name for name in TOKENS if name in os.environ),
        "env": {name: os.environ.get(name) for name in ("GH_CONFIG_DIR", "GH_REPO", "HOME", "GH_PROMPT_DISABLED")},
        "gh_names": sorted(name for name in os.environ if name.startswith(("GH_", "GITHUB_"))),
        "config_mode": oct(os.stat(config_dir).st_mode & 0o777) if config_dir and os.path.isdir(config_dir) else None,
        "mutating": mutating,
    }
    with open(LOG_PATH, "a", encoding="utf-8") as handle:
        handle.write(json.dumps(record, sort_keys=True) + "\n")

    def violate(reason):
        state["violations"].append(reason)
        save(state)
        sys.exit(97)

    if "--repo" in argv or "-R" in argv or any(arg.startswith("--repo=") for arg in argv):
        violate("--repo in argv")
    if record["tokens"]:
        violate("a GitHub token variable reached gh")
    if not config_dir or not os.path.isdir(config_dir):
        violate("no GH_CONFIG_DIR")
    hosts = os.path.join(config_dir, "hosts.yml")
    for failure in list(state["fail"]):
        if failure["argv"] == argv[: len(failure["argv"])]:
            state["fail"].remove(failure)
            save(state)
            sys.exit(1)

    if command == ["auth", "login"]:
        if state.get("login_fails") or "--insecure-storage" not in argv or "--web" not in argv:
            sys.exit(1)
        if os.listdir(config_dir):
            violate("login into a non-empty config dir")
        with open(hosts, "w", encoding="utf-8") as handle:
            handle.write("github.com:\n    oauth_token: %s\n    user: %s\n" % (state["session_token"], state["maintainer"]["login"]))
        os.chmod(hosts, 0o600)
        state["logins"] += 1
        save(state)
        return
    if not os.path.isfile(hosts):
        sys.exit(4)
    if command == ["auth", "status"]:
        return
    if command[:1] != ["api"] and os.environ.get("GH_REPO") != state["repository"]:
        violate("GH_REPO is not the repository")

    envs = state["environments"]
    if command[:1] == ["api"]:
        method, path = flag(argv, "--method"), argv[3]
        body = json.loads(stdin) if stdin else None
        if path == "user":
            print(json.dumps(state["maintainer"]))
            return
        prefix = "repos/%s/" % state["repository"]
        if not path.startswith(prefix):
            sys.exit(1)
        parts = path[len(prefix):].split("?")[0].split("/")
        if parts == ["environments"] and method == "GET":
            listed = [public(env) for env in envs.values()]
            print(json.dumps({"total_count": len(listed), "environments": listed}))
            return
        if parts[0] != "environments" or len(parts) < 2:
            sys.exit(1)
        name = parts[1]
        if len(parts) == 2 and method == "PUT":
            env = envs.setdefault(name, {"name": name, "can_admins_bypass": True, "protection_rules": [], "deployment_branch_policy": None, "branch_policies": [], "secrets": {}, "variables": {}})
            reviewers = body.get("reviewers") or []
            env["protection_rules"] = [
                {"type": "required_reviewers", "prevent_self_review": bool(body.get("prevent_self_review")),
                 "reviewers": [{"type": r["type"], "reviewer": {"id": r["id"], "login": state["maintainer"]["login"] if r["id"] == state["maintainer"]["id"] else "someone"}} for r in reviewers]}
            ] if reviewers else []
            if "can_admins_bypass" in body and not state.get("ignore_can_admins_bypass"):
                env["can_admins_bypass"] = bool(body["can_admins_bypass"])
            env["deployment_branch_policy"] = body.get("deployment_branch_policy")
            save(state)
            print(json.dumps(public(env)))
            return
        env = envs.get(name)
        if env is None:
            sys.exit(1)
        if parts[2:] == ["deployment-branch-policies"] and method == "GET":
            print(json.dumps({"total_count": len(env["branch_policies"]), "branch_policies": env["branch_policies"]}))
            return
        if parts[2:] == ["deployment-branch-policies"] and method == "POST":
            if not (env["deployment_branch_policy"] or {}).get("custom_branch_policies"):
                sys.exit(1)
            state["next_id"] += 1
            env["branch_policies"].append({"id": state["next_id"], "name": body["name"], "type": body.get("type", "branch")})
            save(state)
            print(json.dumps(env["branch_policies"][-1]))
            return
        if len(parts) == 4 and parts[2] == "deployment-branch-policies" and method == "DELETE":
            env["branch_policies"] = [p for p in env["branch_policies"] if str(p["id"]) != parts[3]]
            save(state)
            return
        sys.exit(1)

    name = flag(argv, "--env")
    env = envs.get(name)
    if env is None:
        sys.exit(1)
    if command == ["secret", "list"]:
        print(json.dumps([{"name": key, "updatedAt": value["updatedAt"]} for key, value in sorted(env["secrets"].items())]))
    elif command == ["secret", "set"]:
        if not stdin:
            sys.exit(1)
        if state.get("drop_writes"):  # an acknowledged write the server never applied
            return
        env["secrets"][argv[2]] = {"sha256": hashlib.sha256(stdin).hexdigest(), "len": len(stdin), "ed25519": is_ed25519(stdin), "updatedAt": tick(state)}
        save(state)
    elif command == ["secret", "delete"]:
        env["secrets"].pop(argv[2], None)
        save(state)
    elif command == ["variable", "list"]:
        print(json.dumps([{"name": key, "value": value["value"]} for key, value in sorted(env["variables"].items())]))
    elif command == ["variable", "set"]:
        env["variables"][argv[2]] = {"value": stdin.decode("utf-8"), "updatedAt": tick(state)}
        save(state)
    else:
        sys.exit(1)


main()
'''


@dataclass
class FakeGitHub:
    root: Path

    @property
    def bin_dir(self) -> Path:
        return self.root / "bin"

    @property
    def state_path(self) -> Path:
        return self.root / "github-state.json"

    @property
    def log_path(self) -> Path:
        return self.root / "calls.jsonl"

    def environ(self, base: dict[str, str] | None = None) -> dict[str, str]:
        """An operator environment that finds the fake first and holds ambient tokens to scrub."""

        found = dict(os.environ if base is None else base)
        found.update(
            {
                "PATH": f"{self.bin_dir}{os.pathsep}{os.environ.get('PATH', '')}",
                "FAKEGH_STATE": str(self.state_path),
                "FAKEGH_LOG": str(self.log_path),
                "GH_TOKEN": "ambient-gh-token-canary",
                "GITHUB_TOKEN": "ambient-github-token-canary",
                "GH_REPO": "attacker/ambient",
                "GH_DEBUG": "api",
            }
        )
        return found

    def state(self) -> dict[str, Any]:
        return json.loads(self.state_path.read_text(encoding="utf-8"))

    def write(self, state: dict[str, Any]) -> None:
        self.state_path.write_text(json.dumps(state, indent=1, sort_keys=True), encoding="utf-8")

    def calls(self) -> list[dict[str, Any]]:
        if not self.log_path.exists():
            return []
        return [json.loads(line) for line in self.log_path.read_text(encoding="utf-8").splitlines() if line]

    def mutations(self) -> list[dict[str, Any]]:
        return [call for call in self.calls() if call["mutating"]]

    def clear_log(self) -> None:
        self.log_path.write_text("", encoding="utf-8")

    def environment(self, name: str) -> dict[str, Any]:
        return self.state()["environments"][name]

    def seed_environment(self, name: str, **fields: Any) -> None:
        state = self.state()
        env = {
            "name": name,
            "can_admins_bypass": True,
            "protection_rules": [],
            "deployment_branch_policy": None,
            "branch_policies": [],
            "secrets": {},
            "variables": {},
        }
        env.update(fields)
        state["environments"][name] = env
        self.write(state)

    def fail_next(self, *argv: str) -> None:
        state = self.state()
        state["fail"].append({"argv": list(argv)})
        self.write(state)


def install_fake_gh(root: Path) -> FakeGitHub:
    fake = FakeGitHub(root)
    fake.bin_dir.mkdir(parents=True)
    program = fake.bin_dir / "gh"
    program.write_text(f"#!{sys.executable} -I\n{_PROGRAM}", encoding="utf-8")
    program.chmod(program.stat().st_mode | stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH)
    fake.write(
        {
            "repository": REPOSITORY,
            "maintainer": dict(MAINTAINER),
            "session_token": SESSION_TOKEN,
            "environments": {},
            "violations": [],
            "fail": [],
            "clock": 0,
            "next_id": 100,
            "logins": 0,
        }
    )
    return fake
