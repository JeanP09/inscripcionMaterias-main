#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
actualizar_grafo.py
===================
Sincroniza las relaciones [:PRERREQUIERE] del grafo Neo4j a partir de las
reglas de negocio almacenadas en MongoDB (colección ``reglas_negocio``).

El catálogo y los prerrequisitos ahora son Data-Driven:
  * El Data Lake llena ``catalogo_materias`` desde CSV.
  * El Administrador mantiene ``reglas_negocio`` (una regla por materia con
    sus prerrequisitos). Este script es el "puente" que mantiene el grafo de
    prerrequisitos siempre alineado con esas reglas.

Semántica de sincronización
---------------------------
La colección ``reglas_negocio`` es la fuente de verdad de TODAS las relaciones
[:PRERREQUIERE] (sentido: (curso)-[:PRERREQUIERE]->(prerrequisito)):

  * Por cada regla {materia, prerrequisitos}: se asegura que existan los nodos
    :Curso (MERGE por ``codigo``) y que las relaciones salientes de la materia
    sean EXACTAMENTE las de la lista (se borran las que sobran).
  * Si una materia deja de tener regla (su documento fue eliminado), se borran
    sus relaciones salientes ("huérfanas").

Configuración (variables de entorno, con valores por defecto)
-------------------------------------------------------------
  MONGO_URI        -> mongodb://localhost:27017
  MONGO_DB         -> inscripcion
  NEO4J_URI        -> bolt://localhost:7687
  NEO4J_USER       -> neo4j
  NEO4J_PASSWORD   -> password
  SYNC_CATALOGO    -> 0  (opcional: "1" además copia nombre/creditos/semestre
                          desde ``catalogo_materias`` a los nodos :Curso)

Requisitos
----------
  pip install pymongo neo4j

Uso
---
  python actualizar_grafo.py
"""

import os

from pymongo import MongoClient
from neo4j import GraphDatabase

# ---------------------------------------------------------------------------
# Configuración (variables de entorno con defaults de desarrollo)
# ---------------------------------------------------------------------------
MONGO_URI = os.getenv("MONGO_URI", "mongodb://localhost:27017")
MONGO_DB = os.getenv("MONGO_DB", "inscripcion")

NEO4J_URI = os.getenv("NEO4J_URI", "bolt://localhost:7687")
NEO4J_USER = os.getenv("NEO4J_USER", "neo4j")
NEO4J_PASSWORD = os.getenv("NEO4J_PASSWORD", "password")

# Opcional: además de sincronizar relaciones, copia las propiedades del
# catálogo (nombre/creditos/semestre) a los nodos :Curso.
SYNC_CATALOGO = os.getenv("SYNC_CATALOGO", "0").strip().lower() in (
    "1", "true", "si", "yes",
)

# ---------------------------------------------------------------------------
# Queries Cypher
# ---------------------------------------------------------------------------
# Borra las relaciones salientes de una materia cuyo prerrequisito ya NO
# aparece en la lista vigente.
CYPHER_BORRAR_SOBRANTES = """
MATCH (c:Curso {codigo: $materia})-[r:PRERREQUIERE]->(p:Curso)
WHERE NOT p.codigo IN $prerrequisitos
DELETE r
"""

# Garantiza que el nodo de la materia exista (MERGE) y crea (MERGE) cada
# relación hacia sus prerrequisitos. Con lista vacía solo crea el nodo.
CYPHER_ASEGURAR_REGLA = """
MERGE (c:Curso {codigo: $materia})
WITH c, $prerrequisitos AS prereqs
UNWIND prereqs AS codigo
MERGE (p:Curso {codigo: codigo})
MERGE (c)-[:PRERREQUIERE]->(p)
"""

# Borra las relaciones salientes de materias que YA NO tienen regla
# (reglas eliminadas de MongoDB). Si la lista viene vacía, borra todas:
# sin reglas => sin prerrequisitos.
CYPHER_BORRAR_HUERFANAS = """
MATCH (c:Curso)-[r:PRERREQUIERE]->()
WHERE NOT c.codigo IN $materias_con_regla
DELETE r
"""

# (Opcional) Crea/actualiza un nodo :Curso con sus propiedades del catálogo.
CYPHER_MERGE_CURSO = """
MERGE (c:Curso {codigo: $codigo})
SET c.nombre = $nombre,
    c.creditos = $creditos,
    c.semestre = $semestre
