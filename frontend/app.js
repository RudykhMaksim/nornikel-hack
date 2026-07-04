const API = "/api";
let lastResult = null;
let network = null;

const TYPE_COLORS = {
  Material: "#3fb950", Process: "#1f6feb", Equipment: "#a371f7",
  Condition: "#d9822b", Experiment: "#58a6ff", Publication: "#f2cc60",
  Expert: "#ff7b72", Facility: "#39d3bb", Conclusion: "#d9534f",
  Topic: "#7ee787", Archive: "#6e7681", Patent: "#e3b341", Standard: "#79c0ff",
};

function $(sel) { return document.querySelector(sel); }
function $all(sel) { return Array.from(document.querySelectorAll(sel)); }

function currentRole() { return $("#roleSelect").value || "researcher"; }

async function init() {
  const rolesResp = await fetch(`${API}/roles`).then(r => r.json());
  $("#roleSelect").innerHTML = rolesResp.roles.map(r => `<option value="${r}">${r}</option>`).join("");

  $all(".tab-btn").forEach(btn => btn.addEventListener("click", () => switchTab(btn.dataset.tab)));
  $all(".example-btn").forEach(btn => btn.addEventListener("click", () => {
    $("#queryInput").value = btn.dataset.q;
    runSearch();
  }));
  $("#searchBtn").addEventListener("click", runSearch);
  $("#queryInput").addEventListener("keydown", e => { if (e.key === "Enter") runSearch(); });
  $("#exportMd").addEventListener("click", () => doExport("markdown"));
  $("#exportJsonld").addEventListener("click", () => doExport("json-ld"));
  $("#exportPdf").addEventListener("click", () => doExport("pdf"));
  $("#compareBtn").addEventListener("click", runCompare);

  loadCompareOptions();
  loadGaps();
  loadDashboard();
}

function switchTab(name) {
  $all(".tab-btn").forEach(b => b.classList.toggle("active", b.dataset.tab === name));
  $all(".tab-panel").forEach(p => p.classList.toggle("active", p.id === `tab-${name}`));
  if (name === "dashboard") loadDashboard();
  if (name === "gaps") loadGaps();
  if (name === "glossary") loadGlossary();
}

function selectedGeo() {
  return $all(".geoFilter:checked").map(el => el.value);
}

function selectedConfidence() {
  return $all(".confFilter:checked").map(el => el.value);
}

async function runSearch() {
  const q = $("#queryInput").value.trim();
  if (!q) return;
  $("#searchStatus").textContent = "Ищем по графу знаний...";
  $("#resultsArea").style.display = "none";

  const params = new URLSearchParams({ q, role: currentRole() });
  const geo = selectedGeo();
  if (geo.length) params.set("geography", geo.join(","));
  const yf = $("#yearFrom").value, yt = $("#yearTo").value;
  if (yf) params.set("year_from", yf);
  if (yt) params.set("year_to", yt);
  const conf = selectedConfidence();
  if (conf.length) params.set("confidence", conf.join(","));

  try {
    const result = await fetch(`${API}/search?${params.toString()}`).then(r => r.json());
    lastResult = result;
    renderResult(result);
    $("#searchStatus").textContent = "";
    $("#resultsArea").style.display = "grid";
  } catch (e) {
    $("#searchStatus").textContent = "Ошибка запроса: " + e;
  }
}

