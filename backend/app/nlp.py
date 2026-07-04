"""Лёгкий NLP на правилах/словарях для MVP.

Промышленная система использовала бы spaCy/DeepPavlov/ruBERT (см. README).
Для MVP мы используем прозрачное, отлаживаемое сопоставление по regex +
словарям, которого "достаточно хорошо", чтобы корректно разбирать числовые
ограничения, географию и диапазоны дат в примерах запросов из ТЗ, при этом
оставаясь простым для аудита жюри/рецензентом (без чёрного ящика модели).
"""
import re
from datetime import date
from typing import Optional

UNIT_PATTERNS: list[tuple[str, str]] = [
    (r"мг\s*/\s*л|mg\s*/\s*l", "mg/l"),
    (r"мг\s*/\s*дм\s*3|мг\s*/\s*дм\s*³|mg\s*/\s*dm\s*3|mg\s*/\s*dm\s*³", "mg/dm3"),
    (r"м\s*3\s*/\s*ч|m\s*3\s*/\s*h|м\s*³\s*/\s*ч", "m3/h"),
    (r"°\s?[cс]|градус\w*", "°C"),  # примечание: в реальных документах корпуса после "°" встречаются и латинская C, и кириллическая С
    (r"а\s*/\s*м\s*2|а\s*/\s*м\s*²|a\s*/\s*m\s*2|a\s*/\s*m\s*²", "A/m2"),
    (r"млн\.?\s*руб\.?", "million_rub"),
    (r"млн\.?\s*(?:usd|\$)", "million_usd"),
    (r"%", "%"),
    (r"т\s*/\s*сут|t\s*/\s*day", "t/day"),
]
_UNIT_ALT = "|".join(f"(?:{p})" for p, _ in UNIT_PATTERNS)

_RANGE_RE = re.compile(rf"(\d+(?:[.,]\d+)?)\s*[-–—]\s*(\d+(?:[.,]\d+)?)\s*({_UNIT_ALT})", re.IGNORECASE)
_LE_RE = re.compile(
    rf"(?:≤|<=|не\s+более|до|not\s+more\s+than|no\s+more\s+than|at\s+most|up\s+to|below|under)"
    rf"\s*(\d+(?:[.,]\d+)?)\s*({_UNIT_ALT})",
    re.IGNORECASE,
)
_GE_RE = re.compile(
    rf"(?:≥|>=|не\s+менее|от|not\s+less\s+than|no\s+less\s+than|at\s+least|over|above|exceeding)"
    rf"\s*(\d+(?:[.,]\d+)?)\s*({_UNIT_ALT})",
    re.IGNORECASE,
)

PARAM_KEYWORDS: list[tuple[str, str]] = [
    (r"сульфат|sulfate", "sulfate_concentration"),
    (r"хлорид|chloride", "chloride_concentration"),
    (r"кальц|calcium|\bca\b", "calcium_concentration"),
    (r"магни(?!т)|magnesium|\bmg\b", "magnesium_concentration"),  # (?!т) исключает "магнит-" (магнетизм)
    (r"натри|sodium|\bna\b", "sodium_concentration"),
    (r"сухой остаток|dry residue|total dissolved solids|\btds\b", "dry_residue"),
    (r"скорост\w*\s+(?:потока|циркуляции)|flow\s*rate|циркуляц\w*|circulation\s*rate", "catholyte_flow_rate"),
    (r"температур|temperature", "temperature"),
    (r"капитальн\w*|capex|capital\s*(?:cost|expenditure)", "injection_capex"),
    (r"климат|climate", "climate"),
    (r"плотност\w*\s*тока|current\s*density", "current_density"),
    (r"производительност\w*|производств\w*\s*мощност\w*|(?:annual\s*)?capacity|throughput|output\s*rate", "annual_capacity"),
    (r"содержани\w*\s*меди|\bмеди\b|copper\s*(?:content|concentration|grade)", "copper_concentration"),
    (r"кислот\w*|acid\s*concentration", "sulfuric_acid_concentration"),
    (r"щёлочност\w*|щелочност\w*|alkalinity", "excess_alkalinity"),
]

GEO_RU_RE = re.compile(r"росси\w*|рф\b|отечественн\w*|российск\w*|russia\w*|domestic(?:ally)?", re.IGNORECASE)
GEO_INTL_RE = re.compile(
    r"мировая практика|мировой практик\w*|зарубежн\w*|международн\w*"
    r"|world(?:\s*wide)?\s*practice|international|global|abroad|foreign",
    re.IGNORECASE,
)

