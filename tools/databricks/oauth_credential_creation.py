"""Exact inventory reconciliation for one-shot OAuth credential creation."""

from __future__ import annotations

import time
from collections.abc import Callable, Iterable
from dataclasses import dataclass, field

from tools.databricks.oauth_credential_quarantine import (
    CredentialMutationContext,
    CredentialMutationIntent,
    CredentialMutationQuarantineError,
    begin_credential_mutation_session,
    raise_credential_quarantine,
)

_MAX_CREDENTIALS = 1000
_STABLE_OBSERVATIONS = 3
_MAX_STABILITY_OBSERVATIONS = 9
_STABILITY_INTERVAL_SECONDS = 0.5


@dataclass(frozen=True)
class ExactOAuthCredential:
    """One newly created credential and the inventory it must restore to."""

    credential_id: str
    secret: str = field(repr=False, compare=False, hash=False)
    before_ids: frozenset[str]
    intent: CredentialMutationIntent = field(repr=False, compare=False, hash=False)


def _field(value: object, name: str) -> str:
    raw = value.get(name) if isinstance(value, dict) else getattr(value, name, None)
    return str(getattr(raw, "value", raw) or "").strip()


def credential_ids(
    list_credentials: Callable[[], Iterable[object]],
    *,
    label: str,
) -> frozenset[str]:
    """Return one complete, duplicate-free immutable credential-ID snapshot."""

    try:
        credentials = tuple(list_credentials())
    except BaseException as exc:
        raise RuntimeError(f"{label} credential inventory could not be read") from exc
    if len(credentials) > _MAX_CREDENTIALS:
        raise RuntimeError(f"{label} credential inventory is unbounded")
    values = tuple(_field(item, "id") for item in credentials)
    if any(not value for value in values) or len(values) != len(set(values)):
        raise RuntimeError(f"{label} credential inventory is malformed")
    return frozenset(values)


def _stable_credential_ids(
    list_credentials: Callable[[], Iterable[object]],
    *,
    assert_single_writer: Callable[[], None],
    label: str,
    sleep: Callable[[float], None],
    require_full_window: bool = False,
) -> frozenset[str]:
    """Return only after several consecutive exact inventory observations."""

    previous: frozenset[str] | None = None
    stable_count = 0
    for observation in range(_MAX_STABILITY_OBSERVATIONS):
        assert_single_writer()
        current = credential_ids(list_credentials, label=label)
        if current == previous:
            stable_count += 1
        else:
            previous = current
            stable_count = 1
        if stable_count >= _STABLE_OBSERVATIONS and not require_full_window:
            return current
        if observation + 1 < _MAX_STABILITY_OBSERVATIONS:
            sleep(_STABILITY_INTERVAL_SECONDS)
    if previous is not None and stable_count >= _STABLE_OBSERVATIONS:
        return previous
    raise RuntimeError(f"{label} credential inventory did not become stable")


def prove_stable_credential_ids(
    list_credentials: Callable[[], Iterable[object]],
    *,
    assert_single_writer: Callable[[], None],
    label: str,
    sleep: Callable[[float], None] | None = None,
    require_full_window: bool = True,
) -> frozenset[str]:
    """Return one fence-bound, bounded, stable provider inventory."""

    return _stable_credential_ids(
        list_credentials,
        assert_single_writer=assert_single_writer,
        label=label,
        sleep=sleep or time.sleep,
        require_full_window=require_full_window,
    )


