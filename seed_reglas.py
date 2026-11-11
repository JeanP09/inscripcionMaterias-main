#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
seed_reglas.py
==============
Precarga en MongoDB las 28 materias del plan de estudios de Ingeniería de
Sistemas dentro de la colección ``reglas_negocio``, que es la fuente de verdad
de los prerrequisitos dinámicos que consume el backend FastAPI.

Estrategia de siembra (idempotente y NO destructiva)
----------------------------------------------------
Por cada materia se ejecuta::

    update_one(
        {"materia": codigo},
        {"$setOnInsert": {
            "materia": codigo,
            "nombre": nombre,
            "creditos": creditos,
            "semestre": semestre,
            "prerrequisitos": [...],
        }},
        upsert=True,
    )

El operador ``$setOnInsert`` solo escribe cuando el documento se inserta; si la
regla ya existe **no se toca**. Así el Administrador puede editar los
prerrequisitos (o cualquier otro campo) sin que una nueva ejecución del seed
sobrescriba sus cambios: únicamente se crean las materias que falten.

También se asegura un índice único sobre ``materia`` (una regla por materia).
Si existe un índice no único sobre ese campo, se reemplaza por el único.

Configuración (variables de entorno, con valores por defecto)
-------------------------------------------------------------
  MONGO_URI          -> mongodb://localhost:27017
  MONGO_DB           -> inscripcion
  MONGO_TIMEOUT_MS   -> 5000   (espera máxima para seleccionar el servidor)

La colección se puede cambiar con ``MONGO_COL_REGLAS`` (default
``reglas_negocio``), igual que en ``backend/main.py``.

Requisitos
----------
  pip install pymongo

Uso
---
  python seed_reglas.py

Códigos de salida
-----------------
  0  Todo correcto (se hayan insertado o no materias).
  1  Error controlado: pymongo ausente, MongoDB no disponible, catálogo
     inválido o imposible crear el índice único. Se muestra un mensaje claro,
     sin traceback.