function renderResult(result) {
  const parsed = result.parsed;
  const chips = [];
  parsed.matched_entities.forEach(e => chips.push(`<span class="chip">${e.name_ru} (${e.type})</span>`));
  let constraintsHtml = "";
  if (parsed.constraints.length) {
    constraintsHtml = "<div style='margin-top:8px'>" + parsed.constraints.map(c => {
      const label = c.operator === "range" ? `${c.value}-${c.value2}` : `${c.operator} ${c.value}`;
      return `<span class="chip">${c.parameter || "?"}: ${label} ${c.unit}</span>`;
    }).join("") + "</div>";
  }
  let geoYear = "";
  if (parsed.geography_filters.length) geoYear += `<span class="chip">География: ${parsed.geography_filters.join(", ")}</span>`;
  if (parsed.year_range) geoYear += `<span class="chip">Период: ${parsed.year_range[0]}-${parsed.year_range[1]}</span>`;
  if (parsed.confidence_filter && parsed.confidence_filter.length) geoYear += `<span class="chip">Достоверность: ${parsed.confidence_filter.join(", ")}</span>`;

  $("#parsedBox").innerHTML = `<b>Распознано в запросе:</b><br/>${chips.join("")}${constraintsHtml}<div style="margin-top:8px">${geoYear}</div>`;

  const contra = result.contradictions || [];
  $("#contradictionsBox").className = "box" + (contra.length ? " contradiction" : "");
  $("#contradictionsBox").innerHTML = contra.length
    ? "<b>⚠ Обнаружены противоречия:</b><br/>" + contra.map(c =>
        `«${c.a.name_ru}» (${c.a.geography}) <b>противоречит</b> «${c.b.name_ru}» (${c.b.geography})`).join("<br/>")
    : "";

  const consensus = result.consensus_conclusions || [];
  $("#consensusBox").className = "box" + (consensus.length ? " consensus" : "");
  $("#consensusBox").innerHTML = consensus.length
    ? "<b>✓ Консенсусные выводы (без обнаруженных противоречий):</b><br/>" + consensus.map(c =>
        `${c.name_ru} <span class="meta">(достоверность: ${c.confidence || "-"}, источников: ${c.supporting_sources})</span>`).join("<br/>")
    : "";

  const gaps = result.gaps || [];
  $("#gapsBox").className = "box" + (gaps.length ? " gap" : "");
  $("#gapsBox").innerHTML = gaps.length
    ? gaps.map(g => `<b>⚠ Пробел в знаниях:</b> ${g.description}` +
        (g.partial_matches.length ? "<br/>Частичные совпадения: " + g.partial_matches.map(pm =>
          `${pm.experiment.name_ru} (${pm.experiment.geography}) — покрывает: ${pm.covers.join(", ")}`).join("; ") : ""))
        .join("<br/><br/>")
    : "";

  const rec = result.recommendations || { similar_cases: [], adjacent_experts: [], adjacent_facilities: [] };
  const recParts = [];
  if (rec.similar_cases.length) recParts.push(`Похожие случаи из смежных областей: ${rec.similar_cases.map(c => c.name_ru).join("; ")}`);
  if (rec.adjacent_experts.length) recParts.push(`Эксперты по смежным задачам: ${rec.adjacent_experts.map(e => e.name_ru).join(", ")}`);
  if (rec.adjacent_facilities.length) recParts.push(`Смежные команды/лаборатории: ${rec.adjacent_facilities.map(f => f.name_ru).join(", ")}`);
  $("#recommendationsBox").innerHTML = recParts.length
    ? "<b>💡 Рекомендации:</b><br/>" + recParts.join("<br/>")
    : "";

  const sectionTitles = { processes: "Технологии / процессы", experiments: "Эксперименты", publications: "Публикации", patents: "Патенты", standards: "Стандарты", conclusions: "Выводы", topics: "Тематики / направления" };
  let sectionsHtml = "";
  for (const [key, title] of Object.entries(sectionTitles)) {
    const items = result.results[key] || [];
    if (!items.length) continue;
    const total = (result.results_total_counts || {})[key] || items.length;
    const countLabel = total > items.length ? `${items.length} из ${total}` : `${items.length}`;
    sectionsHtml += `<div class="result-section"><h4>${title} (${countLabel})</h4>`;
    items.forEach(item => {
      const sens = item.sensitivity === "internal" ? `<span class="sensitivity">[внутренний документ]</span>` : "";
      let extra = "";
      if (item.conditions && item.conditions.length) {
        extra += "<div class='meta'>Условия: " + item.conditions.map(c => `${c.name_ru}`).join("; ") + "</div>";
      }
      if (item.publications && item.publications.length) {
        extra += "<div class='meta'>Источники: " + item.publications.map(p => p.name_ru).join("; ") + "</div>";
      }
      if (item.description) {
        extra += `<div class='meta'>${item.description}</div>`;
      }
      sectionsHtml += `<div class="result-card"><b>${item.name_ru}</b> ${sens}
        <div class="meta">geo: ${item.geography || "-"} | год: ${item.year || "-"} | достоверность: ${item.confidence || "-"} | обновлено: ${item.last_updated || "-"}</div>
        ${extra}</div>`;
    });
    sectionsHtml += "</div>";
  }
  $("#sectionsBox").innerHTML = sectionsHtml;

  renderGraph(result.subgraph);
}

function renderGraph(subgraph) {
  const nodes = new vis.DataSet(subgraph.nodes.map(n => ({
    id: n.id,
    label: n.name_ru || n.id,
    color: TYPE_COLORS[n.type] || "#888",
    shape: "dot",
    size: 14,
    font: { color: "#e6e9ef", size: 11 },
  })));
  const edges = new vis.DataSet(subgraph.edges.map((e, i) => ({
    id: i, from: e.source, to: e.target, arrows: "to", label: e.type,
    font: { size: 8, color: "#93a0b8", strokeWidth: 0 }, color: { color: "#2a3348" },
  })));
  const container = $("#graphViz");
  const data = { nodes, edges };
  const options = {
    physics: { stabilization: true, barnesHut: { gravitationalConstant: -6000 } },
    interaction: { hover: true },
  };
  if (network) network.destroy();
  network = new vis.Network(container, data, options);
}