def _restore_new_credential(
    *,
    principal_id: str,
    before_ids: frozenset[str],
    list_credentials: Callable[[], Iterable[object]],
    delete_credential: Callable[[str], None],
    assert_single_writer: Callable[[], None],
    label: str,
    sleep: Callable[[float], None],
    attributable_id: str = "",
) -> str:
    """Revoke the sole attributable inventory delta and prove exact restoration."""

    known_id = attributable_id.strip()
    after_ids = before_ids
    if known_id and known_id not in before_ids:
        credential_id = known_id
        new_ids = frozenset({known_id})
    else:
        try:
            after_ids = _stable_credential_ids(
                list_credentials,
                assert_single_writer=assert_single_writer,
                label=label,
                sleep=sleep,
                require_full_window=True,
            )
        except BaseException as observation_error:
            raise_credential_quarantine(
                message=(
                    f"{label} credential creation outcome could not be observed "
                    "authoritatively"
                ),
                label=label,
                principal_id=principal_id,
                before_ids=before_ids,
                fence=assert_single_writer,
                cause=observation_error,
            )
        new_ids = after_ids.difference(before_ids)
    if not new_ids:
        if after_ids != before_ids:
            raise_credential_quarantine(
                message=(
                    f"{label} credential inventory drifted during create reconciliation"
                ),
                label=label,
                principal_id=principal_id,
                before_ids=before_ids,
                candidate_ids=new_ids,
                fence=assert_single_writer,
            )
        raise_credential_quarantine(
            message=(
                f"{label} credential creation returned an ambiguous error with no "
                "attributable credential; principal recovery is required"
            ),
            label=label,
            principal_id=principal_id,
            before_ids=before_ids,
            fence=assert_single_writer,
        )
    if not known_id and len(new_ids) == 1:
        credential_id = next(iter(new_ids))
    elif not known_id:
        raise_credential_quarantine(
            message=(
                f"{label} credential creation is ambiguous; exact cleanup cannot "
                "be proven"
            ),
            label=label,
            principal_id=principal_id,
            before_ids=before_ids,
            candidate_ids=new_ids,
            fence=assert_single_writer,
        )
    try:
        assert_single_writer()
    except BaseException as fence_error:
        raise_credential_quarantine(
            message=f"{label} credential cleanup lost its single-writer fence",
            label=label,
            principal_id=principal_id,
            before_ids=before_ids,
            candidate_ids=new_ids,
            fence=assert_single_writer,
            cause=fence_error,
        )
    delete_error: BaseException | None = None
    try:
        delete_credential(credential_id)
    except BaseException as exc:
        delete_error = exc
    try:
        restored = _stable_credential_ids(
            list_credentials,
            assert_single_writer=assert_single_writer,
            label=label,
            sleep=sleep,
            require_full_window=True,
        )
    except BaseException as proof_error:
        raise_credential_quarantine(
            message=f"{label} credential cleanup postflight is unavailable",
            label=label,
            principal_id=principal_id,
            before_ids=before_ids,
            candidate_ids=new_ids,
            fence=assert_single_writer,
            cause=proof_error,
        )
    if delete_error is not None:
        raise_credential_quarantine(
            message=f"{label} credential delete result is ambiguous",
            label=label,
            principal_id=principal_id,
            before_ids=before_ids,
            candidate_ids=new_ids,
            fence=assert_single_writer,
            cause=delete_error,
        )
    if restored != before_ids:
        raise_credential_quarantine(
            message=f"{label} credential cleanup could not be proven",
            label=label,
            principal_id=principal_id,
            before_ids=before_ids,
            candidate_ids=new_ids,
            fence=assert_single_writer,
            cause=delete_error,
        )
    return credential_id


