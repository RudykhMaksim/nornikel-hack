"""Запрос на естественном языке -> структурированный ответ с источниками.

Пайплайн: словарное/нечёткое связывание сущностей + извлечение числовых
ограничений/географии/дат (nlp.py) -> выбор стартовых узлов в графе ->
ограниченное BFS-расширение по доменным типам связей -> фильтрация по
извлечённым ограничениям -> группировка в ответ с источниками/достоверностью,
детекция противоречий и детекция пробелов в знаниях.
"""
import re
from typing import Any, Optional

from . import nlp
from .graph_store import GraphStore

EXPANSION_RELATIONS = {
    "APPLIES_TO_MATERIAL", "APPLIES_TO_PROCESS", "APPLIES_TO", "USES_MATERIAL",
    "OPERATES_AT_CONDITION", "PRODUCES_OUTPUT", "DESCRIBED_IN", "CONCLUDES",
    "VALIDATED_BY", "AUTHORED_BY", "AFFILIATED_WITH", "EXPERT_IN", "OCCURRED_AT",
    "MENTIONS", "CONTAINS",
}

RESULT_TYPES = {"Process", "Equipment", "Experiment", "Publication", "Conclusion", "Topic", "Patent", "Standard"}


def _tokenize(text: str) -> list[str]:
    return re.findall(r"[a-zA-Zа-яёА-ЯЁ0-9]+", text.lower())


def _stem(word: str, n: int = 6) -> str:
    return word if len(word) <= n else word[:n]


def _expand_synonyms(text: str, synonyms: dict[str, str]) -> str:
    """Добавлять сопоставленный термин всякий раз, когда в тексте запроса
    встречается ключ или значение из словаря синонимов (RU<->EN /
    аббревиатура<->полная форма), чтобы нечёткое сопоставление также
    находило сущности, чей собственный список алиасов не содержит именно
    этот термин (например, запрос, использующий только "electrowinning",
    или "ПВП", где в алиасе сущности термин написан полностью)."""
    if not synonyms:
        return text
    low = text.lower()
    extra = []
    for key, value in synonyms.items():
        if key.lower() in low and value.lower() not in low:
            extra.append(value)
        if value.lower() in low and key.lower() not in low:
            extra.append(key)
    return text + " " + " ".join(extra) if extra else text


def match_entities_fuzzy(store: GraphStore, text: str, min_ratio: float = 0.5, min_strong_len: int = 5) -> list[dict]:
    text = _expand_synonyms(text, store.synonyms)
    text_stems = {_stem(w) for w in _tokenize(text) if len(w) >= 2}
    matches = []
    for ent in store.all_entities():
        candidates = [ent.get("name_ru", ""), ent.get("name_en", "") or ""] + ent.get("aliases", [])
        best_ratio = 0.0
        best_strong = False
        for cand in candidates:
            if not cand:
                continue
            words = [w for w in _tokenize(cand) if len(w) >= 2]
            if not words:
                continue
            matched = sum(1 for w in words if _stem(w) in text_stems)
            has_strong = any(_stem(w) in text_stems and len(w) >= min_strong_len for w in words)
            ratio = matched / len(words)
            if ratio > best_ratio:
                best_ratio, best_strong = ratio, has_strong
        if best_ratio >= min_ratio and (best_strong or best_ratio == 1.0):
            matches.append(ent)
    return matches


def _interval(operator: str, value: Optional[float], value2: Optional[float]) -> tuple[float, float]:
    inf = float("inf")
    if operator == "range":
        return (value, value2)
    if operator == "<=":
        return (-inf, value)
    if operator == ">=":
        return (value, inf)
    return (value, value)  # "равно"


def _overlaps(a: tuple[float, float], b: tuple[float, float]) -> bool:
    return a[0] <= b[1] and b[0] <= a[1]


