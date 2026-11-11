# -*- coding: utf-8 -*-
"""
ingesta_datalake.py
===================
Ingesta Data-Driven del **Sistema de Inscripción de Materias**.

Este script es el **nuevo punto de entrada** de datos: ya no se usan datos
ficticios (``seed_data.py`` / ``mongo_seed.js``), sino **CSV reales** que el
administrador carga. El Data Lake (arquitectura Medallón) pasa a ser la fuente
de verdad y desde aquí se alimenta PostgreSQL.

Flujo general
-------------
  1. BRONZE : copia 1:1 de los CSV crudos en ``datalake/bronze/``.
  2. SILVER : limpieza/normalización y guardado en Parquet
              (``datalake/silver/*.parquet``) con pandas + pyarrow.
  3. POSTGRESQL: inserción idempotente de la oferta académica en las tablas
              ``curso``, ``nrc`` y ``horario``, y de la matriz del estudiante
              en ``historial``, ``estudiante_perfil`` y ``matriz_por_ver``.

Modos de ejecución (mutuamente excluyentes)
-------------------------------------------
El modo decide **qué CSV se exige, se copia, se lee y qué tablas se tocan**.
Los modos ``--solo-*`` nunca leen el CSV del otro dominio:

  * **completo** (sin flags)
      Procesa oferta + matriz. Refresca ``curso``/``nrc``/``horario`` y
      reemplaza **todo** ``historial`` con la matriz en formato antiguo. Es el
      comportamiento histórico del script.
  * **--solo-oferta**
      Procesa SOLO la oferta (curso/nrc/horario). No exige, no copia y no lee
      la matriz. No ejecuta DDL, DELETE ni INSERT sobre ``historial``,
      ``estudiante_perfil`` ni ``matriz_por_ver``: esos datos quedan intactos.
      En Silver escribe solo ``curso``/``nrc``/``horario``.parquet.
      Lo usa ``POST /api/admin/upload-oferta``.
  * **--solo-matriz**
      Procesa SOLO la matriz. No exige, no copia y no lee la oferta; no toca
      ``curso``/``nrc``/``horario``. Crea si faltan las tablas ``historial``,
      ``estudiante_perfil`` y ``matriz_por_ver``; hace upsert del estudiante
      (nombre ``Estudiante <id>``, ``plan_id`` 0) y reemplaza con DELETE +
      INSERT, **por cada estudiante presente en el CSV**, sus aprobadas y sus
      materias por ver, todo dentro de una única transacción. En Silver escribe
      ``historial.parquet``, ``matriz_por_ver.parquet`` y
      ``estudiante_perfil.parquet`` con los estudiantes del CSV.

Entradas (CSV)
--------------
  * ``oferta_academica.csv`` : una fila por **sesión de horario**, con las
    columnas del curso, del NRC y de la sesión (formato detallado en
    ``README_INGESTA.md``).
  * ``matriz_estudiante.csv`` : una fila por (estudiante, materia). Se aceptan
    dos formatos:

      - **Enriquecido** (recomendado, usado por ``--solo-matriz``)::

            estudiante_id,carrera,semestre,estado,codigo_curso,nombre_curso

        ``estado`` ∈ {``aprobada``, ``vista``} -> ``historial``.
        ``estado`` ∈ {``por_ver``, ``pendiente``} -> ``matriz_por_ver``.
        ``carrera`` y ``semestre`` alimentan ``estudiante_perfil``.
        ``nombre_curso`` es opcional: se acepta y se ignora.

      - **Antiguo** (tolerado por ``--solo-matriz``)::

            estudiante_id,codigo_curso

        Toda fila se asume ``aprobada``. Es el único formato que procesa el
        modo completo, que mantiene su comportamiento histórico.

    Los encabezados toleran mayúsculas, acentos y espacios accidentales.

Tablas ``historial``, ``estudiante_perfil`` y ``matriz_por_ver``
----------------------------------------------------------------
El esquema de ``schema.sql`` **no tiene** estas tablas, por lo que el script
las crea de forma autocontenida cuando el modo las necesita:

    CREATE TABLE IF NOT EXISTS Historial (
        estudiante_id INTEGER NOT NULL,
        codigo_curso  VARCHAR(10) NOT NULL,
        PRIMARY KEY (estudiante_id, codigo_curso)
    );

    CREATE TABLE IF NOT EXISTS estudiante_perfil (
        estudiante_id INTEGER PRIMARY KEY,
        carrera       TEXT,
        semestre      INTEGER
    );

    CREATE TABLE IF NOT EXISTS matriz_por_ver (
        estudiante_id INTEGER NOT NULL,
        codigo_curso  VARCHAR(10) NOT NULL,
        PRIMARY KEY (estudiante_id, codigo_curso)
    );

Decisiones documentadas:
  * **Sin claves foráneas** hacia ``Estudiante`` ni ``Curso``: la matriz puede
    referenciar estudiantes y materias que aún no existen en el catálogo/oferta
    vigente (por ejemplo, materias aprobadas en semestres anteriores que hoy no
    se ofertan). Un FK haría fallar la ingesta por integridad referencial. La
    validación queda a nivel de aplicación o de un paso posterior.
  * **PK compuesta (estudiante_id, codigo_curso)** : evita duplicados y permite
    carga idempotente.
  * En el modo completo **no** se crean filas en ``Estudiante`` (igual que
    antes). En ``--solo-matriz`` sí se hace upsert porque el CSV enriquecido es
    la fuente que registra al estudiante; para los nuevos se usa
    ``'Estudiante <id>'`` y ``plan_id`` 0.
  * ``--solo-matriz`` reemplaza únicamente los estudiantes del CSV: los datos
    de otros estudiantes se conservan.

Requisitos
----------
  pip install pandas pyarrow psycopg2-binary

Configuración (variables de entorno con valores por defecto)
------------------------------------------------------------
  DB_HOST (localhost) · DB_PORT (5432) · DB_NAME (universidad)
  DB_USER (postgres)  · DB_PASSWORD (postgres)

Uso
---
  # Con las rutas por defecto (datalake/oferta_academica.csv y
  # datalake/matriz_estudiante.csv):
  python datalake/ingesta_datalake.py

  # Con rutas explícitas:
  python datalake/ingesta_datalake.py \
      --oferta /ruta/oferta_academica.csv \
      --matriz /ruta/matriz_estudiante.csv

  # Solo procesar y escribir Bronze/Silver, sin tocar PostgreSQL:
  python datalake/ingesta_datalake.py --sin-bd

  # Cargar SOLO la oferta (curso/nrc/horario) sin leer ni vaciar la matriz
  # de aprobados (historial). Lo usa POST /api/admin/upload-oferta:
  python datalake/ingesta_datalake.py --oferta /ruta/oferta.csv --solo-oferta

  # Cargar SOLO la matriz (aprobadas, por ver y perfil) de los estudiantes
  # del CSV, sin leer ni modificar la oferta:
  python datalake/ingesta_datalake.py --matriz /ruta/matriz.csv --solo-matriz

  # También se pueden combinar con --sin-bd para escribir solo los Parquet:
  python datalake/ingesta_datalake.py --matriz /ruta/matriz.csv --solo-matriz --sin-bd

Validaciones y tolerancia a errores
-----------------------------------
  * CSV en UTF-8 (con/sin BOM); si falla la decodificación se reintenta latin-1.
  * Encabezados normalizados (sin BOM/acentos, minúsculas, espacios y guiones
    convertidos en ``_``); se rechazan columnas duplicadas tras normalizar.
  * Los textos se recortan y los códigos se pasan a MAYÚSCULAS.
  * Los códigos de más de 10 caracteres se descartan con aviso (VARCHAR(10)).
  * Los numéricos inválidos o con decimales pasan a nulo y la fila se descarta
    en la tabla que corresponda; se informa por consola.
  * Horas: se aceptan ``HH:MM`` y ``HH:MM:SS``; las inválidas se descartan
    junto con su sesión.
  * Se filtran NRC sin curso válido y horarios sin NRC válido para no violar
    las claves foráneas de ``schema.sql``.
  * Matriz: se descartan filas sin ``estudiante_id`` positivo o sin
    ``codigo_curso``; los estados desconocidos se descartan con aviso (nunca se
    asumen aprobados por error). Si un mismo par (estudiante, curso) aparece
    con estados distintos, gana ``aprobada``.
  * Toda la carga a PostgreSQL ocurre en una sola transacción (COMMIT/ROLLBACK).
"""

