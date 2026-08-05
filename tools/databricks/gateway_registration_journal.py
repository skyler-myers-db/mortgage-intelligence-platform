"""Append-only MLflow experiment-tag storage for Gateway registration journals."""

from __future__ import annotations

import hashlib
import re
import time
from collections.abc import Callable
from contextlib import suppress
from dataclasses import dataclass
from typing import Any

JOURNAL_TAG = "mip.gateway_registration_journal_v1"
JOURNAL_TAG_PREFIX = f"{JOURNAL_TAG}."
RETIREMENT_TAG_PREFIX = "mip.gateway_registration_retired_v1."
RETIREMENT_VALUE_PREFIX = "mip.gateway.registration.retired.v1:"
_DIGEST = re.compile(r"[0-9a-f]{64}\Z")


@dataclass(frozen=True)
class JournalTagState:
    """The single append-only journal admitted for an exact experiment."""

    value: str | None
    retired: bool


def journal_digest(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def journal_tag_key(value: str) -> str:
    return f"{JOURNAL_TAG_PREFIX}{journal_digest(value)}"


def retirement_tag_key(value: str) -> str:
    return f"{RETIREMENT_TAG_PREFIX}{journal_digest(value)}"


def retirement_tag_value(value: str) -> str:
    return f"{RETIREMENT_VALUE_PREFIX}{journal_digest(value)}"


def _experiment_tags(client: Any, *, experiment_id: str) -> dict[str, str]:
    experiment = client.get_experiment(experiment_id)
    if str(getattr(experiment, "experiment_id", "") or "").strip() != experiment_id:
        raise RuntimeError("MLflow returned an unexpected Gateway experiment identity")
    raw = getattr(experiment, "tags", None) or {}
    if not isinstance(raw, dict) or any(
        not isinstance(key, str) or not isinstance(value, str) for key, value in raw.items()
    ):
        raise RuntimeError("Gateway experiment tags have an invalid shape")
    return dict(raw)


def read_journal_tag_state(client: Any, *, experiment_id: str) -> JournalTagState:
    """Validate all reserved tags and return the experiment's sole journal."""

    journals: dict[str, str] = {}
    retirements: set[str] = set()
    for key, value in _experiment_tags(client, experiment_id=experiment_id).items():
        if key == JOURNAL_TAG:
            digest = journal_digest(value)
            if digest in journals and journals[digest] != value:
                raise RuntimeError("Gateway registration journal digest collision")
            journals[digest] = value
        elif key.startswith(JOURNAL_TAG_PREFIX):
            digest = key.removeprefix(JOURNAL_TAG_PREFIX)
            if _DIGEST.fullmatch(digest) is None or journal_digest(value) != digest:
                raise RuntimeError("Gateway registration journal tag identity drifted")
            if digest in journals and journals[digest] != value:
                raise RuntimeError("Gateway registration journal digest collision")
            journals[digest] = value
        elif key.startswith(RETIREMENT_TAG_PREFIX):
            digest = key.removeprefix(RETIREMENT_TAG_PREFIX)
            if _DIGEST.fullmatch(digest) is None or value != f"{RETIREMENT_VALUE_PREFIX}{digest}":
                raise RuntimeError("Gateway registration retirement tag is invalid")
            retirements.add(digest)
    if retirements - journals.keys():
        raise RuntimeError("Gateway registration retirement has no exact journal")
    if len(journals) > 1:
        raise RuntimeError("Gateway experiment contains multiple durable registration journals")
    if not journals:
        return JournalTagState(value=None, retired=False)
    digest, value = next(iter(journals.items()))
    return JournalTagState(value=value, retired=digest in retirements)


def persist_journal_tag(
    client: Any,
    *,
    experiment_id: str,
    value: str,
    attempts: int,
    interval_s: float,
    assert_single_writer: Callable[[], None],
) -> None:
    """Append one journal without overwriting any prior or concurrent state."""

    last_error: Exception | None = None
    assert_single_writer()
    for attempt in range(attempts):
        try:
            state = read_journal_tag_state(client, experiment_id=experiment_id)
        except Exception as exc:  # noqa: BLE001 - read failure must preserve the source
            last_error = exc
        else:
            if state.retired:
                raise RuntimeError("Gateway experiment journal is terminal and cannot be reused")
            if state.value == value:
                assert_single_writer()
                return
            if state.value is not None:
                raise RuntimeError("Gateway registration journal conflicts with another write")
            assert_single_writer()
            try:
                client.set_experiment_tag(experiment_id, journal_tag_key(value), value)
            except Exception as exc:  # noqa: BLE001 - response may be lost after commit
                last_error = exc
        try:
            state = read_journal_tag_state(client, experiment_id=experiment_id)
        except Exception as exc:  # noqa: BLE001 - ambiguity must preserve the source
            last_error = exc
        else:
            if not state.retired and state.value == value:
                assert_single_writer()
                return
            if state.value is not None or state.retired:
                raise RuntimeError("Gateway registration journal readback conflicted")
        if attempt + 1 < attempts:
            time.sleep(interval_s)
    detail = f": {last_error}" if last_error is not None else ""
    error = RuntimeError(f"Gateway registration journal readback was ambiguous{detail}")
    if last_error is not None:
        raise error from last_error
    raise error


def retire_journal_tag(
    client: Any,
    *,
    experiment_id: str,
    value: str,
    allow_absent: bool,
    attempts: int,
    interval_s: float,
    assert_single_writer: Callable[[], None],
) -> None:
    """Append exact retirement proof and require two authoritative readbacks."""

    assert_single_writer()
    state = read_journal_tag_state(client, experiment_id=experiment_id)
    if state.value == value and state.retired:
        return
    if state.value is None and allow_absent:
        return
    if state.value != value or state.retired:
        raise RuntimeError("Gateway registration journal drifted before retirement")
    assert_single_writer()
    with suppress(Exception):  # Lost response is resolved only by exact readback.
        client.set_experiment_tag(
            experiment_id,
            retirement_tag_key(value),
            retirement_tag_value(value),
        )
    confirmed = 0
    for attempt in range(attempts):
        state = read_journal_tag_state(client, experiment_id=experiment_id)
        if state.value == value and state.retired:
            confirmed += 1
            if confirmed == 2:
                assert_single_writer()
                return
        else:
            confirmed = 0
        if attempt + 1 < attempts:
            time.sleep(interval_s)
    raise RuntimeError("Gateway registration journal retirement was not authoritative")


def load_journal_tag_state(
    client: Any,
    *,
    experiment_id: str,
    attempts: int,
    interval_s: float,
) -> JournalTagState:
    """Read the sole journal, retaining retirement state for domain validation."""

    state = JournalTagState(value=None, retired=False)
    for attempt in range(attempts):
        state = read_journal_tag_state(client, experiment_id=experiment_id)
        if state.value is not None:
            return state
        if attempt + 1 < attempts:
            time.sleep(interval_s)
    return state
