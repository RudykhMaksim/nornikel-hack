"""Быстрый, дешёвый каталожный обход реального разнородного исходного корпуса.

Здесь нет разбора -- только метаданные файловой системы (+ один уровень
листинга zip-архивов), поэтому весь корпус из ~1450 файлов, ~5 ГБ можно
каталогизировать за секунды, даже несмотря на то, что глубокое извлечение
текста позже получает только ограниченное подмножество.
"""
import hashlib
import os
import zipfile
from dataclasses import dataclass
from pathlib import Path
from typing import Optional

ARCHIVE_EXTS = {"zip", "rar", "001", "002"}


def _make_id(prefix: str, rel_path: str) -> str:
    h = hashlib.sha1(rel_path.encode("utf-8")).hexdigest()[:12]
    return f"{prefix}_{h}"


@dataclass
class FileRecord:
    id: str
    rel_path: str
    filename: str
    ext: str
    category: str
    size_bytes: int
    is_archive: bool = False
    archive_parent: Optional[str] = None


def walk_corpus(root: Path) -> list[FileRecord]:
    root = Path(root)
    records: list[FileRecord] = []
    for dirpath, _dirnames, filenames in os.walk(root):
        for fname in filenames:
            full = Path(dirpath) / fname
            try:
                size = full.stat().st_size
            except OSError:
                continue
            rel = full.relative_to(root).as_posix()
            ext = fname.rsplit(".", 1)[-1].lower() if "." in fname else ""
            category = rel.split("/", 1)[0]
            rec_id = _make_id("doc", rel)
            is_archive = ext in ARCHIVE_EXTS
            records.append(FileRecord(rec_id, rel, fname, ext, category, size, is_archive))

            if ext == "zip":
                try:
                    with zipfile.ZipFile(full) as z:
                        for info in z.infolist():
                            if info.is_dir():
                                continue
                            inner_rel = f"{rel}::{info.filename}"
                            inner_ext = info.filename.rsplit(".", 1)[-1].lower() if "." in info.filename else ""
                            inner_name = info.filename.rsplit("/", 1)[-1]
                            inner_id = _make_id("doc", inner_rel)
                            records.append(FileRecord(inner_id, inner_rel, inner_name, inner_ext, category, info.file_size, False, rec_id))
                except Exception:
                    pass
    return records
