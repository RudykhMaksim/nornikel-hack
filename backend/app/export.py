"""Экспорт ответа на запрос в Markdown (в стиле литературного обзора), JSON-LD или PDF."""
import io
from pathlib import Path
from typing import Any

CONTEXT = {
    "@context": {
        "id": "@id",
        "type": "@type",
        "Material": "https://schema.org/Substance",
        "Process": "https://schema.org/Action",
        "Experiment": "https://schema.org/Dataset",
        "Publication": "https://schema.org/ScholarlyArticle",
        "Expert": "https://schema.org/Person",
        "Facility": "https://schema.org/Organization",
        "Conclusion": "https://schema.org/Claim",
        "geography": "https://schema.org/spatialCoverage",
        "year": "https://schema.org/datePublished",
        "confidence": "https://schema.org/confidenceLevel",
    }
}


def to_markdown(result: dict[str, Any]) -> str:
    lines = [f"# Ответ на запрос", "", f"> {result['query']}", ""]

    parsed = result["parsed"]
    lines.append("## Разобранный запрос")
    if parsed["matched_entities"]:
        lines.append("**Сущности:** " + ", ".join(f"{e['name_ru']} ({e['type']})" for e in parsed["matched_entities"]))
    if parsed["constraints"]:
        cons = []
        for c in parsed["constraints"]:
            if c["operator"] == "range":
                cons.append(f"{c['parameter']}: {c['value']}-{c['value2']} {c['unit']}")
            else:
                cons.append(f"{c['parameter']} {c['operator']} {c['value']} {c['unit']}")
        lines.append("**Числовые ограничения:** " + "; ".join(cons))
    if parsed["geography_filters"]:
        lines.append("**География:** " + ", ".join(parsed["geography_filters"]))
    if parsed["year_range"]:
        lines.append(f"**Период:** {parsed['year_range'][0]}-{parsed['year_range'][1]}")
    lines.append("")

    section_titles = {
        "processes": "## Технологии/процессы",
        "experiments": "## Эксперименты",
        "publications": "## Публикации",
        "patents": "## Патенты",
        "standards": "## Стандарты",
        "conclusions": "## Выводы",
        "topics": "## Тематики/направления",
    }
    for bucket, title in section_titles.items():
        items = result["results"].get(bucket, [])
        if not items:
            continue
        lines.append(title)
        for item in items:
            meta = f" _(geo: {item.get('geography')}, год: {item.get('year')}, достоверность: {item.get('confidence')})_"
            lines.append(f"- **{item.get('name_ru')}**{meta}")
        lines.append("")

    if result.get("contradictions"):
        lines.append("## Противоречия")
        for c in result["contradictions"]:
            lines.append(f"- \"{c['a']['name_ru']}\" ({c['a']['geography']}) **противоречит** \"{c['b']['name_ru']}\" ({c['b']['geography']})")
        lines.append("")

    if result.get("consensus_conclusions"):
        lines.append("## Консенсусные выводы (без обнаруженных противоречий)")
        for c in result["consensus_conclusions"]:
            lines.append(f"- **{c['name_ru']}** _(достоверность: {c.get('confidence')}, источников: {c['supporting_sources']})_")
        lines.append("")

    if result.get("gaps"):
        lines.append("## Пробелы в знаниях")
        for g in result["gaps"]:
            lines.append(f"- {g['description']}")
            for pm in g.get("partial_matches", []):
                lines.append(f"  - частично покрывает {pm['experiment']['name_ru']} ({pm['experiment']['geography']}): {', '.join(pm['covers'])}")
        lines.append("")

    if result.get("experts"):
        lines.append("## Эксперты")
        lines.append(", ".join(e.get("name_ru", "") for e in result["experts"]))
        lines.append("")

    rec = result.get("recommendations") or {}
    if rec.get("similar_cases") or rec.get("adjacent_experts") or rec.get("adjacent_facilities"):
        lines.append("## Рекомендации (смежные области)")
        if rec.get("similar_cases"):
            lines.append("**Похожие случаи:** " + "; ".join(c["name_ru"] for c in rec["similar_cases"]))
        if rec.get("adjacent_experts"):
            lines.append("**Эксперты по смежным задачам:** " + ", ".join(e["name_ru"] for e in rec["adjacent_experts"]))
        if rec.get("adjacent_facilities"):
            lines.append("**Смежные команды/лаборатории:** " + ", ".join(f["name_ru"] for f in rec["adjacent_facilities"]))
        lines.append("")

    return "\n".join(lines)