import argparse
import json
import math
import os
import re
import shutil
import time
import unicodedata
from pathlib import Path

# pandas/pyarrow se importan de forma tolerante para que ``--help`` y la
# validación de argumentos funcionen incluso en un entorno sin dependencias.
# ``main`` comprueba ``_ERROR_DEPENDENCIAS`` antes de procesar los CSV.
try:
    import pandas as pd
    import pyarrow  # noqa: F401  # engine por defecto de pandas.to_parquet
except ImportError as _exc:  # pragma: no cover - depende del entorno
    pd = None
    _ERROR_DEPENDENCIAS = _exc
else:
    _ERROR_DEPENDENCIAS = None

# ---------------------------------------------------------------------------
# Rutas de las capas del data lake
# ---------------------------------------------------------------------------
RAIZ = Path(__file__).resolve().parent       # .../datalake
BRONZE = RAIZ / "bronze"
SILVER = RAIZ / "silver"

# CSV por defecto (el administrador los deja junto al script o pasa rutas).
OFERTA_DEFAULT = RAIZ / "oferta_academica.csv"
MATRIZ_DEFAULT = RAIZ / "matriz_estudiante.csv"

# ---------------------------------------------------------------------------
# Configuración de conexión a PostgreSQL
# ---------------------------------------------------------------------------
DB_CONFIG = {
    "host": os.getenv("DB_HOST", "localhost"),
    "port": int(os.getenv("DB_PORT", "5432")),
    "dbname": os.getenv("DB_NAME", "universidad"),
    "user": os.getenv("DB_USER", "postgres"),
    "password": os.getenv("DB_PASSWORD", "postgres"),
}

BATCH_SIZE = 1_000  # filas por lote en la inserción masiva
MAX_CODIGO = 10     # VARCHAR(10) en Curso.codigo_alfanumerico / Historial.codigo_curso

# ---------------------------------------------------------------------------
# Contratos de los CSV (columnas requeridas)
# ---------------------------------------------------------------------------
COL_OFERTA = [
    "codigo_curso", "nombre_curso", "creditos",   # -> Curso
    "id_nrc", "jornada", "modalidad", "cupo_max",  # -> NRC
    "numero_sesion", "dia", "hora_inicio", "hora_fin",  # -> Horario
]
COL_MATRIZ = ["estudiante_id", "codigo_curso"]  # mínimo (formato antiguo)
# Formato enriquecido reconocido por --solo-matriz. Solo ``estudiante_id`` y
# ``codigo_curso`` son obligatorios; el resto se usa si está presente.
COL_MATRIZ_ENRIQUECIDA = [
    "estudiante_id", "carrera", "semestre", "estado", "codigo_curso",
]
COL_MATRIZ_OPCIONALES = ["nombre_curso"]

# Estados del CSV -> conjunto destino (se comparan normalizados).
ESTADOS_APROBADA = {
    "aprobada", "aprobadas", "aprobado", "aprobados",
    "vista", "vistas", "visto", "vistos",
}
ESTADOS_POR_VER = {
    "por_ver", "porver", "por_verificar", "pendiente", "pendientes",
}

# DDL de las tablas nuevas (se pliegan a minúsculas, como el resto del esquema).
DDL_HISTORIAL = [
    """
    CREATE TABLE IF NOT EXISTS Historial (
        estudiante_id INTEGER NOT NULL,
        codigo_curso  VARCHAR(10) NOT NULL,
        PRIMARY KEY (estudiante_id, codigo_curso)
    )
    """,
    "CREATE INDEX IF NOT EXISTS idx_historial_curso ON Historial (codigo_curso)",
]

DDL_ESTUDIANTE_PERFIL = """
    CREATE TABLE IF NOT EXISTS estudiante_perfil (
        estudiante_id INTEGER PRIMARY KEY,
        carrera       TEXT,
        semestre      INTEGER
    )
"""

DDL_MATRIZ_POR_VER = """
    CREATE TABLE IF NOT EXISTS matriz_por_ver (
        estudiante_id INTEGER NOT NULL,
        codigo_curso  VARCHAR(10) NOT NULL,
        PRIMARY KEY (estudiante_id, codigo_curso)
    )
"""

# DDL que necesita --solo-matriz (crea lo que falte, sin tocar curso/nrc/horario).
DDL_MATRIZ = [DDL_ESTUDIANTE_PERFIL, DDL_MATRIZ_POR_VER, *DDL_HISTORIAL]

# Columnas en el orden exacto en que se insertan (deben coincidir con el SQL).
COL_INSERT_CURSO = ["codigo_alfanumerico", "nombre", "creditos"]
COL_INSERT_NRC = ["id_nrc", "curso_id", "jornada", "modalidad", "cupo_max"]
COL_INSERT_HORARIO = ["nrc_id", "numero_sesion", "dia", "hora_inicio", "hora_fin"]
COL_INSERT_HISTORIAL = ["estudiante_id", "codigo_curso"]
COL_INSERT_PERFIL = ["estudiante_id", "carrera", "semestre"]


# ---------------------------------------------------------------------------
# Utilidades de limpieza
# ---------------------------------------------------------------------------
def _entero(serie):
    """Convierte a entero nullable (Int64), tolerando nulos y texto inválido.

    Los valores con parte decimal (p. ej. ``4.5``) se tratan como inválidos y
    pasan a nulo en lugar de truncarse silenciosamente.
    """
    num = pd.to_numeric(serie, errors="coerce")
    decimales = num.notna() & (num % 1 != 0)
    return num.where(~decimales).astype("Int64")


