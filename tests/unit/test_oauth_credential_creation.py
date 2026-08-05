from __future__ import annotations

from types import SimpleNamespace

import pytest

from tools.databricks import oauth_credential_creation as creation
from tools.databricks.oauth_credential_creation import (
    create_exact_oauth_credential,
    credential_ids,
    resolve_exact_oauth_credential_delivery,
    revoke_exact_oauth_credential,
)
from tools.databricks.oauth_credential_quarantine import (
    CredentialMutationContext,
    CredentialMutationQuarantineError,
)

_MUTATION_CONTEXT = CredentialMutationContext(
    authority_scope="workspace",
    authority_identity="test-application-id",
    provider_api="test.credentials",
    operation_mode="temporary_probe",
    sink_descriptor="temporary:test",
    credential_lifetime_seconds=300,
)
_CUTOVER_CONTEXT = CredentialMutationContext(
    authority_scope="workspace",
    authority_identity="proxy-application-id",
    provider_api="test.credentials",
    operation_mode="persistent_delivery",
    sink_descriptor="github:owner/repo:atomic=true:PROXY_BUNDLE",
    credential_lifetime_seconds=0,
    sink_repository="owner/repo",
    sink_secret_names=frozenset({"PROXY_BUNDLE"}),
    sink_atomic_credential_bundle=True,
    retirement_mode="signed_app_cutover",
)


class _TestIntent:
    def __init__(
        self,
        *,
        session: _TestSession,
        before_ids: frozenset[str],
    ) -> None:
        self.session = session
        self.before_ids = before_ids
        self.principal_id = session.principal_id
        self.fence = session.fence
        self.sink_path = ""
        self.observed_credential_id = ""
        self.retirement_mode = session.retirement_mode
        self.acknowledgements: list[frozenset[str]] = []
        self.resolutions: list[tuple[str, frozenset[str], str, str]] = []

    def __call__(self) -> None:
        self.session()

    def quarantine(self, error: CredentialMutationQuarantineError) -> None:
        self.session.quarantine(error)

    def observe(
        self,
        *,
        credential_id: str,
        observed_ids: frozenset[str],
    ) -> None:
        assert observed_ids == self.before_ids | {credential_id}
        self.observed_credential_id = credential_id

    def acknowledge_delivery(
        self,
        *,
        acknowledged_ids: frozenset[str],
    ) -> None:
        assert acknowledged_ids == self.before_ids | {self.observed_credential_id}
        self.acknowledgements.append(acknowledged_ids)

    def resolve(
        self,
        *,
        outcome: str,
        final_ids: frozenset[str],
        retained_credential_id: str = "",
        sink_disposition: str = "not_attempted",
    ) -> None:
        self.resolutions.append(
            (outcome, final_ids, retained_credential_id, sink_disposition)
        )
        self.session.released = True


class _TestSession:
    def __init__(
        self,
        fence: object,
        *,
        principal_id: str,
        retirement_mode: str,
    ) -> None:
        self.fence = fence
        self.principal_id = principal_id
        self.retirement_mode = retirement_mode
        self.released = False
        self.intent: _TestIntent | None = None

    def __call__(self) -> None:
        if not self.released:
            self.fence()  # type: ignore[operator]

    def quarantine(self, error: CredentialMutationQuarantineError) -> None:
        recorder = getattr(self.fence, "quarantine", None)
        if callable(recorder):
            recorder(error)

    def persist_intent(self, *, before_ids: frozenset[str]) -> _TestIntent:
        self.intent = _TestIntent(session=self, before_ids=before_ids)
        return self.intent

    def abort_before_intent(self) -> None:
        self.released = True


@pytest.fixture(autouse=True)
def _use_in_memory_mutation_session(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        creation,
        "begin_credential_mutation_session",
        lambda fence, *, label, principal_id, context: _TestSession(
            fence,
            principal_id=principal_id,
            retirement_mode=context.retirement_mode,
        ),
    )


class _Credentials:
    def __init__(self, values: tuple[str, ...] = ("existing",)) -> None:
        self.live = list(values)
        self.deleted: list[str] = []
        self.delete_error: BaseException | None = None
        self.delete_commits = False

    def list(self) -> object:
        return (SimpleNamespace(id=value) for value in self.live)

    def delete(self, credential_id: str) -> None:
        if self.delete_commits:
            self.live.remove(credential_id)
        if self.delete_error is not None:
            raise self.delete_error
        if not self.delete_commits:
            self.live.remove(credential_id)
        self.deleted.append(credential_id)