def match_conditions_by_constraints(store: GraphStore, constraints: list[dict]) -> list[dict]:
    """Найти сущности Condition, чей параметр соответствует запрошенному
    ограничению и чей числовой интервал пересекается с запрошенным."""
    matched = []
    conditions = store.find_by_type("Condition")
    for c in constraints:
        if c["parameter"] is None or c.get("value") is None:
            continue
        q_interval = _interval(c["operator"], c.get("value"), c.get("value2"))
        for cond in conditions:
            if cond.get("parameter") != c["parameter"]:
                continue
            if cond.get("value") is None:
                continue
            cond_interval = _interval(cond.get("operator", "="), cond.get("value"), cond.get("value2"))
            if _overlaps(q_interval, cond_interval):
                matched.append(cond)
    return matched


def _bfs_expand(store: GraphStore, seed_ids: set[str], depth: int = 2) -> set[str]:
    seen = set(seed_ids)
    frontier = set(seed_ids)
    for _ in range(depth):
        next_frontier = set()
        for node_id in frontier:
            for _, t, rtype in store.out_edges(node_id):
                if rtype in EXPANSION_RELATIONS:
                    next_frontier.add(t)
            for s, _, rtype in store.in_edges(node_id):
                if rtype in EXPANSION_RELATIONS:
                    next_frontier.add(s)
        frontier = next_frontier - seen
        seen |= frontier
    return seen


def _passes_geography(entity: dict, geo_filters: list[str]) -> bool:
    if not geo_filters:
        return True
    geo = entity.get("geography")
    return geo in geo_filters or geo == "Global" or geo is None


def _passes_year(entity: dict, year_range: Optional[tuple[int, int]]) -> bool:
    if not year_range:
        return True
    year = entity.get("year")
    if year is None:
        return True  # не привязано ко времени, не исключаем
    return year_range[0] <= year <= year_range[1]


def _passes_confidence(entity: dict, confidence_filter: Optional[list[str]]) -> bool:
    if not confidence_filter:
        return True
    return entity.get("confidence") in confidence_filter


def _related(store: GraphStore, node_id: str, rel_type: str) -> list[dict]:
    ids = [t for _, t, rt in store.out_edges(node_id) if rt == rel_type]
    ids += [s for s, _, rt in store.in_edges(node_id) if rt == rel_type]
    return [store.get_entity(i) for i in ids if store.get_entity(i)]


def _entity_summary(store: GraphStore, entity: dict) -> dict:
    eid = entity["id"]
    summary = {
        "id": eid,
        "type": entity["type"],
        "name_ru": entity.get("name_ru"),
        "name_en": entity.get("name_en"),
        "geography": entity.get("geography"),
        "year": entity.get("year"),
        "confidence": entity.get("confidence"),
        "last_updated": entity.get("last_updated"),
        "sensitivity": entity.get("sensitivity", "public"),
        "description": entity.get("description"),
    }
    if entity["type"] in ("Experiment", "Process", "Patent", "Standard"):
        conditions = _related(store, eid, "OPERATES_AT_CONDITION")
        outputs = _related(store, eid, "PRODUCES_OUTPUT")
        publications = _related(store, eid, "DESCRIBED_IN")
        conclusions = _related(store, eid, "CONCLUDES")
        summary["conditions"] = [{"name_ru": c.get("name_ru"), "parameter": c.get("parameter"),
                                   "operator": c.get("operator"), "value": c.get("value"),
                                   "value2": c.get("value2"), "unit": c.get("unit")} for c in conditions]
        summary["outputs"] = [{"name_ru": o.get("name_ru"), "parameter": o.get("parameter"),
                                "value": o.get("value"), "unit": o.get("unit")} for o in outputs]
        summary["publications"] = [{"id": p["id"], "name_ru": p.get("name_ru"), "geography": p.get("geography"),
                                     "year": p.get("year")} for p in publications]
        summary["conclusions"] = [{"id": c["id"], "name_ru": c.get("name_ru")} for c in conclusions]
    if entity["type"] == "Publication":
        authors = _related(store, eid, "AUTHORED_BY")
        summary["authors"] = [{"id": a["id"], "name_ru": a.get("name_ru")} for a in authors]
    return summary


