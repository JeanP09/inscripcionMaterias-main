// ============================================================
// neo4j_setup.cypher
// Script de Cypher para Neo4j: crea los nodos (:Curso) y las
// relaciones [:PRERREQUIERE] del plan de Ingeniería de Sistemas.
//
// La relación (curso)-[:PRERREQUIERE]->(prerrequisito) se lee:
//   "curso PRERREQUIERE prerrequisito"
// Es decir, el curso apunta a las materias que deben cursarse
// antes de poder inscribirlo.
// ============================================================

// Limpiar datos previos (idempotencia).
MATCH (c:Curso) DETACH DELETE c;

// ------------------------------------------------------------
// Nodos Curso
// ------------------------------------------------------------
CREATE
  (:Curso {codigo: 'CAL-101', nombre: 'Cálculo I', creditos: 4, semestre: 1}),
  (:Curso {codigo: 'ALG-101', nombre: 'Álgebra Lineal', creditos: 3, semestre: 1}),
  (:Curso {codigo: 'ING-101', nombre: 'Introducción a la Ingeniería de Sistemas', creditos: 2, semestre: 1}),
  (:Curso {codigo: 'PRO-101', nombre: 'Fundamentos de Programación', creditos: 4, semestre: 1}),
  (:Curso {codigo: 'MDI-201', nombre: 'Matemáticas Discretas', creditos: 3, semestre: 2}),
  (:Curso {codigo: 'CAL-201', nombre: 'Cálculo II', creditos: 4, semestre: 2}),
  (:Curso {codigo: 'FIS-101', nombre: 'Física I', creditos: 4, semestre: 2}),
  (:Curso {codigo: 'PRO-201', nombre: 'Programación Orientada a Objetos', creditos: 4, semestre: 2}),
  (:Curso {codigo: 'CAL-301', nombre: 'Cálculo III', creditos: 4, semestre: 3}),
  (:Curso {codigo: 'FIS-201', nombre: 'Física II', creditos: 4, semestre: 3}),
  (:Curso {codigo: 'ECD-301', nombre: 'Ecuaciones Diferenciales', creditos: 3, semestre: 3}),
  (:Curso {codigo: 'ESD-301', nombre: 'Estructuras de Datos', creditos: 4, semestre: 3}),
  (:Curso {codigo: 'BDA-301', nombre: 'Bases de Datos I', creditos: 4, semestre: 3}),
  (:Curso {codigo: 'EST-301', nombre: 'Estadística y Probabilidad', creditos: 3, semestre: 4}),
  (:Curso {codigo: 'ADC-301', nombre: 'Arquitectura de Computadores', creditos: 4, semestre: 4}),
  (:Curso {codigo: 'BDA-401', nombre: 'Bases de Datos II', creditos: 4, semestre: 4}),
  (:Curso {codigo: 'SIS-401', nombre: 'Sistemas Operativos', creditos: 4, semestre: 4}),
  (:Curso {codigo: 'ISW-401', nombre: 'Ingeniería de Software I', creditos: 4, semestre: 4}),
  (:Curso {codigo: 'ANU-401', nombre: 'Análisis Numérico', creditos: 3, semestre: 5}),
  (:Curso {codigo: 'RED-401', nombre: 'Redes de Computadores I', creditos: 4, semestre: 5}),
  (:Curso {codigo: 'ISW-501', nombre: 'Ingeniería de Software II', creditos: 4, semestre: 5}),
  (:Curso {codigo: 'SID-501', nombre: 'Sistemas Distribuidos', creditos: 4, semestre: 5}),
  (:Curso {codigo: 'TCO-501', nombre: 'Teoría de la Computación', creditos: 3, semestre: 5}),
  (:Curso {codigo: 'RED-501', nombre: 'Redes de Computadores II', creditos: 4, semestre: 6}),
  (:Curso {codigo: 'IAR-601', nombre: 'Inteligencia Artificial', creditos: 4, semestre: 6}),
  (:Curso {codigo: 'SEG-601', nombre: 'Seguridad Informática', creditos: 3, semestre: 6}),
  (:Curso {codigo: 'SGI-601', nombre: 'Sistemas de Información Gerencial', creditos: 3, semestre: 7}),
  (:Curso {codigo: 'PRO-701', nombre: 'Proyecto de Grado', creditos: 6, semestre: 8});

// Índice para búsquedas rápidas por código.
CREATE CONSTRAINT curso_codigo IF NOT EXISTS
FOR (c:Curso) REQUIRE c.codigo IS UNIQUE;

// ------------------------------------------------------------
// Relaciones PRERREQUIERE: (curso)-[:PRERREQUIERE]->(prerequisito)
// ------------------------------------------------------------
MATCH (c:Curso), (p:Curso)
WHERE c.codigo = 'CAL-201' AND p.codigo = 'CAL-101'
CREATE (c)-[:PRERREQUIERE]->(p);

