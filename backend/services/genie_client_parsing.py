"""Pure parsing helpers for the stdlib Genie Conversation API client.

Split out of ``backend.services.genie_client`` to keep that module under the
file-size gate. These functions have no HTTP/client state -- they turn raw
Genie message/attachment JSON into normalized Python values:

- UC three-part reference collection (``_collect_uc_refs``) used to derive
  trusted assets from arbitrary Genie metadata.
- ``enable_visualization`` attachment parsing: suggested follow-up questions
  (``_parse_suggested_questions``) and the native-visualization reference
  (``_parse_native_visualization``).
"""
from __future__ import annotations

import hashlib
import re
from typing import Any

_UC_REF_RE = re.compile(
    r"\b([A-Za-z_][\w-]*)\s*\.\s*([A-Za-z_][\w-]*)\s*\.\s*([A-Za-z_][\w-]*)\b"
)

# Genie enhancement (2026-07): `enable_visualization` turns can attach a
# native-visualization reference plus model-suggested follow-up questions.
# We cap the suggestions defensively so a runaway attachment can't bloat the
# response body, and length-cap each string so a single suggestion stays a
# short prompt, not a paragraph.
_GENIE_MAX_SUGGESTED_QUESTIONS = 5
_GENIE_SUGGESTED_QUESTION_MAX_LEN = 200


def _question_hash(q: str) -> str:
    """SHA1 prefix of the question text.

    Genie questions are user-authored natural language and may contain
    borrower or property details; we log the hash, not the text, so
    operators can group "all instances of the same question" for
    latency analysis without leaking content.
    """
    return hashlib.sha1(q.encode("utf-8")).hexdigest()[:16]  # noqa: S324 -- not a secret


def _normalise_uc_ref(raw: str) -> str:
    return raw.replace("`", "").replace(" ", "").lower()


def _collect_uc_refs(value: Any) -> list[str]:
    """Collect three-part UC refs from arbitrary Genie metadata.

    The Genie API can declare trusted data sources inside query text,
    natural-language source notes, `query.parameters`, or exposed query
    thoughts. This helper is intentionally generic so a minor response-shape
    change does not turn a valid answer into `policy_blocked`.
    """
    refs: list[str] = []
    if value is None:
        return refs
    if isinstance(value, str):
        for match in _UC_REF_RE.finditer(value):
            ref = _normalise_uc_ref(".".join(match.groups()))
            if ref not in refs:
                refs.append(ref)
        return refs
    if isinstance(value, dict):
        for child in value.values():
            _extend_unique(refs, _collect_uc_refs(child))
        return refs
    if isinstance(value, list | tuple | set):
        for child in value:
            _extend_unique(refs, _collect_uc_refs(child))
    return refs


def _extend_unique(target: list[str], values: list[str]) -> None:
    for value in values:
        if value not in target:
            target.append(value)


def _parse_suggested_questions(att: dict[str, Any]) -> list[str]:
    """Pull follow-up prompts from a ``suggested_questions`` attachment.

    Shape (2026-07 ``enable_visualization`` turns):
    ``{"suggested_questions": {"questions": [str, ...]}}``. Each question is
    stripped, length-capped, and deduped. Non-string / blank entries are
    dropped. Absent attachment -> empty list (older API shape).
    """
    block = att.get("suggested_questions")
    if not isinstance(block, dict):
        return []
    raw_questions = block.get("questions")
    if not isinstance(raw_questions, list):
        return []
    out: list[str] = []
    for raw in raw_questions:
        if not isinstance(raw, str):
            continue
        text = raw.strip()[:_GENIE_SUGGESTED_QUESTION_MAX_LEN].strip()
        if text and text not in out:
            out.append(text)
    return out


def _parse_native_visualization(att: dict[str, Any]) -> dict[str, Any] | None:
    """Pull the native-visualization reference from a ``viz`` attachment.

    Shape (2026-07 ``enable_visualization`` turns):
    ``{"attachment_id": str, "viz": {"query_attachment_id": str,
    "title": str?}}``. Returns ``{"attachment_id", "query_attachment_id",
    "title"}`` for the first viz attachment, or ``None`` when absent. The
    Beta download endpoint (``.../attachments/{id}/visualization``) is not
    called here -- it is not rolled out on this workspace (404).
    """
    viz = att.get("viz")
    if not isinstance(viz, dict):
        return None
    attachment_id = att.get("attachment_id")
    if not attachment_id:
        return None
    query_attachment_id = viz.get("query_attachment_id")
    title = viz.get("title")
    return {
        "attachment_id": str(attachment_id),
        "query_attachment_id": (
            str(query_attachment_id) if query_attachment_id else None
        ),
        "title": str(title) if title else None,
    }