def _create(
    credentials: _Credentials,
    create: object,
    *,
    assert_single_writer: object = lambda: None,
) -> object:
    return create_exact_oauth_credential(
        principal_id="principal-id",
        list_credentials=credentials.list,
        create_credential=create,  # type: ignore[arg-type]
        delete_credential=credentials.delete,
        assert_single_writer=assert_single_writer,  # type: ignore[arg-type]
        mutation_context=_MUTATION_CONTEXT,
        label="test",
        sleep=lambda _seconds: None,
    )


def test_create_and_revoke_restore_exact_prior_inventory() -> None:
    credentials = _Credentials()

    def create() -> object:
        credentials.live.append("new")
        return SimpleNamespace(id="new", secret="one-use")

    credential = _create(credentials, create)
    assert credential.credential_id == "new"
    assert credential.secret == "one-use"

    revoke_exact_oauth_credential(
        credential,
        principal_id="principal-id",
        list_credentials=credentials.list,
        delete_credential=credentials.delete,
        assert_single_writer=credential.intent.fence,
        label="test",
        sleep=lambda _seconds: None,
    )

    assert credentials.live == ["existing"]
    assert credentials.deleted == ["new"]


def test_delivered_rotation_acknowledges_then_retires_every_prior_credential() -> None:
    credentials = _Credentials(("old-a", "old-b"))

    def create() -> object:
        credentials.live.append("new")
        return SimpleNamespace(id="new", secret="one-use")

    credential = _create(credentials, create)
    credential.intent.sink_path = "armed-sink-attempt"

    resolve_exact_oauth_credential_delivery(
        credential,
        list_credentials=credentials.list,
        delete_credential=credentials.delete,
        label="test",
        sleep=lambda _seconds: None,
    )

    assert credential.intent.acknowledgements == [
        frozenset({"old-a", "old-b", "new"})
    ]
    assert credentials.deleted == ["old-a", "old-b"]
    assert credentials.live == ["new"]
    assert credential.intent.resolutions == [
        ("delivered", frozenset({"new"}), "new", "acknowledged")
    ]


def test_proxy_delivery_preserves_signed_blue_until_app_cutover() -> None:
    credentials = _Credentials(("signed-blue",))

    def create() -> object:
        credentials.live.append("green")
        return SimpleNamespace(id="green", secret="one-use")

    credential = create_exact_oauth_credential(
        principal_id="principal-id",
        list_credentials=credentials.list,
        create_credential=create,
        delete_credential=credentials.delete,
        assert_single_writer=lambda: None,
        mutation_context=_CUTOVER_CONTEXT,
        label="proxy",
        sleep=lambda _seconds: None,
    )
    credential.intent.sink_path = "armed-sink-attempt"

    resolve_exact_oauth_credential_delivery(
        credential,
        list_credentials=credentials.list,
        delete_credential=credentials.delete,
        label="proxy",
        sleep=lambda _seconds: None,
    )

    assert credentials.deleted == []
    assert credentials.live == ["signed-blue", "green"]
    assert credential.intent.resolutions == [
        (
            "delivered",
            frozenset({"signed-blue", "green"}),
            "green",
            "acknowledged",
        )
    ]


def test_second_proxy_rotation_fails_before_create_when_candidate_is_pending() -> None:
    credentials = _Credentials(("signed-blue", "pending-green"))
    create_calls = 0

    def create() -> object:
        nonlocal create_calls
        create_calls += 1
        return SimpleNamespace(id="another", secret="one-use")

    with pytest.raises(RuntimeError, match="finish signed App retirement"):
        create_exact_oauth_credential(
            principal_id="principal-id",
            list_credentials=credentials.list,
            create_credential=create,
            delete_credential=credentials.delete,
            assert_single_writer=lambda: None,
            mutation_context=_CUTOVER_CONTEXT,
            label="proxy",
            sleep=lambda _seconds: None,
        )

    assert create_calls == 0
    assert credentials.live == ["signed-blue", "pending-green"]


def test_armed_sink_cannot_be_recorded_invalidated_without_exact_proof() -> None:
    credentials = _Credentials()

    def create() -> object:
        credentials.live.append("new")
        return SimpleNamespace(id="new", secret="one-use")

    credential = _create(credentials, create)
    credential.intent.sink_path = "armed-sink-attempt"

    with pytest.raises(
        CredentialMutationQuarantineError,
        match="sink invalidation is unproven",
    ):
        revoke_exact_oauth_credential(
            credential,
            principal_id="principal-id",
            list_credentials=credentials.list,
            delete_credential=credentials.delete,
            assert_single_writer=credential.intent.fence,
            label="test",
            sleep=lambda _seconds: None,
        )

    assert credentials.live == ["existing"]
    assert credential.intent.resolutions == []