async function doExport(format) {
  if (!lastResult) return;
  const resp = await fetch(`${API}/export?format=${format}`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(lastResult),
  });
  const blob = await resp.blob();
  const url = URL.createObjectURL(blob);
  const a = document.createElement("a");
  a.href = url;
  a.download = { markdown: "answer.md", "json-ld": "answer.jsonld", pdf: "answer.pdf" }[format];
  a.click();
  URL.revokeObjectURL(url);
}

async function loadCompareOptions() {
  const [procs, eqs] = await Promise.all([
    fetch(`${API}/entities?type=Process&role=${currentRole()}`).then(r => r.json()),
    fetch(`${API}/entities?type=Equipment&role=${currentRole()}`).then(r => r.json()),
  ]);
  const all = [...procs.entities, ...eqs.entities];
  $("#compareSelect").innerHTML = all.map(e => `<option value="${e.id}">${e.name_ru} [${e.geography || ""}]</option>`).join("");
}

async function runCompare() {
  const ids = $all("#compareSelect option:checked").map(o => o.value);
  if (!ids.length) return;
  const result = await fetch(`${API}/compare?ids=${ids.join(",")}&role=${currentRole()}`).then(r => r.json());
  const rows = result.rows;
  if (!rows.length) { $("#compareResult").innerHTML = "Нет данных."; return; }

  // Транспонированная раскладка -- категории параметров (эффективность/
  // капзатраты/экология/климат/технические) как строки, сравниваемые
  // сущности как столбцы. При выборе ровно 2 это естественно даёт вид
  // "А против Б" бок о бок.
  let html = "<table class='compare-table'><tr><th>Параметр</th>" +
    rows.map(r => `<th>${r.name_ru}<div class="meta">${r.geography || "-"} · ${r.confidence || "-"}</div></th>`).join("") + "</tr>";
  html += `<tr><td>Выводы</td>` + rows.map(r => `<td>${r.conclusions.join("<br/>") || "—"}</td>`).join("") + `</tr>`;
  html += `<tr><td>Публикации</td>` + rows.map(r => `<td>${r.publications.join("<br/>") || "—"}</td>`).join("") + `</tr>`;
  for (const category of result.categories) {
    if (!rows.some(r => (r.conditions_by_category[category] || []).length)) continue;
    html += `<tr><td><b>${category}</b></td>` + rows.map(r =>
      `<td>${(r.conditions_by_category[category] || []).map(c => c.name_ru).join("<br/>") || "—"}</td>`).join("") + `</tr>`;
  }
  html += "</table>";
  $("#compareResult").innerHTML = html;
}

async function loadGaps() {
  const result = await fetch(`${API}/gaps?role=${currentRole()}`).then(r => r.json());
  if (!result.gaps.length) { $("#gapsList").innerHTML = "Пробелов не обнаружено."; return; }
  $("#gapsList").innerHTML = result.gaps.map(g => `
    <div class="box gap">
      <b>Запрос:</b> ${g.query}<br/>
      ${g.gaps.map(gap => `<b>⚠ ${gap.description}</b><br/>
        Запрошенная комбинация: ${gap.requested_combination.map(c => c.name_ru).join(" + ")}<br/>
        ${gap.partial_matches.length ? "Частичные совпадения: " + gap.partial_matches.map(pm =>
          `${pm.experiment.name_ru} (${pm.experiment.geography})`).join(", ") : "Совпадений нет вообще."}`).join("<br/>")}
    </div>`).join("");
}

