"""A pytest plugin that records how each test's whole run changed os.environ.

Load it with ``-p tests.fixtures.env_leak_probe`` and name an output file in
``MIP_ENV_LEAK_PROBE_OUT``. It writes one JSON line per test: the names the
test's setup, call and teardown together added, removed or changed. An empty
record is a test that left os.environ exactly as it found it.
tests/unit/test_env_leak_regressions.py runs it in a child pytest.
"""

from __future__ import annotations

import json
import os
from collections.abc import Iterator

import pytest

OUT_ENV = "MIP_ENV_LEAK_PROBE_OUT"
# Read once: a test under probe may strip MIP_ names from os.environ.
_OUT = os.environ.get(OUT_ENV, "")


@pytest.hookimpl(hookwrapper=True)
def pytest_runtest_protocol(item: pytest.Item, nextitem: pytest.Item | None) -> Iterator[None]:
    before = dict(os.environ)
    yield
    after = dict(os.environ)
    record = {
        "test": item.nodeid,
        "added": sorted(after.keys() - before.keys()),
        "removed": sorted(before.keys() - after.keys()),
        "changed": sorted(name for name in after.keys() & before.keys() if after[name] != before[name]),
    }
    with open(_OUT, "a", encoding="utf-8") as out:
        out.write(json.dumps(record) + "\n")
