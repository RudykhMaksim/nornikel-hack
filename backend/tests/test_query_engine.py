from app.query_engine import answer_query


def _ids(items):
    return {i["id"] for i in items}


def test_water_desalination_scenario_english(store):
    q = ("What water desalination methods are suitable for a concentrator if the feed water "
         "contains sulfates, chlorides, Ca, Mg, Na at 200-300 mg/l each, and the required dry "
         "residue is not more than 1000 mg/dm3?")
    result = answer_query(store, q)

    assert result["parsed"]["constraints"]
    exp_ids = _ids(result["results"]["experiments"])
    assert "exp_stillfontein_savmin" in exp_ids
    contra_ids = {(c["a"]["id"], c["b"]["id"]) for c in result["contradictions"]}
    contra_ids |= {(b, a) for a, b in contra_ids}
    assert ("concl_lime_floor_limitation", "concl_savmin_meets_low_target") in contra_ids


def test_nickel_electrowinning_circulation_scenario_english(store):
    q = ("What technical solutions for organizing catholyte circulation in nickel electrowinning "
         "are described in international practice, and what flow rate is considered optimal?")
    result = answer_query(store, q)

    assert result["parsed"]["geography_filters"] == ["International"]
    proc_ids = _ids(result["results"]["processes"])
    assert "proc_parallel_flow_technology" in proc_ids


def test_deep_well_injection_english_both_geographies(store):
    q = "What methods of injecting mine water into deep horizons have been used in Russia and abroad?"
    result = answer_query(store, q)
    assert result["parsed"]["geography_filters"] == ["RU", "International"]
    assert len(result["gaps"]) >= 1


def test_water_desalination_scenario(store):
    q = ("Какие методы обессоливания воды подходят для обогатительной фабрики, если исходная "
         "вода содержит сульфаты, хлориды, Ca, Mg, Na по 200-300 мг/л, а требуемый сухой "
         "остаток - не более 1000 мг/дм3?")
    result = answer_query(store, q)

    assert result["parsed"]["constraints"], "числовые ограничения должны быть извлечены"
    exp_ids = _ids(result["results"]["experiments"])
    assert "exp_stillfontein_savmin" in exp_ids

    # реальный, задокументированный инженерный контраст: традиционная обработка
    # известью имеет жёсткий предел (растворимость гипса), который SAVMIN
    # доказуемо преодолевает
    contra_ids = {(c["a"]["id"], c["b"]["id"]) for c in result["contradictions"]}
    contra_ids |= {(b, a) for a, b in contra_ids}
    assert ("concl_lime_floor_limitation", "concl_savmin_meets_low_target") in contra_ids


def test_nickel_electrowinning_circulation_scenario(store):
    q = ("Какие технические решения организации циркуляции католита при электроэкстракции "
         "никеля описаны в мировой практике, и какая скорость потока считается оптимальной?")
    result = answer_query(store, q)

    assert result["parsed"]["geography_filters"] == ["International"]
    proc_ids = _ids(result["results"]["processes"])
    assert "proc_parallel_flow_technology" in proc_ids
    exp_ids = _ids(result["results"]["experiments"])
    assert "exp_brixlegg_mettop_brx" in exp_ids


def test_au_ag_pgm_distribution_last_5_years_excludes_2018(store):
    q = ("Покажите все эксперименты и публикации по распределению Au, Ag и МПГ между "
         "медным/никелевым штейном и шлаком за последние 5 лет")
    result = answer_query(store, q)

    assert result["parsed"]["year_range"] is not None
    # реальные исходные эксперименты датированы 2018 годом -- за пределами
    # "последних 5 лет" от 2026, поэтому ни один не должен появиться в
    # отфильтрованных результатах (честный результат, совпадающий с
    # собственной жалобой исходного обзора на скудость недавней литературы
    # по распределению между штейном и шлаком меди/никеля)
    exp_ids = _ids(result["results"]["experiments"])
    assert "exp_ni_matte_slag_distribution" not in exp_ids
    assert "exp_cu_matte_slag_distribution" not in exp_ids


def test_deep_well_injection_is_a_genuine_gap(store):
    q = "Какие способы закачки шахтных вод в глубокие горизонты применялись в России и за рубежом?"
    result = answer_query(store, q)
    assert len(result["gaps"]) >= 1


