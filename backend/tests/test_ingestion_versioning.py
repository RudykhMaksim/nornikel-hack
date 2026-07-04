import json

from app import versioning


def test_record_and_read_ingestion_run(tmp_path, monkeypatch):
    log_path = tmp_path / "ingestion_history.jsonl"
    monkeypatch.setattr(versioning, "INGESTION_HISTORY_PATH", log_path)

    entry = versioning.record_ingestion_run({
        "total_entities": 100, "total_relations": 200, "deep_parsed": 50,
        "new_documents": 5, "new_relations_found": 12, "newly_deep_parsed": 3,
        "is_first_run": False,
    })
    assert log_path.exists()
    assert entry["new_documents"] == 5

    history = versioning.get_ingestion_history()
    assert len(history) == 1
    assert history[0]["new_relations_found"] == 12


def test_ingestion_history_respects_limit(tmp_path, monkeypatch):
    log_path = tmp_path / "ingestion_history.jsonl"
    monkeypatch.setattr(versioning, "INGESTION_HISTORY_PATH", log_path)

    for i in range(5):
        versioning.record_ingestion_run({"total_entities": i, "total_relations": i,
                                          "deep_parsed": 0, "new_documents": 0,
                                          "new_relations_found": 0, "newly_deep_parsed": 0,
                                          "is_first_run": False})
    history = versioning.get_ingestion_history(limit=2)
    assert len(history) == 2
    assert history[-1]["total_entities"] == 4  # сохраняются самые свежие


def test_ingestion_history_missing_file_returns_empty(tmp_path, monkeypatch):
    monkeypatch.setattr(versioning, "INGESTION_HISTORY_PATH", tmp_path / "does_not_exist.jsonl")
    assert versioning.get_ingestion_history() == []
