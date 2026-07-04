import time
from pathlib import Path
from typing import Any, Optional

from fastapi import FastAPI, HTTPException, Query
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import PlainTextResponse, Response
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

from . import annotations, export, neo4j_store, nlp, rbac, versioning
from .graph_store import get_store
from .query_engine import answer_query, build_glossary, detect_contradictions, geography_coverage_report

app = FastAPI(title="Mining R&D Knowledge Graph", version="0.1.0")
_start_time = time.time()

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)


def _parse_csv(value: Optional[str]) -> Optional[list[str]]:
    if not value:
        return None
    return [v.strip() for v in value.split(",") if v.strip()]


@app.get("/api/health")
def health():
    """Проверка живости/готовности: граф загружен, его размер и любые
    предупреждения о деградации из GraphStore.load() (например, повреждённый
    corpus_data.json, который был пропущен вместо падения всего API)."""
    store = get_store()
    return {
        "status": "degraded" if store.load_warnings else "ok",
        "total_entities": store.graph.number_of_nodes(),
        "total_relations": store.graph.number_of_edges(),
        "curated_entities": len(store._curated_ids),
        "uptime_sec": round(time.time() - _start_time, 1),
        "warnings": store.load_warnings,
    }


@app.get("/api/neo4j/status")
def neo4j_status():
    """Проверка живого подключения + количество узлов/связей в опциональном
    развёртывании Neo4j (docker-compose.yml) -- доказывает, что путь
    хранения через Cypher работает прямо из запущенного приложения, а не
    только через офлайн-скрипт scripts/export_to_neo4j_cypher.py +
    cypher-shell. Корректно деградирует (available: false), если Neo4j не
    запущен; в любом случае это не влияет на остальной API."""
    return neo4j_store.status()


class CypherQueryBody(BaseModel):
    cypher: str
    params: dict[str, Any] = {}


@app.post("/api/neo4j/query")
def neo4j_query(body: CypherQueryBody, role: str = "researcher"):
    """Выполнить произвольный READ-ONLY Cypher-запрос к живому графу Neo4j.
    Инструкции записи/администрирования отклоняются по списку запрещённых
    ключевых слов (см. neo4j_store._WRITE_KEYWORDS) -- простая защита,
    достаточная для демо-показа рецензенту, но не замена настоящей роли
    read-only в БД в проде."""
    if not rbac.can_see_internal(role):
        raise HTTPException(403, "Role not permitted to run direct graph queries")
    try:
        rows = neo4j_store.run_read_query(body.cypher, body.params)
    except ConnectionError as exc:
        raise HTTPException(503, str(exc))
    except ValueError as exc:
        raise HTTPException(400, str(exc))
    rbac.log_audit(role, "neo4j_query", body.cypher[:200])
    return {"rows": rows}


@app.get("/api/roles")
def list_roles():
    return {"roles": rbac.ROLES}


@app.get("/api/search")
def search(
    q: str = Query(..., min_length=2),
    role: str = Query("researcher"),
    geography: Optional[str] = Query(None, description="через запятую: RU,International"),
    year_from: Optional[int] = None,
    year_to: Optional[int] = None,
    confidence: Optional[str] = Query(None, description="через запятую: verified,probable,hypothesis"),
):
    if role not in rbac.ROLES:
        raise HTTPException(400, f"Unknown role. Allowed: {rbac.ROLES}")
    store = get_store()
    result = answer_query(store, q, geography_filter=_parse_csv(geography), year_from=year_from, year_to=year_to,
                           confidence_filter=_parse_csv(confidence))
    result = rbac.filter_sensitive(result, role)
    rbac.log_audit(role, "search", q)
    return result


@app.get("/api/entity/{entity_id}")
def get_entity(entity_id: str, role: str = "researcher"):
    store = get_store()
    entity = store.get_entity(entity_id)
    if not entity:
        raise HTTPException(404, "Entity not found")
    if entity.get("sensitivity") == "internal" and not rbac.can_see_internal(role):
        raise HTTPException(403, "Insufficient role to view this internal entity")
    entity = dict(entity)
    entity["comments"] = annotations.get_comments(entity_id)
    rbac.log_audit(role, "view_entity", entity_id)
    return entity


@app.get("/api/graph/{entity_id}")
def get_subgraph(entity_id: str, depth: int = 1, role: str = "researcher"):
    store = get_store()
    if not store.get_entity(entity_id):
        raise HTTPException(404, "Entity not found")
    sub = store.subgraph_for_ids([entity_id], depth=depth)
    if not rbac.can_see_internal(role):
        sub["nodes"] = [n for n in sub["nodes"] if n.get("sensitivity", "public") != "internal"]
        kept_ids = {n["id"] for n in sub["nodes"]}
        sub["edges"] = [e for e in sub["edges"] if e["source"] in kept_ids and e["target"] in kept_ids]
    return sub


class AnnotateBody(BaseModel):
    role: str
    author: str
    text: str


