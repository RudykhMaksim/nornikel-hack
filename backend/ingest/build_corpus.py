"""Оркестратор загрузки реального, неупорядоченного исходного корпуса.

Использование (из корня проекта):
    python -m backend.ingest.build_corpus --root "../Источники информации"

Двухслойный подход (см. план для обоснования):
1. Быстрый каталог каждого файла (только метаданные) -> всегда полный,
   ~1450 файлов.
2. Ограниченное глубокое извлечение текста + разметка по ключевым словам,
   с приоритетом дешёвых офисных форматов (docx/docm/pptx/xlsx, всегда
   обрабатываются полностью) и выборки PDF, приоритизированной по
   размеру/ключевым словам, ограниченной параметрами --max-deep-files и
   --time-budget-sec, чтобы запуск всегда завершался предсказуемо. Всё
   остальное остаётся каталогизированным (доступным для просмотра по
   имени файла/пути), но не разбирается на текст в этом проходе -- повторный
   запуск с большим бюджетом обработает больше файлов.
"""
import argparse
import json
import sys
import time
from pathlib import Path
from typing import Optional

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from backend.app import versioning  # noqa: E402
from backend.app.graph_store import GraphStore  # noqa: E402
from backend.ingest.domain_vocab import EXTRA_CONCEPTS  # noqa: E402
from backend.ingest.extract_text import extract_text, extractable  # noqa: E402
from backend.ingest.walk_corpus import FileRecord, walk_corpus  # noqa: E402

INTERNAL_CATEGORIES = {"Обзоры", "Доклады", "Статьи"}  # R&D-материалы, авторства института
MIN_ALIAS_LEN_FOR_TAGGING = 3

# Все четыре названия журналов из категории "Журналы" (Горная промышленность,
# Горный журнал, Обогащение руд, Цветные металлы) -- русскоязычные отраслевые
# журналы; "Материалы конференций" в этом корпусе преимущественно состоят из
# англоязычных международных материалов (ALTA, Copper 20XX и т.д.).
# Оба варианта -- разумные допущения, но не гарантии -- всё остальное
# определяется грубым соотношением кириллических и латинских символов в
# имени файла + начале текста, либо остаётся без разметки, если сигнала
# недостаточно ни для того, ни для другого.
CATEGORY_GEOGRAPHY_PRIOR = {"Журналы": "RU", "Материалы конференций": "International"}


def guess_geography(category: str, filename: str, text: str) -> Optional[str]:
    if category in CATEGORY_GEOGRAPHY_PRIOR:
        return CATEGORY_GEOGRAPHY_PRIOR[category]
    sample = filename + (text[:500] if text else "")
    cyr = sum(1 for ch in sample if "а" <= ch.lower() <= "я")
    lat = sum(1 for ch in sample if "a" <= ch.lower() <= "z")
    if cyr + lat < 8:
        return None
    return "RU" if cyr > lat else "International"


def tag_document(text: str, candidates: list[dict]) -> list[str]:
    text_low = text.lower()
    matched = []
    for ent in candidates:
        aliases = [ent.get("name_ru", ""), ent.get("name_en", "") or ""] + ent.get("aliases", [])
        for alias in aliases:
            if alias and len(alias) >= MIN_ALIAS_LEN_FOR_TAGGING and alias.lower() in text_low:
                matched.append(ent["id"])
                break
    return matched


