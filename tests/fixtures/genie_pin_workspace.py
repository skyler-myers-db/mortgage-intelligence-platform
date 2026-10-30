"""A fake workspace for the Genie space pin (tools/databricks/genie_space_pin.py, M1-PR3).

It holds Genie spaces (each with its direct CAN_MANAGE holders), the App
rollback secret scope's secrets, and the deploying identity, and records every
mutating call in ``mutations`` so a test can prove a refusal changed nothing.
It never calls Genie: the spaces are in memory. The scope-ownership proof and
the signed rollback record reader are the real modules' own concern, so tests
patch them (``patch_rollback_scope``).
"""

from __future__ import annotations

import base64
from dataclasses import dataclass, field
from types import SimpleNamespace
from typing import Any

import pytest
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
from databricks.sdk.errors import NotFound

APP = "mip-app"
SCOPE = "mip-app-rollback"
INSTANCE = "mip-app-state"
TITLE = "Mortgage Lead Intelligence"
DEPLOYER = "00000000-0000-4000-8000-000000000001"


def _seed(start: int) -> str:
    return base64.urlsafe_b64encode(bytes(range(start, start + 32))).decode("ascii").rstrip("=")


def signing_env(start: int = 0) -> dict[str, str]:
    """A synthetic Ed25519 signing key and its verify key, as the deploy exports them."""

    seed = _seed(start)
    private = Ed25519PrivateKey.from_private_bytes(base64.urlsafe_b64decode(seed + "="))
    verify = private.public_key().public_bytes(
        encoding=serialization.Encoding.Raw, format=serialization.PublicFormat.Raw
    )
    return {
        "MIP_AI_GATEWAY_PROOF_SIGNING_KEY": seed,
        "MIP_AI_GATEWAY_PROOF_VERIFY_KEY": base64.urlsafe_b64encode(verify).decode("ascii").rstrip("="),
    }


@dataclass
class Space:
    space_id: str
    title: str = TITLE
    managers: tuple[str, ...] = ()
    warehouse_id: str = "warehouse-alpha"


@dataclass
class PinWorkspace:
    spaces: list[Space] = field(default_factory=list)
    secrets_by_key: dict[str, str] = field(default_factory=dict)
    scope_exists: bool = True
    deployer: str = DEPLOYER
    mutations: list[str] = field(default_factory=list)
    created_ids: list[str] = field(default_factory=list)

    def __post_init__(self) -> None:
        self.genie = SimpleNamespace(
            list_spaces=self._list_spaces,
            get_space=self._get_space,
            create_space=self._create_space,
            update_space=self._update_space,
        )
        self.secrets = SimpleNamespace(
            list_scopes=lambda: [SimpleNamespace(name=SCOPE)] if self.scope_exists else [],
            get_secret=self._get_secret,
            put_secret=self._put_secret,
        )
        self.api_client = SimpleNamespace(do=self._do)
        self.current_user = SimpleNamespace(me=lambda: SimpleNamespace(user_name=self.deployer))
        self.config = SimpleNamespace(host="https://dbc-unit.cloud.databricks.com")

    def _list_spaces(self, *, page_token: str | None = None) -> Any:
        return SimpleNamespace(spaces=[SimpleNamespace(space_id=s.space_id, title=s.title) for s in self.spaces])

    def _find(self, space_id: str) -> Space:
        for space in self.spaces:
            if space.space_id == space_id:
                return space
        raise NotFound("no such space")

    def _get_space(self, space_id: str, include_serialized_space: bool | None = None) -> Any:
        space = self._find(space_id)
        return SimpleNamespace(
            space_id=space.space_id,
            title=space.title,
            warehouse_id=space.warehouse_id,
            as_dict=lambda: {"title": space.title, "warehouse_id": space.warehouse_id, "serialized_space": "{}"},
        )

    def _create_space(self, **kwargs: Any) -> Any:
        self.mutations.append("genie.create_space")
        space = Space(space_id=f"created-space-{len(self.created_ids) + 1}", title=kwargs["title"], managers=(self.deployer,))
        space.warehouse_id = kwargs["warehouse_id"]
        self.spaces.append(space)
        self.created_ids.append(space.space_id)
        return SimpleNamespace(space_id=space.space_id)

    def _update_space(self, **kwargs: Any) -> None:
        self.mutations.append(f"genie.update_space:{kwargs['space_id']}")
        space = self._find(kwargs["space_id"])
        space.title = kwargs.get("title", space.title)
        if kwargs.get("warehouse_id"):
            space.warehouse_id = kwargs["warehouse_id"]

    def _get_secret(self, scope: str, key: str) -> Any:
        if not self.scope_exists or key not in self.secrets_by_key:
            raise NotFound("no such secret")
        return SimpleNamespace(value=base64.b64encode(self.secrets_by_key[key].encode()).decode())

    def _put_secret(self, *, scope: str, key: str, string_value: str) -> None:
        self.mutations.append(f"secrets.put_secret:{key}")
        self.secrets_by_key[key] = string_value

    def _do(self, method: str, path: str) -> Any:
        assert method == "GET", "the pin reads permissions, never writes them"
        space_id = path.rsplit("/", 1)[-1]
        space = self._find(space_id)
        return {
            "access_control_list": [
                *(
                    {"user_name": name, "all_permissions": [{"permission_level": "CAN_MANAGE", "inherited": False}]}
                    for name in space.managers
                ),
                {
                    "group_name": "users",
                    "all_permissions": [{"permission_level": "CAN_MANAGE", "inherited": True}],
                },
            ]
        }


def patch_rollback_scope(monkeypatch: pytest.MonkeyPatch, *, record_space: str | None = None) -> list[str]:
    """Stand in for the scope-ownership proof and the signed rollback record; returns the proof calls."""

    from tools.databricks import app_rollback_record_contract, app_rollback_secret_scope

    proofs: list[str] = []

    def owned(workspace: Any, *, app_name: str, scope: str) -> None:
        proofs.append(f"{app_name}:{scope}")

    monkeypatch.setattr(app_rollback_secret_scope, "assert_owned_app_rollback_scope", owned)

    def load(workspace: Any, *, app_name: str, scope: str, expected_lakebase_instance: str) -> dict[str, Any]:
        if record_space is None:
            raise AssertionError("no record exists in this fixture")
        return {"genie_space_id": record_space}

    monkeypatch.setattr(app_rollback_record_contract, "_load_record", load)
    return proofs


def store_record(workspace: PinWorkspace, space_id: str) -> None:
    """Make the fake scope hold a (stand-in) signed rollback record naming ``space_id``."""

    from tools.databricks.app_rollback_record_contract import _record_key

    workspace.secrets_by_key[_record_key(APP)] = '{"stand-in": true}'
    workspace.record_space = space_id  # type: ignore[attr-defined]