def _texto(serie):
    """Normaliza a texto (StringDtype), recorta espacios y convierte '' en nulo."""
    return serie.astype("string").str.strip().replace("", pd.NA)


def _sin_acentos(texto):
    """Quita diacríticos (para comparar 'Matemática' / 'matematica')."""
    descompuesto = unicodedata.normalize("NFKD", str(texto))
    return "".join(c for c in descompuesto if not unicodedata.combining(c))


def _normalizar_columna(nombre):
    """
    Normaliza un encabezado de CSV: sin BOM, sin acentos, minúsculas y con
    espacios/guiones convertidos en ``_``.

    Ejemplos: ``" Estudiante ID "`` -> ``estudiante_id``;
    ``"Código_Curso"`` -> ``codigo_curso``.
    """
    texto = _sin_acentos(str(nombre)).lstrip("\ufeff").strip().lower()
    texto = re.sub(r"[\s\-]+", "_", texto)
    return re.sub(r"_+", "_", texto).strip("_")


def _normalizar_estado(valor):
    """Mapea el estado del CSV a 'aprobada' / 'por_ver' (o None si no es válido)."""
    if valor is None or pd.isna(valor):
        return None
    clave = _normalizar_columna(str(valor))
    if clave in ESTADOS_APROBADA:
        return "aprobada"
    if clave in ESTADOS_POR_VER:
        return "por_ver"
    return None


def _primero_no_nulo(serie):
    """Primer valor no nulo de una serie (pd.NA si todos son nulos)."""
    validos = serie.dropna()
    return validos.iloc[0] if len(validos) else pd.NA


def _hora_hhmmss(valor):
    """Normaliza una hora a ``HH:MM:SS``. Acepta ``HH:MM`` y ``HH:MM:SS``.

    Devuelve ``None`` si el valor está vacío, no es una hora o está fuera de
    rango (horas 0-23, minutos/segundos 0-59).
    """
    if valor is None or pd.isna(valor):
        return None
    s = str(valor).strip()
    if not s:
        return None
    partes = s.split(":")
    if len(partes) == 2:
        hh, mm = partes
        ss = "00"
    elif len(partes) == 3:
        hh, mm, ss = partes
    else:
        return None
    try:
        hh, mm, ss = int(hh), int(mm), int(ss)
    except ValueError:
        return None
    if not (0 <= hh <= 23 and 0 <= mm <= 59 and 0 <= ss <= 59):
        return None
    return f"{hh:02d}:{mm:02d}:{ss:02d}"


def _advertir(df, condicion, mensaje):
    """Imprime una advertencia si ``condicion`` (serie booleana) tiene True."""
    n = int(condicion.fillna(False).sum())
    if n:
        print(f"   [AVISO] {mensaje}: {n:,} fila(s)")


def _descartar_codigos_largos(df, columna, etiqueta):
    """Descarta códigos que exceden ``MAX_CODIGO`` (harían fallar el INSERT)."""
    largos = df[columna].astype("string").str.len()
    malos = (largos.notna() & (largos > MAX_CODIGO)).fillna(False)
    n = int(malos.sum())
    if n:
        ejemplos = df.loc[malos, columna].head(3).tolist()
        print(
            f"   [AVISO] {n:,} fila(s) de {etiqueta} descartadas por código de "
            f"más de {MAX_CODIGO} caracteres (p. ej. {ejemplos})"
        )
        df = df.loc[~malos].reset_index(drop=True)
    return df


# ---------------------------------------------------------------------------
# Limpieza y derivación de tablas
# ---------------------------------------------------------------------------
def limpiar_oferta(df):
    """Limpia el DataFrame crudo de la oferta (una fila por sesión)."""
    df = df.copy()

    # Textos.
    df["codigo_curso"] = _texto(df["codigo_curso"]).str.upper()
    df["nombre_curso"] = _texto(df["nombre_curso"])
    df["jornada"] = _texto(df["jornada"])
    df["modalidad"] = _texto(df["modalidad"])

    # Numéricos.
    df["creditos"] = _entero(df["creditos"])
    df["id_nrc"] = _entero(df["id_nrc"])
    df["cupo_max"] = _entero(df["cupo_max"])
    df["numero_sesion"] = _entero(df["numero_sesion"])
    df["dia"] = _entero(df["dia"])

    # Horas normalizadas a HH:MM:SS (se descartan valores fuera de rango).
    ini_crudo, fin_crudo = df["hora_inicio"], df["hora_fin"]
    df["hora_inicio"] = ini_crudo.map(_hora_hhmmss)
    df["hora_fin"] = fin_crudo.map(_hora_hhmmss)
    _advertir(df, ini_crudo.notna() & df["hora_inicio"].isna(),
              "hora_inicio vacía o inválida (se descartará la sesión)")
    _advertir(df, fin_crudo.notna() & df["hora_fin"].isna(),
              "hora_fin vacía o inválida (se descartará la sesión)")
    _advertir(df, df["hora_inicio"].notna() & df["hora_fin"].notna()
              & (df["hora_inicio"].fillna("") >= df["hora_fin"].fillna("")),
              "hora_inicio >= hora_fin (se conserva, pero revise la sesión)")

    # Los códigos deben caber en VARCHAR(10).
    df = _descartar_codigos_largos(df, "codigo_curso", "oferta")

    # Advertencias de calidad (no bloquean la ingesta).
    _advertir(df, df["dia"].notna() & ((df["dia"] < 1) | (df["dia"] > 7)),
              "día fuera del rango 1..7 (1=lunes)")
    _advertir(df, df["creditos"].notna() & (df["creditos"] < 0),
              "créditos negativos")
    _advertir(df, df["cupo_max"].notna() & (df["cupo_max"] < 0),
              "cupo máximo negativo")

    # Sesiones exactamente duplicadas.
    n_antes = len(df)
    df = df.drop_duplicates().reset_index(drop=True)
    if len(df) < n_antes:
        print(f"   [AVISO] {n_antes - len(df):,} fila(s) de sesión duplicadas eliminadas")

    return df


