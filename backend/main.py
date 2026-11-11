# -*- coding: utf-8 -*-
"""
main.py — API REST del Sistema de Inscripción de Materias (FastAPI).

Endpoints
---------
  GET  /api/oferta                       -> NRCs disponibles (con cupo e inscritos)
  POST /api/inscribir                    -> inscribe un estudiante en un NRC
  GET  /api/horario/{estudiante_id}      -> horario consolidado del estudiante
  POST /api/horario/automatico           -> genera y guarda un horario automático
  GET  /api/admin/reglas                 -> lista reglas (prerrequisitos y metadatos)
  PUT  /api/admin/reglas                 -> crea/actualiza reglas de negocio dinámicas
  POST /api/admin/upload-oferta          -> sube CSV de oferta e ingesta a PostgreSQL
  POST /api/estudiante/upload-matriz     -> sube CSV de matriz (aprobadas/pendientes)
  GET  /api/estudiante/{id}/matriz       -> resumen de la matriz del estudiante

  * POST /api/admin/upload-oferta usa el modo ``--solo-oferta`` del Data Lake:
    refresca curso/nrc/horario y NO toca ``historial`` (aprobados intactos).
  * POST /api/estudiante/upload-matriz crea si faltan las tablas
    ``estudiante_perfil``, ``matriz_por_ver`` e ``historial``.

Persistencia
------------
  * PostgreSQL vía psycopg2 (tablas: estudiante, curso, nrc, horario,
    inscripcion, historial, estudiante_perfil y matriz_por_ver). Las tablas se
    crearon sin comillas en ``schema.sql``, por lo que PostgreSQL las guarda en
    minúsculas; aquí se referencian en minúsculas.

    La tabla ``historial`` (materias aprobadas por estudiante) la crea el
    módulo de Data Lake (``datalake/ingesta_datalake.py``) con este esquema:

        historial(
            estudiante_id INTEGER NOT NULL,
            codigo_curso  VARCHAR(10) NOT NULL,
            PRIMARY KEY (estudiante_id, codigo_curso)
        )

    La matriz de pendientes vive en::

        matriz_por_ver(
            estudiante_id INTEGER NOT NULL,
            codigo_curso  VARCHAR(10) NOT NULL,
            PRIMARY KEY (estudiante_id, codigo_curso)
        )

  * MongoDB vía pymongo (colecciones ``catalogo_materias`` y ``reglas_negocio``).
    Se configura por variables de entorno MONGO_URI y MONGO_DB.

Ejecución
---------
  uvicorn main:app --host 0.0.0.0 --port 8000
"""

import io
import json
import logging
import os
import re
import subprocess
import sys
import time
import unicodedata
import uuid
from datetime import datetime
from pathlib import Path
from typing import List, Optional, Union

from fastapi import FastAPI, File, Form, HTTPException, UploadFile
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel, Field

# ---------------------------------------------------------------------------
# Rutas base del contenedor / repo
# ---------------------------------------------------------------------------
# En Docker: /app/main.py -> BASE_DIR="/app".
# En local:  backend/main.py -> BASE_DIR=".../backend".
BASE_DIR = Path(__file__).resolve().parent

logger = logging.getLogger("inscripcion.api")

# ---------------------------------------------------------------------------
# Configuración de conexión — PostgreSQL
# ---------------------------------------------------------------------------
DB_CONFIG = {
    "host": os.getenv("DB_HOST", "localhost"),
    "port": int(os.getenv("DB_PORT", "5432")),
    "dbname": os.getenv("DB_NAME", "universidad"),
    "user": os.getenv("DB_USER", "postgres"),
    "password": os.getenv("DB_PASSWORD", "postgres"),
}

# ---------------------------------------------------------------------------
# Configuración de conexión — MongoDB
# ---------------------------------------------------------------------------
MONGO_URI = os.getenv("MONGO_URI", "mongodb://localhost:27017")
MONGO_DB = os.getenv("MONGO_DB", "inscripcion")
# Nombres de colección (ajustables por si el seed usa otros nombres).
MONGO_COL_CATALOGO = os.getenv("MONGO_COL_CATALOGO", "catalogo_materias")
MONGO_COL_REGLAS = os.getenv("MONGO_COL_REGLAS", "reglas_negocio")

# Import diferido de pymongo: si no está instalado, la API sigue funcionando
# para los endpoints que solo usan PostgreSQL y los endpoints Mongo devuelven
# un 503 claro.
try:
    import pymongo
    from pymongo.errors import PyMongoError
    MONGO_DISPONIBLE = True
except ImportError:  # pragma: no cover - entorno sin pymongo
    pymongo = None
    PyMongoError = Exception
    MONGO_DISPONIBLE = False

app = FastAPI(
    title="Sistema de Inscripción de Materias — API",
    version="1.2.0",
)

# ---------------------------------------------------------------------------
# CORS — orígenes permitidos explícitamente.
#
# Se cubren los tres escenarios del proyecto:
#   * React/Vite en desarrollo:  http://localhost:3000 y http://localhost:5173
#   * Frontend dockerizado (Nginx sirviendo la SPA en el puerto 80):
#     http://localhost, http://localhost:80, http://127.0.0.1 y
#     http://127.0.0.1:80
#   * Variantes equivalentes con 127.0.0.1 en los puertos de desarrollo.
#
# Se usa una lista explícita (en lugar de "*") para poder habilitar
# credenciales si algún día se necesitan cookies de sesión. Se pueden añadir
# orígenes extra por entorno con CORS_ORIGINS="https://a,https://b".
# ---------------------------------------------------------------------------
CORS_ORIGINS = [
    "http://localhost:3000",
    "http://localhost:5173",
    "http://localhost",
    "http://localhost:80",
    "http://127.0.0.1:3000",
    "http://127.0.0.1:5173",
    "http://127.0.0.1",
    "http://127.0.0.1:80",
]

# Orígenes adicionales opcionales (separados por coma), sin duplicados.
for _origen in os.getenv("CORS_ORIGINS", "").split(","):
    _origen = _origen.strip().rstrip("/")
    if _origen and _origen not in CORS_ORIGINS:
        CORS_ORIGINS.append(_origen)

app.add_middleware(
    CORSMiddleware,
    allow_origins=CORS_ORIGINS,
    allow_credentials=True,
    allow_methods=["GET", "POST", "PUT", "PATCH", "DELETE", "OPTIONS", "HEAD"],
    allow_headers=[
        "Content-Type",
        "Authorization",
        "Accept",
        "Origin",
        "X-Requested-With",
    ],
    expose_headers=["Content-Length"],
    max_age=600,
)

# Nombres legibles de los días (la columna horario.dia es 1..7).
DIA_NOMBRES = {
    1: "Lunes",
    2: "Martes",
    3: "Miércoles",
    4: "Jueves",
    5: "Viernes",
    6: "Sábado",
    7: "Domingo",
}


# ---------------------------------------------------------------------------
# Arranque: sembrado de reglas de negocio (MongoDB)
# ---------------------------------------------------------------------------
def _sembrar_reglas_con_reintentos(intentos=3):
    """
    Intenta importar ``seed_reglas`` y ejecutar ``sembrar_reglas()``.

    Reintenta hasta ``intentos`` veces con una espera corta (1s, 2s) porque
    MongoDB puede tardar unos segundos en aceptar conexiones cuando se levanta
    la pila con docker compose. Si el módulo no existe, Mongo no está listo o
    el seed falla, se registra una advertencia y la API arranca igualmente:
    el sembrado de reglas es opcional y nunca debe tumbar el servicio.
    """
    for ruta in (str(BASE_DIR), str(BASE_DIR.parent)):
        if ruta not in sys.path:
            sys.path.append(ruta)

    for intento in range(1, intentos + 1):
        try:
            import importlib

            modulo = importlib.import_module("seed_reglas")
            sembrar = getattr(modulo, "sembrar_reglas", None)
            if not callable(sembrar):
                raise RuntimeError(
                    "seed_reglas no expone una función sembrar_reglas()"
                )
            sembrar()
            logger.info("Reglas de negocio sembradas correctamente (intento %s).", intento)
            return True
        except ModuleNotFoundError as exc:
            # Si falta el propio seed_reglas o una dependencia suya (p. ej.
            # pymongo), reintentar no aporta nada: se avisa y se continúa.
            logger.warning(
                "No se pudo sembrar reglas de negocio (%s). "
                "La API arranca sin seed.",
                exc,
            )
            return False
        except Exception as exc:  # noqa: BLE001 - el arranque nunca debe fallar
            if intento < intentos:
                espera = min(intento, 2)
                logger.warning(
                    "Intento %s/%s de sembrar reglas falló: %s. "
                    "Reintentando en %ss...",
                    intento,
                    intentos,
                    exc,
                    espera,
                )
                time.sleep(espera)
            else:
                logger.warning(
                    "No se pudieron sembrar reglas de negocio tras %s intentos: %s. "
                    "La API arranca sin seed.",
                    intentos,
                    exc,
                )
    return False