async function loadDashboard() {
  const [d, geo, neo] = await Promise.all([
    fetch(`${API}/dashboard?role=${currentRole()}`).then(r => r.json()),
    fetch(`${API}/geography-coverage?role=${currentRole()}`).then(r => r.json()),
    fetch(`${API}/neo4j/status`).then(r => r.json()),
  ]);
  const bar = (obj) => Object.entries(obj).map(([k, v]) => {
    const pct = Math.round((v / d.total_entities) * 100);
    return `<div class="bar-row"><div style="width:160px">${k}</div><div class="bar-track"><div class="bar-fill" style="width:${pct}%"></div></div><div>${v}</div></div>`;
  }).join("");

  const geoList = (rows) => rows.length
    ? rows.map(r => `<div class="meta">${r.name_ru} <span style="opacity:.6">(${r.type}, источников: ${r.source_count})</span></div>`).join("")
    : "<div class='meta'>Нет.</div>";

  const ing = d.ingestion || {};
  const neoLine = neo.available
    ? `<span style="color:var(--ok)">● подключён</span> (${neo.uri}) · узлов: ${neo.node_count} · связей: ${neo.relationship_count}`
    : `<span style="color:var(--warn)">● недоступен</span> (${neo.uri}) — опциональный путь через docker-compose, основной API работает без него`;
  $("#dashboardArea").innerHTML = `
    <div class="box">Всего сущностей в графе: <b>${d.total_entities}</b> · Противоречий: <b>${d.contradictions_count}</b></div>
    <div class="box">Neo4j: ${neoLine}
      <div style="margin-top:8px">
        <input id="cypherInput" type="text" placeholder="MATCH (n) RETURN n.name_ru LIMIT 5" style="width:60%">
        <button id="cypherRunBtn">Выполнить (read-only)</button>
      </div>
      <pre id="cypherResult" class="meta" style="white-space:pre-wrap"></pre>
    </div>
    <div class="box">Реальный корпус источников: каталогизировано <b>${ing.catalogued_files ?? 0}</b> файлов ·
      глубоко разобрано (текст+теги) <b>${ing.deep_parsed ?? 0}</b> ·
      архивов без разбора содержимого <b>${ing.archives_metadata_only ?? 0}</b> ·
      найдено упоминаний (MENTIONS) <b>${ing.mentions_found ?? 0}</b></div>
    <div class="dash-grid">
      <div class="box"><h4>По типу сущности</h4>${bar(d.by_type)}</div>
      <div class="box"><h4>По географии</h4>${bar(d.by_geography)}</div>
      <div class="box"><h4>История переиндексации корпуса</h4>${
        (d.ingestion_history || []).length
          ? d.ingestion_history.slice().reverse().map(h =>
              `<div class="meta">${h.ts} · всего: ${h.total_entities} сущностей/${h.total_relations} связей ·
               новых документов: ${h.new_documents}, новых связей: ${h.new_relations_found}, доразобрано: ${h.newly_deep_parsed}</div>`).join("")
          : "Пока нет записей о переиндексации (запустите build_corpus.py)."
      }</div>
      <div class="box"><h4>По уровню достоверности</h4>${bar(d.by_confidence)}</div>
      <div class="box"><h4>Последние записи аудита</h4>${
        d.recent_audit.length
          ? d.recent_audit.slice().reverse().map(a => `<div class="meta">${a.ts} · ${a.role} · ${a.action}: ${a.detail}</div>`).join("")
          : "Пока нет записей."
      }</div>
      <div class="box"><h4>Только в отечественной практике (${geo.ru_only.length})</h4>${geoList(geo.ru_only)}</div>
      <div class="box"><h4>Только в зарубежной практике (${geo.international_only.length})</h4>${geoList(geo.international_only)}</div>
      <div class="box"><h4>И там, и там (${geo.both.length})</h4>${geoList(geo.both)}</div>
      <div class="box"><h4>Нет источника с указанием географии (${geo.unclear.length})</h4>${geoList(geo.unclear)}</div>
    </div>`;

  if (neo.available) {
    $("#cypherRunBtn").addEventListener("click", async () => {
      const cypher = $("#cypherInput").value.trim();
      if (!cypher) return;
      const resp = await fetch(`${API}/neo4j/query?role=${currentRole()}`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ cypher, params: {} }),
      });
      const body = await resp.json();
      $("#cypherResult").textContent = resp.ok ? JSON.stringify(body.rows, null, 1) : `Ошибка: ${body.detail}`;
    });
  } else {
    $("#cypherRunBtn").disabled = true;
  }
}

async function loadGlossary() {
  const g = await fetch(`${API}/glossary`).then(r => r.json());
  const rows = (items, cols) => items.map(item => `<div class="result-card">
      <b>${item.name_ru}</b>${item.name_en ? ` <span class="meta">(${item.name_en})</span>` : ""}
      ${item.aliases && item.aliases.length ? `<div class="meta">Синонимы: ${item.aliases.join(", ")}</div>` : ""}
      ${item.description ? `<div class="meta">${item.description}</div>` : ""}
      <div class="meta">geo: ${item.geography || "-"} · достоверность: ${item.confidence || "-"}</div>
    </div>`).join("") || "<div class='meta'>Пусто.</div>";

  $("#glossaryArea").innerHTML = `
    <div class="dash-grid">
      <div class="box"><h4>Материалы (${g.materials.length})</h4>${rows(g.materials)}</div>
      <div class="box"><h4>Оборудование (${g.equipment.length})</h4>${rows(g.equipment)}</div>
      <div class="box"><h4>Стандарты (${g.standards.length})</h4>${rows(g.standards)}</div>
      <div class="box"><h4>Единицы измерения (${g.units.length})</h4>${
        g.units.map(u => `<div class="meta"><b>${u.canonical}</b> — паттерн: <code>${u.pattern}</code></div>`).join("")
      }</div>
    </div>`;
}

init();
