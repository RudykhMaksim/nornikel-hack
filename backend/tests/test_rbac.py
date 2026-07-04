from app import rbac
from app.query_engine import answer_query


def test_external_partner_cannot_see_internal(store):
    q = "Методы очистки шахтных вод"
    result = answer_query(store, q)
    filtered = rbac.filter_sensitive(result, "external_partner")

    for bucket in filtered["results"].values():
        for item in bucket:
            assert item.get("sensitivity", "public") != "internal"


def test_researcher_sees_internal(store):
    q = "Методы очистки шахтных вод"
    result = answer_query(store, q)
    filtered = rbac.filter_sensitive(result, "researcher")

    all_items = [item for bucket in filtered["results"].values() for item in bucket]
    assert any(item.get("sensitivity") == "internal" for item in all_items)


def test_can_annotate_roles():
    assert rbac.can_annotate("researcher")
    assert rbac.can_annotate("admin")
    assert not rbac.can_annotate("external_partner")