"""

import os
import sys

# ---------------------------------------------------------------------------
# Dependencia opcional: permite que `python -m py_compile` funcione incluso en
# entornos sin pymongo, y que el error se reporte de forma amigable al ejecutar.
# ---------------------------------------------------------------------------
try:
    from pymongo import MongoClient
    from pymongo.errors import DuplicateKeyError, PyMongoError

    PYMONGO_DISPONIBLE = True
    _ERROR_IMPORTACION = None
except ImportError as exc:  # pragma: no cover - depende del entorno
    MongoClient = None

    class PyMongoError(Exception):
        """Sustituto local cuando pymongo no está instalado."""

    class DuplicateKeyError(PyMongoError):
        """Sustituto local cuando pymongo no está instalado."""

    PYMONGO_DISPONIBLE = False
    _ERROR_IMPORTACION = exc


# ---------------------------------------------------------------------------
# Configuración
# ---------------------------------------------------------------------------
MONGO_URI = os.getenv("MONGO_URI", "mongodb://localhost:27017")
MONGO_DB = os.getenv("MONGO_DB", "inscripcion")
MONGO_COL = os.getenv("MONGO_COL_REGLAS", "reglas_negocio")

NOMBRE_INDICE = "uq_reglas_materia"


def _leer_entero(nombre: str, default: int) -> int:
    """Lee una variable de entorno entera y cae al default si no es válida."""
    valor = os.getenv(nombre)
    if valor is None or not str(valor).strip():
        return default
    try:
        return int(valor)
    except (TypeError, ValueError):
        return default


MONGO_TIMEOUT_MS = _leer_entero("MONGO_TIMEOUT_MS", 5000)


class SeedError(Exception):
    """Error controlado del seed; se informa sin traceback."""


# ---------------------------------------------------------------------------
# Catálogo: (código, nombre, créditos, semestre, prerrequisitos)
# ---------------------------------------------------------------------------
MATERIAS = [
    ("CAL-101", "Cálculo I", 4, 1, []),
    ("ALG-101", "Álgebra Lineal", 3, 1, []),
    ("ING-101", "Introducción a la Ingeniería de Sistemas", 2, 1, []),
    ("PRO-101", "Fundamentos de Programación", 4, 1, []),
    ("MDI-201", "Matemáticas Discretas", 3, 2, []),
    ("CAL-201", "Cálculo II", 4, 2, ["CAL-101"]),
    ("FIS-101", "Física I", 4, 2, ["CAL-101"]),
    ("PRO-201", "Programación Orientada a Objetos", 4, 2, ["PRO-101"]),
    ("CAL-301", "Cálculo III", 4, 3, ["CAL-201"]),
    ("FIS-201", "Física II", 4, 3, ["FIS-101", "CAL-201"]),
    ("ECD-301", "Ecuaciones Diferenciales", 3, 3, ["CAL-201", "ALG-101"]),
    ("ESD-301", "Estructuras de Datos", 4, 3, ["PRO-201", "MDI-201"]),
    ("BDA-301", "Bases de Datos I", 4, 3, ["PRO-201"]),
    ("EST-301", "Estadística y Probabilidad", 3, 4, ["CAL-201"]),
    ("ADC-301", "Arquitectura de Computadores", 4, 4, []),
    ("BDA-401", "Bases de Datos II", 4, 4, ["BDA-301"]),
    ("SIS-401", "Sistemas Operativos", 4, 4, ["ESD-301", "ADC-301"]),
    ("ISW-401", "Ingeniería de Software I", 4, 4, ["ESD-301", "BDA-301"]),
    ("ANU-401", "Análisis Numérico", 3, 5, ["ECD-301", "PRO-201"]),
    ("RED-401", "Redes de Computadores I", 4, 5, ["SIS-401"]),
    ("ISW-501", "Ingeniería de Software II", 4, 5, ["ISW-401"]),
    ("SID-501", "Sistemas Distribuidos", 4, 5, ["SIS-401", "RED-401"]),
    ("TCO-501", "Teoría de la Computación", 3, 5, ["MDI-201", "ESD-301"]),
    ("RED-501", "Redes de Computadores II", 4, 6, ["RED-401"]),
    ("IAR-601", "Inteligencia Artificial", 4, 6, ["ESD-301", "EST-301"]),
    ("SEG-601", "Seguridad Informática", 3, 6, ["RED-401", "SIS-401"]),
    ("SGI-601", "Sistemas de Información Gerencial", 3, 7, ["BDA-401"]),
    ("PRO-701", "Proyecto de Grado", 6, 8, ["ISW-501", "BDA-401", "SGI-601"]),
]


def _documento(materia) -> dict:
    """Convierte una tupla del catálogo en el documento a insertar."""
    codigo, nombre, creditos, semestre, prerrequisitos = materia
    return {
        "materia": codigo,
        "nombre": nombre,
        "creditos": creditos,
        "semestre": semestre,
        "prerrequisitos": list(prerrequisitos),
    }


def validar_catalogo() -> None:
    """Comprueba la integridad del catálogo antes de tocar la base de datos."""
    if len(MATERIAS) != 28:
        raise SeedError(
            f"El catálogo debe tener 28 materias y tiene {len(MATERIAS)}."
        )

    codigos = set()
    for materia in MATERIAS:
        if len(materia) != 5:
            raise SeedError(f"Entrada de catálogo mal formada: {materia!r}")
        codigo, nombre, creditos, semestre, prerrequisitos = materia
        if not isinstance(codigo, str) or not codigo.strip():
            raise SeedError(f"Materia sin código válido: {materia!r}")
        if codigo in codigos:
            raise SeedError(f"Código duplicado en el catálogo: {codigo}")
        codigos.add(codigo)
        if not isinstance(nombre, str) or not nombre.strip():
            raise SeedError(f"La materia {codigo} no tiene nombre.")
        if not isinstance(creditos, int) or creditos <= 0:
            raise SeedError(f"La materia {codigo} tiene créditos inválidos.")
        if not isinstance(semestre, int) or semestre <= 0:
            raise SeedError(f"La materia {codigo} tiene semestre inválido.")
        if not isinstance(prerrequisitos, (list, tuple)):
            raise SeedError(
                f"Los prerrequisitos de {codigo} deben ser una lista."
            )

    desconocidos = sorted(
        {
            prerrequisito
            for _, _, _, _, prerrequisitos in MATERIAS
            for prerrequisito in prerrequisitos
            if prerrequisito not in codigos
        }
    )
    if desconocidos:
        raise SeedError(
            "Hay prerrequisitos que no existen en el catálogo: "
            + ", ".join(desconocidos)
        )


# ---------------------------------------------------------------------------
# MongoDB
# ---------------------------------------------------------------------------
def conectar():
    """Crea el cliente de MongoDB y verifica la conexión (ping)."""
    if not PYMONGO_DISPONIBLE:
        raise SeedError(
            "Falta el paquete 'pymongo'. Instálalo con: pip install pymongo"
        )

    cliente = None
    try:
        cliente = MongoClient(
            MONGO_URI,
            serverSelectionTimeoutMS=MONGO_TIMEOUT_MS,
        )
        # Fuerza una ida y vuelta real al servidor: MongoClient es perezoso.
        cliente.admin.command("ping")
        return cliente
    except PyMongoError as exc:
        if cliente is not None:
            cliente.close()
        raise SeedError(
            f"No se pudo conectar a MongoDB en '{MONGO_URI}' "
            f"(timeout {MONGO_TIMEOUT_MS} ms): {exc}"
        ) from exc


def asegurar_indice(coleccion) -> str:
    """
    Garantiza un índice único sobre ``materia``.

    - Si ya existe uno único, no hace nada.
    - Si existe uno no único, lo elimina y crea el único.
    - Si hay documentos duplicados por ``materia``, informa el problema.
    """
    try:
        indices = list(coleccion.list_indexes())
    except PyMongoError as exc:
        raise SeedError(
            f"No se pudieron leer los índices de '{MONGO_COL}': {exc}"
        ) from exc

    for indice in indices:
        claves = dict(indice.get("key", {}))
        if claves != {"materia": 1}:
            continue
        if indice.get("unique"):
            return indice.get("name", NOMBRE_INDICE)

        nombre = indice.get("name")
        if nombre and nombre != "_id_":
            try:
                coleccion.drop_index(nombre)
            except PyMongoError as exc:
                raise SeedError(
                    f"No se pudo reemplazar el índice '{nombre}' por uno "
                    f"único sobre 'materia': {exc}"
                ) from exc
        break

    try:
        return coleccion.create_index(
            "materia",
            unique=True,
            name=NOMBRE_INDICE,
        )
    except PyMongoError as exc:
        detalle = str(exc)
        if "duplicate" in detalle.lower() or "E11000" in detalle:
            raise SeedError(
                "No se pudo crear el índice único sobre 'materia' porque "
                "existen documentos duplicados en 'reglas_negocio'. "
                "Elimina los duplicados (conservando la regla vigente) y "
                "vuelve a ejecutar el seed."
            ) from exc
        raise SeedError(
            f"No se pudo crear el índice único sobre 'materia' en "
            f"'{MONGO_COL}': {exc}"
        ) from exc


def sembrar_reglas(mongo_db=None) -> dict:
    """
    Inserta/asegura las 28 materias en ``reglas_negocio``.

    Usa ``$setOnInsert`` + ``upsert`` para no sobrescribir las reglas que el
    Administrador ya haya editado. Las materias ausentes se crean con sus
    datos descriptivos (nombre, créditos, semestre) y sus prerrequisitos.

    Parámetro opcional ``mongo_db``: permite reutilizar una base ya conectada
    (útil para pruebas). Si es ``None`` se conecta usando las variables de
    entorno.

    Devuelve ``{"insertadas": int, "existentes": int}``.
    """
    validar_catalogo()

    cliente = None
    try:
        if mongo_db is None:
            cliente = conectar()
            mongo_db = cliente[MONGO_DB]

        coleccion = mongo_db[MONGO_COL]
        asegurar_indice(coleccion)

        insertadas = 0
        existentes = 0

        for materia in MATERIAS:
            documento = _documento(materia)
            filtro = {"materia": documento["materia"]}
            try:
                resultado = coleccion.update_one(
                    filtro,
                    {"$setOnInsert": documento},
                    upsert=True,
                )
            except DuplicateKeyError:
                # Otra ejecución simultánea insertó la regla justo antes:
                # el índice único nos protege y la materia ya existe.
                existentes += 1
                continue

            if resultado.upserted_id is not None:
                insertadas += 1
            else:
                existentes += 1

        return {"insertadas": insertadas, "existentes": existentes}
    finally:
        if cliente is not None:
            cliente.close()


# ---------------------------------------------------------------------------
# Punto de entrada
# ---------------------------------------------------------------------------
def main() -> int:
    print("=" * 70)
    print("Seed de reglas de negocio — Ingeniería de Sistemas (28 materias)")
    print("=" * 70)
    print(f"MongoDB   : {MONGO_URI}")
    print(f"Base      : {MONGO_DB}")
    print(f"Colección : {MONGO_COL}")
    print()

    try:
        resumen = sembrar_reglas()
    except SeedError as exc:
        print(f"[ERROR] {exc}", file=sys.stderr)
        if not PYMONGO_DISPONIBLE and _ERROR_IMPORTACION is not None:
            print(
                "        Detalle de la importación: "
                f"{_ERROR_IMPORTACION}",
                file=sys.stderr,
            )
        elif "No se pudo conectar" in str(exc):
            print(
                "        Comprueba que MongoDB esté en marcha "
                "(por ejemplo: docker compose up -d mongo) y que MONGO_URI / "
                "MONGO_DB sean correctos.",
                file=sys.stderr,
            )
        return 1
    except PyMongoError as exc:  # red de seguridad ante errores no envueltos
        print(
            f"[ERROR] Fallo de MongoDB durante el seed: {exc}",
            file=sys.stderr,
        )
        return 1

    total = resumen["insertadas"] + resumen["existentes"]
    print("Resumen del seed:")
    print(f"  Insertadas : {resumen['insertadas']}")
    print(f"  Existentes : {resumen['existentes']}")
    print(f"  Total      : {total} / {len(MATERIAS)} materias")
    print()
    print(
        "Las reglas ya existentes NO se modificaron "
        "($setOnInsert protege las ediciones del Administrador)."
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