def _inicializar_bd_con_reintentos(
    intentos=2, timeout_por_intento=15.0, espera=2.0
):
    """
    Crea/verifica el esquema PostgreSQL al arrancar la API.

    El entrypoint del contenedor ya ejecuta ``python init_db.py`` antes de
    uvicorn (ver backend/Dockerfile y docker-compose.yml); esta llamada es una
    red de seguridad para arranques locales con ``uvicorn main:app`` o si el
    entrypoint no se usó. Es acotada: como máximo ``intentos`` llamadas de
    ``timeout_por_intento`` segundos, así que no bloquea indefinidamente.
    Si PostgreSQL no responde, se registra una advertencia y la API arranca
    igualmente (los endpoints de BD devolverán 503 con un mensaje claro).
    """
    for ruta in (str(BASE_DIR), str(BASE_DIR.parent)):
        if ruta not in sys.path:
            sys.path.append(ruta)

    for intento in range(1, intentos + 1):
        try:
            import importlib

            modulo = importlib.import_module("init_db")
            inicializar = getattr(modulo, "inicializar_bd", None)
            if not callable(inicializar):
                raise RuntimeError(
                    "init_db no expone una función inicializar_bd()"
                )
            if inicializar(timeout=timeout_por_intento):
                logger.info(
                    "Esquema PostgreSQL creado/verificado (intento %s).", intento
                )
                return True
            raise RuntimeError(
                "inicializar_bd() no pudo crear/verificar el esquema"
            )
        except ModuleNotFoundError as exc:
            # Si falta init_db.py o una dependencia (p. ej. psycopg2),
            # reintentar no aporta nada: se avisa y se continúa.
            logger.warning(
                "No se pudo inicializar el esquema PostgreSQL (%s). "
                "La API arranca igualmente; ejecuta 'python init_db.py'.",
                exc,
            )
            return False
        except Exception as exc:  # noqa: BLE001 - el arranque nunca debe fallar
            if intento < intentos:
                logger.warning(
                    "Intento %s/%s de inicializar el esquema PostgreSQL falló: "
                    "%s. Reintentando en %ss...",
                    intento,
                    intentos,
                    exc,
                    espera,
                )
                time.sleep(espera)
            else:
                logger.warning(
                    "No se pudo inicializar el esquema PostgreSQL tras %s "
                    "intentos: %s. La API arranca igualmente; ejecuta "
                    "'python init_db.py'.",
                    intentos,
                    exc,
                )
    return False


@app.on_event("startup")
def _startup_sembrar_reglas():
    """Hook de arranque tolerante a fallos (PostgreSQL/Mongo pueden no estar listos)."""
    # Primero el esquema (tablas base + auxiliares de la matriz), luego el seed.
    _inicializar_bd_con_reintentos()
    _sembrar_reglas_con_reintentos()


# ---------------------------------------------------------------------------
# Utilidades de base de datos — PostgreSQL
# ---------------------------------------------------------------------------
def conectar():
    """
    Abre una conexión con cursor tipo dict (RealDictCursor).

    Si PostgreSQL no está levantado (o las credenciales son incorrectas) se
    devuelve un 503 con un mensaje claro en lugar de un 500 genérico.
    """
    try:
        import psycopg2
        from psycopg2.extras import RealDictCursor
    except ImportError as exc:  # pragma: no cover - entorno sin psycopg2
        raise HTTPException(
            status_code=503,
            detail="psycopg2 no está instalado. Ejecuta: pip install psycopg2-binary",
        ) from exc

    try:
        return psycopg2.connect(**DB_CONFIG, cursor_factory=RealDictCursor)
    except psycopg2.Error as exc:
        raise HTTPException(
            status_code=503,
            detail=f"No se pudo conectar a PostgreSQL: {exc}",
        ) from exc


def horarios_por_nrc():
    """Devuelve {nrc_id: [sesiones...]} con las sesiones de cada NRC."""
    conn = conectar()
    try:
        with conn.cursor() as cur:
            cur.execute(
                """
                SELECT nrc_id, numero_sesion, dia,
                       hora_inicio::text AS hora_inicio,
                       hora_fin::text    AS hora_fin
                FROM horario
                ORDER BY nrc_id, dia, hora_inicio
                """
            )
            filas = cur.fetchall()
    finally:
        conn.close()

    mapa = {}
    for fila in filas:
        mapa.setdefault(fila["nrc_id"], []).append(fila)
    return mapa


def codigos_de_tabla(cur, tabla, estudiante_id, obligatoria=False, mensaje=None):
    """
    Devuelve los ``codigo_curso`` (en mayúsculas) de ``tabla`` para un
    estudiante.

    ``tabla`` solo puede ser ``historial`` o ``matriz_por_ver`` (se interpola en
    el SQL, por eso se valida contra una lista blanca). Si la tabla no existe:
      * ``obligatoria=True`` -> HTTP 503 con un mensaje claro.
      * ``obligatoria=False`` -> lista vacía (la API sigue funcionando).
    """
    if tabla not in ("historial", "matriz_por_ver"):
        raise ValueError(f"Tabla no permitida: {tabla}")

    cur.execute("SELECT to_regclass(%s) AS tabla", (f"public.{tabla}",))
    if cur.fetchone()["tabla"] is None:
        if obligatoria:
            raise HTTPException(
                status_code=503,
                detail=mensaje
                or (
                    f"La tabla '{tabla}' no existe todavía. "
                    "Debe crearla el módulo de Data Lake antes de usar este endpoint."
                ),
            )
        return []

    cur.execute(
        f"SELECT DISTINCT codigo_curso FROM {tabla} WHERE estudiante_id = %s",
        (estudiante_id,),
    )
    codigos = set()
    for fila in cur.fetchall():
        codigo = str(fila["codigo_curso"] or "").strip().upper()
        if codigo:
            codigos.add(codigo)
    return sorted(codigos)


def materias_aprobadas(cur, estudiante_id):
    """
    Devuelve los códigos de curso (normalizados a mayúsculas) aprobados por el
    estudiante según la tabla ``historial`` (creada por el Data Lake).

    Se asume que la tabla tiene, como mínimo, las columnas ``estudiante_id`` y
    ``codigo_curso``. Si la tabla no existe se lanza un 503 con un mensaje claro.
    """
    return codigos_de_tabla(
        cur,
        "historial",
        estudiante_id,
        obligatoria=True,
        mensaje=(
            "La tabla 'historial' no existe todavía. "
            "Debe crearla el módulo de Data Lake antes de usar este endpoint."
        ),
    )


def materias_por_ver(cur, estudiante_id):
    """
    Códigos de materias pendientes (``matriz_por_ver``) del estudiante.

    Es opcional: si la tabla aún no existe (estudiante sin matriz cargada) se
    devuelve una lista vacía para no romper el generador automático.
    """
    return codigos_de_tabla(cur, "matriz_por_ver", estudiante_id, obligatoria=False)


# ---------------------------------------------------------------------------
# Utilidades de base de datos — MongoDB
# ---------------------------------------------------------------------------
_mongo_client = None


def mongo_db():
    """Devuelve la base de datos MongoDB (cliente perezoso y en caché)."""
    global _mongo_client
    if not MONGO_DISPONIBLE:
        raise HTTPException(
            status_code=503,
            detail="pymongo no está instalado. Ejecuta: pip install pymongo",
        )
    if _mongo_client is None:
        # serverSelectionTimeoutMS acota el tiempo de espera si Mongo no está
        # levantado, para que el endpoint falle rápido en lugar de colgarse.
        _mongo_client = pymongo.MongoClient(MONGO_URI, serverSelectionTimeoutMS=3000)
    return _mongo_client[MONGO_DB]