def priority_key(rec: FileRecord, keyword_terms: list[str]):
    if rec.ext != "pdf":
        tier = 0
    elif any(k in rec.filename.lower() for k in keyword_terms if len(k) >= 4):
        tier = 1
    else:
        tier = 2
    return (tier, rec.size_bytes)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    default_root = Path(__file__).resolve().parents[2].parent / "Источники информации"
    parser.add_argument("--root", default=str(default_root))
    parser.add_argument("--out", default=str(Path(__file__).resolve().parents[2] / "data" / "corpus_data.json"))
    parser.add_argument("--max-deep-files", type=int, default=260)
    parser.add_argument("--time-budget-sec", type=int, default=900)
    args = parser.parse_args()

    root = Path(args.root)
    if not root.exists():
        print(f"ERROR: root not found: {root}")
        sys.exit(1)

    out_path = Path(args.out)
    prev_entity_ids: set[str] = set()
    prev_relation_keys: set[tuple[str, str, str]] = set()
    prev_deep_parsed_ids: set[str] = set()
    if out_path.exists():
        try:
            prev = json.loads(out_path.read_text(encoding="utf-8"))
            prev_entity_ids = {e["id"] for e in prev["entities"]}
            prev_relation_keys = {(r["from"], r["to"], r["type"]) for r in prev["relations"]}
            prev_deep_parsed_ids = {e["id"] for e in prev["entities"] if e.get("deep_parsed")}
        except (json.JSONDecodeError, KeyError, OSError) as exc:
            print(f"WARNING: couldn't read previous {out_path} for diffing ({exc}); treating this as a first run")

    print(f"Walking corpus root: {root}")
    records = walk_corpus(root)
    print(f"Catalogued {len(records)} file records (incl. one level of zip-inner entries)")

    store = GraphStore()
    curated_entities = [e for e in store.all_entities()
                        if e["type"] in ("Material", "Process", "Equipment", "Condition", "Standard", "Patent")]
    tagging_candidates = curated_entities + EXTRA_CONCEPTS
    keyword_terms = [alias.lower() for c in tagging_candidates
                     for alias in ([c.get("name_ru", "")] + c.get("aliases", [])) if alias]

    deep_eligible = [r for r in records if not r.is_archive and extractable(r.ext, r.size_bytes)]
    deep_eligible.sort(key=lambda r: priority_key(r, keyword_terms))
    print(f"{len(deep_eligible)} files are eligible for deep text extraction "
          f"(will process up to {args.max_deep_files} within {args.time_budget_sec}s budget)")

    entities = []
    relations = []
    by_id = {}
    for rec in records:
        etype = "Archive" if rec.is_archive else "Publication"
        ent = {
            "id": rec.id,
            "type": etype,
            "name_ru": rec.filename,
            "aliases": [],
            "path": rec.rel_path,
            "category": rec.category,
            "ext": rec.ext,
            "size_bytes": rec.size_bytes,
            "sensitivity": "internal" if rec.category in INTERNAL_CATEGORIES else "public",
            "confidence": "verified",
            "deep_parsed": False,
            "geography": guess_geography(rec.category, rec.filename, ""),
        }
        entities.append(ent)
        by_id[rec.id] = ent
        if rec.archive_parent:
            relations.append({"from": rec.archive_parent, "type": "CONTAINS", "to": rec.id})

    start = time.time()
    deep_count = 0
    mentions_count = 0
    for rec in deep_eligible:
        if deep_count >= args.max_deep_files or (time.time() - start) > args.time_budget_sec:
            break
        full_path = root / rec.rel_path
        text = extract_text(full_path, rec.ext)
        if not text:
            continue
        deep_count += 1
        ent = by_id[rec.id]
        ent["deep_parsed"] = True
        ent["snippet"] = text[:400]
        ent["char_count"] = len(text)
        if rec.category not in CATEGORY_GEOGRAPHY_PRIOR:
            ent["geography"] = guess_geography(rec.category, rec.filename, text)
        matched_ids = tag_document(text, tagging_candidates)
        for mid in matched_ids:
            relations.append({"from": rec.id, "type": "MENTIONS", "to": mid})
            mentions_count += 1
        if deep_count % 25 == 0:
            print(f"  deep-parsed {deep_count}/{min(args.max_deep_files, len(deep_eligible))} ...")

    elapsed = time.time() - start
    print(f"Deep-parsed {deep_count} files, found {mentions_count} MENTIONS relations, elapsed {elapsed:.1f}s")

    for c in EXTRA_CONCEPTS:
        entities.append(dict(c))

    new_entity_ids = {e["id"] for e in entities}
    new_relation_keys = {(r["from"], r["to"], r["type"]) for r in relations}
    new_deep_parsed_ids = {e["id"] for e in entities if e.get("deep_parsed")}

    new_documents = len(new_entity_ids - prev_entity_ids)
    new_relations_found = len(new_relation_keys - prev_relation_keys)
    newly_deep_parsed = len(new_deep_parsed_ids - prev_deep_parsed_ids)

    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(json.dumps({"entities": entities, "relations": relations}, ensure_ascii=False, indent=1), encoding="utf-8")

    archive_count = sum(1 for r in records if r.is_archive)
    print(f"Wrote {out_path} ({len(entities)} entities, {len(relations)} relations)")
    print(f"Summary: {len(records)} catalogued, {deep_count} deep-parsed, {archive_count} archives (metadata-only)")

    if prev_entity_ids or prev_relation_keys:
        print(f"Changes since previous run: {new_documents} new documents, "
              f"{new_relations_found} new relations, {newly_deep_parsed} files newly deep-parsed")
    run_log = versioning.record_ingestion_run({
        "root": str(root),
        "total_entities": len(entities),
        "total_relations": len(relations),
        "deep_parsed": deep_count,
        "new_documents": new_documents,
        "new_relations_found": new_relations_found,
        "newly_deep_parsed": newly_deep_parsed,
        "is_first_run": not (prev_entity_ids or prev_relation_keys),
    })
    print(f"Logged ingestion run to {versioning.INGESTION_HISTORY_PATH.name}: {run_log['ts']}")


if __name__ == "__main__":
    main()
