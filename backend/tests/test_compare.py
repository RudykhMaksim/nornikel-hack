from fastapi.testclient import TestClient

from app.main import app

client = TestClient(app)


def test_compare_categorizes_conditions_by_type():
    resp = client.get("/api/compare", params={"ids": "proc_slag_flotation_depletion", "role": "researcher"})
    assert resp.status_code == 200
    body = resp.json()
    row = body["rows"][0]
    categories = row["conditions_by_category"]
    assert "Капитальные затраты / экономика" in categories
    assert "Эффективность" in categories
    assert any("10 млн" in c["name_ru"] for c in categories["Капитальные затраты / экономика"])


def test_compare_pulls_conditions_from_linked_experiment():
    # у самой proc_slag_flotation_depletion нет прямых связей
    # OPERATES_AT_CONDITION -- реальные показатели находятся у связанного
    # Experiment, на один шаг дальше
    resp = client.get("/api/compare", params={"ids": "proc_slag_flotation_depletion", "role": "researcher"})
    row = resp.json()["rows"][0]
    assert len(row["conditions"]) >= 4
