"""Комментарии/правки экспертов к сущностям графа с указанием автора и даты.

Хранятся в плоском JSON-файле, чтобы переживать перезапуск без необходимости
настоящей базы данных для MVP.
"""
import json
import time
from pathlib import Path

ANNOTATIONS_PATH = Path(__file__).resolve().parents[2] / "data" / "annotations.json"


def _load() -> dict[str, list[dict]]:
    if not ANNOTATIONS_PATH.exists():
        return {}
    return json.loads(ANNOTATIONS_PATH.read_text(encoding="utf-8"))


def _save(data: dict[str, list[dict]]) -> None:
    ANNOTATIONS_PATH.parent.mkdir(parents=True, exist_ok=True)
    ANNOTATIONS_PATH.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")


def add_comment(entity_id: str, role: str, author: str, text: str) -> dict:
    data = _load()
    comment = {"author": author, "role": role, "text": text, "ts": time.strftime("%Y-%m-%dT%H:%M:%S")}
    data.setdefault(entity_id, []).append(comment)
    _save(data)
    return comment


def get_comments(entity_id: str) -> list[dict]:
    return _load().get(entity_id, [])
