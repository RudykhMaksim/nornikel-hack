import pytest
from fastapi.testclient import TestClient

from app.main import app

client = TestClient(app)


@pytest.fixture(autouse=True)
def _revert_test_edits():
    """Каждый тест здесь изменяет живой seed_data.json (edit_entity сохраняет
    на диск); после теста восстанавливаем исходные поля затронутой сущности,
    чтобы поставляемый файл данных не накапливал артефакты тестов."""
    yield
    client.request("PATCH", "/api/entities/proc_nanofiltration", json={
        "role": "admin", "author": "test-teardown", "reason": "revert test edit",
        "changes": {"confidence": "verified"},
    })


def test_edit_entity_requires_permission():
    resp = client.request("PATCH", "/api/entities/proc_nanofiltration", json={
        "role": "external_partner", "author": "x", "reason": "test", "changes": {"confidence": "probable"},
    })
    assert resp.status_code == 403


def test_edit_entity_rejects_corpus_derived():
    entities = client.get("/api/entities?type=Publication").json()["entities"]
    corpus_entity = next(e for e in entities if e["id"].startswith("doc_"))
    resp = client.request("PATCH", f"/api/entities/{corpus_entity['id']}", json={
        "role": "researcher", "author": "x", "reason": "test", "changes": {"confidence": "hypothesis"},
    })
    assert resp.status_code == 400


def test_edit_entity_rejects_non_editable_field():
    resp = client.request("PATCH", "/api/entities/proc_nanofiltration", json={
        "role": "researcher", "author": "x", "reason": "test", "changes": {"id": "hacked"},
    })
    assert resp.status_code == 400


def test_edit_entity_records_history():
    resp = client.request("PATCH", "/api/entities/proc_nanofiltration", json={
        "role": "researcher", "author": "Тест Тестов", "reason": "проверка версионирования",
        "changes": {"confidence": "probable"},
    })
    assert resp.status_code == 200
    assert resp.json()["confidence"] == "probable"

    history = client.get("/api/entities/proc_nanofiltration/history").json()["history"]
    assert history
    last = history[-1]
    assert last["field"] == "confidence"
    assert last["new_value"] == "probable"
    assert last["author"] == "Тест Тестов"
