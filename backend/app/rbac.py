"""Минимальный ролевой контроль доступа (RBAC) + аудит-лог для MVP.

Роли: researcher, analyst, project_lead, admin, external_partner.
Ограничена только `external_partner`: она не видит сущности с пометкой
sensitivity="internal" (внутренние отчёты/эксперименты) и не может
аннотировать граф. В реальном развёртывании это опиралось бы на группы
claims из SSO/IdP; здесь роль передаётся клиентом явно, в демонстрационных целях.
"""
import json
import time
from pathlib import Path
from typing import Any

AUDIT_LOG_PATH = Path(__file__).resolve().parents[2] / "data" / "audit_log.jsonl"

ROLES = ["researcher", "analyst", "project_lead", "admin", "external_partner"]

ANNOTATE_ROLES = {"researcher", "analyst", "project_lead", "admin"}
SEE_INTERNAL_ROLES = {"researcher", "analyst", "project_lead", "admin"}


def can_see_internal(role: str) -> bool:
    return role in SEE_INTERNAL_ROLES


def can_annotate(role: str) -> bool:
    return role in ANNOTATE_ROLES


def filter_sensitive(result: dict[str, Any], role: str) -> dict[str, Any]:
    if can_see_internal(role):
        return result
    filtered = dict(result)
    results = dict(result.get("results", {}))
    for bucket, items in results.items():
        results[bucket] = [item for item in items if item.get("sensitivity", "public") != "internal"]
    filtered["results"] = results
    filtered["redacted_for_role"] = role
    return filtered


def log_audit(role: str, action: str, detail: str) -> None:
    entry = {"ts": time.strftime("%Y-%m-%dT%H:%M:%S"), "role": role, "action": action, "detail": detail}
    AUDIT_LOG_PATH.parent.mkdir(parents=True, exist_ok=True)
    with AUDIT_LOG_PATH.open("a", encoding="utf-8") as f:
        f.write(json.dumps(entry, ensure_ascii=False) + "\n")


def read_audit_log(limit: int = 200) -> list[dict]:
    if not AUDIT_LOG_PATH.exists():
        return []
    lines = AUDIT_LOG_PATH.read_text(encoding="utf-8").splitlines()
    return [json.loads(line) for line in lines[-limit:]]