def obtener_catalogo():
    """
    Devuelve la lista de materias del plan de estudios desde MongoDB.

    Espera documentos con la forma {codigo, nombre, creditos, semestre,
    prerrequisitos: [...]}. También tolera un documento de "plan" que anide un
    array ``materias`` y, por compatibilidad con el seed anterior, cae a la
    colección ``materias`` si ``catalogo_materias`` está vacía.

    Normaliza ``codigo`` y ``prerrequisitos`` a mayúsculas sin espacios.
    """
    db = mongo_db()
    try:
        docs = list(db[MONGO_COL_CATALOGO].find({}, {"_id": 0}))
        if not docs:
            # Compatibilidad con el seed previo (colección "materias").
            docs = list(db["materias"].find({}, {"_id": 0}))
    except PyMongoError as exc:
        raise HTTPException(
            status_code=503,
            detail=f"No se pudo leer el catálogo desde MongoDB: {exc}",
        ) from exc

    materias = []
    for doc in docs:
        # Documento tipo plan con array de materias anidado.
        if "materias" in doc and isinstance(doc["materias"], list):
            for item in doc["materias"]:
                if isinstance(item, dict):
                    materias.append(item)
                else:
                    materias.append({"codigo": str(item), "prerrequisitos": []})
        elif doc.get("codigo"):
            materias.append(doc)

    # Normalización.
    for m in materias:
        m["codigo"] = str(m.get("codigo", "")).strip().upper()
        m["prerrequisitos"] = [
            str(p).strip().upper() for p in (m.get("prerrequisitos") or [])
        ]
    return materias


def catalogo_desde_postgres():
    """
    Fallback: construye el catálogo (materias del plan) desde PostgreSQL cuando
    MongoDB no está disponible o ``catalogo_materias`` aún está vacía (por
    ejemplo, antes de que el Data Lake sincronice el catálogo).

    Devuelve la misma forma que ``obtener_catalogo()``:
    [{codigo, nombre, creditos, semestre, prerrequisitos}]. Los prerrequisitos
    efectivos se resuelven aparte con ``reglas_negocio`` (MongoDB) o, si no hay
    reglas, quedan vacíos.
    """
    conn = conectar()
    try:
        with conn.cursor() as cur:
            cur.execute(
                """
                SELECT codigo_alfanumerico AS codigo, nombre, creditos
                FROM curso
                ORDER BY codigo_alfanumerico
                """
            )
            filas = cur.fetchall()
    finally:
        conn.close()

    return [
        {
            "codigo": str(f["codigo"]).strip().upper(),
            "nombre": f["nombre"],
            "creditos": f["creditos"],
            "semestre": 0,
            "prerrequisitos": [],
        }
        for f in filas
    ]


def obtener_reglas_completas():
    """
    Devuelve las reglas de negocio dinámicas desde MongoDB conservando los
    metadatos que siembra ``seed_reglas.py``.

    Forma: [{materia, prerrequisitos, nombre?, creditos?, semestre?}, ...]
    ordenado por materia. ``prerrequisitos`` y ``materia`` se normalizan a
    mayúsculas; ``nombre``/``creditos``/``semestre`` se incluyen solo si el
    documento los trae (así el contrato básico no cambia).
    """
    db = mongo_db()
    try:
        docs = list(db[MONGO_COL_REGLAS].find({}, {"_id": 0}))
    except PyMongoError as exc:
        raise HTTPException(
            status_code=503,
            detail=f"No se pudo leer reglas_negocio desde MongoDB: {exc}",
        ) from exc

    por_materia = {}
    for doc in docs:
        materia = str(doc.get("materia", "")).strip().upper()
        if not materia:
            continue
        regla = {
            "materia": materia,
            "prerrequisitos": [
                str(p).strip().upper()
                for p in (doc.get("prerrequisitos") or [])
            ],
        }
        # Metadatos opcionales del catálogo sembrados por seed_reglas.py.
        for campo in ("nombre", "creditos", "semestre"):
            if doc.get(campo) is not None:
                regla[campo] = doc[campo]
        # Si hubiera documentos duplicados, gana el último (como antes).
        por_materia[materia] = regla
    return sorted(por_materia.values(), key=lambda r: r["materia"])


def obtener_reglas():
    """
    Devuelve las reglas de negocio dinámicas desde MongoDB como un diccionario
    {materia: [prerrequisitos]}, con claves/valores normalizados a mayúsculas.
    """
    return {
        regla["materia"]: regla["prerrequisitos"]
        for regla in obtener_reglas_completas()
    }


def prerrequisitos_efectivos(codigo, catalogo_map, reglas):
    """
    Devuelve los prerrequisitos efectivos de una materia.

    Prioridad: si existe una regla dinámica en ``reglas_negocio`` para la
    materia, ésta manda (el admin puede cambiarla sin tocar código); en caso
    contrario se usan los prerrequisitos estáticos del catálogo.
    """
    codigo = codigo.strip().upper()
    if codigo in reglas:
        return list(reglas[codigo])
    materia = catalogo_map.get(codigo)
    if materia is None:
        return []
    return list(materia.get("prerrequisitos") or [])


# ---------------------------------------------------------------------------
# Utilidades de horario (para el generador automático)
# ---------------------------------------------------------------------------
def a_minutos(hora):
    """Convierte 'HH:MM:SS' (o 'HH:MM') a minutos desde medianoche."""
    partes = str(hora).split(":")
    try:
        return int(partes[0]) * 60 + int(partes[1])
    except (IndexError, ValueError):
        return 0


def a_entero(valor, por_defecto=0):
    """Convierte a int tolerando None, cadenas vacías o valores no numéricos."""
    try:
        return int(valor)
    except (TypeError, ValueError):
        return por_defecto


def choca(sesiones, ocupados):
    """
    True si alguna sesión de ``sesiones`` solapa (mismo día + rangos de tiempo
    que se traslapan) con alguno de los intervalos ya ocupados.

    ``ocupados`` es una lista de tuplas (dia, inicio_min, fin_min).
    """
    for s in sesiones:
        dia = s["dia"]
        ini = a_minutos(s["hora_inicio"])
        fin = a_minutos(s["hora_fin"])
        for o_dia, o_ini, o_fin in ocupados:
            if o_dia == dia and ini < o_fin and o_ini < fin:
                return True
    return False


# ---------------------------------------------------------------------------
# Utilidades de carga de archivos (admin: oferta / estudiante: matriz)
# ---------------------------------------------------------------------------
# Tablas auxiliares de la matriz del estudiante. Se crean de forma idempotente
# aquí porque ``schema.sql`` no las incluye (igual que hace el Data Lake con
# ``historial``). Los nombres sin comillas se guardan en minúsculas en
# PostgreSQL.
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

DDL_HISTORIAL = """
    CREATE TABLE IF NOT EXISTS historial (
        estudiante_id INTEGER NOT NULL,
        codigo_curso  VARCHAR(10) NOT NULL,
        PRIMARY KEY (estudiante_id, codigo_curso)
    )
"""

# Columnas que debe traer el CSV de matriz (``nombre_curso`` es opcional: se
# acepta y se ignora, pero no se usa para poblar PostgreSQL).
COLUMNAS_MATRIZ_REQUERIDAS = [
    "estudiante_id",
    "carrera",
    "semestre",
    "estado",
    "codigo_curso",
]
COLUMNAS_MATRIZ_OPCIONALES = ["nombre_curso"]

# Estados del CSV -> conjunto destino.
ESTADOS_APROBADA = {
    "aprobada", "aprobadas", "aprobado", "aprobados",
    "vista", "vistas", "visto", "vistos",
}
ESTADOS_POR_VER = {
    "por_ver", "porver", "por_verificar", "pendiente", "pendientes",
}


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


def _texto_celda(valor):
    """Convierte una celda de pandas a texto limpio ('' para nulos)."""
    if valor is None:
        return ""
    if isinstance(valor, float) and valor != valor:  # NaN
        return ""
    texto = str(valor).strip()
    if texto.lower() in ("nan", "none", "<na>", "nat"):
        return ""
    return texto


def _normalizar_estado(valor):
    """Mapea el estado del CSV a 'aprobada' / 'por_ver' (o None si no es válido)."""
    clave = _normalizar_columna(_texto_celda(valor))
    if clave in ESTADOS_APROBADA:
        return "aprobada"
    if clave in ESTADOS_POR_VER:
        return "por_ver"
    return None


def _parsear_semestre(valor):
    """Convierte '3' o '3.0' a entero; devuelve None si está vacío o es inválido."""
    texto = _texto_celda(valor).replace(",", ".")
    if not texto:
        return None
    try:
        numero = float(texto)
    except ValueError:
        return None
    if numero != int(numero) or numero < 0:
        return None
    return int(numero)