def build_glossary(store: GraphStore) -> dict[str, Any]:
    """Структурированный справочник материалов, оборудования, стандартов и
    единиц измерения. ТЗ требует их как импортируемые справочники; это
    живое, просматриваемое представление, получаемое напрямую из графа и
    из шаблонов единиц измерения, распознаваемых NLP-слоем
    (nlp.UNIT_PATTERNS), поэтому оно никогда не может разойтись с тем, что
    реально есть в данных."""
    materials, equipment, standards = [], [], []
    for entity in store.all_entities():
        if not store.is_curated_id(entity["id"]) or entity["type"] not in ("Material", "Equipment", "Standard"):
            continue
        row = {"id": entity["id"], "name_ru": entity.get("name_ru"), "name_en": entity.get("name_en"),
               "aliases": entity.get("aliases", []), "geography": entity.get("geography"),
               "confidence": entity.get("confidence"), "description": entity.get("description")}
        {"Material": materials, "Equipment": equipment, "Standard": standards}[entity["type"]].append(row)

    for bucket in (materials, equipment, standards):
        bucket.sort(key=lambda r: r["name_ru"] or "")

    units = [{"canonical": canonical, "pattern": pattern} for pattern, canonical in nlp.UNIT_PATTERNS]
    return {"materials": materials, "equipment": equipment, "standards": standards, "units": units}


def detect_contradictions(store: GraphStore, candidate_ids: set[str]) -> list[dict]:
    out = []
    seen_pairs = set()
    for node_id in candidate_ids:
        entity = store.get_entity(node_id)
        if not entity or entity["type"] != "Conclusion":
            continue
        for _, t, rtype in store.out_edges(node_id, "CONTRADICTS"):
            pair = tuple(sorted([node_id, t]))
            if pair in seen_pairs:
                continue
            seen_pairs.add(pair)
            a, b = store.get_entity(pair[0]), store.get_entity(pair[1])
            if a and b:
                out.append({
                    "a": {"id": a["id"], "name_ru": a.get("name_ru"), "geography": a.get("geography")},
                    "b": {"id": b["id"], "name_ru": b.get("name_ru"), "geography": b.get("geography")},
                })
    return out


def _supporting_sources_count(store: GraphStore, conclusion_id: str) -> int:
    return len({t for _, t, _ in store.out_edges(conclusion_id, "VALIDATED_BY")})


def build_consensus_summary(store: GraphStore, conclusions: list[dict], contradictions: list[dict]) -> list[dict]:
    """Выводы среди результатов, НЕ участвующие ни в одном обнаруженном
    противоречии -- то есть среди источников, рассмотренных для этого
    запроса, не найдено конфликтующего утверждения. Каждая запись сообщает,
    сколько разных публикаций его подтверждают, согласно требованию ТЗ
    "укажите степень уверенности и количество подтверждающих источников"."""
    disputed_ids = {c["a"]["id"] for c in contradictions} | {c["b"]["id"] for c in contradictions}
    consensus = []
    for concl in conclusions:
        if concl["id"] in disputed_ids:
            continue
        consensus.append({
            "id": concl["id"],
            "name_ru": concl.get("name_ru"),
            "geography": concl.get("geography"),
            "confidence": concl.get("confidence"),
            "supporting_sources": _supporting_sources_count(store, concl["id"]),
        })
    return consensus