def construir_tablas(oferta):
    """Deriva los DataFrames ``curso``, ``nrc`` y ``horario`` a partir de la oferta."""
    # --- Curso: un registro por código de materia ---------------------------
    curso = (
        oferta[["codigo_curso", "nombre_curso", "creditos"]]
        .rename(columns={"codigo_curso": "codigo_alfanumerico",
                         "nombre_curso": "nombre"})
        .dropna(subset=["codigo_alfanumerico", "nombre", "creditos"])
    )
    # Un mismo código con nombre/créditos distintos => inconsistencia.
    conflicto_curso = curso.groupby("codigo_alfanumerico")[
        ["nombre", "creditos"]].nunique()
    n_conf = int((conflicto_curso > 1).any(axis=1).sum())
    if n_conf:
        print(f"   [AVISO] {n_conf} código(s) de curso con nombre/créditos "
              f"inconsistentes entre filas (se conserva la primera aparición)")
    curso = curso.drop_duplicates(subset=["codigo_alfanumerico"], keep="first")
    curso = curso.reset_index(drop=True)

    # --- NRC: un registro por id_nrc --------------------------------------
    nrc = (
        oferta[["id_nrc", "codigo_curso", "jornada", "modalidad", "cupo_max"]]
        .rename(columns={"codigo_curso": "curso_id"})
        .dropna(subset=["id_nrc", "curso_id", "jornada", "modalidad", "cupo_max"])
    )
    # Un id_nrc apuntando a más de un curso => inconsistencia.
    cursos_por_nrc = nrc.groupby("id_nrc")["curso_id"].nunique()
    n_conf = int((cursos_por_nrc > 1).sum())
    if n_conf:
        print(f"   [AVISO] {n_conf} id_nrc con más de un curso asociado "
              f"(se conserva la primera aparición)")

    # Mismo id_nrc con jornada/modalidad/cupo distintos => inconsistencia.
    atributos_nrc = nrc.groupby("id_nrc")[
        ["jornada", "modalidad", "cupo_max"]].nunique()
    n_conf = int((atributos_nrc > 1).any(axis=1).sum())
    if n_conf:
        print(f"   [AVISO] {n_conf} id_nrc con jornada/modalidad/cupo "
              f"inconsistentes entre filas (se conserva la primera aparición)")

    nrc = nrc.drop_duplicates(subset=["id_nrc"], keep="first")
    nrc = nrc.reset_index(drop=True)

    # Integridad referencial NRC -> Curso: si un curso se descartó por datos
    # incompletos, sus NRC tampoco se pueden insertar (FK de NRC.curso_id).
    codigos_validos = set(curso["codigo_alfanumerico"])
    n_antes = len(nrc)
    nrc = nrc[nrc["curso_id"].isin(codigos_validos)].reset_index(drop=True)
    if len(nrc) < n_antes:
        print(f"   [AVISO] {n_antes - len(nrc):,} NRC descartado(s): su curso "
              f"no quedó válido (código/nombre/créditos incompletos)")

    # --- Horario: una fila por sesión (nrc_id, número de sesión, día, horas) ---
    horario = (
        oferta[["id_nrc", "numero_sesion", "dia", "hora_inicio", "hora_fin"]]
        .rename(columns={"id_nrc": "nrc_id"})
        .dropna(subset=["nrc_id", "numero_sesion", "dia",
                        "hora_inicio", "hora_fin"])
        .reset_index(drop=True)
    )

    # Integridad referencial Horario -> NRC (FK de Horario.nrc_id).
    nrc_validos = set(nrc["id_nrc"])
    n_antes = len(horario)
    horario = horario[horario["nrc_id"].isin(nrc_validos)].reset_index(drop=True)
    if len(horario) < n_antes:
        print(f"   [AVISO] {n_antes - len(horario):,} sesión(es) descartada(s): "
              f"su NRC no quedó válido")

    n_antes = len(horario)
    horario = horario.drop_duplicates().reset_index(drop=True)
    if len(horario) < n_antes:
        print(f"   [AVISO] {n_antes - len(horario):,} sesión(es) duplicada(s) "
              f"eliminada(s)")

    return curso, nrc, horario


def limpiar_matriz(df):
    """Limpia la matriz de aprobados -> DataFrame ``historial`` (formato antiguo).

    Es el camino del modo completo, que mantiene el refresco total de
    ``historial``. El formato enriquecido (con ``estado``/``carrera``/
    ``semestre``) debe procesarse con ``--solo-matriz``.
    """
    df = df.copy()
    if set(df.columns) & {"carrera", "semestre", "estado", "nombre_curso"}:
        print(
            "   [AVISO] El modo completo procesa la matriz en formato antiguo: "
            "se ignoran estado/carrera/semestre y todas las filas se cargan "
            "como aprobadas. Use --solo-matriz para el formato enriquecido."
        )
    df["estudiante_id"] = _entero(df["estudiante_id"])
    df["codigo_curso"] = _texto(df["codigo_curso"]).str.upper()

    n_antes = len(df)
    df = df.dropna(subset=["estudiante_id", "codigo_curso"])
    if len(df) < n_antes:
        print(f"   [AVISO] {n_antes - len(df):,} fila(s) descartada(s) por "
              f"estudiante_id/codigo_curso vacíos o inválidos")
    _advertir(df, df["estudiante_id"] <= 0,
              "estudiante_id <= 0 (se conserva, pero revise el dato)")

    df = _descartar_codigos_largos(df, "codigo_curso", "matriz")

    n_antes = len(df)
    df = df.drop_duplicates().reset_index(drop=True)
    if len(df) < n_antes:
        print(f"   [AVISO] {n_antes - len(df):,} par(es) "
              f"(estudiante, curso) duplicado(s) eliminado(s)")
    return df


