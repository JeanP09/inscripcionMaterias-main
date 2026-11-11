#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
init_db.py — Creación/verificación idempotente del esquema PostgreSQL.

Problema que resuelve
---------------------
``schema.sql`` solo se ejecuta la PRIMERA vez que se inicializa el volumen de
PostgreSQL (docker-entrypoint-initdb.d). Si el volumen ya existía de una
ejecución anterior, la base ``universidad`` puede quedar sin tablas y la API
falla con ``relation "estudiante" does not exist``.

Este script se ejecuta al arrancar el contenedor del backend
(``python init_db.py && exec uvicorn ...``) y:

  1. Lee la conexión de DB_HOST/DB_PORT/DB_NAME/DB_USER/DB_PASSWORD.
  2. Reintenta la conexión hasta ``timeout`` segundos (60 s por defecto),
     tolerando que PostgreSQL todavía esté arrancando.
  3. Aplica DDL ``CREATE TABLE IF NOT EXISTS`` para TODAS las tablas del
     sistema, en orden de dependencias, más índices útiles.
  4. Verifica que las tablas existan e imprime un resumen con el número de
     filas de cada una.

Es seguro ejecutarlo múltiples veces: NO borra ni modifica datos existentes
(solo crea lo que falte). También es importable::

    import init_db
    init_db.inicializar_bd()

Uso::

    python init_db.py
    # Exit code 0 si el esquema quedó verificado; 1 si algo falló.