"""


# ---------------------------------------------------------------------------
# Lectura de reglas desde MongoDB
# ---------------------------------------------------------------------------
def leer_reglas(mongo_db):
    """
    Lee ``reglas_negocio`` y la normaliza a un diccionario
    {materia: [prerrequisito, ...]} limpio (sin vacíos ni duplicados).
    """
    reglas = {}
    for doc in mongo_db["reglas_negocio"].find({}):
        materia = doc.get("materia")
        if not isinstance(materia, str) or not materia.strip():
            continue

        prereqs = doc.get("prerrequisitos") or []
        if not isinstance(prereqs, list):
            prereqs = [prereqs]  # tolera un solo valor en vez de array

        limpios, vistos = [], set()
        for p in prereqs:
            if isinstance(p, str) and p.strip() and p.strip() not in vistos:
                vistos.add(p.strip())
                limpios.append(p.strip())

        reglas[materia.strip()] = limpios
    return reglas


# ---------------------------------------------------------------------------
# Transacciones Neo4j
# ---------------------------------------------------------------------------
def _tx_sincronizar(tx, reglas):
    """Aplica todas las reglas y limpia las relaciones huérfanas."""
    for materia, prereqs in reglas.items():
        tx.run(
            CYPHER_BORRAR_SOBRANTES,
            materia=materia,
            prerrequisitos=prereqs,
        )
        tx.run(
            CYPHER_ASEGURAR_REGLA,
            materia=materia,
            prerrequisitos=prereqs,
        )
    # Al final: materias sin regla => sin relaciones salientes.
    tx.run(CYPHER_BORRAR_HUERFANAS, materias_con_regla=list(reglas.keys()))


def _tx_catalogo(tx, materias):
    """(Opcional) MERGE de nodos :Curso con propiedades del catálogo."""
    for m in materias:
        codigo = m.get("codigo")
        if not isinstance(codigo, str) or not codigo.strip():
            continue
        tx.run(
            CYPHER_MERGE_CURSO,
            codigo=codigo.strip(),
            nombre=m.get("nombre", ""),
            creditos=m.get("creditos"),
            semestre=m.get("semestre"),
        )


def sincronizar_catalogo(mongo_db, driver):
    """Copia nombre/creditos/semestre de ``catalogo_materias`` a :Curso."""
    materias = list(mongo_db["catalogo_materias"].find({}, {"_id": 0}))
    with driver.session() as session:
        session.execute_write(_tx_catalogo, materias)
    return len(materias)


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------
def main():
    print("=" * 70)
    print("Sincronización del grafo de prerrequisitos (MongoDB -> Neo4j)")
    print("=" * 70)

    mongo = None
    driver = None
    try:
        # 1. Leer reglas desde MongoDB
        print(f"\n[1/3] Conectando a MongoDB ({MONGO_URI}, db='{MONGO_DB}')...")
        mongo = MongoClient(MONGO_URI)
        mongo_db = mongo[MONGO_DB]
        reglas = leer_reglas(mongo_db)
        print(f"   Reglas leídas: {len(reglas)}")

        # 2. Conectar a Neo4j
        print(f"\n[2/3] Conectando a Neo4j ({NEO4J_URI})...")
        driver = GraphDatabase.driver(
            NEO4J_URI, auth=(NEO4J_USER, NEO4J_PASSWORD)
        )
        driver.verify_connectivity()
        print("   Conexión OK")

        # 3. Sincronizar relaciones
        print("\n[3/3] Sincronizando relaciones [:PRERREQUIERE]...")
        with driver.session() as session:
            session.execute_write(_tx_sincronizar, reglas)

        # (Opcional) sincronizar propiedades del catálogo a los nodos.
        if SYNC_CATALOGO:
            n = sincronizar_catalogo(mongo_db, driver)
            print(f"   Catálogo sincronizado: {n} nodos :Curso actualizados.")

        # Resumen
        print("\nReglas aplicadas al grafo:")
        if not reglas:
            print("   (sin reglas: se eliminaron todas las relaciones)")
        for materia, prereqs in sorted(reglas.items()):
            print(f"   - {materia} -> {prereqs or '(sin prerrequisitos)'}")

        print("\nSincronización completada.")
    finally:
        if driver is not None:
            driver.close()
        if mongo is not None:
            mongo.close()


if __name__ == "__main__":
    main()
