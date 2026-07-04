from app.query_engine import geography_coverage_report


def test_geography_coverage_buckets_are_disjoint(store):
    report = geography_coverage_report(store)
    all_ids = [row["id"] for bucket in report.values() for row in bucket]
    assert len(all_ids) == len(set(all_ids)), "сущность должна попадать ровно в одну категорию"


def test_geography_coverage_only_covers_curated_entities(store):
    report = geography_coverage_report(store)
    for bucket in report.values():
        for row in bucket:
            assert store.is_curated_id(row["id"])


def test_geography_coverage_finds_ru_only_technology(store):
    report = geography_coverage_report(store)
    ru_only_ids = {row["id"] for row in report["ru_only"]}
    # источник -- только внутренний RU-обзор по шахтным водам, ни одна
    # международная публикация не описывает это в данном корпусе
    assert "proc_electrodialysis" in ru_only_ids
