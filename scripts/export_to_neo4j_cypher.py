"""Экспорт объединённого графа (data/seed_data.json + data/corpus_data.json)
в обычный Cypher-скрипт, который можно загрузить в настоящий экземпляр
Neo4j через cypher-shell -- путь к развёртыванию "настоящей" графовой БД,
упомянутому в плане, без необходимости запускать Neo4j для самого FastAPI
MVP (который использует GraphStore на базе networkx в памяти).

Использование (из корня проекта):
    python scripts/export_to_neo4j_cypher.py
    # затем, с запущенным Neo4j (см. docker-compose.yml):
    cat cypher/import.cypher | cypher-shell -u neo4j -p password
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "backend"))

from app.graph_store import GraphStore  # noqa: E402

OUT_PATH = Path(__file__).resolve().parents[1] / "cypher" / "import.cypher"

SCALAR_TYPES = (str, int, float, bool, type(None))


def esc(value) -> str:
    if value is None:
        return "null"
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, (int, float)):
        return str(value)
    text = str(value).replace("\\", "\\\\").replace('"', '\\"').replace("\n", " ").replace("\r", " ")
    return f'"{text}"'


def node_props(entity: dict) -> str:
    parts = []
    for key, value in entity.items():
        if key == "type":
            continue
        if isinstance(value, list):
            if not value or not all(isinstance(v, SCALAR_TYPES) for v in value):
                continue
            items = ", ".join(esc(v) for v in value)
            parts.append(f"{key}: [{items}]")
        elif isinstance(value, SCALAR_TYPES):
            parts.append(f"{key}: {esc(value)}")
    return ", ".join(parts)


def sanitize_label(type_name: str) -> str:
    return "".join(ch for ch in type_name if ch.isalnum() or ch == "_") or "Entity"


def main():
    store = GraphStore()
    lines = [
        "// Автоматически сгенерировано scripts/export_to_neo4j_cypher.py -- не редактировать вручную",
        "CREATE CONSTRAINT entity_id_unique IF NOT EXISTS FOR (e:Entity) REQUIRE e.id IS UNIQUE;",
        "",
    ]

    node_count = 0
    for node_id, data in store.graph.nodes(data=True):
        label = sanitize_label(data.get("type", "Entity"))
        props = node_props(data)
        lines.append(f"CREATE (:Entity:{label} {{{props}}});")
        node_count += 1

    lines.append("")
    rel_count = 0
    for source, target, data in store.graph.edges(data=True):
        rel_type = sanitize_label(data.get("type", "RELATED_TO")).upper()
        lines.append(
            f'MATCH (a:Entity {{id: {esc(source)}}}), (b:Entity {{id: {esc(target)}}}) '
            f"CREATE (a)-[:{rel_type}]->(b);"
        )
        rel_count += 1

    OUT_PATH.parent.mkdir(parents=True, exist_ok=True)
    OUT_PATH.write_text("\n".join(lines) + "\n", encoding="utf-8")
    print(f"Wrote {OUT_PATH} ({node_count} node CREATEs, {rel_count} relationship CREATEs)")


if __name__ == "__main__":
    main()