@app.post("/api/entities/{entity_id}/annotate")
def annotate_entity(entity_id: str, body: AnnotateBody):
    store = get_store()
    if not store.get_entity(entity_id):
        raise HTTPException(404, "Entity not found")
    if not rbac.can_annotate(body.role):
        raise HTTPException(403, "Role not permitted to annotate the graph")
    comment = annotations.add_comment(entity_id, body.role, body.author, body.text)
    rbac.log_audit(body.role, "annotate", f"{entity_id}: {body.text[:80]}")
    return comment


EDITABLE_FIELDS = {"confidence", "description", "value", "value2", "unit", "geography",
                    "name_ru", "name_en", "year", "source_note"}


class EditEntityBody(BaseModel):
    role: str
    author: str
    reason: str
    changes: dict[str, Any]


@app.patch("/api/entities/{entity_id}")
def edit_entity(entity_id: str, body: EditEntityBody):
    """Структурированная правка факта с обязательным указанием причины,
    версионируемая в data/version_history.jsonl (см. GET .../history) и
    сохраняемая обратно в data/seed_data.json. Таким способом редактируется
    только курируемая онтология -- загруженные документы корпуса являются
    каталожными записями, а не фактами, введёнными вручную; для них вместо
    этого используйте /annotate для свободных комментариев эксперта."""
    store = get_store()
    entity = store.get_entity(entity_id)
    if not entity:
        raise HTTPException(404, "Entity not found")
    if not store.is_curated(entity):
        raise HTTPException(400, "Corpus-derived catalog entries aren't hand-edited directly; use /annotate for comments")
    if not rbac.can_annotate(body.role):
        raise HTTPException(403, "Role not permitted to edit the graph")
    invalid_fields = set(body.changes) - EDITABLE_FIELDS
    if invalid_fields:
        raise HTTPException(400, f"Fields not editable: {sorted(invalid_fields)}")
    if not body.reason.strip():
        raise HTTPException(400, "A reason is required for fact edits")

    changes = dict(body.changes)
    changes["last_updated"] = time.strftime("%Y-%m-%d")
    old_values = store.update_entity(entity_id, changes)
    for field, new_value in body.changes.items():
        versioning.record_change(entity_id, field, old_values.get(field), new_value, body.author, body.role, body.reason)
    store.persist_seed()
    rbac.log_audit(body.role, "edit_entity", f"{entity_id}: {sorted(body.changes)} ({body.reason[:60]})")
    return store.get_entity(entity_id)


@app.get("/api/entities/{entity_id}/history")
def entity_history(entity_id: str):
    if not get_store().get_entity(entity_id):
        raise HTTPException(404, "Entity not found")
    return {"history": versioning.get_history(entity_id)}


@app.get("/api/entities")
def list_entities(type: Optional[str] = None, role: str = "researcher"):
    store = get_store()
    entities = store.all_entities()
    if type:
        entities = [e for e in entities if e["type"] == type]
    if not rbac.can_see_internal(role):
        entities = [e for e in entities if e.get("sensitivity", "public") != "internal"]
    entities.sort(key=lambda e: e.get("name_ru") or "")
    return {"entities": [{"id": e["id"], "name_ru": e.get("name_ru"), "type": e["type"], "geography": e.get("geography")} for e in entities]}


CONDITION_CATEGORY_LABELS = {
    "economic": "Капитальные затраты / экономика",
    "environmental": "Экологические ограничения",
    "climate": "Применимость по климату",
    "efficiency": "Эффективность",
    "technical": "Технические параметры",
}


@app.get("/api/compare")
def compare(ids: str, role: str = "researcher"):
    """Сравнительная таблица по списку id сущностей Process/Equipment/
    Standard, с условиями, сгруппированными по категории (эффективность/
    капзатраты/климат/экология/технические) согласно требованию ТЗ о
    сравнительном анализе. Подтягиваются условия, привязанные как напрямую
    к сущности, так и к любому Experiment, применяющему этот процесс/
    оборудование -- реальные экономические/эффективностные показатели в
    этом графе обычно находятся у Experiment (например, конкретный кейс
    завода), на один шаг дальше от Process."""
    store = get_store()
    entity_ids = [i.strip() for i in ids.split(",") if i.strip()]
    rows = []
    for eid in entity_ids:
        entity = store.get_entity(eid)
        if not entity:
            continue
        if entity.get("sensitivity") == "internal" and not rbac.can_see_internal(role):
            continue

        condition_ids = {t for _, t, rt in store.out_edges(eid, "OPERATES_AT_CONDITION")}
        condition_ids |= {t for _, t, rt in store.out_edges(eid, "PRODUCES_OUTPUT")}
        for exp_id, _, _ in store.in_edges(eid, "APPLIES_TO_PROCESS"):
            condition_ids |= {t for _, t, rt in store.out_edges(exp_id, "OPERATES_AT_CONDITION")}
            condition_ids |= {t for _, t, rt in store.out_edges(exp_id, "PRODUCES_OUTPUT")}
        conditions = [store.get_entity(cid) for cid in condition_ids]
        conditions = [c for c in conditions if c]

        by_category: dict[str, list[dict]] = {}
        for c in conditions:
            cat = c.get("parameter_category", "technical")
            by_category.setdefault(cat, []).append({
                "name_ru": c.get("name_ru"), "parameter": c.get("parameter"),
                "value": c.get("value"), "value2": c.get("value2"), "unit": c.get("unit"),
            })

        conclusions = [store.get_entity(t) for _, t, rt in store.out_edges(eid, "CONCLUDES")]
        publications = [store.get_entity(t) for _, t, rt in store.out_edges(eid, "DESCRIBED_IN")]
        rows.append({
            "id": eid,
            "name_ru": entity.get("name_ru"),
            "geography": entity.get("geography"),
            "confidence": entity.get("confidence"),
            "conditions": [{"name_ru": c.get("name_ru"), "parameter": c.get("parameter"),
                             "value": c.get("value"), "value2": c.get("value2"), "unit": c.get("unit")} for c in conditions],
            "conditions_by_category": {CONDITION_CATEGORY_LABELS.get(k, k): v for k, v in by_category.items()},
            "conclusions": [c.get("name_ru") for c in conclusions if c],
            "publications": [p.get("name_ru") for p in publications if p],
        })
    rbac.log_audit(role, "compare", ids)
    return {"rows": rows, "categories": list(CONDITION_CATEGORY_LABELS.values())}