def dir_uploads():
    """Directorio de uploads (UPLOADS_DIR; por defecto <backend|/app>/uploads)."""
    destino = Path(
        os.getenv("UPLOADS_DIR", str(BASE_DIR / "uploads"))
    ).expanduser()
    return destino.resolve()


def ruta_script_ingesta():
    """
    Ruta absoluta de ``datalake/ingesta_datalake.py`` calculada desde __file__.

    Cubre los dos layouts del proyecto:
      * Docker (contexto = raíz del repo): /app/main.py -> /app/datalake/...
      * Local: backend/main.py -> <repo>/datalake/...
    """
    candidatos = []
    ruta_env = os.getenv("INGESTA_SCRIPT")
    if ruta_env:
        candidatos.append(Path(ruta_env).expanduser())
    candidatos.extend(
        [
            BASE_DIR / "datalake" / "ingesta_datalake.py",
            BASE_DIR.parent / "datalake" / "ingesta_datalake.py",
        ]
    )
    for candidato in candidatos:
        if candidato.is_file():
            return candidato.resolve()
    return None


def _recortar_salida(texto, limite=8000):
    """Recorta la salida de un subproceso conservando el final (el error real)."""
    texto = texto or ""
    if len(texto) <= limite:
        return texto
    return f"...(salida recortada, últimos {limite} caracteres)...\n" + texto[-limite:]


def extraer_resumen_ingesta(salida):
    """
    Extrae {cursos, nrcs, sesiones} de la salida de la ingesta.

    Primero busca la línea ``RESUMEN_JSON: {...}`` que imprime el script; si no
    está (versión antigua), cae a parsear el resumen en texto. Devuelve 0 para
    los conteos que no se puedan determinar.
    """
    resumen = {}
    for linea in reversed((salida or "").splitlines()):
        if linea.strip().startswith("RESUMEN_JSON:"):
            try:
                datos = json.loads(linea.split(":", 1)[1].strip())
                if isinstance(datos, dict):
                    resumen = datos
                break
            except (ValueError, TypeError):
                continue

    for clave in ("cursos", "nrcs", "sesiones"):
        if clave in resumen:
            try:
                resumen[clave] = int(resumen[clave])
                continue
            except (TypeError, ValueError):
                pass
        coincidencia = re.search(
            rf"{clave}\s*:\s*([\d][\d.,]*)", salida or "", re.IGNORECASE
        )
        if coincidencia:
            digitos = re.sub(r"\D", "", coincidencia.group(1))
            resumen[clave] = int(digitos) if digitos else 0
        else:
            resumen[clave] = 0
    return {clave: resumen.get(clave, 0) for clave in ("cursos", "nrcs", "sesiones")}


def _unir_salida(stdout, stderr):
    """Une stdout/stderr de un subproceso en un único texto (admite bytes)."""
    partes = []
    for valor in (stdout, stderr):
        if not valor:
            continue
        if isinstance(valor, bytes):
            valor = valor.decode("utf-8", errors="replace")
        partes.append(str(valor).rstrip())
    return "\n".join(partes)


def _leer_csv_subido(datos, etiqueta):
    """
    Lee un CSV subido (bytes) con pandas, tolerando UTF-8/latin-1, BOM,
    separador coma o punto y coma, y encabezados con espacios/mayúsculas.

    Devuelve un DataFrame de texto (sin NaN) con columnas normalizadas.
    """
    if not datos:
        raise HTTPException(status_code=400, detail=f"{etiqueta}: el archivo está vacío")

    try:
        import pandas as pd
    except ImportError as exc:  # pragma: no cover - entorno sin pandas
        raise HTTPException(
            status_code=503,
            detail=(
                "pandas no está instalado en el backend. "
                "Ejecuta: pip install pandas pyarrow"
            ),
        ) from exc

    df = None
    ultimo_error = None
    for encoding in ("utf-8-sig", "latin-1"):
        muestra = datos[:8192].decode(encoding, errors="replace")
        primera_linea = muestra.splitlines()[0] if muestra.splitlines() else ""
        separador = ";" if primera_linea.count(";") > primera_linea.count(",") else ","
        try:
            df = pd.read_csv(
                io.BytesIO(datos),
                dtype=str,
                encoding=encoding,
                sep=separador,
                keep_default_na=False,
            )
            break
        except UnicodeDecodeError as exc:
            ultimo_error = exc
            continue
        except pd.errors.EmptyDataError as exc:
            raise HTTPException(
                status_code=400,
                detail=f"{etiqueta}: el CSV está vacío o no tiene encabezados",
            ) from exc
        except Exception as exc:  # pandas.errors.ParserError y similares
            raise HTTPException(
                status_code=400,
                detail=f"{etiqueta}: no se pudo leer el CSV: {exc}",
            ) from exc

    if df is None:
        raise HTTPException(
            status_code=400,
            detail=f"{etiqueta}: no se pudo decodificar el CSV ({ultimo_error})",
        )

    # Encabezados normalizados y sin columnas duplicadas.
    columnas = [_normalizar_columna(c) for c in df.columns]
    if len(set(columnas)) != len(columnas):
        raise HTTPException(
            status_code=400,
            detail=(
                f"{etiqueta}: hay columnas duplicadas tras normalizar los "
                f"encabezados: {columnas}"
            ),
        )
    df.columns = columnas

    # Fuera filas totalmente vacías y espacios sobrantes en los valores.
    df = df[
        df.apply(lambda fila: any(_texto_celda(v) for v in fila), axis=1)
    ].reset_index(drop=True)
    for columna in df.columns:
        df[columna] = df[columna].map(_texto_celda)

    if df.empty:
        raise HTTPException(
            status_code=400, detail=f"{etiqueta}: no contiene filas de datos"
        )
    return df


def _parsear_matriz(df, estudiante_id_form=None):
    """
    Valida y transforma el DataFrame de matriz en los datos a persistir.

    Devuelve {estudiante_id, carrera, semestre, aprobadas, por_ver}. Lanza 400
    con mensajes claros ante columnas faltantes, estados desconocidos, códigos
    inválidos, varios estudiantes o un ``estudiante_id`` que no coincide con el
    del formulario.
    """
    faltantes = [c for c in COLUMNAS_MATRIZ_REQUERIDAS if c not in df.columns]
    if faltantes:
        raise HTTPException(
            status_code=400,
            detail=(
                "La matriz no tiene las columnas requeridas: "
                f"{faltantes}. Formato esperado: "
                "estudiante_id,carrera,semestre,estado,codigo_curso,nombre_curso"
            ),
        )

    errores = []
    estudiantes = set()
    aprobadas = set()
    por_ver = set()
    carrera = None
    semestre = None

    for posicion, fila in df.iterrows():
        linea = posicion + 2  # +1 por índice 0 y +1 por el encabezado
        id_texto = _texto_celda(fila["estudiante_id"])
        codigo = _texto_celda(fila["codigo_curso"]).upper()
        estado_texto = _texto_celda(fila["estado"])

        if not id_texto and not codigo:
            continue

        try:
            estudiante_fila = int(id_texto)
        except (TypeError, ValueError):
            errores.append(
                f"línea {linea}: estudiante_id inválido ({id_texto!r})"
            )
            continue
        if estudiante_fila <= 0:
            errores.append(
                f"línea {linea}: estudiante_id debe ser positivo ({estudiante_fila})"
            )
            continue

        if not codigo:
            errores.append(f"línea {linea}: codigo_curso vacío")
            continue
        if len(codigo) > 10:
            errores.append(
                f"línea {linea}: codigo_curso supera VARCHAR(10) ({codigo!r})"
            )
            continue

        estado = _normalizar_estado(estado_texto)
        if estado is None:
            errores.append(
                f"línea {linea}: estado no reconocido ({estado_texto!r}); "
                "use aprobada/vista o por_ver/pendiente"
            )
            continue

        estudiantes.add(estudiante_fila)

        carrera_fila = _texto_celda(fila["carrera"])
        if carrera_fila and carrera is None:
            carrera = carrera_fila

        semestre_fila = _texto_celda(fila["semestre"])
        if semestre_fila:
            valor_semestre = _parsear_semestre(semestre_fila)
            if valor_semestre is None:
                errores.append(
                    f"línea {linea}: semestre inválido ({semestre_fila!r})"
                )
            elif semestre is None:
                semestre = valor_semestre

        if estado == "aprobada":
            aprobadas.add(codigo)
        else:
            por_ver.add(codigo)

    if len(estudiantes) > 1:
        raise HTTPException(
            status_code=400,
            detail=(
                "El CSV contiene varios estudiantes distintos "
                f"({', '.join(str(e) for e in sorted(estudiantes))}). "
                "Sube una matriz por estudiante."
            ),
        )

    if estudiante_id_form is not None and estudiantes:
        if estudiante_id_form not in estudiantes:
            encontrado = next(iter(estudiantes))
            raise HTTPException(
                status_code=400,
                detail=(
                    f"El estudiante_id del formulario ({estudiante_id_form}) no "
                    f"coincide con el del CSV ({encontrado})."
                ),
            )

    if errores:
        detalle = "\n".join(f"- {e}" for e in errores[:20])
        if len(errores) > 20:
            detalle += f"\n... y {len(errores) - 20} error(es) más"
        raise HTTPException(
            status_code=400,
            detail=f"La matriz contiene errores:\n{detalle}",
        )

    if not aprobadas and not por_ver:
        raise HTTPException(
            status_code=400,
            detail="La matriz no contiene materias aprobadas ni pendientes válidas.",
        )

    # Una materia no puede ser aprobada y pendiente a la vez: gana aprobada.
    por_ver -= aprobadas

    if estudiante_id_form is not None:
        estudiante_id = estudiante_id_form
    elif estudiantes:
        estudiante_id = next(iter(estudiantes))
    else:
        raise HTTPException(
            status_code=400,
            detail=(
                "No se pudo determinar el estudiante_id: inclúyelo en el CSV o "
                "envíalo en el campo 'estudiante_id' del formulario."
            ),
        )

    return {
        "estudiante_id": estudiante_id,
        "carrera": carrera,
        "semestre": semestre,
        "aprobadas": sorted(aprobadas),
        "por_ver": sorted(por_ver),
    }