def test_armed_sink_provider_restore_stays_unresolved_for_sink_recovery() -> None:
    credentials = _Credentials()

    def create() -> object:
        credentials.live.append("new")
        return SimpleNamespace(id="new", secret="one-use")

    credential = _create(credentials, create)
    credential.intent.sink_path = "armed-sink-attempt"

    revoke_exact_oauth_credential(
        credential,
        principal_id="principal-id",
        list_credentials=credentials.list,
        delete_credential=credentials.delete,
        assert_single_writer=credential.intent.fence,
        label="test",
        sleep=lambda _seconds: None,
        finalize_resolution=False,
    )

    assert credentials.live == ["existing"]
    assert credential.intent.resolutions == []


def test_successful_secret_is_observed_before_return_and_excluded_from_repr() -> None:
    credentials = _Credentials()

    def create() -> object:
        credentials.live.append("new")
        return SimpleNamespace(id="new", secret="one-shot-super-secret")

    credential = _create(credentials, create)

    assert credential.intent.observed_credential_id == "new"
    assert "one-shot-super-secret" not in repr(credential)
    assert "one-shot-super-secret" not in str(credential)


def test_commit_then_timeout_discovers_and_revokes_only_new_delta() -> None:
    credentials = _Credentials()

    def create() -> object:
        credentials.live.append("committed")
        raise TimeoutError("response lost")

    with pytest.raises(TimeoutError, match="response lost"):
        _create(credentials, create)

    assert credentials.live == ["existing"]
    assert credentials.deleted == ["committed"]


def test_uncommitted_create_failure_quarantines_without_delete() -> None:
    credentials = _Credentials()

    with pytest.raises(
        CredentialMutationQuarantineError,
        match="no attributable credential",
    ):
        _create(
            credentials,
            lambda: (_ for _ in ()).throw(ConnectionError("request rejected")),
        )

    assert credentials.live == ["existing"]
    assert credentials.deleted == []


def test_delete_commit_then_timeout_is_resolved_by_exact_postflight() -> None:
    credentials = _Credentials()

    def create() -> object:
        credentials.live.append("new")
        return SimpleNamespace(id="new", secret="one-use")

    credential = _create(credentials, create)
    credentials.delete_commits = True
    credentials.delete_error = TimeoutError("response lost after delete")

    with pytest.raises(
        CredentialMutationQuarantineError,
        match="delete result is ambiguous",
    ):
        revoke_exact_oauth_credential(
            credential,
            principal_id="principal-id",
            list_credentials=credentials.list,
            delete_credential=credentials.delete,
            assert_single_writer=credential.intent.fence,
            label="test",
            sleep=lambda _seconds: None,
        )

    assert credentials.live == ["existing"]


def test_delete_failure_without_commit_is_fatal() -> None:
    credentials = _Credentials()

    def create() -> object:
        credentials.live.append("new")
        return SimpleNamespace(id="new", secret="one-use")

    credential = _create(credentials, create)
    credentials.delete_error = PermissionError("delete denied")

    with pytest.raises(
        CredentialMutationQuarantineError,
        match="delete result is ambiguous",
    ):
        revoke_exact_oauth_credential(
            credential,
            principal_id="principal-id",
            list_credentials=credentials.list,
            delete_credential=credentials.delete,
            assert_single_writer=credential.intent.fence,
            label="test",
            sleep=lambda _seconds: None,
        )

    assert "new" in credentials.live


def test_stale_post_delete_reads_cannot_mask_explicit_delete_denial() -> None:
    credentials = _Credentials()
    stale_after_delete = False
    stale_reads = 0

    def list_credentials() -> object:
        nonlocal stale_reads
        if stale_after_delete:
            stale_reads += 1
            return (SimpleNamespace(id="existing") for _index in range(1))
        return credentials.list()

    def create() -> object:
        credentials.live.append("new")
        return SimpleNamespace(id="new", secret="one-use")

    credential = create_exact_oauth_credential(
        principal_id="principal-id",
        list_credentials=list_credentials,
        create_credential=create,
        delete_credential=credentials.delete,
        assert_single_writer=lambda: None,
        mutation_context=_MUTATION_CONTEXT,
        label="test",
        sleep=lambda _seconds: None,
    )
    stale_after_delete = True

    with pytest.raises(
        CredentialMutationQuarantineError,
        match="delete result is ambiguous",
    ):
        revoke_exact_oauth_credential(
            credential,
            principal_id="principal-id",
            list_credentials=list_credentials,
            delete_credential=lambda _credential_id: (_ for _ in ()).throw(
                PermissionError("delete denied")
            ),
            assert_single_writer=credential.intent.fence,
            label="test",
            sleep=lambda _seconds: None,
        )

    assert stale_reads == creation._MAX_STABILITY_OBSERVATIONS
    assert credentials.live == ["existing", "new"]