"""

from __future__ import annotations

import logging
import os
import sys
import time
from typing import Dict, Iterable, List, Mapping, Optional, Sequence, Tuple

logger = logging.getLogger("inscripcion.init_db")

# ---------------------------------------------------------------------------
# Configuración de conexión (mismos nombres que backend/main.py y la ingesta)
# ---------------------------------------------------------------------------
DB_CONFIG_POR_DEFECTO: Dict[str, object] = {
    "host": "localhost",
    "port": 5432,
    "dbname": "universidad",
    "user": "postgres",
    "password": "postgres",
}

# Tablas que la API y la ingesta esperan encontrar (nombres en minúsculas:
# PostgreSQL pliega a minúsculas los identificadores sin comillas).
TABLAS_ESPERADAS: Tuple[str, ...] = (
    "estudiante",
    "curso",
    "nrc",
    "horario",
    "inscripcion",
    "historial",
    "estudiante_perfil",
    "matriz_por_ver",
)

# ---------------------------------------------------------------------------
# DDL idempotente.
#
# El orden importa: curso -> nrc -> horario e inscripcion; estudiante antes de
# inscripcion. Las tablas auxiliares de la matriz no tienen claves foráneas
# (igual que las crea la ingesta/API) para tolerar cargas parciales.
# ---------------------------------------------------------------------------
DDL_TABLAS: Tuple[Tuple[str, str], ...] = (
    (
        "estudiante",
        """
        CREATE TABLE IF NOT EXISTS estudiante (
            id      SERIAL PRIMARY KEY,
            nombre  TEXT NOT NULL,
            plan_id INTEGER NOT NULL
        )
        """,
    ),
    (
        "curso",
        """
        CREATE TABLE IF NOT EXISTS curso (
            codigo_alfanumerico VARCHAR(10) PRIMARY KEY,
            nombre              TEXT NOT NULL,
            creditos            INTEGER NOT NULL
        )
        """,
    ),
    (
        "nrc",
        """
        CREATE TABLE IF NOT EXISTS nrc (
            id_nrc    INTEGER PRIMARY KEY,
            curso_id  VARCHAR(10) NOT NULL REFERENCES curso (codigo_alfanumerico),
            jornada   TEXT NOT NULL,
            modalidad TEXT NOT NULL,
            cupo_max  INTEGER NOT NULL
        )
        """,
    ),
    (
        "horario",
        """
        CREATE TABLE IF NOT EXISTS horario (
            id            SERIAL PRIMARY KEY,
            nrc_id        INTEGER NOT NULL REFERENCES nrc (id_nrc),
            numero_sesion INTEGER NOT NULL,
            dia           INTEGER NOT NULL,
            hora_inicio   TIME NOT NULL,
            hora_fin      TIME NOT NULL
        )
        """,
    ),
    (
        "inscripcion",
        """
        CREATE TABLE IF NOT EXISTS inscripcion (
            estudiante_id INTEGER NOT NULL REFERENCES estudiante (id),
            nrc_id        INTEGER NOT NULL REFERENCES nrc (id_nrc),
            fecha         DATE NOT NULL,
            PRIMARY KEY (estudiante_id, nrc_id)
        )
        """,
    ),
    (
        "historial",
        """
        CREATE TABLE IF NOT EXISTS historial (
            estudiante_id INTEGER NOT NULL,
            codigo_curso  VARCHAR(10) NOT NULL,
            PRIMARY KEY (estudiante_id, codigo_curso)
        )
        """,
    ),
    (
        "estudiante_perfil",
        """
        CREATE TABLE IF NOT EXISTS estudiante_perfil (
            estudiante_id INTEGER PRIMARY KEY,
            carrera       TEXT,
            semestre      INTEGER
        )
        """,
    ),
    (
        "matriz_por_ver",
        """
        CREATE TABLE IF NOT EXISTS matriz_por_ver (
            estudiante_id INTEGER NOT NULL,
            codigo_curso  VARCHAR(10) NOT NULL,
            PRIMARY KEY (estudiante_id, codigo_curso)
        )
        """,
    ),
)

# Índices recomendados por el diseño (consulta de cupos, horarios, matriz).
DDL_INDICES: Tuple[Tuple[str, str], ...] = (
    (
        "idx_horario_nrc_id",
        "CREATE INDEX IF NOT EXISTS idx_horario_nrc_id ON horario (nrc_id)",
    ),
    (
        "idx_inscripcion_estudiante_id",
        "CREATE INDEX IF NOT EXISTS idx_inscripcion_estudiante_id "
        "ON inscripcion (estudiante_id)",
    ),
    # El conteo de cupo por NRC (GET /api/oferta y horario automático) también
    # se beneficia de este índice.
    (
        "idx_inscripcion_nrc_id",
        "CREATE INDEX IF NOT EXISTS idx_inscripcion_nrc_id "
        "ON inscripcion (nrc_id)",
    ),
    (
        # Mismo nombre que usa datalake/ingesta_datalake.py para evitar crear
        # un índice duplicado sobre la misma columna.
        "idx_historial_curso",
        "CREATE INDEX IF NOT EXISTS idx_historial_curso "
        "ON historial (codigo_curso)",
    ),
    (
        "idx_historial_estudiante_id",
        "CREATE INDEX IF NOT EXISTS idx_historial_estudiante_id "
        "ON historial (estudiante_id)",
    ),
    (
        "idx_matriz_por_ver_estudiante_id",
        "CREATE INDEX IF NOT EXISTS idx_matriz_por_ver_estudiante_id "
        "ON matriz_por_ver (estudiante_id)",
    ),
    # Búsqueda de NRC por curso (catálogo / generador automático).
    (
        "idx_nrc_curso_id",
        "CREATE INDEX IF NOT EXISTS idx_nrc_curso_id ON nrc (curso_id)",
    ),
)


# ---------------------------------------------------------------------------
# Utilidades de configuración
# ---------------------------------------------------------------------------
def _entero_desde_entorno(nombre: str, por_defecto: int) -> int:
    """Lee una variable de entorno entera y avisa (sin fallar) si no es válida."""
    crudo = os.getenv(nombre)
    if crudo is None or str(crudo).strip() == "":
        return por_defecto
    try:
        return int(str(crudo).strip())
    except (TypeError, ValueError):
        logger.warning(
            "%s=%r no es un entero válido; se usa %s.", nombre, crudo, por_defecto
        )
        return por_defecto


def config_bd() -> Dict[str, object]:
    """
    Devuelve la configuración de conexión leída del entorno.

    Variables: DB_HOST, DB_PORT, DB_NAME, DB_USER, DB_PASSWORD
    (defaults: localhost, 5432, universidad, postgres, postgres).
    """
    return {
        "host": os.getenv("DB_HOST", str(DB_CONFIG_POR_DEFECTO["host"])),
        "port": _entero_desde_entorno("DB_PORT", int(DB_CONFIG_POR_DEFECTO["port"])),
        "dbname": os.getenv("DB_NAME", str(DB_CONFIG_POR_DEFECTO["dbname"])),
        "user": os.getenv("DB_USER", str(DB_CONFIG_POR_DEFECTO["user"])),
        "password": os.getenv(
            "DB_PASSWORD", str(DB_CONFIG_POR_DEFECTO["password"])
        ),
    }


def _destino(config: Mapping[str, object]) -> str:
    """Representación segura (sin contraseña) para logs y resumen."""
    return (
        f"{config.get('user', '?')}@{config.get('host', '?')}:"
        f"{config.get('port', '?')}/{config.get('dbname', '?')}"
    )


# ---------------------------------------------------------------------------
# Conexión con reintentos
# ---------------------------------------------------------------------------
def conectar_bd(
    config: Optional[Mapping[str, object]] = None,
    timeout_conexion: int = 5,
):
    """
    Abre una conexión psycopg2 con ``config`` (o la configuración del entorno).

    ``timeout_conexion`` es el timeout de red de libpq por intento, para que un
    host inalcanzable no cuelgue el proceso. Lanza RuntimeError si psycopg2 no
    está instalado y psycopg2.Error si la conexión falla.
    """
    try:
        import psycopg2
    except ImportError as exc:  # pragma: no cover - dependencia del contenedor
        raise RuntimeError(
            "psycopg2 no está instalado. Ejecuta: pip install psycopg2-binary"
        ) from exc

    parametros = dict(config or config_bd())
    # No pisar un valor explícito si el llamador ya lo indicó.
    parametros.setdefault("connect_timeout", max(1, int(timeout_conexion)))
    return psycopg2.connect(**parametros)


def esperar_conexion(
    config: Optional[Mapping[str, object]] = None,
    timeout: float = 60.0,
    intervalo: float = 1.0,
    timeout_conexion: int = 5,
):
    """
    Reintenta la conexión a PostgreSQL hasta ``timeout`` segundos.

    Espera ``intervalo`` segundos entre intentos (1 s por defecto). Si se agota
    el tiempo, lanza RuntimeError conservando el último error como causa.
    """
    parametros = dict(config or config_bd())
    intervalo = max(0.0, float(intervalo))
    limite = time.monotonic() + max(0.0, float(timeout))
    intento = 0

    while True:
        intento += 1
        try:
            conexion = conectar_bd(parametros, timeout_conexion=timeout_conexion)
            logger.info(
                "Conexión a PostgreSQL establecida (%s, intento %s).",
                _destino(parametros),
                intento,
            )
            return conexion
        except RuntimeError:
            # psycopg2 no instalado: reintentar no sirve.
            raise
        except Exception as exc:  # noqa: BLE001 - se reintenta cualquier error de red
            restante = limite - time.monotonic()
            if restante <= 0:
                raise RuntimeError(
                    f"No se pudo conectar a PostgreSQL ({_destino(parametros)}) "
                    f"tras {intento} intento(s) en {timeout:g}s: {exc}"
                ) from exc
            espera = min(intervalo, restante)
            logger.warning(
                "PostgreSQL aún no responde (%s), intento %s: %s. "
                "Reintentando en %.0fs...",
                _destino(parametros),
                intento,
                exc,
                espera,
            )
            if espera > 0:
                time.sleep(espera)


# ---------------------------------------------------------------------------
# DDL y verificación
# ---------------------------------------------------------------------------
def aplicar_esquema(conexion) -> None:
    """
    Ejecuta todo el DDL idempotente en una sola transacción.

    No elimina ni altera datos existentes: ``CREATE ... IF NOT EXISTS`` solo
    crea objetos que falten.
    """
    with conexion:  # COMMIT al salir; ROLLBACK si algo falla.
        with conexion.cursor() as cursor:
            for nombre, sentencia in DDL_TABLAS:
                logger.debug("Creando/verificando tabla %s", nombre)
                cursor.execute(sentencia)
            for nombre, sentencia in DDL_INDICES:
                logger.debug("Creando/verificando índice %s", nombre)
                cursor.execute(sentencia)


def verificar_esquema(conexion) -> List[Tuple[str, bool, Optional[int]]]:
    """
    Comprueba que cada tabla esperada exista y cuenta sus filas.

    Devuelve ``[(tabla, existe, filas), ...]``. ``filas`` es ``None`` si la
    tabla no existe o si el conteo no se pudo obtener (no se considera fatal).
    """
    resultado: List[Tuple[str, bool, Optional[int]]] = []
    with conexion.cursor() as cursor:
        for tabla in TABLAS_ESPERADAS:
            cursor.execute("SELECT to_regclass(%s) AS tabla", (f"public.{tabla}",))
            fila = cursor.fetchone()
            existe = bool(fila and fila[0] is not None)
            total: Optional[int] = None
            if existe:
                try:
                    # ``tabla`` proviene de una constante interna, no del usuario.
                    cursor.execute(f"SELECT COUNT(*) FROM {tabla}")
                    fila_total = cursor.fetchone()
                    if fila_total is not None:
                        total = int(fila_total[0])
                except Exception as exc:  # noqa: BLE001 - el conteo es informativo
                    logger.warning("No se pudo contar filas de %s: %s", tabla, exc)
                    # Un error aborta la transacción actual; se limpia para
                    # poder seguir verificando las tablas restantes.
                    try:
                        conexion.rollback()
                    except Exception:  # noqa: BLE001
                        pass
            resultado.append((tabla, existe, total))
    return resultado


def imprimir_resumen(
    resumen: Iterable[Tuple[str, bool, Optional[int]]], destino: str
) -> None:
    """Imprime el resumen legible usado por ``python init_db.py``."""
    filas = list(resumen)
    existentes = sum(1 for _, existe, _ in filas if existe)
    print(f"[init_db] PostgreSQL: {destino}")
    print(f"[init_db] Tablas verificadas: {existentes}/{len(filas)}")
    for tabla, existe, total in filas:
        if not existe:
            print(f"[init_db]   {tabla:<20} FALTA")
        elif total is None:
            print(f"[init_db]   {tabla:<20} OK")
        else:
            print(f"[init_db]   {tabla:<20} OK ({total:,} filas)")
    print(
        f"[init_db] Índices declarados: {len(DDL_INDICES)} "
        f"(CREATE INDEX IF NOT EXISTS)"
    )


# ---------------------------------------------------------------------------
# API pública
# ---------------------------------------------------------------------------
def inicializar_bd(
    config: Optional[Mapping[str, object]] = None,
    timeout: float = 60.0,
    intervalo: float = 1.0,
    verbose: bool = True,
) -> bool:
    """
    Crea (si faltan) y verifica todas las tablas del sistema.

    Parámetros
    ----------
    config : dict opcional
        Configuración psycopg2. Por defecto se lee del entorno con ``config_bd()``.
    timeout : float
        Segundos máximos para conseguir la primera conexión (60 por defecto).
    intervalo : float
        Segundos entre reintentos de conexión (1 por defecto).
    verbose : bool
        Imprime el resumen de tablas (útil al ejecutar como script).

    Devuelve ``True`` si el esquema quedó verificado y ``False`` ante cualquier
    fallo (conexión, permisos, DDL). Nunca propaga la excepción: el arranque de
    la API no debe caerse por esto, y quien llama decide qué hacer.
    """
    parametros = dict(config or config_bd())
    destino = _destino(parametros)
    conexion = None

    try:
        conexion = esperar_conexion(
            parametros, timeout=timeout, intervalo=intervalo
        )
        aplicar_esquema(conexion)
        resumen = verificar_esquema(conexion)
    except Exception as exc:  # noqa: BLE001 - se informa y se devuelve False
        logger.error(
            "No se pudo inicializar el esquema PostgreSQL en %s: %s",
            destino,
            exc,
        )
        if verbose:
            print(f"[init_db] ERROR: no se pudo inicializar el esquema: {exc}")
        return False
    finally:
        if conexion is not None:
            try:
                conexion.close()
            except Exception:  # noqa: BLE001 - cerrar nunca debe enmascarar el error
                pass

    faltantes = [tabla for tabla, existe, _ in resumen if not existe]
    if verbose:
        imprimir_resumen(resumen, destino)

    if faltantes:
        logger.error(
            "Faltan tablas tras aplicar el DDL: %s", ", ".join(faltantes)
        )
        if verbose:
            print(
                "[init_db] ERROR: faltan tablas tras el DDL: "
                + ", ".join(faltantes)
            )
        return False

    logger.info(
        "Esquema PostgreSQL listo en %s (%s tablas verificadas).",
        destino,
        len(resumen),
    )
    if verbose:
        print("[init_db] Esquema listo. No se modificaron datos existentes.")
    return True


def main(argv: Optional[Sequence[str]] = None) -> int:
    """Entry point de ``python init_db.py``. Devuelve 0 si OK, 1 si falla."""
    logging.basicConfig(
        level=getattr(logging, os.getenv("LOG_LEVEL", "INFO").upper(), logging.INFO),
        format="[%(levelname)s] %(name)s: %(message)s",
    )
    # ``argv`` se acepta por comodidad de pruebas; el script no usa argumentos.
    _ = argv
    return 0 if inicializar_bd() else 1


if __name__ == "__main__":
    sys.exit(main())