async def guardar_upload(archivo, prefijo):
    """
    Guarda un UploadFile como ``<prefijo>_<timestamp>_<uuid>.csv`` en uploads.

    Devuelve la ruta absoluta del archivo guardado.
    """
    nombre_original = Path(archivo.filename or "").name
    if not nombre_original.lower().endswith(".csv"):
        raise HTTPException(
            status_code=400,
            detail=f"El archivo debe ser un CSV (.csv); recibido: {nombre_original!r}",
        )

    destino_dir = dir_uploads()
    try:
        destino_dir.mkdir(parents=True, exist_ok=True)
    except OSError as exc:
        raise HTTPException(
            status_code=500,
            detail=f"No se pudo crear el directorio de uploads {destino_dir}: {exc}",
        ) from exc

    marca = datetime.now().strftime("%Y%m%d_%H%M%S_%f")
    nombre = f"{prefijo}_{marca}_{uuid.uuid4().hex[:8]}.csv"
    destino = destino_dir / nombre

    total = 0
    try:
        with destino.open("wb") as salida:
            while True:
                bloque = await archivo.read(1024 * 1024)
                if not bloque:
                    break
                salida.write(bloque)
                total += len(bloque)
    except OSError as exc:
        raise HTTPException(
            status_code=500,
            detail=f"No se pudo guardar el archivo en {destino}: {exc}",
        ) from exc
    finally:
        await archivo.close()

    if total == 0:
        try:
            destino.unlink(missing_ok=True)
        except OSError:
            pass
        raise HTTPException(status_code=400, detail="El archivo subido está vacío")

    return destino


# ---------------------------------------------------------------------------
# Modelos de entrada
# ---------------------------------------------------------------------------
class InscripcionIn(BaseModel):
    estudiante_id: int
    nrc_id: int


class HorarioAutomaticoIn(BaseModel):
    estudiante_id: int


class ReglaNegocioIn(BaseModel):
    """Regla dinámica: {materia: 'CAL-201', prerrequisitos: ['CAL-101']}."""

    materia: str
    prerrequisitos: List[str] = Field(default_factory=list)


# ---------------------------------------------------------------------------
# Endpoints
# ---------------------------------------------------------------------------
@app.get("/")
def raiz():
    return {
        "servicio": "Sistema de Inscripción de Materias — API",
        "endpoints": [
            "/api/oferta",
            "/api/inscribir",
            "/api/horario/{id}",
            "/api/horario/automatico",
            "/api/admin/reglas",
            "/api/admin/upload-oferta",
            "/api/estudiante/upload-matriz",
            "/api/estudiante/{id}/matriz",
        ],
    }


@app.get("/api/oferta")
def oferta():
    """
    Retorna los NRC ofertados con su materia, créditos, cupo e inscritos.
    Incluye la lista de horarios (sesiones) de cada NRC.
    """
    conn = conectar()
    try:
        with conn.cursor() as cur:
            cur.execute(
                """
                SELECT
                    n.id_nrc,
                    n.curso_id,
                    c.nombre      AS materia,
                    c.creditos,
                    n.jornada,
                    n.modalidad,
                    n.cupo_max,
                    COALESCE(i.total, 0)                          AS inscritos,
                    (n.cupo_max - COALESCE(i.total, 0))           AS cupos_disponibles
                FROM nrc n
                JOIN curso c ON c.codigo_alfanumerico = n.curso_id
                LEFT JOIN (
                    SELECT nrc_id, COUNT(*) AS total
                    FROM inscripcion
                    GROUP BY nrc_id
                ) i ON i.nrc_id = n.id_nrc
                ORDER BY n.id_nrc
                """
            )
            nrcs = cur.fetchall()
    finally:
        conn.close()

    horarios = horarios_por_nrc()
    for nrc in nrcs:
        nrc["disponible"] = nrc["cupos_disponibles"] > 0
        nrc["horario"] = horarios.get(nrc["id_nrc"], [])
    return nrcs


# ---------------------------------------------------------------------------
# Carga de CSV — Administrador (oferta) y Estudiante (matriz)
# ---------------------------------------------------------------------------
@app.post("/api/admin/upload-oferta")
async def upload_oferta(archivo: UploadFile = File(...)):
    """
    Sube el CSV de oferta académica y puebla PostgreSQL (curso, nrc, horario).

    Contrato del CSV (una fila por sesión de horario)::

        codigo_curso,nombre_curso,creditos,id_nrc,jornada,modalidad,cupo_max,
        numero_sesion,dia,hora_inicio,hora_fin

    Flujo:
      1. Guarda el archivo en ``UPLOADS_DIR`` con un nombre único
         (``oferta_<timestamp>_<uuid>.csv``).
      2. Ejecuta ``datalake/ingesta_datalake.py --oferta <archivo> --solo-oferta``
         con el mismo intérprete de la API (``sys.executable``).
      3. Devuelve ``{ok, archivo, cursos, nrcs, sesiones, salida}``.

    El modo ``--solo-oferta`` no lee ni vacía la matriz de aprobados
    (``historial``), así que los estudiantes conservan sus materias aprobadas.
    Si la ingesta falla se devuelve HTTP 400 con la salida del script.
    """
    destino = await guardar_upload(archivo, "oferta")

    script = ruta_script_ingesta()
    if script is None:
        raise HTTPException(
            status_code=500,
            detail=(
                "No se encontró datalake/ingesta_datalake.py. Se buscó en "
                f"{BASE_DIR / 'datalake'} y {BASE_DIR.parent / 'datalake'} "
                "(configura INGESTA_SCRIPT para indicar otra ruta)."
            ),
        )

    try:
        timeout = float(os.getenv("INGESTA_TIMEOUT", "600"))
    except ValueError:
        timeout = 600.0
    if timeout <= 0:
        timeout = 600.0

    comando = [
        sys.executable,
        str(script),
        "--oferta",
        str(destino),
        "--solo-oferta",
    ]
    logger.info("Ejecutando ingesta de oferta: %s", " ".join(comando))

    try:
        proceso = subprocess.run(
            comando,
            cwd=str(script.parent),
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=timeout,
        )
    except subprocess.TimeoutExpired as exc:
        salida = _unir_salida(exc.stdout, exc.stderr)
        raise HTTPException(
            status_code=400,
            detail={
                "mensaje": (
                    f"La ingesta de la oferta excedió el tiempo máximo "
                    f"({timeout:.0f}s)."
                ),
                "archivo": str(destino),
                "salida": _recortar_salida(salida),
            },
        ) from exc
    except OSError as exc:
        raise HTTPException(
            status_code=500,
            detail=f"No se pudo ejecutar la ingesta ({script}): {exc}",
        ) from exc

    salida = _unir_salida(proceso.stdout, proceso.stderr)

    if proceso.returncode != 0:
        logger.warning(
            "La ingesta de oferta falló (código %s): %s",
            proceso.returncode,
            _recortar_salida(salida, 2000),
        )
        raise HTTPException(
            status_code=400,
            detail={
                "mensaje": "La ingesta de la oferta académica falló.",
                "archivo": str(destino),
                "codigo_retorno": proceso.returncode,
                "salida": _recortar_salida(salida),
            },
        )

    resumen = extraer_resumen_ingesta(proceso.stdout or salida)
    logger.info(
        "Oferta cargada desde %s: %s cursos, %s NRC, %s sesiones",
        destino,
        resumen["cursos"],
        resumen["nrcs"],
        resumen["sesiones"],
    )
    return {
        "ok": True,
        "archivo": str(destino),
        "cursos": resumen["cursos"],
        "nrcs": resumen["nrcs"],
        "sesiones": resumen["sesiones"],
        "salida": salida,
    }


