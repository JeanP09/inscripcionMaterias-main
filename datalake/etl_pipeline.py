# -*- coding: utf-8 -*-
"""
etl_pipeline.py
===============
Pipeline ETL (arquitectura Medallón: Bronze → Silver → Gold) para el
Sistema de Inscripción de Materias.

Flujo
-----
  1. EXTRACT : conecta a PostgreSQL y lee las 5 tablas del esquema
               (estudiante, curso, nrc, horario, inscripcion) con pandas.
  2. BRONZE  : guarda los datos crudos tal cual en JSON (datalake/bronze/).
  3. SILVER  : limpia/normaliza los datos y los guarda en Parquet
               (datalake/silver/) usando pyarrow.
  4. GOLD    : genera la tabla agregada `demanda_cursos.parquet`
               (datalake/gold/) con el número de inscripciones por NRC.

Requisitos
----------
  pip install pandas pyarrow psycopg2-binary

Configuración (variables de entorno, con valores por defecto)
-------------------------------------------------------------
  DB_HOST (localhost) · DB_PORT (5432) · DB_NAME (universidad)
  DB_USER (postgres)  · DB_PASSWORD (postgres)

Uso
---
  python etl_pipeline.py
"""

import os
import time
from pathlib import Path

import pandas as pd
import pyarrow as pa  # engine por defecto de pandas.to_parquet (referencia)

# ---------------------------------------------------------------------------
# Rutas de las capas del data lake
# ---------------------------------------------------------------------------
RAIZ = Path(__file__).resolve().parent
BRONZE = RAIZ / "bronze"
SILVER = RAIZ / "silver"
GOLD = RAIZ / "gold"

# Tablas del esquema (PostgreSQL pliega los nombres sin comillas a minúsculas).
TABLAS = ["curso", "estudiante", "nrc", "horario", "inscripcion"]

DB_CONFIG = {
    "host": os.getenv("DB_HOST", "localhost"),
    "port": int(os.getenv("DB_PORT", "5432")),
    "dbname": os.getenv("DB_NAME", "universidad"),
    "user": os.getenv("DB_USER", "postgres"),
    "password": os.getenv("DB_PASSWORD", "postgres"),
}


# ---------------------------------------------------------------------------
# 1. EXTRACT
# ---------------------------------------------------------------------------
def conectar():
    """Abre una conexión a PostgreSQL (import diferido de psycopg2)."""
    try:
        import psycopg2
    except ImportError as exc:
        raise SystemExit(
            "psycopg2 no está instalado. Ejecuta: pip install psycopg2-binary"
        ) from exc
    return psycopg2.connect(**DB_CONFIG)


def extraer(conn, tabla):
    """Lee una tabla completa desde PostgreSQL y la devuelve como DataFrame."""
    return pd.read_sql(f"SELECT * FROM {tabla}", conn)


# ---------------------------------------------------------------------------
# 2. SILVER — funciones de limpieza por tabla
# ---------------------------------------------------------------------------
def _a_entero(serie):
    """Convierte a entero nullable (Int64), tolerando nulos."""
    return pd.to_numeric(serie, errors="coerce").astype("Int64")


def limpiar_estudiante(df):
    df = df.copy()
    df["nombre"] = df["nombre"].astype(str).str.strip()
    df["plan_id"] = _a_entero(df["plan_id"])
    return df.dropna(subset=["id", "nombre"])


def limpiar_curso(df):
    df = df.copy()
    df["codigo_alfanumerico"] = df["codigo_alfanumerico"].astype(str).str.strip()
    df["nombre"] = df["nombre"].astype(str).str.strip()
    df["creditos"] = _a_entero(df["creditos"])
    return df


def limpiar_nrc(df):
    df = df.copy()
    df["id_nrc"] = _a_entero(df["id_nrc"])
    df["curso_id"] = df["curso_id"].astype(str).str.strip()
    df["jornada"] = df["jornada"].astype(str).str.strip()
    df["modalidad"] = df["modalidad"].astype(str).str.strip()
    df["cupo_max"] = _a_entero(df["cupo_max"])
    return df


def limpiar_horario(df):
    df = df.copy()
    df["nrc_id"] = _a_entero(df["nrc_id"])
    df["numero_sesion"] = _a_entero(df["numero_sesion"])
    df["dia"] = _a_entero(df["dia"])
    # psycopg2 devuelve objetos time; se normalizan a "HH:MM:SS" para Parquet.
    df["hora_inicio"] = df["hora_inicio"].astype(str)
    df["hora_fin"] = df["hora_fin"].astype(str)
    return df