def limpiar_matriz_solo(df):
    """
    Limpia la matriz para el modo ``--solo-matriz``.

    Acepta el formato enriquecido
    (``estudiante_id,carrera,semestre,estado,codigo_curso,nombre_curso``) y el
    formato antiguo (``estudiante_id,codigo_curso``, se asume ``aprobada``).
    Devuelve un diccionario con los DataFrames listos para persistir:

      * ``historial``      : (estudiante_id, codigo_curso) aprobadas/vistas.
      * ``matriz_por_ver`` : (estudiante_id, codigo_curso) por ver/pendientes.
      * ``perfil``         : (estudiante_id, carrera, semestre) por estudiante.
      * ``estudiantes``    : ids únicos presentes en el CSV.
      * ``formato``        : ``"enriquecida"`` o ``"antigua"``.
      * ``aprobadas`` / ``por_ver`` : conteos de filas a insertar.
    """
    df = df.copy()

    # --- Detección de formato ---------------------------------------------
    columnas_extra = (
        set(COL_MATRIZ_ENRIQUECIDA) | set(COL_MATRIZ_OPCIONALES)
    ) - set(COL_MATRIZ)
    enriquecida = bool(set(df.columns) & columnas_extra)
    formato = "enriquecida" if enriquecida else "antigua"

    # --- Claves ------------------------------------------------------------
    df["estudiante_id"] = _entero(df["estudiante_id"])
    df["codigo_curso"] = _texto(df["codigo_curso"]).str.upper()

    # --- Estado ------------------------------------------------------------
    if "estado" in df.columns:
        df["estado"] = _texto(df["estado"]).map(_normalizar_estado)
        n_estado_inv = int(df["estado"].isna().sum())
        if n_estado_inv:
            print(
                f"   [AVISO] {n_estado_inv:,} fila(s) descartada(s) por "
                f"'estado' vacío o desconocido (se esperan aprobada/vista o "
                f"por_ver/pendiente)"
            )
    else:
        if enriquecida:
            print("   [AVISO] matriz sin columna 'estado'; se asume 'aprobada'")
        df["estado"] = "aprobada"

    # --- Perfil (opcional) -------------------------------------------------
    df["carrera"] = _texto(df["carrera"]) if "carrera" in df.columns else pd.NA
    if "semestre" in df.columns:
        df["semestre"] = _entero(df["semestre"])
        _advertir(df, df["semestre"].notna() & (df["semestre"] < 0),
                  "semestre negativo (se descarta solo el dato de perfil)")
        df.loc[df["semestre"] < 0, "semestre"] = pd.NA
    else:
        df["semestre"] = pd.NA

    # --- Filas inválidas ---------------------------------------------------
    n_antes = len(df)
    df = df.dropna(
        subset=["estudiante_id", "codigo_curso", "estado"]
    ).reset_index(drop=True)
    if len(df) < n_antes:
        print(
            f"   [AVISO] {n_antes - len(df):,} fila(s) descartada(s) por "
            f"estudiante_id/codigo_curso/estado vacíos o inválidos"
        )

    n_no_positivos = int((df["estudiante_id"] <= 0).sum())
    if n_no_positivos:
        print(
            f"   [AVISO] {n_no_positivos:,} fila(s) descartada(s) por "
            f"estudiante_id <= 0"
        )
        df = df[df["estudiante_id"] > 0].reset_index(drop=True)

    df = _descartar_codigos_largos(df, "codigo_curso", "matriz")

    # --- Duplicados y conflictos de estado ---------------------------------
    if not df.empty:
        conflictos = df.groupby(
            ["estudiante_id", "codigo_curso"]
        )["estado"].nunique()
        n_conflictos = int((conflictos > 1).sum())
        if n_conflictos:
            print(
                f"   [AVISO] {n_conflictos:,} par(es) (estudiante, curso) con "
                f"estados distintos; se prioriza 'aprobada'"
            )
        # Una materia solo puede estar en historial o en matriz_por_ver.
        prioridad = {"aprobada": 0, "por_ver": 1}
        df["_prioridad"] = df["estado"].map(prioridad).astype("Int64")
        n_antes = len(df)
        df = (
            df.sort_values(
                ["estudiante_id", "codigo_curso", "_prioridad"], kind="stable"
            )
            .drop_duplicates(subset=["estudiante_id", "codigo_curso"], keep="first")
            .drop(columns="_prioridad")
            .reset_index(drop=True)
        )
        if len(df) < n_antes:
            print(
                f"   [AVISO] {n_antes - len(df):,} par(es) "
                f"(estudiante, curso) duplicado(s) eliminado(s)"
            )

        # Un estudiante debería tener un único carrera/semestre; si hay
        # variantes se conserva el primer valor no nulo de cada campo.
        variantes = df.groupby("estudiante_id")[["carrera", "semestre"]].nunique()
        n_variantes = int((variantes > 1).any(axis=1).sum())
        if n_variantes:
            print(
                f"   [AVISO] {n_variantes:,} estudiante(s) con carrera/semestre "
                f"inconsistentes; se conserva el primer valor no nulo"
            )

    # --- Tablas destino ----------------------------------------------------
    historial = df.loc[
        df["estado"] == "aprobada", ["estudiante_id", "codigo_curso"]
    ].reset_index(drop=True)
    matriz_por_ver = df.loc[
        df["estado"] == "por_ver", ["estudiante_id", "codigo_curso"]
    ].reset_index(drop=True)

    if df.empty:
        perfil = pd.DataFrame(columns=COL_INSERT_PERFIL).astype(
            {"estudiante_id": "Int64", "carrera": "string", "semestre": "Int64"}
        )
        estudiantes = []
    else:
        perfil = (
            df.groupby("estudiante_id", as_index=False)
            .agg(
                carrera=("carrera", _primero_no_nulo),
                semestre=("semestre", _primero_no_nulo),
            )
            .loc[:, COL_INSERT_PERFIL]
            .reset_index(drop=True)
        )
        estudiantes = sorted(
            int(x) for x in df["estudiante_id"].drop_duplicates().tolist()
        )

    print(f"   matriz formato        : {formato}")
    print(f"   estudiantes en CSV    : {len(estudiantes):,}")
    print(f"   aprobadas/vistas      : {len(historial):,}")
    print(f"   por_ver/pendientes    : {len(matriz_por_ver):,}")

    return {
        "historial": historial,
        "matriz_por_ver": matriz_por_ver,
        "perfil": perfil,
        "estudiantes": estudiantes,
        "formato": formato,
        "aprobadas": int(len(historial)),
        "por_ver": int(len(matriz_por_ver)),
    }


# ---------------------------------------------------------------------------
# Lectura de CSV + validación de columnas
# ---------------------------------------------------------------------------
def _leer_csv(ruta, requeridas, etiqueta):
    """Lee un CSV (utf-8/latin-1, con/sin BOM) y valida sus columnas."""
    ruta = Path(ruta)
    if not ruta.is_file():
        raise SystemExit(f"No se encontró el archivo de {etiqueta}: {ruta}")

    try:
        df = pd.read_csv(ruta, dtype=str, encoding="utf-8-sig")
    except UnicodeDecodeError:
        # Excel en Windows suele guardar en latin-1/cp1252.
        print(f"   [AVISO] {ruta.name} no es UTF-8; se relee como latin-1")
        df = pd.read_csv(ruta, dtype=str, encoding="latin-1")
    except pd.errors.EmptyDataError:
        raise SystemExit(f"{etiqueta} ({ruta.name}) está vacío o no tiene encabezados")

    # Encabezados normalizados (sin BOM/acentos, minúsculas, '_' por espacios
    # y guiones) y sin filas totalmente vacías.
    df.columns = [_normalizar_columna(c) for c in df.columns]
    duplicadas = sorted({c for c in df.columns if list(df.columns).count(c) > 1})
    if duplicadas:
        raise SystemExit(
            f"{etiqueta} ({ruta.name}) tiene columnas duplicadas tras "
            f"normalizar los encabezados: {duplicadas}"
        )
    df = df.dropna(how="all").reset_index(drop=True)

    faltantes = [c for c in requeridas if c not in df.columns]
    if faltantes:
        raise SystemExit(
            f"{etiqueta} ({ruta.name}) no tiene las columnas requeridas: "
            f"{faltantes}\nColumnas encontradas: {list(df.columns)}"
        )
    if df.empty:
        raise SystemExit(f"{etiqueta} ({ruta.name}) no contiene filas de datos")
    return df


def _copiar_bronze(ruta, destino):
    """Copia el CSV crudo a bronze preservando los bytes originales."""
    ruta = Path(ruta)
    destino = Path(destino)
    if not ruta.is_file():
        raise SystemExit(f"No se encontró el CSV crudo a copiar: {ruta}")
    destino.parent.mkdir(parents=True, exist_ok=True)
    if ruta.resolve() == destino.resolve():
        return  # ya está en bronze
    shutil.copyfile(ruta, destino)