@app.post("/api/estudiante/upload-matriz")
async def upload_matriz(
    archivo: UploadFile = File(...),
    estudiante_id: Optional[int] = Form(None),
):
    """
    Sube la matriz académica de UN estudiante y reemplaza sus aprobadas/pendientes.

    Contrato del CSV (encabezados con espacios o mayúsculas son tolerados)::

        estudiante_id,carrera,semestre,estado,codigo_curso,nombre_curso

    * ``estado`` ∈ {``aprobada``, ``vista``} -> se guarda en ``historial``
      (materias aprobadas).
    * ``estado`` ∈ {``por_ver``, ``pendiente``} -> se guarda en
      ``matriz_por_ver`` (materias pendientes).
    * ``nombre_curso`` es opcional (se acepta pero no se persiste).

    Reglas:
      * El CSV debe contener un único ``estudiante_id``. Si el formulario trae
        ``estudiante_id`` y no coincide con el del CSV, se devuelve 400.
      * Crea si no existen ``estudiante_perfil``, ``matriz_por_ver`` e
        ``historial``.
      * Hace upsert del estudiante (nombre ``'Estudiante <id>'`` si es nuevo)
        y REEMPLAZA (DELETE + INSERT) solo los datos de ESE estudiante.

    Devuelve::

        {ok, estudiante_id, carrera, semestre, aprobadas: [...],
         por_ver: [...], total_aprobadas, total_por_ver}
    """
    if estudiante_id is not None and estudiante_id <= 0:
        raise HTTPException(
            status_code=400,
            detail="El estudiante_id del formulario debe ser un entero positivo.",
        )

    try:
        datos = await archivo.read()
    finally:
        await archivo.close()

    df = _leer_csv_subido(datos, "matriz de estudiantes")
    matriz = _parsear_matriz(df, estudiante_id_form=estudiante_id)

    sid = matriz["estudiante_id"]
    aprobadas = matriz["aprobadas"]
    por_ver = matriz["por_ver"]
    carrera = matriz["carrera"]
    semestre = matriz["semestre"]

    conn = conectar()
    try:
        with conn.cursor() as cur:
            # Tablas auxiliares idempotentes (el esquema base no las trae).
            cur.execute(DDL_ESTUDIANTE_PERFIL)
            cur.execute(DDL_MATRIZ_POR_VER)
            cur.execute(DDL_HISTORIAL)

            # Upsert del estudiante: si ya existe se conserva su nombre real.
            cur.execute(
                """
                INSERT INTO estudiante (id, nombre, plan_id)
                VALUES (%s, %s, 0)
                ON CONFLICT (id) DO NOTHING
                """,
                (sid, f"Estudiante {sid}"),
            )

            # Perfil: actualiza solo los valores informados (no borra con NULL).
            cur.execute(
                """
                INSERT INTO estudiante_perfil (estudiante_id, carrera, semestre)
                VALUES (%s, %s, %s)
                ON CONFLICT (estudiante_id) DO UPDATE SET
                    carrera  = COALESCE(EXCLUDED.carrera,
                                        estudiante_perfil.carrera),
                    semestre = COALESCE(EXCLUDED.semestre,
                                        estudiante_perfil.semestre)
                """,
                (sid, carrera, semestre),
            )

            # Reemplazo total SOLO del historial de este estudiante.
            cur.execute("DELETE FROM historial WHERE estudiante_id = %s", (sid,))
            if aprobadas:
                cur.executemany(
                    "INSERT INTO historial (estudiante_id, codigo_curso) "
                    "VALUES (%s, %s) ON CONFLICT DO NOTHING",
                    [(sid, codigo) for codigo in aprobadas],
                )

            # Reemplazo total SOLO de la matriz por ver de este estudiante.
            cur.execute(
                "DELETE FROM matriz_por_ver WHERE estudiante_id = %s", (sid,)
            )
            if por_ver:
                cur.executemany(
                    "INSERT INTO matriz_por_ver (estudiante_id, codigo_curso) "
                    "VALUES (%s, %s) ON CONFLICT DO NOTHING",
                    [(sid, codigo) for codigo in por_ver],
                )
        conn.commit()
    except HTTPException:
        conn.rollback()
        raise
    except Exception as exc:  # noqa: BLE001 - se informa con detalle
        conn.rollback()
        logger.exception("Error guardando la matriz del estudiante %s", sid)
        # 42P01 = undefined_table: el volumen de PostgreSQL existe pero le
        # faltan las tablas base (schema.sql no se ejecutó nunca). init_db.py
        # las crea al arrancar; damos una instrucción accionable en vez de un
        # 500 genérico.
        if getattr(exc, "pgcode", None) == "42P01":
            raise HTTPException(
                status_code=503,
                detail=(
                    "Faltan tablas básicas en PostgreSQL (por ejemplo "
                    "'estudiante'). El backend las crea al arrancar con "
                    "init_db.py; reinicia el contenedor del backend o ejecuta "
                    "'python init_db.py' dentro de él. No se borrará ningún "
                    f"dato. Detalle: {exc}"
                ),
            ) from exc
        raise HTTPException(
            status_code=500,
            detail=f"No se pudo guardar la matriz en PostgreSQL: {exc}",
        ) from exc
    finally:
        conn.close()

    return {
        "ok": True,
        "estudiante_id": sid,
        "carrera": carrera,
        "semestre": semestre,
        "aprobadas": aprobadas,
        "por_ver": por_ver,
        "total_aprobadas": len(aprobadas),
        "total_por_ver": len(por_ver),
    }


@app.get("/api/estudiante/{estudiante_id}/matriz")
def obtener_matriz_estudiante(estudiante_id: int):
    """
    Devuelve el resumen de la matriz cargada para un estudiante.

    Respuesta::

        {estudiante_id, carrera, semestre, aprobadas: [...], por_ver: [...],
         total_aprobadas, total_por_ver}

    Devuelve 404 si el estudiante no tiene perfil (matriz nunca cargada).
    """
    conn = conectar()
    try:
        with conn.cursor() as cur:
            cur.execute(
                "SELECT to_regclass('public.estudiante_perfil') AS tabla"
            )
            if cur.fetchone()["tabla"] is None:
                raise HTTPException(
                    status_code=404,
                    detail=(
                        "No hay perfil cargado para el estudiante "
                        f"{estudiante_id}. Sube su matriz primero."
                    ),
                )

            cur.execute(
                "SELECT carrera, semestre FROM estudiante_perfil "
                "WHERE estudiante_id = %s",
                (estudiante_id,),
            )
            perfil = cur.fetchone()
            if perfil is None:
                raise HTTPException(
                    status_code=404,
                    detail=(
                        "No hay perfil cargado para el estudiante "
                        f"{estudiante_id}. Sube su matriz primero."
                    ),
                )

            aprobadas = codigos_de_tabla(
                cur, "historial", estudiante_id, obligatoria=False
            )
            por_ver = codigos_de_tabla(
                cur, "matriz_por_ver", estudiante_id, obligatoria=False
            )
    finally:
        conn.close()

    return {
        "estudiante_id": estudiante_id,
        "carrera": perfil["carrera"],
        "semestre": perfil["semestre"],
        "aprobadas": aprobadas,
        "por_ver": por_ver,
        "total_aprobadas": len(aprobadas),
        "total_por_ver": len(por_ver),
    }