def test_stale_post_delete_reads_cannot_mask_compensation_delete_denial() -> None:
    credentials = _Credentials()
    delete_attempted = False
    stale_reads = 0

    def list_credentials() -> object:
        nonlocal stale_reads
        if delete_attempted:
            stale_reads += 1
            return (SimpleNamespace(id="existing") for _index in range(1))
        return credentials.list()

    def create() -> object:
        credentials.live.append("new")
        return SimpleNamespace(id="new", secret="")

    def denied_delete(_credential_id: str) -> None:
        nonlocal delete_attempted
        delete_attempted = True
        raise PermissionError("delete denied")

    with pytest.raises(
        CredentialMutationQuarantineError,
        match="delete result is ambiguous",
    ):
        create_exact_oauth_credential(
            principal_id="principal-id",
            list_credentials=list_credentials,
            create_credential=create,
            delete_credential=denied_delete,
            assert_single_writer=lambda: None,
            mutation_context=_MUTATION_CONTEXT,
            label="test",
            sleep=lambda _seconds: None,
        )

    assert stale_reads == creation._MAX_STABILITY_OBSERVATIONS
    assert credentials.live == ["existing", "new"]


def test_concurrent_create_delta_is_ambiguous_and_never_mass_deleted() -> None:
    credentials = _Credentials()

    def create() -> object:
        credentials.live.extend(("ours", "concurrent"))
        raise TimeoutError("response lost")

    with pytest.raises(RuntimeError, match="ambiguous"):
        _create(credentials, create)

    assert credentials.live == ["existing", "ours", "concurrent"]
    assert credentials.deleted == []


@pytest.mark.parametrize(
    "values",
    [
        ("",),
        ("duplicate", "duplicate"),
    ],
)
def test_malformed_inventory_is_rejected_before_create(values: tuple[str, ...]) -> None:
    credentials = _Credentials(values)

    with pytest.raises(RuntimeError, match="inventory is malformed"):
        credential_ids(credentials.list, label="test")


def test_incomplete_create_response_restores_new_inventory_delta() -> None:
    credentials = _Credentials()

    def create() -> object:
        credentials.live.append("new")
        return SimpleNamespace(id="new", secret="")

    with pytest.raises(RuntimeError, match="incomplete or ambiguous"):
        _create(credentials, create)

    assert credentials.live == ["existing"]
    assert credentials.deleted == ["new"]


def test_known_response_id_cleanup_never_deletes_concurrent_delta() -> None:
    credentials = _Credentials()

    def create() -> object:
        credentials.live.extend(("ours", "concurrent"))
        return SimpleNamespace(id="ours", secret="")

    with pytest.raises(RuntimeError, match="cleanup could not be proven"):
        _create(credentials, create)

    assert credentials.live == ["existing", "concurrent"]
    assert credentials.deleted == ["ours"]


def test_delayed_commit_visibility_is_reconciled_before_absence_claim() -> None:
    credentials = _Credentials()
    list_calls = 0

    def list_credentials() -> object:
        nonlocal list_calls
        list_calls += 1
        visible = credentials.live
        if list_calls == 4:
            visible = ["existing"]
        return (SimpleNamespace(id=value) for value in visible)

    def create() -> object:
        credentials.live.append("committed")
        raise TimeoutError("response lost")

    with pytest.raises(TimeoutError, match="response lost"):
        create_exact_oauth_credential(
            principal_id="principal-id",
            list_credentials=list_credentials,
            create_credential=create,
            delete_credential=credentials.delete,
            assert_single_writer=lambda: None,
            mutation_context=_MUTATION_CONTEXT,
            label="test",
            sleep=lambda _seconds: None,
        )

    assert credentials.live == ["existing"]
    assert credentials.deleted == ["committed"]
    assert list_calls >= 10