def build_recommendations(store: GraphStore, seed_ids: set[str], already_shown_ids: set[str],
                           depth: int = 4) -> dict[str, list[dict]]:
    """Рекомендации "вас также может заинтересовать" по близости в графе:
    сущности/эксперты, достижимые чуть дальше в курируемой онтологии, чем
    основной радиус поиска (depth=2) -- смежные технологии/материалы и
    эксперты, работающие в соседних областях, которые прямой поиск не
    показал бы, согласно требованию ТЗ "похожие кейсы... из смежных
    областей" / "эксперты... с аналогичными задачами". Намеренно остаётся
    в пределах курируемой онтологии (а не слоя корпуса из ~2000 документов),
    иначе "смежное" взорвалось бы шумом из несвязанных хаб-узлов точно так
    же, как это происходило с обычным BFS для результатов поиска (см.
    _curated_context)."""
    extended = _curated_context(store, seed_ids, depth=depth)
    new_ids = extended - already_shown_ids - seed_ids

    similar_cases, adjacent_experts, adjacent_facilities = [], [], []
    for node_id in new_ids:
        entity = store.get_entity(node_id)
        if not entity:
            continue
        if entity["type"] in ("Process", "Material", "Equipment", "Topic", "Experiment"):
            similar_cases.append({"id": entity["id"], "type": entity["type"], "name_ru": entity.get("name_ru")})
        elif entity["type"] == "Expert":
            adjacent_experts.append({"id": entity["id"], "name_ru": entity.get("name_ru"),
                                      "geography": entity.get("geography")})
        elif entity["type"] == "Facility":
            adjacent_facilities.append({"id": entity["id"], "name_ru": entity.get("name_ru"),
                                         "geography": entity.get("geography")})

    similar_cases.sort(key=lambda e: e["name_ru"] or "")
    adjacent_experts.sort(key=lambda e: e["name_ru"] or "")
    adjacent_facilities.sort(key=lambda e: e["name_ru"] or "")
    return {"similar_cases": similar_cases[:8], "adjacent_experts": adjacent_experts[:8],
            "adjacent_facilities": adjacent_facilities[:8]}


GEOGRAPHY_COVERAGE_TYPES = {"Process", "Equipment", "Material"}


def geography_coverage_report(store: GraphStore, allow_internal: bool = True) -> dict[str, list[dict]]:
    """Для каждого курируемого Process/Equipment/Material рассмотреть каждый
    источник, который его описывает/упоминает (курируемые Publication через
    DESCRIBED_IN, реальные документы корпуса через MENTIONS) и
    классифицировать как задокументированный только в RU-источниках, только
    в International, в обоих, или неясно (не найдено источника с указанной
    географией) -- напрямую отвечает на "какие технологии описаны только в
    отечественной или только в зарубежной практике"."""
    report: dict[str, list[dict]] = {"ru_only": [], "international_only": [], "both": [], "unclear": []}
    for entity in store.all_entities():
        if entity["type"] not in GEOGRAPHY_COVERAGE_TYPES or not store.is_curated_id(entity["id"]):
            continue
        source_ids = {t for _, t, rt in store.out_edges(entity["id"], "DESCRIBED_IN")}
        source_ids |= {s for s, _, rt in store.in_edges(entity["id"], "MENTIONS")}

        geos = set()
        for sid in source_ids:
            pub = store.get_entity(sid)
            if not pub or pub.get("type") != "Publication":
                continue
            if not allow_internal and pub.get("sensitivity") == "internal":
                continue
            geo = pub.get("geography")
            if geo in ("RU", "International"):
                geos.add(geo)

        row = {"id": entity["id"], "name_ru": entity.get("name_ru"), "type": entity["type"],
               "source_count": len(source_ids)}
        if geos == {"RU"}:
            report["ru_only"].append(row)
        elif geos == {"International"}:
            report["international_only"].append(row)
        elif geos == {"RU", "International"}:
            report["both"].append(row)
        else:
            report["unclear"].append(row)
    return report