@app.post("/api/inscribir")
def inscribir(body: InscripcionIn):
    """
    Inscribe un estudiante en un NRC aplicando las validaciones:
      1. El NRC y el estudiante existen.
      2. El NRC tiene cupo disponible.
      3. No hay choque de horario con NRCs ya inscritos (misma consulta de
         solapamiento de ``schema.sql``).
      4. El estudiante no está ya inscrito en ese NRC (PK compuesta).
    """
    conn = conectar()
    try:
        with conn.cursor() as cur:
            # 1. Existencia del NRC
            cur.execute(
                "SELECT id_nrc, cupo_max FROM nrc WHERE id_nrc = %s",
                (body.nrc_id,),
            )
            nrc = cur.fetchone()
            if nrc is None:
                raise HTTPException(status_code=404, detail="El NRC no existe")

            # 2. Existencia del estudiante
            cur.execute(
                "SELECT id FROM estudiante WHERE id = %s",
                (body.estudiante_id,),
            )
            if cur.fetchone() is None:
                raise HTTPException(status_code=404, detail="El estudiante no existe")

            # 3. Ya inscrito en el mismo NRC
            cur.execute(
                "SELECT 1 FROM inscripcion WHERE estudiante_id = %s AND nrc_id = %s",
                (body.estudiante_id, body.nrc_id),
            )
            if cur.fetchone():
                raise HTTPException(
                    status_code=409,
                    detail="El estudiante ya está inscrito en este NRC",
                )

            # 4. Cupo disponible
            cur.execute(
                "SELECT COUNT(*) AS total FROM inscripcion WHERE nrc_id = %s",
                (body.nrc_id,),
            )
            inscritos = cur.fetchone()["total"]
            if inscritos >= nrc["cupo_max"]:
                raise HTTPException(
                    status_code=409,
                    detail="El NRC no tiene cupo disponible",
                )

            # 5. Choque de horario (mismo día y rangos solapados)
            cur.execute(
                """
                SELECT 1
                FROM inscripcion i
                JOIN horario h1 ON h1.nrc_id = i.nrc_id
                JOIN horario h2 ON h2.nrc_id = %(nrc_id)s
                WHERE i.estudiante_id = %(estudiante_id)s
                  AND h2.dia = h1.dia
                  AND h2.hora_inicio < h1.hora_fin
                  AND h1.hora_inicio < h2.hora_fin
                LIMIT 1
                """,
                {"nrc_id": body.nrc_id, "estudiante_id": body.estudiante_id},
            )
            if cur.fetchone():
                raise HTTPException(
                    status_code=409,
                    detail="Choque de horario con un NRC ya inscrito",
                )

            # Todo OK -> insertar
            cur.execute(
                """
                INSERT INTO inscripcion (estudiante_id, nrc_id, fecha)
                VALUES (%s, %s, CURRENT_DATE)
                """,
                (body.estudiante_id, body.nrc_id),
            )
        conn.commit()
    finally:
        conn.close()

    return {
        "ok": True,
        "mensaje": "Inscripción exitosa",
        "estudiante_id": body.estudiante_id,
        "nrc_id": body.nrc_id,
    }


@app.get("/api/horario/{estudiante_id}")
def horario_estudiante(estudiante_id: int):
    """Retorna el horario consolidado (todas las sesiones) del estudiante."""
    conn = conectar()
    try:
        with conn.cursor() as cur:
            cur.execute(
                """
                SELECT
                    h.nrc_id,
                    c.codigo_alfanumerico,
                    c.nombre            AS materia,
                    c.creditos,
                    h.numero_sesion,
                    h.dia,
                    h.hora_inicio::text AS hora_inicio,
                    h.hora_fin::text    AS hora_fin
                FROM inscripcion i
                JOIN nrc n   ON n.id_nrc = i.nrc_id
                JOIN curso c ON c.codigo_alfanumerico = n.curso_id
                JOIN horario h ON h.nrc_id = n.id_nrc
                WHERE i.estudiante_id = %s
                ORDER BY h.dia, h.hora_inicio, h.nrc_id
                """,
                (estudiante_id,),
            )
            filas = cur.fetchall()
    finally:
        conn.close()
    return filas