# ---------------------------------------------------------------------------
# Persistencia en PostgreSQL (psycopg2, import diferido)
# ---------------------------------------------------------------------------
def conectar():
    """Abre una conexión a PostgreSQL según DB_CONFIG."""
    try:
        import psycopg2
    except ImportError as exc:
        raise SystemExit(
            "psycopg2 no está instalado. Ejecuta: pip install psycopg2-binary"
        ) from exc
    return psycopg2.connect(**DB_CONFIG)


def _a_tuplas(df, columnas):
    """Convierte las columnas a una lista de tuplas con tipos Python nativos."""
    filas = []
    for fila in df[columnas].itertuples(index=False, name=None):
        tupla = []
        for valor in fila:
            # Nulos de pandas -> NULL de SQL.
            if valor is None or valor is pd.NA or valor is pd.NaT:
                tupla.append(None)
                continue
            # Escalar numpy (np.int64, np.float64, ...) -> tipo nativo.
            if hasattr(valor, "item") and not isinstance(valor, (str, bytes)):
                valor = valor.item()
            if isinstance(valor, float) and math.isnan(valor):
                tupla.append(None)
            else:
                tupla.append(valor)
        filas.append(tuple(tupla))
    return filas


def cargar_postgres(curso, nrc, horario, historial, solo_oferta=False):
    """
    Inserta en PostgreSQL de forma idempotente:

      * ``curso``     -> UPSERT (ON CONFLICT codigo_alfanumerico DO UPDATE).
      * ``nrc``       -> UPSERT (ON CONFLICT id_nrc DO UPDATE).
      * ``horario``   -> refresco total (DELETE + INSERT); ``horario`` no tiene
                         claves foráneas entrantes, así que borrar es seguro.
      * ``historial`` -> refresco total (DELETE + INSERT); la matriz es la
                         fuente autoritativa del registro académico.

    Con ``solo_oferta=True`` no se ejecuta ningún DDL ni DELETE/INSERT sobre
    ``historial``, ``estudiante_perfil`` ni ``matriz_por_ver``: esos datos
    quedan intactos. En este camino ``historial`` solo se crea/refresca en el
    modo completo; ``--solo-matriz`` usa :func:`cargar_postgres_matriz`.

    Se evita TRUNCATE ... CASCADE para no borrar la tabla ``inscripcion``
    (que referencia a ``nrc``) ni otras tablas con datos operativos.
    Los cursos/NRC que ya no vienen en el CSV **no se eliminan** (podrían tener
    inscripciones); solo ``horario`` e ``historial`` se refrescan por completo.
    """
    # La conexión se abre primero para dar un error claro si falta psycopg2.
    conn = conectar()
    from psycopg2 import extras
    try:
        with conn.cursor() as cur:
            # 1. Historial: DDL solo en modo completo (--solo-oferta no toca
            #    ninguna tabla de la matriz).
            if not solo_oferta:
                for sentencia in DDL_HISTORIAL:
                    cur.execute(sentencia)

            # 2. Curso: upsert.
            filas = _a_tuplas(curso, COL_INSERT_CURSO)
            if filas:
                extras.execute_values(
                    cur,
                    """
                    INSERT INTO curso (codigo_alfanumerico, nombre, creditos)
                    VALUES %s
                    ON CONFLICT (codigo_alfanumerico) DO UPDATE SET
                        nombre   = EXCLUDED.nombre,
                        creditos = EXCLUDED.creditos
                    """,
                    filas,
                    page_size=BATCH_SIZE,
                )

            # 3. NRC: upsert (referencia a Curso ya insertado).
            filas = _a_tuplas(nrc, COL_INSERT_NRC)
            if filas:
                extras.execute_values(
                    cur,
                    """
                    INSERT INTO nrc (id_nrc, curso_id, jornada, modalidad, cupo_max)
                    VALUES %s
                    ON CONFLICT (id_nrc) DO UPDATE SET
                        curso_id  = EXCLUDED.curso_id,
                        jornada   = EXCLUDED.jornada,
                        modalidad = EXCLUDED.modalidad,
                        cupo_max  = EXCLUDED.cupo_max
                    """,
                    filas,
                    page_size=BATCH_SIZE,
                )

            # 4. Horario: refresco total.
            cur.execute("DELETE FROM horario")
            filas = _a_tuplas(horario, COL_INSERT_HORARIO)
            if filas:
                extras.execute_values(
                    cur,
                    """
                    INSERT INTO horario
                        (nrc_id, numero_sesion, dia, hora_inicio, hora_fin)
                    VALUES %s
                    """,
                    filas,
                    page_size=BATCH_SIZE,
                )

            # 5. Historial: refresco total SOLO en la ingesta completa.
            #    En --solo-oferta se conservan los aprobados existentes.
            if solo_oferta:
                print("   historial  : sin cambios (modo --solo-oferta)")
            else:
                cur.execute("DELETE FROM historial")
                filas = _a_tuplas(historial, COL_INSERT_HISTORIAL)
                if filas:
                    extras.execute_values(
                        cur,
                        """
                        INSERT INTO historial (estudiante_id, codigo_curso)
                        VALUES %s
                        """,
                        filas,
                        page_size=BATCH_SIZE,
                    )

            # 6. Conteos de verificación (visibles antes del COMMIT).
            tablas_conteo = ["curso", "nrc", "horario"]
            if not solo_oferta:
                tablas_conteo.append("historial")
            conteos = {}
            for tabla in tablas_conteo:
                cur.execute(f"SELECT COUNT(*) FROM {tabla}")
                conteos[tabla] = cur.fetchone()[0]

        conn.commit()
        print("   COMMIT realizado con éxito.")
        for tabla, n in conteos.items():
            print(f"   {tabla:<10}: {n:>8,} filas en PostgreSQL")
    except Exception as exc:
        conn.rollback()
        print(f"\nERROR durante la carga en PostgreSQL: {exc}")
        raise
    finally:
        conn.close()