def create_exact_oauth_credential(
    *,
    principal_id: str,
    list_credentials: Callable[[], Iterable[object]],
    create_credential: Callable[[], object],
    delete_credential: Callable[[str], None],
    assert_single_writer: Callable[[], None],
    mutation_context: CredentialMutationContext,
    label: str,
    sleep: Callable[[float], None] | None = None,
) -> ExactOAuthCredential:
    """Create exactly one credential or restore the complete prior inventory."""

    settle = sleep or time.sleep
    session = begin_credential_mutation_session(
        assert_single_writer,
        label=label,
        principal_id=principal_id,
        context=mutation_context,
    )
    try:
        before_ids = _stable_credential_ids(
            list_credentials,
            assert_single_writer=session,
            label=label,
            sleep=settle,
        )
    except BaseException:
        session.abort_before_intent()
        raise
    if (
        mutation_context.retirement_mode == "signed_app_cutover"
        and len(before_ids) > 1
    ):
        session.abort_before_intent()
        raise RuntimeError(
            f"{label} staged-cutover credential inventory has an unresolved "
            "candidate; finish signed App retirement before rotating again"
        )
    intent = session.persist_intent(before_ids=before_ids)
    intent()
    try:
        response = create_credential()
    except BaseException:
        _restore_new_credential(
            principal_id=principal_id,
            before_ids=before_ids,
            list_credentials=list_credentials,
            delete_credential=delete_credential,
            assert_single_writer=intent,
            label=label,
            sleep=settle,
        )
        intent.resolve(
            outcome="restored",
            final_ids=before_ids,
        )
        raise
    credential_id = ""
    try:
        credential_id = _field(response, "id")
        secret = _field(response, "secret")
        after_ids = _stable_credential_ids(
            list_credentials,
            assert_single_writer=intent,
            label=label,
            sleep=settle,
        )
        valid = (
            bool(credential_id)
            and bool(secret)
            and credential_id not in before_ids
            and after_ids.difference(before_ids) == {credential_id}
            and not before_ids.difference(after_ids)
        )
        if valid:
            intent.observe(
                credential_id=credential_id,
                observed_ids=after_ids,
            )
            return ExactOAuthCredential(
                credential_id=credential_id,
                secret=secret,
                before_ids=before_ids,
                intent=intent,
            )
        validation_error: BaseException = RuntimeError(
            f"{label} credential create response is incomplete or ambiguous "
            f"[id_present={bool(credential_id)} secret_present={bool(secret)} "
            f"id_prefix={credential_id[:8] or '<empty>'} "
            f"before={sorted(i[:8] for i in before_ids)} "
            f"after={sorted(i[:8] for i in after_ids)} "
            f"added={sorted(i[:8] for i in after_ids.difference(before_ids))} "
            f"removed={sorted(i[:8] for i in before_ids.difference(after_ids))}]"
        )
    except CredentialMutationQuarantineError:
        raise
    except BaseException as exc:
        validation_error = exc
    try:
        _restore_new_credential(
            principal_id=principal_id,
            before_ids=before_ids,
            list_credentials=list_credentials,
            delete_credential=delete_credential,
            assert_single_writer=intent,
            label=label,
            attributable_id=credential_id,
            sleep=settle,
        )
        intent.resolve(
            outcome="restored",
            final_ids=before_ids,
        )
    except CredentialMutationQuarantineError:
        raise
    raise validation_error


def revoke_exact_oauth_credential(
    credential: ExactOAuthCredential,
    *,
    principal_id: str,
    list_credentials: Callable[[], Iterable[object]],
    delete_credential: Callable[[str], None],
    assert_single_writer: Callable[[], None],
    label: str,
    sleep: Callable[[float], None] | None = None,
    finalize_resolution: bool = True,
    sink_invalidated: bool = False,
) -> None:
    """Revoke one known credential and prove the prior inventory was restored.

    An armed external sink is a separate mutation surface.  Callers may leave
    the provider restoration unresolved while that sink remains unproven so
    the recovery workflow can finish exact invalidation later.
    """

    settle = sleep or time.sleep
    intent = credential.intent
    if (
        intent.principal_id != principal_id
        or intent.before_ids != credential.before_ids
        or intent.fence is not assert_single_writer
    ):
        raise RuntimeError("OAuth credential intent binding is invalid")
    try:
        intent()
    except BaseException as fence_error:
        raise_credential_quarantine(
            message=f"{label} credential revocation lost its single-writer fence",
            label=label,
            principal_id=principal_id,
            before_ids=credential.before_ids,
            candidate_ids=frozenset({credential.credential_id}),
            fence=intent,
            cause=fence_error,
        )
    delete_error: BaseException | None = None
    try:
        delete_credential(credential.credential_id)
    except BaseException as exc:
        delete_error = exc
    try:
        after_ids = _stable_credential_ids(
            list_credentials,
            assert_single_writer=intent,
            label=label,
            sleep=settle,
            require_full_window=True,
        )
    except BaseException as proof_error:
        raise_credential_quarantine(
            message=f"{label} credential revocation postflight is unavailable",
            label=label,
            principal_id=principal_id,
            before_ids=credential.before_ids,
            candidate_ids=frozenset({credential.credential_id}),
            fence=intent,
            cause=proof_error,
        )
    if delete_error is not None:
        raise_credential_quarantine(
            message=f"{label} credential delete result is ambiguous",
            label=label,
            principal_id=principal_id,
            before_ids=credential.before_ids,
            candidate_ids=frozenset({credential.credential_id}),
            fence=intent,
            cause=delete_error,
        )
    if after_ids != credential.before_ids:
        raise_credential_quarantine(
            message=f"{label} credential cleanup could not be proven",
            label=label,
            principal_id=principal_id,
            before_ids=credential.before_ids,
            candidate_ids=frozenset({credential.credential_id}),
            fence=intent,
            cause=delete_error,
        )
    if not finalize_resolution:
        return
    if intent.sink_path and not sink_invalidated:
        raise_credential_quarantine(
            message=f"{label} credential sink invalidation is unproven",
            label=label,
            principal_id=principal_id,
            before_ids=credential.before_ids,
            candidate_ids=frozenset({credential.credential_id}),
            fence=intent,
        )
    intent.resolve(
        outcome="restored",
        final_ids=after_ids,
        sink_disposition=(
            "invalidated" if sink_invalidated else "not_attempted"
        ),
    )


