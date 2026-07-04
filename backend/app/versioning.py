"""История версий на уровне фактов: кто, какое поле у какой сущности, когда
и почему изменил. Дополняет annotations.py (свободные комментарии экспертов),
отслеживая структурированные правки (например, понижение достоверности
после появления противоречащего источника, исправление числового условия).
"""
import json
import time
from pathlib import Path
from typing import Any

HISTORY_PATH = Path(__file__).resolve().parents[2] / "data" / "version_history.jsonl"
INGESTION_HISTORY_PATH = Path(__file__).resolve().parents[2] / "data" / "ingestion_history.jsonl"


def record_change(entity_id: str, field: str, old_value: Any, new_value: Any,
                   author: str, role: str, reason: str) -> dict:
    entry = {
        "ts": time.strftime("%Y-%m-%dT%H:%M:%S"),
        "entity_id": entity_id,
        "field": field,
        "old_value": old_value,
        "new_value": new_value,
        "author": author,
        "role": role,
        "reason": reason,
    }
    HISTORY_PATH.parent.mkdir(parents=True, exist_ok=True)
    with HISTORY_PATH.open("a", encoding="utf-8") as f:
        f.write(json.dumps(entry, ensure_ascii=False) + "\n")
    return entry


def get_history(entity_id: str) -> list[dict]:
    if not HISTORY_PATH.exists():
        return []
    out = []
    for line in HISTORY_PATH.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        entry = json.loads(line)
        if entry.get("entity_id") == entity_id:
            out.append(entry)
    return out


def record_ingestion_run(stats: dict[str, Any]) -> dict:
    """Автоматическое версионирование для слоя корпуса: build_corpus.py
    вызывает это при каждом повторном запуске, чтобы записать, что
    изменилось с предыдущего запуска (обнаружены новые документы, найдены
    новые связи MENTIONS, файлы впервые глубоко разобраны) -- "обновление
    данных при появлении новых источников" без необходимости фиксировать
    каждое изменение вручную через record_change()."""
    entry = {"ts": time.strftime("%Y-%m-%dT%H:%M:%S"), **stats}
    INGESTION_HISTORY_PATH.parent.mkdir(parents=True, exist_ok=True)
    with INGESTION_HISTORY_PATH.open("a", encoding="utf-8") as f:
        f.write(json.dumps(entry, ensure_ascii=False) + "\n")
    return entry


def get_ingestion_history(limit: int = 50) -> list[dict]:
    if not INGESTION_HISTORY_PATH.exists():
        return []
    lines = INGESTION_HISTORY_PATH.read_text(encoding="utf-8").splitlines()
    return [json.loads(line) for line in lines[-limit:] if line.strip()]
