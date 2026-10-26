"""tools/refactor_proof.py css: the cascade-layer forms (css-05).

The layering commit proves itself with ``refactor_proof.py css
--strip-layers``: an entry whose imports carry ``layer(name)`` must expand to
the original, and a sheet wrapped in place in ``@layer name { ... }`` blocks
(including a mode block split out in place) must reduce to its original byte
for byte. The last cases are the non-vacuity controls: an edited declaration
inside a wrapper, or a wrapper closer that also carries a rule, fails.
"""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
TOOL = REPO / "tools" / "refactor_proof.py"

ORIGINAL = """/* header */
.a { color: red; }
@media (max-width: 900px) {
  .a { color: blue; }
}
@media (forced-colors: active) {
  .a { color: CanvasText; }
}
.b { color: green; }
"""


def _proof(tmp_path: Path, original: str, files: dict[str, str], *flags: str) -> subprocess.CompletedProcess[str]:
    (tmp_path / "original.css").write_text(original, encoding="utf-8")
    for name, text in files.items():
        (tmp_path / name).write_text(text, encoding="utf-8")
    return subprocess.run(
        [sys.executable, str(TOOL), "css", str(tmp_path / "original.css"), str(tmp_path / "entry.css"), *flags],
        capture_output=True,
        text=True,
        check=False,
    )


def test_both_import_forms_expand_in_place(tmp_path: Path) -> None:
    files = {
        "entry.css": '/* entry */\n@import "./one.css" layer(components);\n@import "./two.css";\n',
        "one.css": ".one { color: red; }\n",
        "two.css": ".two { color: blue; }\n",
    }
    result = _proof(tmp_path, "/* entry */\n.one { color: red; }\n.two { color: blue; }\n", files)
    assert result.returncode == 0, result.stdout + result.stderr
    assert "slices: 2" in result.stdout


def test_an_import_layer_needs_no_strip(tmp_path: Path) -> None:
    files = {"entry.css": '@import "./one.css" layer(modes);\n', "one.css": ".one { color: red; }\n"}
    result = _proof(tmp_path, ".one { color: red; }\n", files, "--strip-layers")
    assert result.returncode == 0, result.stdout + result.stderr


def test_a_wrapper_holding_media_braces_strips_to_the_original(tmp_path: Path) -> None:
    wrapped = ORIGINAL.replace("/* header */\n", "/* header */\n@layer routes {\n", 1) + "}\n"
    result = _proof(tmp_path, ORIGINAL, {"entry.css": wrapped}, "--strip-layers")
    assert result.returncode == 0, result.stdout + result.stderr


def test_a_mode_block_split_in_place_strips_to_the_original(tmp_path: Path) -> None:
    wrapped = (
        "/* header */\n@layer routes {\n.a { color: red; }\n@media (max-width: 900px) {\n  .a { color: blue; }\n}\n}\n"
        "@layer modes {\n@media (forced-colors: active) {\n  .a { color: CanvasText; }\n}\n}\n"
        "@layer routes {\n.b { color: green; }\n}\n"
    )
    result = _proof(tmp_path, ORIGINAL, {"entry.css": wrapped}, "--strip-layers")
    assert result.returncode == 0, result.stdout + result.stderr


def test_the_layer_statement_line_strips(tmp_path: Path) -> None:
    files = {"entry.css": "/* manifest */\n@layer reset, tokens, base, components, routes, modes, print;\n"}
    result = _proof(tmp_path, "/* manifest */\n", files, "--strip-layers")
    assert result.returncode == 0, result.stdout + result.stderr


def test_without_the_flag_a_wrapped_sheet_differs(tmp_path: Path) -> None:
    wrapped = "@layer routes {\n" + ORIGINAL + "}\n"
    result = _proof(tmp_path, ORIGINAL, {"entry.css": wrapped})
    assert result.returncode == 1
    assert "expansion differs" in result.stderr


def test_one_edited_declaration_inside_a_wrapper_fails(tmp_path: Path) -> None:
    wrapped = "@layer routes {\n" + ORIGINAL.replace("color: green", "color: lime") + "}\n"
    result = _proof(tmp_path, ORIGINAL, {"entry.css": wrapped}, "--strip-layers")
    assert result.returncode == 1
    assert "expansion differs" in result.stderr


def test_a_closer_sharing_its_line_with_a_rule_fails(tmp_path: Path) -> None:
    wrapped = "@layer routes {\n" + ORIGINAL.rstrip("\n") + " }\n"
    result = _proof(tmp_path, ORIGINAL, {"entry.css": wrapped}, "--strip-layers")
    assert result.returncode == 1
    assert "closes on a line with other text" in result.stderr


def test_a_class_only_in_the_entry_text_counts_in_the_inventory(tmp_path: Path) -> None:
    files = {
        "entry.css": '@import "./fonts.css";\n.keep { color: red; }\n',
        "fonts.css": "@font-face { font-family: 'X'; }\n",
    }
    result = _proof(tmp_path, "@font-face { font-family: 'X'; }\n.keep { color: red; }\n", files)
    assert result.returncode == 0, result.stdout + result.stderr
