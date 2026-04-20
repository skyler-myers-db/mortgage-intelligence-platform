from fastapi import APIRouter

router = APIRouter(prefix="/api")

_RULES = {"offer_rules_version": "demo-v1"}


@router.get("/config/options")
def get_config_options() -> dict[str, list[str]]:
    return {"segments": ["equity_rich", "rate_sensitive", "investor"], "channels": ["email", "sms", "call"]}


@router.get("/admin/rules")
def get_rules() -> dict[str, str]:
    return _RULES


@router.put("/admin/rules")
def put_rules(payload: dict) -> dict:
    _RULES.update(payload)
    return {"status": "updated", "rules": _RULES}