@app.get("/api/gaps")
def known_gaps(role: str = "researcher"):
    """Небольшой набор курируемых примеров запросов о пробелах, выполняемых
    на текущем графе, чтобы UI мог что-то показать без ввода запроса пользователем."""
    store = get_store()
    examples = [
        "Есть ли опыт кучного выщелачивания никелевой руды в условиях холодного климата?",
        "Есть ли отечественные эксперименты по электродиализу шахтных вод?",
    ]
    out = []
    for q in examples:
        result = answer_query(store, q)
        result = rbac.filter_sensitive(result, role)
        if result["gaps"]:
            out.append({"query": q, "gaps": result["gaps"]})
    return {"gaps": out}


@app.get("/api/geography-coverage")
def geography_coverage(role: str = "researcher"):
    """Какие курируемые технологии/материалы задокументированы только в
    отечественных источниках, только в зарубежных, в обоих, или вовсе без
    источника с указанием географии -- согласно требованию ТЗ "какие
    технологии описаны только в отечественной или только в зарубежной
    практике"."""
    store = get_store()
    return geography_coverage_report(store, allow_internal=rbac.can_see_internal(role))


@app.get("/api/glossary")
def glossary():
    """Справочный каталог материалов/оборудования/стандартов + единиц
    измерения, распознаваемых NLP-слоем -- всегда синхронизирован с графом."""
    return build_glossary(get_store())


@app.get("/api/dashboard")
def dashboard(role: str = "researcher"):
    store = get_store()
    entities = store.all_entities()
    if not rbac.can_see_internal(role):
        entities = [e for e in entities if e.get("sensitivity", "public") != "internal"]

    by_type: dict[str, int] = {}
    by_geo: dict[str, int] = {}
    by_confidence: dict[str, int] = {}
    for e in entities:
        by_type[e["type"]] = by_type.get(e["type"], 0) + 1
        geo = e.get("geography") or "n/a"
        by_geo[geo] = by_geo.get(geo, 0) + 1
        conf = e.get("confidence") or "n/a"
        by_confidence[conf] = by_confidence.get(conf, 0) + 1

    all_ids = {e["id"] for e in entities}
    contradictions = detect_contradictions(store, all_ids)

    corpus_docs = [e for e in entities if e["type"] in ("Publication", "Archive") and "path" in e]
    ingestion = {
        "catalogued_files": len(corpus_docs),
        "deep_parsed": sum(1 for e in corpus_docs if e.get("deep_parsed")),
        "archives_metadata_only": sum(1 for e in corpus_docs if e["type"] == "Archive"),
        "mentions_found": sum(1 for _, _, d in store.graph.edges(data=True) if d.get("type") == "MENTIONS"),
    }

    return {
        "total_entities": len(entities),
        "by_type": by_type,
        "by_geography": by_geo,
        "by_confidence": by_confidence,
        "contradictions_count": len(contradictions),
        "ingestion": ingestion,
        "ingestion_history": versioning.get_ingestion_history(10),
        "recent_audit": rbac.read_audit_log(20),
    }


@app.post("/api/export")
def export_result(body: dict, format: str = Query("markdown", pattern="^(markdown|json-ld|pdf)$")):
    if format == "markdown":
        return PlainTextResponse(export.to_markdown(body), media_type="text/markdown")
    if format == "pdf":
        return Response(export.to_pdf(body), media_type="application/pdf",
                         headers={"Content-Disposition": "attachment; filename=answer.pdf"})
    return export.to_json_ld(body)


FRONTEND_DIR = Path(__file__).resolve().parents[2] / "frontend"
if FRONTEND_DIR.exists():
    app.mount("/", StaticFiles(directory=str(FRONTEND_DIR), html=True), name="frontend")