def detect_gaps(store: GraphStore, seed_entities: list[dict]) -> list[dict]:
    """Если запрос называет вместе >= 2 конкретных (не общих) сущностей
    (например, Process + Material + Condition), проверить, связан ли с
    ними всеми хотя бы один Experiment. Если нет, сообщить об этом как о
    пробеле в знаниях, показав все имеющиеся частичные совпадения."""
    focus_types = {"Process", "Material", "Condition", "Equipment"}
    focus = [e for e in seed_entities if e["type"] in focus_types]
    if len(focus) < 2:
        return []

    experiments = store.find_by_type("Experiment")
    gaps = []
    focus_ids = {e["id"] for e in focus}

    fully_covered = False
    partials = []
    for exp in experiments:
        exp_id = exp["id"]
        linked = _bfs_expand(store, {exp_id}, depth=1)
        covered = focus_ids & linked
        if focus_ids.issubset(linked):
            fully_covered = True
            break
        if covered:
            partials.append((exp, covered))

    if not fully_covered:
        gaps.append({
            "description": "Не найдено ни одного эксперимента/публикации, одновременно охватывающих все запрошенные условия",
            "requested_combination": [{"id": e["id"], "name_ru": e.get("name_ru"), "type": e["type"]} for e in focus],
            "partial_matches": [
                {"experiment": {"id": exp["id"], "name_ru": exp.get("name_ru"), "geography": exp.get("geography")},
                 "covers": [store.get_entity(i).get("name_ru") for i in covered]}
                for exp, covered in partials
            ],
        })
    return gaps


MAX_RESULTS_PER_BUCKET = 25


SPECIFIC_TYPES = {"Process", "Equipment", "Condition", "Experiment", "Conclusion"}


def _is_curated(store: GraphStore, node_id: str) -> bool:
    entity = store.get_entity(node_id)
    return entity is not None and "path" not in entity


def _curated_context(store: GraphStore, seed_ids: set[str], depth: int = 2) -> set[str]:
    """Окрестность курируемой онтологии вокруг стартовых сущностей,
    расширяемая БЕЗ прохождения через узел документа/архива корпуса.

    Используется как основа для _relevance_score. Если бы здесь разрешались
    реальные документы в качестве промежуточных шагов, один богато
    размеченный "хаб"-документ (упоминающий много курируемых понятий) тянул
    бы в свой контекст несвязанные курируемые сущности и затем получал бы
    высокий балл против них -- самоусиливающийся цикл, из-за которого один
    хаб-документ занимал бы первое место для любого запроса независимо от
    темы. Ограничение расширения только связями курируемое-в-курируемое
    делает этот набор контекста чистой функцией от малой демо-онтологии,
    независимой от того, какие документы есть в корпусе.
    """
    seen = {i for i in seed_ids if _is_curated(store, i)}
    frontier = set(seen)
    for _ in range(depth):
        next_frontier = set()
        for node_id in frontier:
            for _, t, rtype in store.out_edges(node_id):
                if rtype in EXPANSION_RELATIONS and _is_curated(store, t):
                    next_frontier.add(t)
            for s, _, rtype in store.in_edges(node_id):
                if rtype in EXPANSION_RELATIONS and _is_curated(store, s):
                    next_frontier.add(s)
        frontier = next_frontier - seen
        seen |= frontier
    return seen


def _relevance_score(store: GraphStore, node_id: str, curated_ids: set[str]) -> int:
    """Насколько сильно этот узел связан с курируемыми сущностями,
    релевантными этому запросу (набор кандидатов BFS, ограниченный малой
    курируемой онтологией, а не ~2000 загруженными реальными документами).

    При таком количестве реальных документов в графе 2-шаговый BFS от
    распространённого материала (например, "сульфаты") затягивает сотни
    косвенно связанных документов через хаб-узлы -- упоминания общих
    Material повсеместны в корпусе по металлургии сульфидных руд и не
    различают релевантность. Связи Process/Equipment/Condition/Experiment/
    Conclusion гораздо более специфичны (документ редко упоминает "Обратный
    осмос" или точное условие концентрации случайно), поэтому им даётся
    более высокий вес. Это позволяет действительно релевантным источникам
    подниматься наверх, а длинный хвост безопасно обрезать.
    """
    score = 0
    for _, t, _ in store.out_edges(node_id):
        if t in curated_ids:
            ent = store.get_entity(t)
            score += 3 if ent and ent["type"] in SPECIFIC_TYPES else 1
    for s, _, _ in store.in_edges(node_id):
        if s in curated_ids:
            ent = store.get_entity(s)
            score += 3 if ent and ent["type"] in SPECIFIC_TYPES else 1
    return score


