# Sistema de Inscripción de Materias

Plataforma web para gestionar la inscripción de materias universitarias de forma
**Data-Driven**: la oferta académica y el historial de los estudiantes entran por
archivos CSV a un **Data Lake** (arquitectura Medallón), y a partir de ellos el
sistema genera **horarios automáticos sin choques**, respetando cupos y
prerrequisitos.

Proyecto políglota: **PostgreSQL · MongoDB · Neo4j · Python (FastAPI, pandas) · React**.

---

## Tabla de contenido

1. [Arquitectura](#arquitectura)
2. [Estructura del repositorio](#estructura-del-repositorio)
3. [Requisitos](#requisitos)
4. [Puesta en marcha con Docker](#puesta-en-marcha-con-docker)
5. [Desarrollo local sin Docker](#desarrollo-local-sin-docker)
6. [Carga de datos (CSV)](#carga-de-datos-csv)
7. [API REST](#api-rest)
8. [Modelo de datos](#modelo-de-datos)
9. [Scripts auxiliares](#scripts-auxiliares)
10. [Variables de entorno](#variables-de-entorno)
11. [Solución de problemas](#solución-de-problemas)
12. [Documentación adicional](#documentación-adicional)

---

## Arquitectura

```
                 Navegador
                     │  http://localhost
                     ▼
        ┌──────────────────────────┐
        │ frontend (Nginx + React) │  SPA servida en el puerto 80
        │   /api/*  ──proxy──┐     │
        └────────────────────┼─────┘
                             ▼
        ┌──────────────────────────┐      ┌──────────────────────────┐
        │ backend (FastAPI :8000)  │─────▶│ Data Lake                │
        │  init_db.py + uvicorn    │      │ ingesta_datalake.py      │
        └───┬───────────────┬──────┘      │ Bronze (CSV) → Silver    │
            │               │             │ (Parquet) → PostgreSQL   │
            ▼               ▼             └──────────────────────────┘
   ┌────────────────┐ ┌──────────────────┐   ┌──────────────────────┐
   │ PostgreSQL 16  │ │ MongoDB 7        │──▶│ Neo4j 5              │
   │ oferta, NRC,   │ │ catalogo_materias│   │ grafo de             │
   │ horarios,      │ │ reglas_negocio   │   │ prerrequisitos       │
   │ inscripciones, │ │ (prerrequisitos) │   │ (actualizar_grafo.py)│
   │ historial      │ └──────────────────┘   └──────────────────────┘
   └────────────────┘
```

| Componente | Rol |
|---|---|
| **PostgreSQL** | Datos transaccionales: cursos, NRC, horarios, inscripciones, historial académico y matriz de materias pendientes. |
| **MongoDB** | Catálogo de materias y **reglas de negocio dinámicas** (prerrequisitos) que administra el Admin. |
| **Neo4j** | Grafo `(:Curso)-[:PRERREQUIERE]->(:Curso)` sincronizado desde las reglas de MongoDB. |
| **Data Lake** | Ingesta de CSV en capas Bronze (crudo) y Silver (Parquet limpio) y carga idempotente a PostgreSQL. |
| **Backend** | API REST en FastAPI: oferta, inscripción, horario automático, administración de reglas y carga de CSV. |
| **Frontend** | SPA en React + Vite + Tailwind, con vista de **Estudiante** y vista de **Administrador**. |

Dentro de Docker el navegador solo habla con Nginx; Nginx reenvía `/api/` al
backend por la red interna `inscripcion_net`, así que no hace falta configurar CORS.

---

## Estructura del repositorio

```
.
├── backend/
│   ├── main.py              # API FastAPI (todos los endpoints)
│   ├── init_db.py           # Crea/verifica el esquema PostgreSQL (idempotente)
│   ├── requirements.txt
│   └── Dockerfile           # Build con contexto en la raíz del repo
├── frontend/
│   ├── src/
│   │   ├── App.jsx          # Pestañas Estudiante / Administrador
│   │   ├── api.js           # Cliente HTTP único (rutas relativas /api)
│   │   └── components/      # Login, OfertaTable, Horario, GenerarHorario,
│   │                        # MatrizEstudiante, SubirOferta, OfertaAdmin, AdminReglas…
│   ├── nginx.conf           # Sirve la SPA y hace proxy de /api
│   ├── vite.config.js       # Proxy /api → localhost:8000 en desarrollo
│   └── Dockerfile           # Build multi-etapa (Node → Nginx)
├── datalake/
│   ├── ingesta_datalake.py  # Ingesta CSV → Bronze → Silver → PostgreSQL
│   ├── etl_pipeline.py      # ETL analítico PostgreSQL → Bronze/Silver/Gold
│   ├── ejemplos/            # CSV de ejemplo
│   ├── README_INGESTA.md    # Formato detallado de los CSV
│   └── DATA_LAKE_README.md
├── pruebas/                 # CSV de prueba (oferta y matriz)
├── schema.sql               # Esquema relacional base
├── mongo_schema.js          # Colecciones, validadores e índices de MongoDB
├── neo4j_setup.cypher       # Carga inicial del grafo de prerrequisitos
├── seed_reglas.py           # Siembra las 28 materias del plan en reglas_negocio
├── actualizar_grafo.py      # Sincroniza Neo4j con reglas_negocio
├── docker-compose.yml       # Pila completa
└── INFORME_PROYECTO.md      # Informe técnico del proyecto
```

---

## Requisitos

- **Docker** y **Docker Compose v2** (recomendado).
- Para desarrollo local o scripts sueltos:
  - Python **3.10+** (la imagen usa 3.12).
  - Node.js **20+** y npm.

---

## Puesta en marcha con Docker

```bash
docker compose up -d --build
```

| Servicio | URL / Puerto | Credenciales |
|---|---|---|
| Frontend | http://localhost | — |
| API (Swagger) | http://localhost:8000/docs | — |
| PostgreSQL | `localhost:5432`, BD `universidad` | `postgres` / `postgres` |
| MongoDB | `mongodb://localhost:27017`, BD `inscripcion` | sin autenticación |
| Neo4j Browser | http://localhost:7474 (bolt `7687`) | `neo4j` / `password` |

Al arrancar, el backend:

1. Ejecuta `init_db.py`, que espera a PostgreSQL y crea las tablas que falten.
2. Intenta sembrar las reglas de prerrequisitos en MongoDB (`seed_reglas.py`);
   si Mongo no está listo, la API arranca de todos modos.
3. Levanta uvicorn en el puerto 8000.

Comandos útiles:

```bash
docker compose ps                 # estado de los servicios
docker compose logs -f backend    # logs de la API
docker compose down               # detener
docker compose down -v            # detener y BORRAR volúmenes (datos)
```

Volúmenes persistentes: `postgres_data`, `mongo_data`, `neo4j_data`,
`uploads_data`, `lake_bronze`, `lake_silver`.

---

## Desarrollo local sin Docker

Se necesitan PostgreSQL y MongoDB accesibles (se pueden levantar solo esos
servicios con `docker compose up -d postgres mongo neo4j`).

**Backend**

```bash
cd backend
python -m venv .venv
# Windows: .venv\Scripts\activate   |   Linux/macOS: source .venv/bin/activate
pip install -r requirements.txt
python init_db.py
uvicorn main:app --reload --port 8000
```

**Frontend**

```bash
cd frontend
npm install
npm run dev        # http://localhost:5173 (proxy de /api a localhost:8000)
```

Para apuntar a otra API, definir `VITE_API_URL` antes del build
(por defecto vacío ⇒ rutas relativas `/api`).

---

## Carga de datos (CSV)

El sistema **no usa datos ficticios**: la información entra por dos CSV. Hay
ejemplos en [pruebas/](pruebas/) y [datalake/ejemplos/](datalake/ejemplos/).

### `oferta_academica.csv` — una fila por sesión de horario

```csv
codigo_curso,nombre_curso,creditos,id_nrc,jornada,modalidad,cupo_max,numero_sesion,dia,hora_inicio,hora_fin
CAL-101,Cálculo I,4,1001,Diurna,Presencial,30,1,1,07:00,08:30
```

`dia` va de 1 (lunes) a 7; las horas en formato `HH:MM`.

### `matriz_estudiante.csv` — una fila por (estudiante, materia)

```csv
estudiante_id,carrera,semestre,estado,codigo_curso,nombre_curso
1,Ingeniería de Sistemas,3,aprobada,CAL-101,Cálculo I
```

- `estado` `aprobada` / `vista` → tabla `historial`.
- `estado` `por_ver` / `pendiente` → tabla `matriz_por_ver`.
- También se acepta el formato antiguo `estudiante_id,codigo_curso` (todo se toma como aprobada).

### Formas de cargarlos

**Desde la interfaz web**

- *Vista Administrador* → subir la oferta (`POST /api/admin/upload-oferta`).
- *Vista Estudiante* → subir la matriz (`POST /api/estudiante/upload-matriz`).

**Desde la línea de comandos**

```bash
pip install pandas pyarrow psycopg2-binary

# Oferta + matriz (modo completo)
python datalake/ingesta_datalake.py --oferta pruebas/oferta_academica.csv --matriz pruebas/matriz_estudiante.csv

# Solo oferta (no toca historial) / solo matriz (no toca la oferta)
python datalake/ingesta_datalake.py --solo-oferta --oferta pruebas/oferta_academica.csv
python datalake/ingesta_datalake.py --solo-matriz --matriz pruebas/matriz_estudiante.csv

# Procesar Bronze/Silver sin conectarse a PostgreSQL
python datalake/ingesta_datalake.py --sin-bd
```

La ingesta es **idempotente**: ejecutarla dos veces con el mismo CSV deja el
mismo estado. El detalle de validaciones y salidas está en
[datalake/README_INGESTA.md](datalake/README_INGESTA.md).

---

## API REST

Documentación interactiva en http://localhost:8000/docs.

| Método | Ruta | Descripción |
|---|---|---|
| GET | `/` | Health check (no toca la BD) |
| GET | `/api/oferta` | NRC disponibles con materia, créditos, cupo, inscritos y horarios |
| POST | `/api/inscribir` | Inscripción manual (valida existencia, cupo, choque y duplicado) |
| GET | `/api/horario/{estudiante_id}` | Horario consolidado del estudiante |
| POST | `/api/horario/automatico` | Genera y guarda un horario sin choques |
| GET | `/api/estudiante/{estudiante_id}/matriz` | Resumen de aprobadas y pendientes |
| POST | `/api/estudiante/upload-matriz` | Sube el CSV de matriz del estudiante |
| POST | `/api/admin/upload-oferta` | Sube el CSV de oferta e ingesta a PostgreSQL |
| GET | `/api/admin/reglas` | Lista las reglas de prerrequisitos |
| PUT | `/api/admin/reglas` | Crea/actualiza reglas (upsert por materia) |

### Algoritmo de horario automático

Heurística *greedy* en `POST /api/horario/automatico`:

1. Lee las materias aprobadas del estudiante (`historial`).
2. Cruza el catálogo y las reglas de MongoDB para obtener las materias
   pendientes cuyos prerrequisitos ya están cumplidos.
3. Obtiene los NRC con cupo y sus sesiones desde PostgreSQL.
4. Ordena las materias elegibles y asigna el primer NRC que no choque con lo
   ya asignado, sin exceder cupos.
5. Guarda las inscripciones resultantes.

---

## Modelo de datos

### PostgreSQL

| Tabla | Columnas clave |
|---|---|
| `estudiante` | `id`, `nombre`, `plan_id` |
| `curso` | `codigo_alfanumerico`, `nombre`, `creditos` |
| `nrc` | `id_nrc`, `curso_id` → curso, `jornada`, `modalidad`, `cupo_max` |
| `horario` | `id`, `nrc_id` → nrc, `numero_sesion`, `dia`, `hora_inicio`, `hora_fin` |
| `inscripcion` | (`estudiante_id`, `nrc_id`), `fecha` |
| `historial` | (`estudiante_id`, `codigo_curso`) — materias aprobadas |
| `estudiante_perfil` | carrera y semestre del estudiante |
| `matriz_por_ver` | materias pendientes por estudiante |

Las cinco primeras están en [schema.sql](schema.sql); el resto las crea
[backend/init_db.py](backend/init_db.py) o la ingesta. `schema.sql` incluye
también una consulta de ejemplo para detectar choques de horario.

### MongoDB ([mongo_schema.js](mongo_schema.js))

| Colección | Documento |
|---|---|
| `catalogo_materias` | `{codigo, nombre, creditos, semestre, prerrequisitos[], plan}` |
| `reglas_negocio` | `{materia, prerrequisitos[], nombre?, creditos?, semestre?}` |

### Neo4j ([neo4j_setup.cypher](neo4j_setup.cypher))

Nodos `:Curso` y relaciones `(curso)-[:PRERREQUIERE]->(prerrequisito)`.

---

## Scripts auxiliares

| Script | Uso | Dependencias |
|---|---|---|
| [seed_reglas.py](seed_reglas.py) | Siembra las 28 materias del plan en `reglas_negocio` sin sobrescribir reglas editadas (`$setOnInsert`). | `pymongo` |
| [actualizar_grafo.py](actualizar_grafo.py) | Sincroniza Neo4j con `reglas_negocio` (crea, borra y limpia relaciones huérfanas). Con `SYNC_CATALOGO=1` copia también nombre/créditos/semestre. | `pymongo`, `neo4j` |
| [datalake/etl_pipeline.py](datalake/etl_pipeline.py) | ETL analítico: lee PostgreSQL y genera Bronze (JSON), Silver (Parquet) y Gold (`demanda_cursos.parquet`). | `pandas`, `pyarrow`, `psycopg2-binary` |
| [backend/init_db.py](backend/init_db.py) | Crea/verifica todas las tablas de PostgreSQL. Seguro de ejecutar varias veces. | `psycopg2-binary` |

Ejemplo:

```bash
pip install pymongo neo4j
python seed_reglas.py
python actualizar_grafo.py
```

Para la carga inicial del grafo, ejecutar `neo4j_setup.cypher` desde Neo4j Browser.

---

## Variables de entorno

| Variable | Default | Usada por |
|---|---|---|
| `DB_HOST` / `DB_PORT` | `localhost` / `5432` | backend, init_db, Data Lake |
| `DB_NAME` | `universidad` | backend, init_db, Data Lake |
| `DB_USER` / `DB_PASSWORD` | `postgres` / `postgres` | backend, init_db, Data Lake |
| `MONGO_URI` | `mongodb://localhost:27017` | backend, seed_reglas, actualizar_grafo |
| `MONGO_DB` | `inscripcion` | backend, seed_reglas, actualizar_grafo |
| `MONGO_COL_CATALOGO` | `catalogo_materias` | backend |
| `MONGO_COL_REGLAS` | `reglas_negocio` | backend, seed_reglas |
| `CORS_ORIGINS` | (vacío) | backend — orígenes extra separados por coma |
| `UPLOADS_DIR` | `backend/uploads` | backend — carpeta de CSV subidos |
| `INGESTA_SCRIPT` | autodetectado | backend — ruta a `ingesta_datalake.py` |
| `INGESTA_TIMEOUT` | `600` | backend — segundos máximos de la ingesta |
| `NEO4J_URI` | `bolt://localhost:7687` | actualizar_grafo |
| `NEO4J_USER` / `NEO4J_PASSWORD` | `neo4j` / `password` | actualizar_grafo |
| `SYNC_CATALOGO` | `0` | actualizar_grafo |
| `VITE_API_URL` | (vacío ⇒ `/api` relativo) | frontend (en tiempo de build) |

> Las credenciales por defecto son solo para desarrollo. Cámbialas antes de
> desplegar en cualquier entorno compartido.

---

## Solución de problemas

| Síntoma | Causa probable / solución |
|---|---|
| `relation "estudiante" does not exist` | El volumen de Postgres ya existía y `schema.sql` no se ejecutó. Reinicia el backend: `init_db.py` crea las tablas faltantes. |
| El frontend dice "No se pudo conectar con el backend" | Revisa `docker compose ps` y `docker compose logs backend`; el frontend espera a que el backend esté *healthy*. |
| 502 en `/api/...` | El backend se reinició; Nginx re-resuelve el nombre en ~10 s. Si persiste, `docker compose restart frontend`. |
| La oferta o el horario salen vacíos | Aún no se han cargado los CSV. Ver [Carga de datos](#carga-de-datos-csv). |
| El horario automático no asigna materias | El estudiante no tiene matriz cargada, faltan reglas en `reglas_negocio` o no hay NRC con cupo. |
| Error 413 al subir CSV | El límite de Nginx es 10 MB (`client_max_body_size` en `frontend/nginx.conf`). |

---

## Documentación adicional

- [INFORME_PROYECTO.md](INFORME_PROYECTO.md): informe técnico completo y decisiones de diseño.
- [datalake/README_INGESTA.md](datalake/README_INGESTA.md): contrato de los CSV y del pipeline de ingesta.
- [datalake/DATA_LAKE_README.md](datalake/DATA_LAKE_README.md): arquitectura Medallón del Data Lake.