# ---------------------------------------------------------------------------
# A. Generador de Horario Automático
# ---------------------------------------------------------------------------
@app.post("/api/horario/automatico")
def horario_automatico(body: HorarioAutomaticoIn):
    """
    Genera un horario completo y sin choques para un estudiante, y lo guarda
    automáticamente en ``inscripcion``.

    Algoritmo (heurístico/greedy):
      1. Lee las materias aprobadas del estudiante desde ``historial``.
      2. Lee el catálogo (MongoDB) y las reglas dinámicas (MongoDB) para
         calcular qué materias faltan y cuáles son elegibles (prerrequisitos
         satisfechos).
      3. Lee los NRC con cupo y sus sesiones desde PostgreSQL.
      4. Ordena las materias elegibles (primero las de ``matriz_por_ver`` si
         existen, luego semestre, las más restringidas y las de más créditos)
         y asigna, para cada una, el primer NRC que no choque con lo ya
         seleccionado.
      5. Inserta las inscripciones elegidas, re-validando cupo/choque contra la
         base de datos dentro de una transacción.

    Devuelve el horario resultante y las materias no asignadas (con motivo).
    """
    estudiante_id = body.estudiante_id

    # ------------------------------------------------------------------
    # 1. Datos de PostgreSQL: estudiante, aprobadas, inscritas y NRCs con cupo.
    # ------------------------------------------------------------------
    conn = conectar()
    try:
        with conn.cursor() as cur:
            cur.execute(
                "SELECT id, plan_id FROM estudiante WHERE id = %s",
                (estudiante_id,),
            )
            if cur.fetchone() is None:
                raise HTTPException(status_code=404, detail="El estudiante no existe")

            # 1.1 Materias aprobadas (tabla historial). La matriz por ver es
            #     opcional y solo se usa para priorizar (si existe).
            aprobadas = set(materias_aprobadas(cur, estudiante_id))
            por_ver = set(materias_por_ver(cur, estudiante_id))

            # 1.2 Materias en las que ya está inscrito actualmente (evita duplicar).
            cur.execute(
                """
                SELECT DISTINCT n.curso_id
                FROM inscripcion i
                JOIN nrc n ON n.id_nrc = i.nrc_id
                WHERE i.estudiante_id = %s
                """,
                (estudiante_id,),
            )
            inscritas = {
                str(fila["curso_id"] or "").strip().upper() for fila in cur.fetchall()
            }
            inscritas.discard("")

            # 1.3 NRCs con cupo disponible.
            cur.execute(
                """
                SELECT
                    n.id_nrc,
                    n.curso_id,
                    c.nombre   AS materia,
                    c.creditos,
                    n.jornada,
                    n.modalidad,
                    n.cupo_max,
                    COALESCE(i.total, 0) AS inscritos
                FROM nrc n
                JOIN curso c ON c.codigo_alfanumerico = n.curso_id
                LEFT JOIN (
                    SELECT nrc_id, COUNT(*) AS total
                    FROM inscripcion
                    GROUP BY nrc_id
                ) i ON i.nrc_id = n.id_nrc
                WHERE COALESCE(i.total, 0) < n.cupo_max
                ORDER BY n.id_nrc
                """
            )
            nrcs = cur.fetchall()
    finally:
        conn.close()

    # Sesiones de todos los NRCs (mapa {nrc_id: [sesiones]}).
    horarios = horarios_por_nrc()

    # Agrupa los NRCs con cupo por curso, y solo conserva NRCs que tengan
    # al menos una sesión (un NRC sin horario no se puede programar).
    nrcs_por_curso = {}
    for nrc in nrcs:
        if not horarios.get(nrc["id_nrc"]):
            continue
        nrc["curso_id"] = str(nrc["curso_id"] or "").strip().upper()
        nrcs_por_curso.setdefault(nrc["curso_id"], []).append(nrc)

    # ------------------------------------------------------------------
    # 2. Catálogo y reglas desde MongoDB. Si MongoDB no está disponible o el
    #    catálogo aún está vacío, se degrada a PostgreSQL (curso + reglas
    #    opcionales) para que el generador siga siendo útil.
    # ------------------------------------------------------------------
    try:
        catalogo = obtener_catalogo()
    except HTTPException:
        catalogo = []
    if not catalogo:
        catalogo = catalogo_desde_postgres()

    try:
        reglas = obtener_reglas()
    except HTTPException:
        reglas = {}

    catalogo_map = {m["codigo"]: m for m in catalogo if m.get("codigo")}

    # ------------------------------------------------------------------
    # 3. Materias que faltan (no aprobadas ni inscritas).
    # ------------------------------------------------------------------
    faltantes = [
        m
        for m in catalogo
        if m.get("codigo")
        and m["codigo"] not in aprobadas
        and m["codigo"] not in inscritas
    ]

    # ------------------------------------------------------------------
    # 4. Filtra elegibles por prerrequisitos.
    # ------------------------------------------------------------------
    elegibles = []
    no_asignadas = []
    for m in faltantes:
        prereqs = prerrequisitos_efectivos(m["codigo"], catalogo_map, reglas)
        pendientes = [p for p in prereqs if p not in aprobadas]
        if pendientes:
            no_asignadas.append(
                {
                    "codigo": m["codigo"],
                    "materia": m.get("nombre"),
                    "motivo": "Prerrequisitos pendientes",
                    "prerrequisitos_pendientes": pendientes,
                }
            )
        else:
            elegibles.append(m)

    # ------------------------------------------------------------------
    # 5. Selección greedy sin choques.
    #    Orden: primero las materias que el estudiante marcó como pendientes en
    #    matriz_por_ver (si hay datos), luego semestre ascendente, luego las
    #    materias con menos NRCs disponibles (las más restringidas) y por
    #    último las de más créditos. Sin matriz, el orden es el de siempre.
    # ------------------------------------------------------------------
    def _nrcs_de(materia):
        return nrcs_por_curso.get(materia["codigo"], [])

    elegibles.sort(
        key=lambda m: (
            0 if m["codigo"] in por_ver else 1,
            a_entero(m.get("semestre"), 999),
            len(_nrcs_de(m)),
            -a_entero(m.get("creditos"), 0),
        )
    )

    asignadas = []   # materias con NRC elegido y sus sesiones
    ocupados = []    # intervalos ya usados: (dia, inicio_min, fin_min)

    for m in elegibles:
        candidatos = _nrcs_de(m)
        # Prefiere NRCs con menos sesiones y menos inscritos (más fácil de
        # encajar y reparte la carga).
        candidatos.sort(
            key=lambda n: (len(horarios.get(n["id_nrc"], [])), n["inscritos"])
        )

        elegido = None
        for nrc in candidatos:
            sesiones = horarios[nrc["id_nrc"]]
            if not choca(sesiones, ocupados):
                elegido = (nrc, sesiones)
                break

        if elegido is None:
            motivo = (
                "Choque de horario con las materias asignadas"
                if candidatos
                else "Sin NRC disponible (sin cupo u oferta)"
            )
            no_asignadas.append(
                {"codigo": m["codigo"], "materia": m.get("nombre"), "motivo": motivo}
            )
            continue

        nrc, sesiones = elegido
        asignadas.append(
            {
                "codigo": m["codigo"],
                "materia": nrc["materia"],
                "creditos": nrc["creditos"],
                "nrc_id": nrc["id_nrc"],
                "jornada": nrc["jornada"],
                "modalidad": nrc["modalidad"],
                "sesiones": [
                    {
                        "numero_sesion": s["numero_sesion"],
                        "dia": s["dia"],
                        "dia_nombre": DIA_NOMBRES.get(s["dia"], s["dia"]),
                        "hora_inicio": s["hora_inicio"],
                        "hora_fin": s["hora_fin"],
                    }
                    for s in sesiones
                ],
            }
        )
        for s in sesiones:
            ocupados.append(
                (s["dia"], a_minutos(s["hora_inicio"]), a_minutos(s["hora_fin"]))
            )

    # ------------------------------------------------------------------
    # 6. Persistencia: inserta las inscripciones elegidas, re-validando
    #    cupo y choque dentro de una transacción (defensa ante carreras).
    # ------------------------------------------------------------------
    confirmadas = []
    conn = conectar()
    try:
        with conn.cursor() as cur:
            for a in asignadas:
                nrc_id = a["nrc_id"]

                # Cupo.
                cur.execute(
                    "SELECT cupo_max FROM nrc WHERE id_nrc = %s", (nrc_id,)
                )
                fila_nrc = cur.fetchone()
                if fila_nrc is None:
                    continue
                cur.execute(
                    "SELECT COUNT(*) AS total FROM inscripcion WHERE nrc_id = %s",
                    (nrc_id,),
                )
                if cur.fetchone()["total"] >= fila_nrc["cupo_max"]:
                    continue

                # Choque contra lo ya inscrito (incluye lo insertado en esta tx).
                cur.execute(
                    """
                    SELECT 1
                    FROM inscripcion i
                    JOIN horario h1 ON h1.nrc_id = i.nrc_id
                    JOIN horario h2 ON h2.nrc_id = %(nrc_id)s
                    WHERE i.estudiante_id = %(estudiante_id)s
                      AND h2.dia = h1.dia
                      AND h2.hora_inicio < h1.hora_fin
                      AND h1.hora_inicio < h2.hora_fin
                    LIMIT 1
                    """,
                    {"nrc_id": nrc_id, "estudiante_id": estudiante_id},
                )
                if cur.fetchone():
                    continue

                cur.execute(
                    """
                    INSERT INTO inscripcion (estudiante_id, nrc_id, fecha)
                    VALUES (%s, %s, CURRENT_DATE)
                    """,
                    (estudiante_id, nrc_id),
                )
                confirmadas.append(a)
        conn.commit()
    finally:
        conn.close()

    # Las que no se confirmaron pasan a "no asignadas".
    confirmadas_ids = {a["nrc_id"] for a in confirmadas}
    for a in asignadas:
        if a["nrc_id"] not in confirmadas_ids:
            no_asignadas.append(
                {
                    "codigo": a["codigo"],
                    "materia": a["materia"],
                    "motivo": "Cupo o choque detectado al confirmar la inscripción",
                }
            )

    return {
        "estudiante_id": estudiante_id,
        "resumen": {
            "asignadas": len(confirmadas),
            "no_asignadas": len(no_asignadas),
        },
        "horario": confirmadas,
        "no_asignadas": no_asignadas,
    }


# ---------------------------------------------------------------------------
# B. Panel de Administrador — Reglas de negocio dinámicas (MongoDB)
# ---------------------------------------------------------------------------
@app.get("/api/admin/reglas")
def listar_reglas():
    """
    Devuelve las reglas de negocio dinámicas guardadas en ``reglas_negocio``,
    ordenadas por materia.

    Cada elemento conserva el contrato básico ``{materia, prerrequisitos}`` y,
    si el documento fue sembrado por ``seed_reglas.py``, incluye además
    ``nombre``, ``creditos`` y ``semestre`` para el panel de administración.
    """
    return obtener_reglas_completas()


@app.put("/api/admin/reglas")
def actualizar_reglas(body: Union[ReglaNegocioIn, List[ReglaNegocioIn]]):
    """
    Crea/actualiza reglas de negocio dinámicas en ``reglas_negocio``.

    Acepta una regla suelta:
        {"materia": "CAL-201", "prerrequisitos": ["CAL-101"]}
    o una lista de reglas. Usa upsert por ``materia``, así el administrador
    puede cambiar los prerrequisitos sin tocar código.

    Solo se actualiza ``prerrequisitos`` con ``$set``; los metadatos que siembra
    ``seed_reglas.py`` (``nombre``, ``creditos``, ``semestre``) se conservan.
    Para una materia nueva solo se crea ``materia`` con ``$setOnInsert``.
    """
    reglas = body if isinstance(body, list) else [body]

    # Validación previa: toda regla debe indicar una materia.
    for r in reglas:
        if not r.materia.strip():
            raise HTTPException(
                status_code=422,
                detail="Cada regla debe indicar el código de la materia en 'materia'",
            )

    db = mongo_db()
    col = db[MONGO_COL_REGLAS]

    guardadas = []
    try:
        for r in reglas:
            materia = r.materia.strip().upper()
            if not materia:
                continue
            # Deduplica y normaliza los prerrequisitos, conservando el orden.
            prereqs = []
            vistos = set()
            for p in r.prerrequisitos:
                p_norm = str(p).strip().upper()
                if p_norm and p_norm not in vistos:
                    vistos.add(p_norm)
                    prereqs.append(p_norm)

            # Actualiza SOLO los prerrequisitos. Los metadatos del catálogo
            # (nombre, creditos, semestre) que sembró seed_reglas.py no se
            # sobrescriben. En un documento nuevo solo se crea la materia.
            col.update_one(
                {"materia": materia},
                {
                    "$set": {"prerrequisitos": prereqs},
                    "$setOnInsert": {"materia": materia},
                },
                upsert=True,
            )
            guardadas.append({"materia": materia, "prerrequisitos": prereqs})
    except PyMongoError as exc:
        raise HTTPException(
            status_code=503,
            detail=f"No se pudo escribir reglas_negocio en MongoDB: {exc}",
        ) from exc

    return {"ok": True, "reglas": guardadas}
