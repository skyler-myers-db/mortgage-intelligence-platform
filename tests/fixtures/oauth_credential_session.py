"""In-memory mutation session for unit tests below the workspace journal."""

from __future__ import annotations

from typing import Any


class InMemoryCredentialIntent:
    def __init__(
        self,
        *,
        session: InMemoryCredentialSession,
        before_ids: frozenset[str],
    ) -> None:
        self.session = session
        self.before_ids = before_ids
        self.principal_id = session.principal_id
        self.fence = session.fence
        self.sink_path = ""
        self.observed_credential_id = ""
        self.retirement_mode = session.retirement_mode

    def __call__(self) -> None:
        self.session()

    def quarantine(self, error: BaseException) -> None:
        self.session.quarantine(error)

    def observe(
        self,
        *,
        credential_id: str,
        observed_ids: frozenset[str],
    ) -> None:
        if observed_ids != self.before_ids | {credential_id}:
            raise AssertionError("test credential observation is inconsistent")
        self.observed_credential_id = credential_id

    def arm_sink(self, **_kwargs: object) -> None:
        self.sink_path = "in-memory-sink-attempt"

    def acknowledge_delivery(
        self,
        *,
        acknowledged_ids: frozenset[str],
    ) -> None:
        if acknowledged_ids != self.before_ids | {self.observed_credential_id}:
            raise AssertionError("credential delivery acknowledgement drifted")

    def resolve(self, **_kwargs: object) -> None:
        self.session.released = True


class InMemoryCredentialSession:
    def __init__(
        self,
        fence: Any,
        *,
        principal_id: str,
        retirement_mode: str,
    ) -> None:
        self.fence = fence
        self.principal_id = principal_id
        self.retirement_mode = retirement_mode
        self.released = False

    def __call__(self) -> None:
        if not self.released:
            self.fence()

    def quarantine(self, error: BaseException) -> None:
        recorder = getattr(self.fence, "quarantine", None)
        if callable(recorder):
            recorder(error)

    def persist_intent(
        self,
        *,
        before_ids: frozenset[str],
    ) -> InMemoryCredentialIntent:
        return InMemoryCredentialIntent(
            session=self,
            before_ids=before_ids,
        )

    def abort_before_intent(self) -> None:
        self.released = True


def install_in_memory_credential_mutation_session(
    monkeypatch: Any,
    credential_creation_module: Any,
) -> None:
    """Replace only the durable-session seam for lower-level unit tests."""

    monkeypatch.setattr(
        credential_creation_module,
        "begin_credential_mutation_session",
        lambda fence, *, label, principal_id, context: (
            InMemoryCredentialSession(
                fence,
                principal_id=principal_id,
                retirement_mode=context.retirement_mode,
            )
        ),
    )