def cargar_postgres_matriz(datos):
    """
    Persiste SOLO la matriz del estudiante (modo ``--solo-matriz``).

    En una única transacción:

      1. Crea si faltan ``historial``, ``estudiante_perfil`` y
         ``matriz_por_ver`` (``CREATE TABLE IF NOT EXISTS``).
      2. Hace upsert de cada estudiante del CSV en ``estudiante``: si no
         existe se crea con nombre ``'Estudiante <id>'`` y ``plan_id`` 0; si
         existe se conserva su nombre/plan actuales.
      3. Hace upsert del perfil (carrera/semestre) sin borrar con NULL.
      4. Para cada estudiante del CSV: ``DELETE`` de sus filas en ``historial``
         y ``matriz_por_ver`` + ``INSERT`` de las filas del CSV. Los datos de
         estudiantes que no aparecen en el CSV no se tocan.
      5. Verifica conteos y hace COMMIT (ROLLBACK ante cualquier error).

    No ejecuta ninguna sentencia sobre ``curso``, ``nrc`` ni ``horario``.
    """
    estudiantes = datos["estudiantes"]
    historial = datos["historial"]
    matriz_por_ver = datos["matriz_por_ver"]
    perfil = datos["perfil"]

    conn = conectar()
    from psycopg2 import extras
    try:
        with conn.cursor() as cur:
            # 1. Tablas auxiliares idempotentes.
            for sentencia in DDL_MATRIZ:
                cur.execute(sentencia)

            # 2. Estudiantes: alta mínima si no existen.
            if estudiantes:
                cur.executemany(
                    """
                    INSERT INTO estudiante (id, nombre, plan_id)
                    VALUES (%s, %s, 0)
                    ON CONFLICT (id) DO NOTHING
                    """,
                    [(sid, f"Estudiante {sid}") for sid in estudiantes],
                )

                # 3. Perfil: actualiza solo los valores informados.
                filas_perfil = _a_tuplas(perfil, COL_INSERT_PERFIL)
                if filas_perfil:
                    cur.executemany(
                        """
                        INSERT INTO estudiante_perfil
                            (estudiante_id, carrera, semestre)
                        VALUES (%s, %s, %s)
                        ON CONFLICT (estudiante_id) DO UPDATE SET
                            carrera = COALESCE(EXCLUDED.carrera,
                                               estudiante_perfil.carrera),
                            semestre = COALESCE(EXCLUDED.semestre,
                                                estudiante_perfil.semestre)
                        """,
                        filas_perfil,
                    )

            # 4. Reemplazo SOLO de los estudiantes presentes en el CSV.
            hist_por_estudiante = {
                int(sid): _a_tuplas(grupo, COL_INSERT_HISTORIAL)
                for sid, grupo in historial.groupby("estudiante_id")
            }
            porver_por_estudiante = {
                int(sid): _a_tuplas(grupo, COL_INSERT_HISTORIAL)
                for sid, grupo in matriz_por_ver.groupby("estudiante_id")
            }

            for sid in estudiantes:
                cur.execute(
                    "DELETE FROM historial WHERE estudiante_id = %s", (sid,)
                )
                cur.execute(
                    "DELETE FROM matriz_por_ver WHERE estudiante_id = %s", (sid,)
                )

                filas = hist_por_estudiante.get(sid, [])
                if filas:
                    extras.execute_values(
                        cur,
                        """
                        INSERT INTO historial (estudiante_id, codigo_curso)
                        VALUES %s
                        ON CONFLICT DO NOTHING
                        """,
                        filas,
                        page_size=BATCH_SIZE,
                    )

                filas = porver_por_estudiante.get(sid, [])
                if filas:
                    extras.execute_values(
                        cur,
                        """
                        INSERT INTO matriz_por_ver (estudiante_id, codigo_curso)
                        VALUES %s
                        ON CONFLICT DO NOTHING
                        """,
                        filas,
                        page_size=BATCH_SIZE,
                    )

            # 5. Conteos de verificación de los estudiantes procesados.
            conteos = {}
            if estudiantes:
                for tabla in ("estudiante", "estudiante_perfil",
                              "historial", "matriz_por_ver"):
                    columna = "id" if tabla == "estudiante" else "estudiante_id"
                    cur.execute(
                        f"SELECT COUNT(*) FROM {tabla} "
                        f"WHERE {columna} = ANY(%s)",
                        (estudiantes,),
                    )
                    conteos[tabla] = cur.fetchone()[0]

        conn.commit()
        print("   COMMIT realizado con éxito.")
        for tabla, n in conteos.items():
            print(f"   {tabla:<18}: {n:>8,} fila(s) de los estudiantes del CSV")
    except Exception as exc:
        conn.rollback()
        print(f"\nERROR durante la carga de la matriz en PostgreSQL: {exc}")
        raise
    finally:
        conn.close()


