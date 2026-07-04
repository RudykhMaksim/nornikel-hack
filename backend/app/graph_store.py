"""Графовое хранилище в памяти на базе networkx, загружаемое из data/seed_data.json.

Заменяет собой настоящую графовую БД (Neo4j/Neptune/JanusGraph) для MVP.
Модель данных (типизированные узлы + типизированные связи с источником/
достоверностью/датой у каждой сущности) намеренно совместима с прямым
экспортом в Cypher -- см. scripts/export_to_neo4j_cypher.py.
"""
import json
import logging
from pathlib import Path
from typing import Any, Optional

import networkx as nx

logger = logging.getLogger(__name__)

DATA_PATH = Path(__file__).resolve().parents[2] / "data" / "seed_data.json"
CORPUS_DATA_PATH = Path(__file__).resolve().parents[2] / "data" / "corpus_data.json"


class GraphStore:
    def __init__(self, data_path: Path = DATA_PATH, corpus_data_path: Path = CORPUS_DATA_PATH):
        self.data_path = data_path
        self.corpus_data_path = corpus_data_path
        self.graph = nx.MultiDiGraph()
        self.synonyms: dict[str, str] = {}
        self._curated_ids: set[str] = set()
        self.load_warnings: list[str] = []
        self.load()

    def _load_file(self, path: Path, track_as_curated: bool) -> None:
        raw = json.loads(path.read_text(encoding="utf-8"))
        for entity in raw["entities"]:
            node_id = entity["id"]
            self.graph.add_node(node_id, **entity)
            if track_as_curated:
                self._curated_ids.add(node_id)
        for rel in raw["relations"]:
            self.graph.add_edge(rel["from"], rel["to"], key=rel["type"], type=rel["type"])
        self.synonyms.update(raw.get("synonyms", {}))

    def load(self) -> None:
        self.graph.clear()
        self.synonyms = {}
        self._curated_ids = set()
        self.load_warnings = []
        # сначала курируемая демо-онтология, чтобы id, на которые ссылается
        # MENTIONS в слое реального корпуса (загружается вторым), уже
        # существовали как узлы. Этот вызов не обёрнут в try/except --
        # сломанная вручную курируемая онтология означает, что всей системе
        # нечего отдавать, поэтому громкий отказ при старте предпочтительнее
        # работы вхолостую.
        self._load_file(self.data_path, track_as_curated=True)

        # слой корпуса из ~2000 документов сгенерирован машинно и гораздо
        # больше; если он отсутствует или повреждён (например, прерванный
        # запуск build_corpus.py оставил неполный файл), деградируем до
        # только курируемых данных, а не роняем весь API.
        if self.corpus_data_path.exists():
            try:
                self._load_file(self.corpus_data_path, track_as_curated=False)
            except (json.JSONDecodeError, KeyError, OSError) as exc:
                msg = f"Failed to load {self.corpus_data_path.name}, continuing with curated ontology only: {exc}"
                logger.warning(msg)
                self.load_warnings.append(msg)

    # -- базовые методы доступа -------------------------------------------
    def get_entity(self, entity_id: str) -> Optional[dict[str, Any]]:
        if entity_id not in self.graph.nodes:
            return None
        return dict(self.graph.nodes[entity_id])

    def all_entities(self) -> list[dict[str, Any]]:
        return [dict(data) for _, data in self.graph.nodes(data=True)]

    def find_by_type(self, entity_type: str) -> list[dict[str, Any]]:
        return [dict(data) for _, data in self.graph.nodes(data=True) if data.get("type") == entity_type]

    def out_edges(self, node_id: str, rel_type: Optional[str] = None) -> list[tuple[str, str, str]]:
        results = []
        for _, target, data in self.graph.out_edges(node_id, data=True):
            if rel_type is None or data.get("type") == rel_type:
                results.append((node_id, target, data.get("type")))
        return results

    def in_edges(self, node_id: str, rel_type: Optional[str] = None) -> list[tuple[str, str, str]]:
        results = []
        for source, _, data in self.graph.in_edges(node_id, data=True):
            if rel_type is None or data.get("type") == rel_type:
                results.append((source, node_id, data.get("type")))
        return results

    def neighbors(self, node_id: str, rel_type: Optional[str] = None) -> list[str]:
        return [t for _, t, _ in self.out_edges(node_id, rel_type)] + [s for s, _, _ in self.in_edges(node_id, rel_type)]

    # -- редактирование / версионирование ---------------------------------
    def is_curated(self, entity: dict[str, Any]) -> bool:
        """Курируемые (введённые вручную) сущности онтологии -- это те, что
        загружены из data_path (seed_data.json), отслеживаемые по id в
        момент загрузки -- А НЕ по наличию поля: некоторые курируемые
        сущности (например, заглушки Publication, цитирующие реальный
        документ-источник) законно тоже несут поле "path", а некоторые
        сущности из корпуса (дополнительные доменные Topics из
        backend/ingest/domain_vocab.py) законно его не имеют.
        Сущности, полученные из корпуса, являются каталожными записями, а
        не фактами, которые рецензент правит напрямую (для них вместо этого
        предусмотрены свободные аннотации)."""
        return entity.get("id") in self._curated_ids

    def is_curated_id(self, entity_id: str) -> bool:
        return entity_id in self._curated_ids

    def update_entity(self, entity_id: str, changes: dict[str, Any]) -> dict[str, Any]:
        """Применить изменения полей на месте; возвращает значения затронутых
        полей до изменения (чтобы вызывающий код записал их в историю версий)."""
        if entity_id not in self.graph.nodes:
            raise KeyError(entity_id)
        node = self.graph.nodes[entity_id]
        old_values = {k: node.get(k) for k in changes}
        node.update(changes)
        return old_values

    def persist_seed(self) -> None:
        """Записать курируемый (не корпусный) срез живого графа обратно в
        data_path, чтобы правки, сделанные через /api/entities/{id},
        переживали перезапуск. Слой корпуса из ~2000 документов
        перегенерируется build_corpus.py, а не правится вручную, поэтому
        здесь он намеренно исключён."""
        entities = [dict(data) for node_id, data in self.graph.nodes(data=True) if node_id in self._curated_ids]
        relations = []
        for source, target, data in self.graph.edges(data=True):
            if source in self._curated_ids and target in self._curated_ids and data.get("type") not in ("MENTIONS", "CONTAINS"):
                relations.append({"from": source, "to": target, "type": data.get("type")})
        payload = {"entities": entities, "relations": relations, "synonyms": self.synonyms}
        self.data_path.write_text(json.dumps(payload, ensure_ascii=False, indent=1), encoding="utf-8")

    # -- сопоставление по алиасам / тексту --------------------------------
    def match_entities_by_text(self, text: str) -> list[dict[str, Any]]:
        """Наивный словарный поиск: сопоставление имён/алиасов сущностей как подстрок текста (без учёта регистра)."""
        text_low = text.lower()
        matches = []
        for _, data in self.graph.nodes(data=True):
            candidates = [data.get("name_ru", ""), data.get("name_en", "") or ""] + data.get("aliases", [])
            for cand in candidates:
                if cand and len(cand) >= 2 and cand.lower() in text_low:
                    matches.append(data)
                    break
        return matches

    # -- извлечение подграфа для визуализации ------------------------------
    def subgraph_for_ids(self, ids: list[str], depth: int = 1) -> dict[str, Any]:
        seen = set(ids)
        frontier = set(ids)
        for _ in range(depth):
            next_frontier = set()
            for node_id in frontier:
                if node_id not in self.graph.nodes:
                    continue
                for _, t, _ in self.graph.out_edges(node_id, data=True):
                    next_frontier.add(t)
                for s, _, _ in self.graph.in_edges(node_id, data=True):
                    next_frontier.add(s)
            frontier = next_frontier - seen
            seen |= next_frontier

        nodes = [dict(self.graph.nodes[n]) for n in seen if n in self.graph.nodes]
        edges = []
        for s, t, data in self.graph.edges(data=True):
            if s in seen and t in seen:
                edges.append({"source": s, "target": t, "type": data.get("type")})
        return {"nodes": nodes, "edges": edges}


_store: Optional[GraphStore] = None


def get_store() -> GraphStore:
    global _store
    if _store is None:
        _store = GraphStore()
    return _store