def test_heap_leaching_nickel_cold_climate_gap(store):
    q = "Есть ли опыт кучного выщелачивания никелевой руды в условиях холодного климата?"
    result = answer_query(store, q)

    assert len(result["gaps"]) == 1
    gap = result["gaps"][0]
    requested_ids = {e["id"] for e in gap["requested_combination"]}
    assert "mat_nickel_ore" in requested_ids
    assert "proc_heap_leaching" in requested_ids
    assert "cond_cold_climate" in requested_ids

    # реальный источник, который у нас есть, о кучном выщелачивании МЕДИ, а не
    # никеля -- он должен появиться как частичное совпадение, а не полное
    partial_exp_ids = {pm["experiment"]["id"] for pm in gap["partial_matches"]}
    assert "exp_heap_leaching_copper_cold_2017" in partial_exp_ids


def test_recommendations_surface_adjacent_entities_not_in_main_results(store):
    q = ("Какие технические решения организации циркуляции католита при электроэкстракции "
         "никеля описаны в мировой практике?")
    result = answer_query(store, q)
    rec = result["recommendations"]

    all_shown_ids = _ids(result["results"]["processes"]) | _ids(result["results"]["experiments"])
    rec_ids = _ids(rec["similar_cases"])
    assert rec_ids, "ожидалась хотя бы одна рекомендация из смежной области"
    assert rec_ids.isdisjoint(all_shown_ids), "рекомендации не должны дублировать основные результаты"
    assert rec["adjacent_facilities"], "ожидалась хотя бы одна рекомендованная команда/лаборатория"


def test_ru_vs_international_standard_comparison(store):
    q = "Какой стандарт на катодную медь марки Cu-CATH-1 действует в Европе?"
    result = answer_query(store, q)
    standard_ids = _ids(result["results"]["standards"])
    assert "std_bs_en_1978" in standard_ids


def test_patents_surface_in_water_desalination_scenario(store):
    q = ("Какие методы обессоливания воды подходят для обогатительной фабрики, если исходная "
         "вода содержит сульфаты, хлориды, Ca, Mg, Na по 200-300 мг/л, а требуемый сухой "
         "остаток - не более 1000 мг/дм3?")
    result = answer_query(store, q)
    patent_ids = _ids(result["results"]["patents"])
    assert {"patent_heavy_metals_lime", "patent_sulfate_barium_freeze"} <= patent_ids


def test_confidence_filter(store):
    q = ("Какие методы обессоливания воды подходят для обогатительной фабрики, если исходная "
         "вода содержит сульфаты, хлориды, Ca, Mg, Na по 200-300 мг/л, а требуемый сухой "
         "остаток - не более 1000 мг/дм3?")
    result = answer_query(store, q, confidence_filter=["verified"])
    for bucket in result["results"].values():
        for item in bucket:
            assert item.get("confidence") in (None, "verified")


def test_pvp_synonym_matches_all_term_forms(store):
    for q in ["Что известно про ПВП?", "Печь взвешенной плавки", "fluidized bed furnace applications"]:
        result = answer_query(store, q)
        matched_ids = _ids(result["parsed"]["matched_entities"])
        assert "eq_flash_smelting_furnace" in matched_ids, f"не сработало для: {q}"


def test_consensus_conclusions_exclude_disputed_ones(store):
    q = ("Какие методы обессоливания воды подходят для обогатительной фабрики, если исходная "
         "вода содержит сульфаты, хлориды, Ca, Mg, Na по 200-300 мг/л, а требуемый сухой "
         "остаток - не более 1000 мг/дм3?")
    result = answer_query(store, q)

    disputed_ids = {c["a"]["id"] for c in result["contradictions"]} | {c["b"]["id"] for c in result["contradictions"]}
    assert "concl_savmin_meets_low_target" in disputed_ids
    assert "concl_lime_floor_limitation" in disputed_ids

    consensus_ids = _ids(result["consensus_conclusions"])
    assert consensus_ids.isdisjoint(disputed_ids)
    for c in result["consensus_conclusions"]:
        assert c["supporting_sources"] >= 0


def test_results_are_capped_and_ranked_by_relevance(store):
    q = "Какие методы обессоливания воды подходят, если вода содержит сульфаты и хлориды?"
    result = answer_query(store, q)
    totals = result["results_total_counts"]
    assert totals["publications"] > len(result["results"]["publications"])
    assert len(result["results"]["publications"]) <= 25
