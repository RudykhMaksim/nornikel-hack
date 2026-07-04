MATCH ()-[r]->() RETURN count(r) AS relationship_count;
MATCH (a)-[:CONTRADICTS]->(b) RETURN a.name_ru AS claim_a, b.name_ru AS claim_b;
MATCH (m:Material {id:"mat_sulfate"})<-[:MENTIONS]-(pub:Publication) RETURN pub.name_ru LIMIT 5;
MATCH (e:Experiment)-[:APPLIES_TO_PROCESS]->(p:Process {id:"proc_heap_leaching"}) RETURN e.name_ru, e.geography;
MATCH (exp:Expert)-[:AFFILIATED_WITH]->(f:Facility) RETURN exp.name_ru AS expert, f.name_ru AS facility;
