import json

from fastapi.testclient import TestClient

from app.graph_store import DATA_PATH, GraphStore
from app.main import app

client = TestClient(app)


def test_health_endpoint():
    resp = client.get("/api/health")
    assert resp.status_code == 200
    body = resp.json()
    assert body["status"] in ("ok", "degraded")
    assert body["total_entities"] > 0
    assert body["curated_entities"] > 0


def test_corrupted_corpus_file_degrades_instead_of_crashing(tmp_path):
    broken_corpus = tmp_path / "corpus_data.json"
    broken_corpus.write_text("{not valid json", encoding="utf-8")

    store = GraphStore(data_path=DATA_PATH, corpus_data_path=broken_corpus)

    assert store.load_warnings, "ожидалось предупреждение о загрузке для повреждённого файла корпуса"
    assert store.graph.number_of_nodes() > 0, "курируемая онтология всё равно должна была загрузиться"
    assert len(store._curated_ids) == store.graph.number_of_nodes()


def test_missing_corpus_file_is_not_an_error(tmp_path):
    missing_path = tmp_path / "does_not_exist.json"
    store = GraphStore(data_path=DATA_PATH, corpus_data_path=missing_path)
    assert not store.load_warnings
    assert store.graph.number_of_nodes() > 0
