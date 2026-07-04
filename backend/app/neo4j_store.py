"""Тонкая обёртка над официальным драйвером Neo4j для настоящего, живого
пути Cypher-запросов внутри запущенного API -- а не только офлайн-экспорта
в scripts/export_to_neo4j_cypher.py. Опционально: приложение FastAPI
полностью работает без запущенного Neo4j (авторитетным источником является
networkx GraphStore); этот модуль позволяет жюри/рецензенту направить
приложение на экземпляр Neo4j из docker-compose и выполнять настоящий
Cypher по тем же данным.
"""
import os
import re
from typing import Any, Optional

from neo4j import GraphDatabase
from neo4j.exceptions import Neo4jError, ServiceUnavailable

NEO4J_URI = os.environ.get("NEO4J_URI", "bolt://localhost:7688")
NEO4J_USER = os.environ.get("NEO4J_USER", "neo4j")
NEO4J_PASSWORD = os.environ.get("NEO4J_PASSWORD", "mining-rd-2026")

# Защита "только чтение": отклонять всё, что похоже на инструкцию записи/
# администрирования. Это намеренно простой список запрещённых ключевых
# слов (не полноценный парсер Cypher) -- достаточен для демо-эндпоинта,
# показываемого рецензенту, но не замена настоящей роли пользователя БД
# "только чтение" в продакшене.
_WRITE_KEYWORDS = re.compile(
    r"\b(CREATE|MERGE|DELETE|DETACH|SET|REMOVE|DROP|CALL\s+db\.|CALL\s+apoc\.|LOAD\s+CSV)\b",
    re.IGNORECASE,
)

_driver = None


def _get_driver():
    global _driver
    if _driver is None:
        # короткий таймаут: Neo4j опционален, поэтому рецензент без запущенного
        # Neo4j не должен долго ждать, пока драйвер сдастся
        _driver = GraphDatabase.driver(NEO4J_URI, auth=(NEO4J_USER, NEO4J_PASSWORD),
                                        connection_timeout=3.0, connection_acquisition_timeout=3.0)
    return _driver


def is_available() -> tuple[bool, Optional[str]]:
    try:
        _get_driver().verify_connectivity()
        return True, None
    except Exception as exc:  # noqa: BLE001 -- намеренно широкий перехват: любой сбой соединения означает "недоступно"
        return False, str(exc)


def status() -> dict[str, Any]:
    ok, error = is_available()
    if not ok:
        return {"available": False, "uri": NEO4J_URI, "error": error}
    rows = run_read_query("MATCH (n) RETURN count(n) AS nodes")
    edges = run_read_query("MATCH ()-[r]->() RETURN count(r) AS edges")
    return {
        "available": True,
        "uri": NEO4J_URI,
        "node_count": rows[0]["nodes"] if rows else None,
        "relationship_count": edges[0]["edges"] if edges else None,
    }


def run_read_query(cypher: str, params: Optional[dict] = None, limit: int = 200) -> list[dict]:
    if _WRITE_KEYWORDS.search(cypher):
        raise ValueError("Only read queries are permitted on this endpoint")
    driver = _get_driver()
    with driver.session() as session:
        try:
            result = session.run(cypher, params or {})
            rows = [dict(record) for record in result]
        except Neo4jError as exc:
            raise ValueError(f"Cypher error: {exc}") from exc
        except ServiceUnavailable as exc:
            raise ConnectionError(f"Neo4j unavailable: {exc}") from exc
    return _to_jsonable(rows)[:limit]


def _to_jsonable(rows: list[dict]) -> list[dict]:
    """Объекты Node/Relationship из Neo4j не сериализуются в JSON напрямую;
    разворачиваем их в обычные словари их свойств."""
    out = []
    for row in rows:
        clean = {}
        for key, value in row.items():
            if hasattr(value, "items") and hasattr(value, "labels"):
                clean[key] = {"labels": list(value.labels), **dict(value.items())}
            elif hasattr(value, "type") and hasattr(value, "items"):
                clean[key] = {"type": value.type, **dict(value.items())}
            else:
                clean[key] = value
        out.append(clean)
    return out