def resolve_exact_oauth_credential_delivery(
    credential: ExactOAuthCredential,
    *,
    list_credentials: Callable[[], Iterable[object]],
    delete_credential: Callable[[str], None],
    label: str,
    sleep: Callable[[float], None] | None = None,
) -> None:
    """Resolve one durable intent only after its one-shot secret is delivered."""

    settle = sleep or time.sleep
    try:
        final_ids = _stable_credential_ids(
            list_credentials,
            assert_single_writer=credential.intent,
            label=label,
            sleep=settle,
            require_full_window=True,
        )
    except BaseException as proof_error:
        raise_credential_quarantine(
            message=f"{label} delivered credential inventory is unproven",
            label=label,
            principal_id=credential.intent.principal_id,
            before_ids=credential.before_ids,
            candidate_ids=frozenset({credential.credential_id}),
            fence=credential.intent,
            cause=proof_error,
        )
    expected = credential.before_ids | {credential.credential_id}
    if final_ids != expected:
        raise_credential_quarantine(
            message=f"{label} delivered credential inventory drifted",
            label=label,
            principal_id=credential.intent.principal_id,
            before_ids=credential.before_ids,
            candidate_ids=frozenset({credential.credential_id}),
            fence=credential.intent,
        )
    credential.intent.acknowledge_delivery(
        acknowledged_ids=final_ids,
    )
    if credential.intent.retirement_mode == "signed_app_cutover":
        try:
            final_ids = _stable_credential_ids(
                list_credentials,
                assert_single_writer=credential.intent,
                label=label,
                sleep=settle,
                require_full_window=True,
            )
        except BaseException as proof_error:
            raise_credential_quarantine(
                message=(
                    f"{label} staged-cutover credential inventory is unproven"
                ),
                label=label,
                principal_id=credential.intent.principal_id,
                before_ids=credential.before_ids,
                candidate_ids=frozenset({credential.credential_id}),
                fence=credential.intent,
                cause=proof_error,
            )
        if final_ids != expected:
            raise_credential_quarantine(
                message=(
                    f"{label} staged-cutover credential inventory drifted"
                ),
                label=label,
                principal_id=credential.intent.principal_id,
                before_ids=credential.before_ids,
                candidate_ids=final_ids,
                fence=credential.intent,
            )
        credential.intent.resolve(
            outcome="delivered",
            final_ids=final_ids,
            retained_credential_id=credential.credential_id,
            sink_disposition="acknowledged",
        )
        return
    retirement_errors: list[BaseException] = []
    for credential_id in sorted(credential.before_ids):
        try:
            credential.intent()
            delete_credential(credential_id)
        except BaseException as retirement_error:
            retirement_errors.append(retirement_error)
    try:
        final_ids = _stable_credential_ids(
            list_credentials,
            assert_single_writer=credential.intent,
            label=label,
            sleep=settle,
            require_full_window=True,
        )
    except BaseException as proof_error:
        raise_credential_quarantine(
            message=f"{label} prior credential retirement is unproven",
            label=label,
            principal_id=credential.intent.principal_id,
            before_ids=credential.before_ids,
            candidate_ids=frozenset({credential.credential_id}),
            fence=credential.intent,
            cause=proof_error,
        )
    if final_ids != {credential.credential_id}:
        raise_credential_quarantine(
            message=f"{label} prior credential retirement did not converge",
            label=label,
            principal_id=credential.intent.principal_id,
            before_ids=credential.before_ids,
            candidate_ids=final_ids,
            fence=credential.intent,
            cause=retirement_errors[0] if retirement_errors else None,
        )
    credential.intent.resolve(
        outcome="delivered",
        final_ids=final_ids,
        retained_credential_id=credential.credential_id,
        sink_disposition="acknowledged",
    )