def limpiar_inscripcion(df):
    df = df.copy()
    df["estudiante_id"] = _a_entero(df["estudiante_id"])
    df["nrc_id"] = _a_entero(df["nrc_id"])
    df["fecha"] = pd.to_datetime(df["fecha"], errors="coerce")
    return df


LIMPIADORES = {
    "curso": limpiar_curso,
    "estudiante": limpiar_estudiante,
    "nrc": limpiar_nrc,
    "horario": limpiar_horario,
    "inscripcion": limpiar_inscripcion,
}


# ---------------------------------------------------------------------------
# 3. GOLD — agregación de demanda por NRC
# ---------------------------------------------------------------------------
def construir_demanda(inscripcion, nrc, curso):
    """
    Cuenta las inscripciones por NRC y enriquece el resultado con los datos
    del curso (código, nombre, créditos) y del NRC (jornada, modalidad, cupo).
    """
    demanda = (
        inscripcion.groupby("nrc_id")
        .size()
        .reset_index(name="total_inscripciones")
    )
    demanda = demanda.merge(
        nrc[["id_nrc", "curso_id", "jornada", "modalidad", "cupo_max"]],
        left_on="nrc_id",
        right_on="id_nrc",
        how="left",
    )
    demanda = demanda.merge(
        curso[["codigo_alfanumerico", "nombre", "creditos"]],
        left_on="curso_id",
        right_on="codigo_alfanumerico",
        how="left",
    )
    demanda = demanda.drop(columns=["id_nrc", "codigo_alfanumerico"])
    demanda = demanda.sort_values("total_inscripciones", ascending=False)
    return demanda.reset_index(drop=True)


# ---------------------------------------------------------------------------
# MAIN
# ---------------------------------------------------------------------------
def main():
    inicio = time.perf_counter()
    for carpeta in (BRONZE, SILVER, GOLD):
        carpeta.mkdir(parents=True, exist_ok=True)

    # --- EXTRACT -----------------------------------------------------------
    print("=" * 70)
    print("ETL — Sistema de Inscripción de Materias (Medallón)")
    print("=" * 70)
    print("\n[1/3] EXTRACT: leyendo tablas desde PostgreSQL...")
    conn = conectar()
    try:
        crudos = {tabla: extraer(conn, tabla) for tabla in TABLAS}
    finally:
        conn.close()
    for tabla, df in crudos.items():
        print(f"   {tabla:<12}: {len(df):>8,} filas")

    # --- BRONZE ------------------------------------------------------------
    print("\n[2/3] BRONZE: guardando datos crudos (JSON)...")
    for tabla, df in crudos.items():
        destino = BRONZE / f"{tabla}.json"
        df.to_json(
            destino, orient="records", date_format="iso",
            indent=2, force_ascii=False,
        )
        print(f"   {destino.name:<25} OK")

    # --- SILVER ------------------------------------------------------------
    print("\n[3/3] SILVER: limpiando y guardando (Parquet)...")
    for tabla, df in crudos.items():
        limpio = LIMPIADORES[tabla](df)
        destino = SILVER / f"{tabla}.parquet"
        limpio.to_parquet(destino, index=False, engine="pyarrow")
        print(f"   {destino.name:<25} OK ({len(limpio):,} filas)")

    # --- GOLD --------------------------------------------------------------
    print("\n[4/4] GOLD: agregando demanda por NRC...")
    # Gold lee de Silver para respetar la separación de capas del medallón.
    inscripcion_s = pd.read_parquet(SILVER / "inscripcion.parquet")
    nrc_s = pd.read_parquet(SILVER / "nrc.parquet")
    curso_s = pd.read_parquet(SILVER / "curso.parquet")

    demanda = construir_demanda(inscripcion_s, nrc_s, curso_s)
    destino = GOLD / "demanda_cursos.parquet"
    demanda.to_parquet(destino, index=False, engine="pyarrow")
    print(f"   {destino.name:<25} OK ({len(demanda):,} NRCs)")

    print("\nTop 5 NRCs por demanda:")
    print(demanda.head(5).to_string(index=False))

    print(f"\nETL finalizado en {time.perf_counter() - inicio:.2f}s.")


if __name__ == "__main__":
    main()
