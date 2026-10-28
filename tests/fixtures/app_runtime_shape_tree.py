"""A minimal repo tree for pinning what each App read net sees.

The module under test is backend/services/fake.py, so ``parents[2]`` is the
repo root; backend/roots.py exports a root for the cross-module shapes. Each
shape reads something under docs/, which no closure entry covers, so a caught
read shows up as uncovered. The flow scan is tests/fixtures/app_runtime_reads.py
and the literal sweep is tests/fixtures/app_runtime_literals.py. Used by
tests/unit/test_app_runtime_read_shapes.py (what the nets catch) and
test_app_runtime_read_residuals.py (what they report as unhandled, and the
known residuals neither sees).
"""

from __future__ import annotations

import textwrap
from pathlib import Path

from tests.fixtures.app_runtime_literals import module_paths, sweep, top_level_names
from tests.fixtures.app_runtime_reads import ScanResult, outside_reads
from tools.databricks import app_runtime_closure as closure

ROOT_MODULE = "from pathlib import Path\n\nREPO_ROOT = Path(__file__).resolve().parents[1]\n"
JOB = (
    "from pathlib import Path\n\n"
    "def run():\n"
    '    return (Path(__file__).resolve().parents[1] / "docs" / "job.md").read_text()\n'
)


def make_tree(tmp_path: Path, source: str) -> Path:
    """Write the minimal tree with ``source`` as backend/services/fake.py."""

    files = {
        "backend/services/fake.py": "import os\nimport subprocess\nfrom pathlib import Path\n\n"
        + textwrap.dedent(source).strip() + "\n",
        "backend/roots.py": ROOT_MODULE,
        "tools/fake_tool.py": "thing = 1\n",
        "tools/pkg/__init__.py": "",
        "jobs/fake_job.py": JOB,
        "docs/README.md": "x\n",
    }
    for relpath, text in files.items():
        path = tmp_path / relpath
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text, encoding="utf-8")
    return tmp_path


def flow_reads(result: ScanResult) -> set[str]:
    """What the flow scan reports outside backend/ that no closure entry covers."""

    return {
        key for key in outside_reads(result) if closure.owning_entry(key, closure.CLOSURE) is None
    }


def literal_reads(root: Path, result: ScanResult) -> set[str]:
    """What the literal sweep flags in the modules the flow scan parsed."""

    literals = sweep(result.trees, top_level_names(root), modules=module_paths(root))
    return {literal.key() for literal in literals}