_YEARS_AGO_RE = re.compile(r"последн\w*\s+(\d+)\s+лет|(?:last|past|previous)\s+(\d+)\s+years?", re.IGNORECASE)
_YEAR_SPAN_RE = re.compile(
    r"(?:19|20)\d{2}\s*[-–—]\s*(?:19|20)\d{2}"
    r"|between\s+((?:19|20)\d{2})\s+and\s+((?:19|20)\d{2})",
    re.IGNORECASE,
)
_SINGLE_YEAR_RE = re.compile(r"\bв\s*(19|20)\d{2}\s*год|\bin\s+((?:19|20)\d{2})\b", re.IGNORECASE)


def normalize_unit(raw: str) -> str:
    raw_low = raw.lower().strip()
    for pattern, canonical in UNIT_PATTERNS:
        if re.fullmatch(pattern, raw_low, re.IGNORECASE):
            return canonical
    return raw_low


def _params_near(text: str, back_start: int, back_end: int,
                  fwd_start: Optional[int] = None, fwd_end: Optional[int] = None) -> list[str]:
    """Искать в тексте между предыдущим числовым совпадением и текущим
    ключевые слова параметров (обрабатывает списки вроде 'сульфаты, хлориды,
    Ca, Mg, Na по 200-300 мг/л', не залезая в ограничение предыдущего
    оборота).

    В русской формулировке вещество стоит перед числом ("сульфаты ... 200
    мг/л"); в английской оно часто идёт после ("200 mg/l of chloride",
    "sodium 100 mg/l"). Если ничего не найдено при поиске назад, делаем
    резервный поиск вперёд, ограниченный началом *следующего* совпадения
    ограничения, чтобы не залезать и в параметр того оборота тоже."""
    window = text[max(0, back_start):back_end]
    found = []
    for pattern, canonical in PARAM_KEYWORDS:
        if re.search(pattern, window, re.IGNORECASE) and canonical not in found:
            found.append(canonical)
    if found or fwd_start is None:
        return found
    fwd_window = text[fwd_start:fwd_end]
    for pattern, canonical in PARAM_KEYWORDS:
        if re.search(pattern, fwd_window, re.IGNORECASE) and canonical not in found:
            found.append(canonical)
    return found


def extract_numeric_constraints(text: str) -> list[dict]:
    raw_matches = []
    for m in _RANGE_RE.finditer(text):
        raw_matches.append(("range", m))
    for m in _LE_RE.finditer(text):
        raw_matches.append(("<=", m))
    for m in _GE_RE.finditer(text):
        raw_matches.append((">=", m))
    raw_matches.sort(key=lambda pair: pair[1].start())

    constraints = []
    prev_end = 0
    for i, (op, m) in enumerate(raw_matches):
        next_start = raw_matches[i + 1][1].start() if i + 1 < len(raw_matches) else len(text)
        params = _params_near(text, prev_end, m.start(), m.end(), next_start) or [None]
        if op == "range":
            value, value2, unit_raw = m.group(1), m.group(2), m.group(3)
            for p in params:
                constraints.append({
                    "parameter": p, "operator": "range",
                    "value": float(value.replace(",", ".")),
                    "value2": float(value2.replace(",", ".")),
                    "unit": normalize_unit(unit_raw),
                })
        else:
            value, unit_raw = m.group(1), m.group(2)
            for p in params:
                constraints.append({
                    "parameter": p, "operator": op,
                    "value": float(value.replace(",", ".")), "value2": None,
                    "unit": normalize_unit(unit_raw),
                })
        prev_end = m.end()

    return constraints


def extract_geography_filters(text: str) -> list[str]:
    filters = []
    if GEO_RU_RE.search(text):
        filters.append("RU")
    if GEO_INTL_RE.search(text):
        filters.append("International")
    return filters


def extract_year_range(text: str, today: Optional[date] = None) -> Optional[tuple[int, int]]:
    today = today or date.today()

    m = _YEARS_AGO_RE.search(text)
    if m:
        n = int(m.group(1) or m.group(2))
        return (today.year - n, today.year)

    m = _YEAR_SPAN_RE.search(text)
    if m:
        nums = [int(x) for x in re.findall(r"(?:19|20)\d{2}", m.group(0))]
        return (min(nums), max(nums))

    m = _SINGLE_YEAR_RE.search(text)
    if m:
        year = int(re.search(r"(?:19|20)\d{2}", m.group(0)).group(0))
        return (year, year)

    return None