_PDF_FONT_NAME = "Helvetica"
_PDF_FONT_BOLD = "Helvetica-Bold"
_PDF_FONT_READY = False


def _ensure_cyrillic_font() -> None:
    """Зарегистрировать TTF-шрифт с кириллическими глифами для reportlab.

    Встроенные шрифты reportlab (Helvetica/Times) не содержат кириллических
    глифов, поэтому без этого каждая RU-строка в PDF отображается пустыми
    прямоугольниками. Мы используем Arial из локальной папки шрифтов Windows
    (присутствует на этом целевом окружении); если он не найден (например,
    хост не на Windows), откатываемся на встроенный шрифт и миримся с тем,
    что кириллица не отобразится -- лучше деградировать, чем уронить экспорт.
    """
    global _PDF_FONT_NAME, _PDF_FONT_BOLD, _PDF_FONT_READY
    if _PDF_FONT_READY:
        return
    _PDF_FONT_READY = True
    try:
        from reportlab.pdfbase import pdfmetrics
        from reportlab.pdfbase.ttfonts import TTFont

        candidates = [
            (Path("C:/Windows/Fonts/arial.ttf"), Path("C:/Windows/Fonts/arialbd.ttf")),
            (Path("/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf"),
             Path("/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf")),
        ]
        for regular, bold in candidates:
            if regular.exists():
                pdfmetrics.registerFont(TTFont("BodyFont", str(regular)))
                _PDF_FONT_NAME = "BodyFont"
                if bold.exists():
                    pdfmetrics.registerFont(TTFont("BodyFont-Bold", str(bold)))
                    _PDF_FONT_BOLD = "BodyFont-Bold"
                else:
                    _PDF_FONT_BOLD = "BodyFont"
                return
    except Exception:
        pass