# ---------------------------------------------------------------------------
# MAIN
# ---------------------------------------------------------------------------
def parse_args(argv=None):
    parser = argparse.ArgumentParser(
        description=(
            "Ingesta Data-Driven: CSV -> Bronze/Silver (Parquet) -> PostgreSQL. "
            "Los modos --solo-oferta y --solo-matriz son mutuamente excluyentes."
        ),
        epilog=(
            "Modos de ejecución:\n"
            "  (sin flags)    procesa oferta + matriz (comportamiento histórico).\n"
            "  --solo-oferta  procesa SOLO curso/nrc/horario; no exige, no lee\n"
            "                 ni copia la matriz, y no toca historial,\n"
            "                 estudiante_perfil ni matriz_por_ver.\n"
            "  --solo-matriz  procesa SOLO la matriz; no exige, no lee ni copia\n"
            "                 la oferta, y no toca curso/nrc/horario.\n"
            "\n"
            "Contrato de la matriz:\n"
            "  Enriquecido: estudiante_id,carrera,semestre,estado,codigo_curso,\n"
            "               nombre_curso\n"
            "               estado: aprobada/vista -> historial;\n"
            "                       por_ver/pendiente -> matriz_por_ver.\n"
            "  Antiguo    : estudiante_id,codigo_curso (toda fila = aprobada).\n"
            "  En --solo-matriz se reemplazan SOLO los estudiantes del CSV.\n"
        ),
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument(
        "--oferta",
        default=str(OFERTA_DEFAULT),
        help=(
            "Ruta del CSV de oferta académica (default: "
            "datalake/oferta_academica.csv). Se ignora en --solo-matriz."
        ),
    )
    parser.add_argument(
        "--matriz",
        default=str(MATRIZ_DEFAULT),
        help=(
            "Ruta del CSV de matriz de estudiantes (default: "
            "datalake/matriz_estudiante.csv). Se ignora en --solo-oferta."
        ),
    )
    parser.add_argument(
        "--sin-bd",
        action="store_true",
        help="Solo procesa y escribe Bronze/Silver; no se conecta a PostgreSQL.",
    )
    modos = parser.add_mutually_exclusive_group()
    modos.add_argument(
        "--solo-oferta",
        action="store_true",
        help=(
            "Procesa SOLO la oferta (curso/nrc/horario): no exige ni lee la "
            "matriz y NO toca historial/estudiante_perfil/matriz_por_ver."
        ),
    )
    modos.add_argument(
        "--solo-matriz",
        action="store_true",
        help=(
            "Procesa SOLO la matriz: no exige ni lee la oferta, no toca "
            "curso/nrc/horario y reemplaza solo los estudiantes del CSV."
        ),
    )
    return parser.parse_args(argv)


def main(argv=None):
    args = parse_args(argv)
    inicio = time.perf_counter()

    if args.solo_oferta:
        modo = "solo_oferta"
        etiqueta_modo = "--solo-oferta (solo oferta)"
    elif args.solo_matriz:
        modo = "solo_matriz"
        etiqueta_modo = "--solo-matriz (solo matriz)"
    else:
        modo = "completo"
        etiqueta_modo = "completo (oferta + matriz)"

    print("=" * 72)
    print("Ingesta Data-Driven — Sistema de Inscripción de Materias")
    print("=" * 72)
    print(f"Modo: {etiqueta_modo}")

    # 1. Validación temprana de las entradas. Cada modo exige SOLO su CSV:
    #    así un --solo-oferta nunca intenta leer la matriz (y viceversa).
    entradas = []
    if modo != "solo_matriz":
        entradas.append((args.oferta, "oferta académica"))
    if modo != "solo_oferta":
        entradas.append((args.matriz, "matriz de estudiantes"))
    faltantes = [f"{etiqueta} ({ruta})" for ruta, etiqueta in entradas
                 if not Path(ruta).is_file()]
    if faltantes:
        raise SystemExit(
            f"Modo {etiqueta_modo}: no se encontraron los CSV requeridos:\n  - "
            + "\n  - ".join(faltantes)
            + "\nNo se leyó ni copió ningún archivo. Revise las rutas."
        )

    # Las dependencias solo se exigen cuando ya hay archivos que procesar; así
    # un error de ruta se informa como tal aunque falte pandas/pyarrow.
    if _ERROR_DEPENDENCIAS is not None:
        raise SystemExit(
            "Faltan dependencias de Python para la ingesta: "
            f"{_ERROR_DEPENDENCIAS}. Ejecute: pip install pandas pyarrow"
        )

    # 2. Carpetas del data lake.
    for carpeta in (BRONZE, SILVER):
        carpeta.mkdir(parents=True, exist_ok=True)

    # 3. BRONZE: copia cruda SOLO de los CSV del modo.
    print("\n[1/3] BRONZE: copiando CSV crudos...")
    if modo != "solo_matriz":
        _copiar_bronze(args.oferta, BRONZE / "oferta_academica.csv")
        print(f"   oferta_academica.csv    <- {Path(args.oferta)}")
    else:
        print("   oferta_academica.csv    -> no se copia (modo --solo-matriz)")
    if modo != "solo_oferta":
        _copiar_bronze(args.matriz, BRONZE / "matriz_estudiante.csv")
        print(f"   matriz_estudiante.csv   <- {Path(args.matriz)}")
    else:
        print("   matriz_estudiante.csv   -> no se copia (modo --solo-oferta)")

    # 4. Lectura y limpieza. Cada bloque es independiente: en los modos
    #    "solo" el otro CSV no se abre siquiera.
    print("\n[2/3] SILVER: leyendo y limpiando...")
    curso = nrc = horario = None
    historial = None
    datos_matriz = None

    if modo == "solo_matriz":
        print("   oferta_academica.csv    -> no se lee (modo --solo-matriz)")
    else:
        oferta_raw = _leer_csv(args.oferta, COL_OFERTA, "oferta_academica.csv")
        oferta = limpiar_oferta(oferta_raw)
        curso, nrc, horario = construir_tablas(oferta)

        if curso.empty:
            raise SystemExit(
                "La oferta académica no produjo cursos válidos. Revise que "
                "codigo_curso, nombre_curso y creditos estén completos."
            )
        if nrc.empty:
            print("   [AVISO] La oferta no produjo NRC válidos; no habrá horarios.")

    if modo == "solo_oferta":
        print("   matriz_estudiante.csv   -> no se lee (modo --solo-oferta)")
    elif modo == "solo_matriz":
        matriz_raw = _leer_csv(args.matriz, COL_MATRIZ, "matriz_estudiante.csv")
        datos_matriz = limpiar_matriz_solo(matriz_raw)
        if not datos_matriz["estudiantes"]:
            print(
                "   [AVISO] La matriz no produjo estudiantes válidos; no se "
                "reemplazará ningún dato."
            )
    else:
        matriz_raw = _leer_csv(args.matriz, COL_MATRIZ, "matriz_estudiante.csv")
        historial = limpiar_matriz(matriz_raw)
        if historial.empty:
            print("   [AVISO] La matriz no produjo pares (estudiante, curso) válidos.")

    # 5. Guardado en Parquet. Solo se escriben las capas del modo.
    parquets = []
    if modo != "solo_matriz":
        parquets.extend([
            ("curso.parquet", curso),
            ("nrc.parquet", nrc),
            ("horario.parquet", horario),
        ])
    if modo == "solo_matriz":
        parquets.extend([
            ("historial.parquet", datos_matriz["historial"]),
            ("matriz_por_ver.parquet", datos_matriz["matriz_por_ver"]),
            ("estudiante_perfil.parquet", datos_matriz["perfil"]),
        ])
    elif modo == "completo":
        parquets.append(("historial.parquet", historial))

    for nombre, df in parquets:
        destino = SILVER / nombre
        df.to_parquet(destino, index=False, engine="pyarrow")
        print(f"   {destino.name:<22} OK ({len(df):,} filas)")

    # 6. Resumen legible.
    print("\nResumen de la ingesta:")
    if modo == "solo_matriz":
        print(f"   estudiantes: {len(datos_matriz['estudiantes']):>8,}")
        print(f"   aprobadas  : {datos_matriz['aprobadas']:>8,}")
        print(f"   por_ver    : {datos_matriz['por_ver']:>8,}")
    else:
        print(f"   cursos     : {len(curso):>8,}")
        print(f"   nrcs       : {len(nrc):>8,}")
        print(f"   sesiones   : {len(horario):>8,}")
        if modo == "solo_oferta":
            print("   aprobados  : NO modificados (modo --solo-oferta)")
        else:
            print(f"   aprobados  : {len(historial):>8,}")

    # 7. PostgreSQL.
    if args.sin_bd:
        print("\n[3/3] Modo --sin-bd: no se conectó a PostgreSQL.")
        print(f"   Parquet disponibles en: {SILVER}")
    else:
        print("\n[3/3] POSTGRESQL: cargando tablas...")
        if modo == "solo_matriz":
            cargar_postgres_matriz(datos_matriz)
        else:
            cargar_postgres(
                curso, nrc, horario, historial,
                solo_oferta=(modo == "solo_oferta"),
            )

    # 8. Línea machine-readable para que la API (subprocess) reporte conteos
    #    sin tener que consultar la base de datos después de la ingesta.
    if modo == "solo_matriz":
        resumen = {
            "estudiantes": len(datos_matriz["estudiantes"]),
            "aprobadas": datos_matriz["aprobadas"],
            "por_ver": datos_matriz["por_ver"],
            "formato": datos_matriz["formato"],
            "solo_matriz": True,
            "modo": modo,
        }
    else:
        resumen = {
            "cursos": int(len(curso)),
            "nrcs": int(len(nrc)),
            "sesiones": int(len(horario)),
            "modo": modo,
        }
        if modo == "solo_oferta":
            resumen["solo_oferta"] = True
        else:
            resumen["aprobados"] = int(len(historial))
    print("RESUMEN_JSON: " + json.dumps(resumen, ensure_ascii=False))

    print(f"\nIngesta finalizada en {time.perf_counter() - inicio:.2f}s.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