MATCH (c:Curso), (p:Curso)
WHERE c.codigo = 'FIS-101' AND p.codigo = 'CAL-101'
CREATE (c)-[:PRERREQUIERE]->(p);

MATCH (c:Curso), (p:Curso)
WHERE c.codigo = 'PRO-201' AND p.codigo = 'PRO-101'
CREATE (c)-[:PRERREQUIERE]->(p);

MATCH (c:Curso), (p:Curso)
WHERE c.codigo = 'CAL-301' AND p.codigo = 'CAL-201'
CREATE (c)-[:PRERREQUIERE]->(p);

MATCH (c:Curso), (p:Curso)
WHERE c.codigo = 'FIS-201' AND p.codigo IN ['FIS-101', 'CAL-201']
CREATE (c)-[:PRERREQUIERE]->(p);

MATCH (c:Curso), (p:Curso)
WHERE c.codigo = 'ECD-301' AND p.codigo IN ['CAL-201', 'ALG-101']
CREATE (c)-[:PRERREQUIERE]->(p);

MATCH (c:Curso), (p:Curso)
WHERE c.codigo = 'ESD-301' AND p.codigo IN ['PRO-201', 'MDI-201']
CREATE (c)-[:PRERREQUIERE]->(p);

MATCH (c:Curso), (p:Curso)
WHERE c.codigo = 'BDA-301' AND p.codigo = 'PRO-201'
CREATE (c)-[:PRERREQUIERE]->(p);

MATCH (c:Curso), (p:Curso)
WHERE c.codigo = 'EST-301' AND p.codigo = 'CAL-201'
CREATE (c)-[:PRERREQUIERE]->(p);

MATCH (c:Curso), (p:Curso)
WHERE c.codigo = 'BDA-401' AND p.codigo = 'BDA-301'
CREATE (c)-[:PRERREQUIERE]->(p);

MATCH (c:Curso), (p:Curso)
WHERE c.codigo = 'SIS-401' AND p.codigo IN ['ESD-301', 'ADC-301']
CREATE (c)-[:PRERREQUIERE]->(p);

MATCH (c:Curso), (p:Curso)
WHERE c.codigo = 'ISW-401' AND p.codigo IN ['ESD-301', 'BDA-301']
CREATE (c)-[:PRERREQUIERE]->(p);

MATCH (c:Curso), (p:Curso)
WHERE c.codigo = 'ANU-401' AND p.codigo IN ['ECD-301', 'PRO-201']
CREATE (c)-[:PRERREQUIERE]->(p);

MATCH (c:Curso), (p:Curso)
WHERE c.codigo = 'RED-401' AND p.codigo = 'SIS-401'
CREATE (c)-[:PRERREQUIERE]->(p);

MATCH (c:Curso), (p:Curso)
WHERE c.codigo = 'ISW-501' AND p.codigo = 'ISW-401'
CREATE (c)-[:PRERREQUIERE]->(p);

MATCH (c:Curso), (p:Curso)
WHERE c.codigo = 'SID-501' AND p.codigo IN ['SIS-401', 'RED-401']
CREATE (c)-[:PRERREQUIERE]->(p);

MATCH (c:Curso), (p:Curso)
WHERE c.codigo = 'TCO-501' AND p.codigo IN ['MDI-201', 'ESD-301']
CREATE (c)-[:PRERREQUIERE]->(p);

MATCH (c:Curso), (p:Curso)
WHERE c.codigo = 'RED-501' AND p.codigo = 'RED-401'
CREATE (c)-[:PRERREQUIERE]->(p);

MATCH (c:Curso), (p:Curso)
WHERE c.codigo = 'IAR-601' AND p.codigo IN ['ESD-301', 'EST-301']
CREATE (c)-[:PRERREQUIERE]->(p);

MATCH (c:Curso), (p:Curso)
WHERE c.codigo = 'SEG-601' AND p.codigo IN ['RED-401', 'SIS-401']
CREATE (c)-[:PRERREQUIERE]->(p);

MATCH (c:Curso), (p:Curso)
WHERE c.codigo = 'SGI-601' AND p.codigo = 'BDA-401'
CREATE (c)-[:PRERREQUIERE]->(p);

MATCH (c:Curso), (p:Curso)
WHERE c.codigo = 'PRO-701' AND p.codigo IN ['ISW-501', 'BDA-401', 'SGI-601']
CREATE (c)-[:PRERREQUIERE]->(p);

// ------------------------------------------------------------
// Verificación
// ------------------------------------------------------------
MATCH (c:Curso)-[:PRERREQUIERE]->(p:Curso)
RETURN c.nombre AS curso, collect(p.nombre) AS prerrequisitos
ORDER BY c.semestre, c.codigo;