def to_pdf(result: dict[str, Any]) -> bytes:
    _ensure_cyrillic_font()

    from reportlab.lib.pagesizes import A4
    from reportlab.lib.styles import ParagraphStyle
    from reportlab.lib.units import mm
    from reportlab.platypus import SimpleDocTemplate, Paragraph, Spacer

    styles = {
        "title": ParagraphStyle("title", fontName=_PDF_FONT_BOLD, fontSize=15, spaceAfter=10),
        "h2": ParagraphStyle("h2", fontName=_PDF_FONT_BOLD, fontSize=12, spaceBefore=10, spaceAfter=4),
        "body": ParagraphStyle("body", fontName=_PDF_FONT_NAME, fontSize=9.5, leading=13),
        "item": ParagraphStyle("item", fontName=_PDF_FONT_NAME, fontSize=9.5, leading=13, leftIndent=10),
    }

    def esc(text: Any) -> str:
        return str(text).replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")

    story = [Paragraph("Ответ на запрос", styles["title"]),
             Paragraph(esc(result["query"]), styles["body"]),
             Spacer(1, 4 * mm)]

    parsed = result["parsed"]
    story.append(Paragraph("Разобранный запрос", styles["h2"]))
    if parsed["matched_entities"]:
        text = "Сущности: " + ", ".join(f"{esc(e['name_ru'])} ({e['type']})" for e in parsed["matched_entities"])
        story.append(Paragraph(text, styles["body"]))
    if parsed["constraints"]:
        cons = []
        for c in parsed["constraints"]:
            if c["operator"] == "range":
                cons.append(f"{c['parameter']}: {c['value']}-{c['value2']} {c['unit']}")
            else:
                cons.append(f"{c['parameter']} {c['operator']} {c['value']} {c['unit']}")
        story.append(Paragraph("Числовые ограничения: " + "; ".join(cons), styles["body"]))
    if parsed["geography_filters"]:
        story.append(Paragraph("География: " + ", ".join(parsed["geography_filters"]), styles["body"]))
    if parsed["year_range"]:
        story.append(Paragraph(f"Период: {parsed['year_range'][0]}-{parsed['year_range'][1]}", styles["body"]))

    section_titles = {
        "processes": "Технологии/процессы",
        "experiments": "Эксперименты",
        "publications": "Публикации",
        "patents": "Патенты",
        "standards": "Стандарты",
        "conclusions": "Выводы",
        "topics": "Тематики/направления",
    }
    for bucket, title in section_titles.items():
        items = result["results"].get(bucket, [])
        if not items:
            continue
        story.append(Paragraph(title, styles["h2"]))
        for item in items:
            meta = f" (geo: {item.get('geography')}, год: {item.get('year')}, достоверность: {item.get('confidence')})"
            story.append(Paragraph(f"&bull; <b>{esc(item.get('name_ru'))}</b>{esc(meta)}", styles["item"]))

    if result.get("contradictions"):
        story.append(Paragraph("Противоречия", styles["h2"]))
        for c in result["contradictions"]:
            text = f"«{esc(c['a']['name_ru'])}» ({c['a']['geography']}) <b>противоречит</b> «{esc(c['b']['name_ru'])}» ({c['b']['geography']})"
            story.append(Paragraph(text, styles["item"]))

    if result.get("consensus_conclusions"):
        story.append(Paragraph("Консенсусные выводы (без обнаруженных противоречий)", styles["h2"]))
        for c in result["consensus_conclusions"]:
            text = f"<b>{esc(c['name_ru'])}</b> (достоверность: {c.get('confidence')}, источников: {c['supporting_sources']})"
            story.append(Paragraph(text, styles["item"]))

    if result.get("gaps"):
        story.append(Paragraph("Пробелы в знаниях", styles["h2"]))
        for g in result["gaps"]:
            story.append(Paragraph(esc(g["description"]), styles["item"]))
            for pm in g.get("partial_matches", []):
                text = f"частично покрывает {esc(pm['experiment']['name_ru'])} ({pm['experiment']['geography']}): {esc(', '.join(pm['covers']))}"
                story.append(Paragraph(text, styles["item"]))

    if result.get("experts"):
        story.append(Paragraph("Эксперты", styles["h2"]))
        story.append(Paragraph(esc(", ".join(e.get("name_ru", "") for e in result["experts"])), styles["body"]))

    rec = result.get("recommendations") or {}
    if rec.get("similar_cases") or rec.get("adjacent_experts") or rec.get("adjacent_facilities"):
        story.append(Paragraph("Рекомендации (смежные области)", styles["h2"]))
        if rec.get("similar_cases"):
            story.append(Paragraph("Похожие случаи: " + esc("; ".join(c["name_ru"] for c in rec["similar_cases"])), styles["item"]))
        if rec.get("adjacent_experts"):
            story.append(Paragraph("Эксперты по смежным задачам: " + esc(", ".join(e["name_ru"] for e in rec["adjacent_experts"])), styles["item"]))
        if rec.get("adjacent_facilities"):
            story.append(Paragraph("Смежные команды/лаборатории: " + esc(", ".join(f["name_ru"] for f in rec["adjacent_facilities"])), styles["item"]))

    buffer = io.BytesIO()
    doc = SimpleDocTemplate(buffer, pagesize=A4, topMargin=15 * mm, bottomMargin=15 * mm)
    doc.build(story)
    return buffer.getvalue()


def to_json_ld(result: dict[str, Any]) -> dict[str, Any]:
    graph = []
    for bucket, items in result["results"].items():
        for item in items:
            graph.append({"id": item["id"], "type": item["type"], **{k: v for k, v in item.items() if k not in ("id", "type")}})
    doc = dict(CONTEXT)
    doc["query"] = result["query"]
    doc["@graph"] = graph
    doc["contradictions"] = result.get("contradictions", [])
    doc["gaps"] = result.get("gaps", [])
    return doc