def answer_query(
    store: GraphStore,
    text: str,
    geography_filter: Optional[list[str]] = None,
    year_from: Optional[int] = None,
    year_to: Optional[int] = None,
    confidence_filter: Optional[list[str]] = None,
) -> dict[str, Any]:
    constraints = nlp.extract_numeric_constraints(text)
    geo_filters = geography_filter or nlp.extract_geography_filters(text)
    year_range = None
    if year_from is not None and year_to is not None:
        year_range = (year_from, year_to)
    else:
        year_range = nlp.extract_year_range(text)

    text_matches = match_entities_fuzzy(store, text)
    constraint_matches = match_conditions_by_constraints(store, constraints)
    seed_entities = {e["id"]: e for e in text_matches + constraint_matches}

    candidate_ids = _bfs_expand(store, set(seed_entities.keys()), depth=2)

    curated_context_ids = _curated_context(store, seed_entities.keys())
    grouped: dict[str, list[dict]] = {"processes": [], "experiments": [], "publications": [], "conclusions": [], "topics": [], "patents": [], "standards": []}
    bucket_totals: dict[str, int] = {}
    for node_id in candidate_ids:
        entity = store.get_entity(node_id)
        if not entity or entity["type"] not in RESULT_TYPES:
            continue
        if not _passes_geography(entity, geo_filters):
            continue
        if not _passes_year(entity, year_range):
            continue
        if not _passes_confidence(entity, confidence_filter):
            continue
        bucket = {"Process": "processes", "Equipment": "processes", "Experiment": "experiments",
                  "Publication": "publications", "Conclusion": "conclusions", "Topic": "topics",
                  "Patent": "patents", "Standard": "standards"}[entity["type"]]
        summary = _entity_summary(store, entity)
        summary["_relevance"] = _relevance_score(store, node_id, curated_context_ids)
        grouped[bucket].append(summary)

    for name, bucket in grouped.items():
        bucket.sort(key=lambda e: (e["_relevance"], e.get("year") or 0, e.get("name_ru") or ""), reverse=True)
        bucket_totals[name] = len(bucket)
        del grouped[name][MAX_RESULTS_PER_BUCKET:]
        for e in bucket:
            del e["_relevance"]

    contradictions = detect_contradictions(store, candidate_ids)
    gaps = detect_gaps(store, list(seed_entities.values()))
    consensus_conclusions = build_consensus_summary(store, grouped["conclusions"], contradictions)

    experts, facilities = [], []
    for node_id in candidate_ids:
        entity = store.get_entity(node_id)
        if entity and entity["type"] == "Expert":
            experts.append(_entity_summary(store, entity))
        if entity and entity["type"] == "Facility":
            facilities.append(_entity_summary(store, entity))

    recommendations = build_recommendations(store, set(seed_entities.keys()), candidate_ids)

    subgraph = store.subgraph_for_ids(list(seed_entities.keys()), depth=2)

    return {
        "query": text,
        "parsed": {
            "matched_entities": [{"id": e["id"], "type": e["type"], "name_ru": e.get("name_ru")} for e in seed_entities.values()],
            "constraints": constraints,
            "geography_filters": geo_filters,
            "year_range": list(year_range) if year_range else None,
            "confidence_filter": confidence_filter,
        },
        "results": grouped,
        "results_total_counts": bucket_totals,
        "experts": experts,
        "facilities": facilities,
        "contradictions": contradictions,
        "consensus_conclusions": consensus_conclusions,
        "gaps": gaps,
        "recommendations": recommendations,
        "subgraph": subgraph,
    }