def test_successful_create_then_first_postflight_list_failure_revokes_known_id() -> None:
    credentials = _Credentials()
    list_calls = 0

    def list_credentials() -> object:
        nonlocal list_calls
        list_calls += 1
        if list_calls == 4:
            raise ConnectionError("first post-create list failed")
        return credentials.list()

    def create() -> object:
        credentials.live.append("new")
        return SimpleNamespace(id="new", secret="one-use")

    with pytest.raises(RuntimeError, match="inventory could not be read"):
        create_exact_oauth_credential(
            principal_id="principal-id",
            list_credentials=list_credentials,
            create_credential=create,
            delete_credential=credentials.delete,
            assert_single_writer=lambda: None,
            mutation_context=_MUTATION_CONTEXT,
            label="test",
            sleep=lambda _seconds: None,
        )

    assert credentials.live == ["existing"]
    assert credentials.deleted == ["new"]


def test_successful_create_then_response_parse_failure_reconciles_inventory() -> None:
    credentials = _Credentials()

    class _UnreadableResponse:
        @property
        def id(self) -> str:
            raise ValueError("response id unreadable")

    def create() -> object:
        credentials.live.append("new")
        return _UnreadableResponse()

    with pytest.raises(ValueError, match="response id unreadable"):
        _create(credentials, create)

    assert credentials.live == ["existing"]
    assert credentials.deleted == ["new"]


def test_successful_create_then_lease_loss_quarantines_known_id() -> None:
    credentials = _Credentials()
    assertion_calls = 0
    quarantines: list[CredentialMutationQuarantineError] = []

    class _Fence:
        def __call__(self) -> None:
            nonlocal assertion_calls
            assertion_calls += 1
            if assertion_calls >= 5:
                raise RuntimeError("lease lost")

        def quarantine(self, error: CredentialMutationQuarantineError) -> None:
            quarantines.append(error)

    def create() -> object:
        credentials.live.append("new")
        return SimpleNamespace(id="new", secret="one-use")

    with pytest.raises(
        CredentialMutationQuarantineError,
        match="lost its single-writer fence",
    ):
        _create(credentials, create, assert_single_writer=_Fence())

    assert credentials.live == ["existing", "new"]
    assert credentials.deleted == []
    assert len(quarantines) == 1
    assert quarantines[0].candidate_ids == frozenset({"new"})


def test_commit_hidden_for_three_complete_reads_is_still_discovered() -> None:
    credentials = _Credentials()
    list_calls = 0

    def list_credentials() -> object:
        nonlocal list_calls
        list_calls += 1
        visible = ["existing"] if 4 <= list_calls <= 6 else credentials.live
        return (SimpleNamespace(id=value) for value in visible)

    def create() -> object:
        credentials.live.append("committed")
        raise TimeoutError("response lost")

    with pytest.raises(TimeoutError, match="response lost"):
        create_exact_oauth_credential(
            principal_id="principal-id",
            list_credentials=list_credentials,
            create_credential=create,
            delete_credential=credentials.delete,
            assert_single_writer=lambda: None,
            mutation_context=_MUTATION_CONTEXT,
            label="test",
            sleep=lambda _seconds: None,
        )

    assert credentials.live == ["existing"]
    assert credentials.deleted == ["committed"]
    assert list_calls >= 15


def test_commit_hidden_for_full_reconciliation_window_is_quarantined() -> None:
    credentials = _Credentials()
    list_calls = 0

    def list_credentials() -> object:
        nonlocal list_calls
        list_calls += 1
        visible = ["existing"] if 4 <= list_calls <= 12 else credentials.live
        return (SimpleNamespace(id=value) for value in visible)

    def create() -> object:
        credentials.live.append("committed")
        raise TimeoutError("response lost")

    with pytest.raises(
        CredentialMutationQuarantineError,
        match="no attributable credential",
    ):
        create_exact_oauth_credential(
            principal_id="principal-id",
            list_credentials=list_credentials,
            create_credential=create,
            delete_credential=credentials.delete,
            assert_single_writer=lambda: None,
            mutation_context=_MUTATION_CONTEXT,
            label="test",
            sleep=lambda _seconds: None,
        )

    assert credentials.live == ["existing", "committed"]
    assert credentials.deleted == []
    assert list_calls == 12


def test_single_writer_fence_is_checked_before_create() -> None:
    credentials = _Credentials()
    create_calls = 0

    def denied() -> None:
        raise RuntimeError("lease lost")

    def create() -> object:
        nonlocal create_calls
        create_calls += 1
        return SimpleNamespace(id="new", secret="one-use")

    with pytest.raises(RuntimeError, match="lease lost"):
        _create(credentials, create, assert_single_writer=denied)

    assert create_calls == 0
    assert credentials.live == ["existing"]


def test_unbounded_inventory_is_rejected() -> None:
    credentials = _Credentials(tuple(f"credential-{index}" for index in range(1001)))

    with pytest.raises(RuntimeError, match="inventory is unbounded"):
        credential_ids(credentials.list, label="test")
