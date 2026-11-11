# INFORME_PROYECTO — Sistema de Inscripción de Materias (Data-Driven)

> Proyecto políglota: **PostgreSQL** · **Python** · **MongoDB** · **Neo4j** · **React**
> Arquitecto/PM: agente principal · Ejecución delegada a subagentes especializados.

---

## 1. Cambio de paradigma: de datos ficticios a Data-Driven

El proyecto dejó de depender de scripts que generaban datos falsos. Ahora la
información entra por **archivos CSV reales** cargados por el administrador y el
**Data Lake es el punto de entrada principal**.

### Scripts de datos ficticios eliminados
| Archivo | Estado |
|---|---|
| `seed_data.py` | ❌ **ELIMINADO** (verificado) |
| `mongo_seed.js` | ❌ **ELIMINADO** (verificado) |

La única fuente de verdad de la oferta y de los aprobados son los CSV; el
catálogo de materias vive en MongoDB y las reglas de prerrequisitos los
administra el Admin.

---

## 2. Arquitectura Data-Driven: Ingesta CSV, Horario Automático y Admin

### 2.1 Flujo end-to-end

```
  Admin                    Data Lake                         Motores
 ┌───────────────┐   ┌──────────────────────┐   ┌──────────────────────────┐
 │ oferta_       │──▶│ ingesta_datalake.py  │──▶│ PostgreSQL (curso, nrc,  │
 │ academica.csv │   │  Bronze → Silver     │   │ horario, historial)      │
 │ matriz_       │   │  (Parquet)           │   └──────────────────────────┘
 │ estudiante.csv│   └──────────────────────┘
 └───────────────┘
 ┌───────────────┐   ┌──────────────────────┐   ┌──────────────────────────┐
 │ Admin (API)   │──▶│ reglas_negocio       │──▶│ Neo4j [:PRERREQUIERE]    │
 │ prerrequisitos│   │ (MongoDB)            │   │ actualizar_grafo.py      │
 └───────────────┘   └──────────────────────┘   └──────────────────────────┘
 ┌───────────────┐   ┌──────────────────────┐   ┌──────────────────────────┐
 │ Estudiante    │──▶│ POST /api/horario/   │──▶│ Inscripción automática   │
 │ (SPA React)   │   │ automatico           │   │ sin choques ni sobrecupo │
 └───────────────┘   └──────────────────────┘   └──────────────────────────┘
```

### 2.2 Data Lake — Ingesta y Procesamiento

**Archivos:** [`datalake/ingesta_datalake.py`](./datalake/ingesta_datalake.py) · [`datalake/README_INGESTA.md`](./datalake/README_INGESTA.md)

`ingesta_datalake.py` (pandas + pyarrow + psycopg2) recibe dos CSV:

| CSV | Contenido | Columnas requeridas |
|---|---|---|
| `oferta_academica.csv` | Una fila por **sesión de horario** (curso + NRC + sesión) | `codigo_curso, nombre_curso, creditos, id_nrc, jornada, modalidad, cupo_max, numero_sesion, dia, hora_inicio, hora_fin` |
| `matriz_estudiante.csv` | Una fila por (estudiante, materia aprobada) | `estudiante_id, codigo_curso` |

Pasos del pipeline (arquitectura Medallón):

1. **BRONZE** → copia 1:1 de los CSV crudos en `datalake/bronze/`.
2. **SILVER** → limpieza/normalización (textos sin espacios, `Int64`, horas
   `HH:MM:SS`, deduplicación, validación de día 1..7) y guardado en Parquet:
   `curso.parquet`, `nrc.parquet`, `horario.parquet`, `historial.parquet`.
3. **POSTGRESQL** → inserción idempotente de la oferta en `curso`, `nrc` y
   `horario`; la matriz de aprobados en la nueva tabla **`historial`**.

**Tabla `historial`** (creada por el propio script con `CREATE TABLE IF NOT EXISTS`,
porque el esquema original no la tenía):

```sql
CREATE TABLE IF NOT EXISTS historial (
    estudiante_id INTEGER NOT NULL,
    codigo_curso  VARCHAR(10) NOT NULL,
    PRIMARY KEY (estudiante_id, codigo_curso)
);
```

Sin FKs a propósito: la matriz puede incluir materias aprobadas que hoy no se
ofertan. Carga idempotente (DELETE + INSERT). Modo `--sin-bd` para procesar sin
tocar PostgreSQL. Detalle del formato de CSV en
[`README_INGESTA.md`](./datalake/README_INGESTA.md).

### 2.3 PostgreSQL

**Archivo:** [`schema.sql`](./schema.sql)

Esquema relacional base (sin cambios en las 5 tablas originales):

| Tabla | Columnas clave |
|---|---|
| `Estudiante` | id, nombre, plan_id |
| `Curso` | codigo_alfanumerico, nombre, creditos |
| `NRC` | id_nrc, curso_id, jornada, modalidad, cupo_max |
| `Horario` | id, nrc_id, numero_sesion, dia, hora_inicio, hora_fin |
| `Inscripcion` | estudiante_id, nrc_id, fecha (PK compuesta) |
| `historial` | estudiante_id, codigo_curso (PK compuesta, creada por la ingesta) |

Se mantiene la consulta de detección de **choque de horario** (mismo día +
rangos solapados) al final de `schema.sql`.

### 2.4 MongoDB

**Archivo:** [`mongo_schema.js`](./mongo_schema.js)

Crea dos colecciones **vacías** con validadores e índices (idempotente):

| Colección | Rol | Documento |
|---|---|---|
| `catalogo_materias` | Plan de estudios; la llena el Data Lake / el Admin | `{codigo, nombre, creditos, semestre, prerrequisitos[], plan}` |
| `reglas_negocio` | Prerrequisitos **dinámicos** que administra el Admin | `{materia, prerrequisitos[]}` |

Índices únicos: `uq_catalogo_codigo` y `uq_reglas_materia`.

### 2.5 Neo4j — Grafo sincronizado con las reglas

**Archivos:** [`actualizar_grafo.py`](./actualizar_grafo.py) · [`neo4j_setup.cypher`](./neo4j_setup.cypher)

- `neo4j_setup.cypher` se conserva como carga inicial del grafo (28 nodos
  `:Curso` + relaciones `[:PRERREQUIERE]`).
- `actualizar_grafo.py` (pymongo + driver neo4j) lee `reglas_negocio` y
  **sincroniza** las relaciones `(curso)-[:PRERREQUIERE]->(prerrequisito)`:
  borra las que sobran, crea las que faltan (`MERGE`), y elimina relaciones
  huérfanas de materias sin regla. `reglas_negocio` es la fuente de verdad del
  grafo; con `SYNC_CATALOGO=1` además copia nombre/créditos/semestre desde
  `catalogo_materias`.

### 2.6 Backend — Horario automático y Admin

**Archivos:** [`backend/main.py`](./backend/main.py) · [`backend/requirements.txt`](./backend/requirements.txt) · [`backend/Dockerfile`](./backend/Dockerfile)

API FastAPI (PostgreSQL con psycopg2 + MongoDB con pymongo). Endpoints:

| Método | Ruta | Función |
|---|---|---|
| GET | `/api/oferta` | NRCs con materia, créditos, cupo/inscritos y horarios |
| POST | `/api/inscribir` | Inscripción manual con validaciones (existe, cupo, choque, duplicado) |
| GET | `/api/horario/{estudiante_id}` | Horario consolidado del estudiante |
| **POST** | **`/api/horario/automatico`** | **Genera y guarda un horario sin choques** |
| **GET** | **`/api/admin/reglas`** | **Lista reglas de prerrequisitos** |
| **PUT** | **`/api/admin/reglas`** | **Crea/actualiza reglas (upsert por materia)** |

**Algoritmo del horario automático** (heurístico/greedy, `POST /api/horario/automatico`):

1. Lee las materias **aprobadas** del estudiante desde `historial`.
2. Lee el **catálogo** (`catalogo_materias`) y las **reglas** (`reglas_negocio`)
   de MongoDB, y calcula qué materias faltan y cuáles son elegibles
   (prerrequisitos satisfechos).
3. Lee los NRC **con cupo** y sus sesiones desde PostgreSQL.
4. Ordena las elegibles (semestre, NRCs disponibles, créditos) y asigna el
   primer NRC que **no choque** con lo ya seleccionado.
5. Persiste en `Inscripcion` re-validando cupo y choque dentro de una
   transacción. Devuelve `{horario, no_asignadas, resumen}`.

**Degradación elegante:** si MongoDB no responde o `catalogo_materias` está
vacío, el catálogo se deriva de la tabla `curso` de PostgreSQL
(`catalogo_desde_postgres()`), y si no hay reglas se asume "sin prerrequisitos".
Así el generador sigue funcionando incluso antes de poblar Mongo.

### 2.7 Frontend — Vistas de Estudiante y Administrador

**Archivos:** [`frontend/src/App.jsx`](./frontend/src/App.jsx) · [`frontend/src/components/GenerarHorario.jsx`](./frontend/src/components/GenerarHorario.jsx) · [`frontend/src/components/AdminReglas.jsx`](./frontend/src/components/AdminReglas.jsx)

SPA React 18 + Tailwind 3 + Vite 5 con un toggle en el header:

- **Vista Estudiante:** botón grande **“Generar mi Horario Automático”** →
  `POST /api/horario/automatico`; tabla con materia, día, hora inicio/fin y NRC
  (las `sesiones` anidadas del backend se aplanan a filas); aviso de materias no
  asignadas. Conserva el login por ID y la oferta manual.
- **Vista Administrador:** tabla de materias con sus prerrequisitos (chips con
  “×” para quitar) + formulario para agregar; consume `GET` y `PUT
  /api/admin/reglas` enviando el arreglo completo de reglas.

### 2.8 Orquestación — Docker Compose

**Archivo:** [`docker-compose.yml`](./docker-compose.yml)

| Servicio | Imagen/build | Puerto(s) | Notas |
|---|---|---|---|
| `backend` | `./backend` | 8000 | `DB_*` + `MONGO_URI=mongodb://mongo:27017` |
| `frontend` | `./frontend` → Nginx | 80 | proxy `/api` al backend |
| `postgres` | `postgres:16-alpine` | 5432 | monta `schema.sql` + healthcheck |
| `mongo` | `mongo:7` | 27017 | monta `mongo_schema.js` (colecciones vacías) |
| `neo4j` | `neo4j:5` | 7474/7687 | `neo4j/password`; grafo vía `actualizar_grafo.py` |

> El compose ya **no** monta `mongo_seed.js` (eliminado); monta `mongo_schema.js`.

---

## 3. Reglas de negocio implementadas

- **Prerrequisitos dinámicos:** una materia solo es elegible si todas sus
  materias previas están en `historial` del estudiante; la regla se lee de
  `reglas_negocio` (Admin) y, si no existe, del catálogo.
- **Cupo:** no se asigna un NRC cuyo número de inscritos alcance `cupo_max`
  (validado al asignar y re-validado dentro de la transacción de inserción).
- **Sin choque de horario:** se rechaza/asigna otro NRC si comparte día y sus
  intervalos `[hora_inicio, hora_fin)` se solapan.
- **Sin duplicados:** PK compuesta `(estudiante_id, nrc_id)` en `Inscripcion`.
- **Idempotencia de ingesta:** Bronze/Silver sobrescriben; PostgreSQL usa
  upsert/DELETE+INSERT.
- **Reglas editables sin código:** el Admin cambia prerrequisitos por API y el
  grafo Neo4j se resincroniza con `actualizar_grafo.py`.

---

## 4. Correcciones de integración aplicadas por el PM

Al verificar los entregables se detectaron y corrigieron incompatibilidades
entre subagentes:

| # | Problema | Corrección |
|---|---|---|
| 1 | El backend leía `historial.curso_id`, pero la ingesta crea `historial.codigo_curso` (el horario automático fallaría) | Consulta y docstring del backend alineados a `codigo_curso` |
| 2 | `catalogo_materias` puede estar vacío (la ingesta solo llena PostgreSQL) | Fallback `catalogo_desde_postgres()` + reglas opcionales |
| 3 | `pymongo` faltaba en `backend/requirements.txt` | Añadido |
| 4 | `docker-compose.yml` montaba el eliminado `mongo_seed.js` | Ahora monta `mongo_schema.js` + `MONGO_URI` al backend |
| 5 | El backend devuelve horario con `sesiones` anidadas; la tabla del frontend esperaba filas planas | El frontend aplana `sesiones` a una fila por sesión |

---

## 5. Estado de archivos

| Capa | Archivo(s) | Estado |
|---|---|---|
| PostgreSQL | [`schema.sql`](./schema.sql) | ✅ |
| Data Lake | [`datalake/ingesta_datalake.py`](./datalake/ingesta_datalake.py) · [`datalake/README_INGESTA.md`](./datalake/README_INGESTA.md) | ✅ |
| MongoDB | [`mongo_schema.js`](./mongo_schema.js) | ✅ |
| Neo4j | [`actualizar_grafo.py`](./actualizar_grafo.py) · [`neo4j_setup.cypher`](./neo4j_setup.cypher) | ✅ |
| Backend | [`backend/main.py`](./backend/main.py) · [`backend/init_db.py`](./backend/init_db.py) · [`backend/requirements.txt`](./backend/requirements.txt) | ✅ |
| Frontend | [`frontend/`](./frontend/) · [`src/api.js`](./frontend/src/api.js) · [`OfertaAdmin.jsx`](./frontend/src/components/OfertaAdmin.jsx) · [`SubirOferta.jsx`](./frontend/src/components/SubirOferta.jsx) · [`MatrizEstudiante.jsx`](./frontend/src/components/MatrizEstudiante.jsx) · [`AdminReglas.jsx`](./frontend/src/components/AdminReglas.jsx) · [`GenerarHorario.jsx`](./frontend/src/components/GenerarHorario.jsx) · [`Spinner.jsx`](./frontend/src/components/Spinner.jsx) | ✅ |
| Reglas seed | [`seed_reglas.py`](./seed_reglas.py) (28 materias, `$setOnInsert`) | ✅ |
| Orquestación | [`docker-compose.yml`](./docker-compose.yml) (red + volúmenes de uploads) | ✅ |
| Ejemplos CSV | [`datalake/ejemplos/oferta_academica.csv`](./datalake/ejemplos/oferta_academica.csv) · [`pruebas/oferta_academica.csv`](./pruebas/oferta_academica.csv) · [`pruebas/matriz_estudiante.csv`](./pruebas/matriz_estudiante.csv) | ✅ |
| Eliminados | `seed_data.py` · `mongo_seed.js` | 🗑️ |

Validación estática realizada: `python3 -m py_compile` OK en
`backend/main.py`, `datalake/ingesta_datalake.py` y `actualizar_grafo.py`;
`docker-compose.yml` parsea como YAML válido.

---

## 6. Cómo levantar y operar

```bash
# 1) Levantar la pila
docker compose up -d --build

# 2) Cargar los CSV reales (colócalos en datalake/)
pip install pandas pyarrow psycopg2-binary
python datalake/ingesta_datalake.py
#    (o --sin-bd para solo Bronze/Silver; ver README_INGESTA.md)

# 3) Reglas de prerrequisitos (Admin, vía API o directamente en MongoDB)
#    GET/PUT http://localhost:8000/api/admin/reglas

# 4) Sincronizar el grafo Neo4j con las reglas
pip install pymongo neo4j
python actualizar_grafo.py
```

- Frontend: `http://localhost:80`
- Backend / Swagger: `http://localhost:8000/docs`

---

## 7. Limitaciones y próximos pasos

1. **Sincronización de `catalogo_materias` desde el Data Lake:** la ingesta
   actual llena PostgreSQL; el catálogo de Mongo queda vacío salvo que el Admin
   lo cargue. El backend ya degrada a PostgreSQL, pero el siguiente paso natural
   es que la ingesta haga *upsert* del catálogo en `catalogo_materias`.
2. **Ejecución real:** no se levantaron los motores en este entorno; la
   validación fue de sintaxis/contrato. Falta una prueba end-to-end con Docker.
3. **Neo4j:** la primera carga del grafo sigue siendo manual
   (`neo4j_setup.cypher`); luego `actualizar_grafo.py` mantiene la sincronía.
4. **Seguridad/roles:** no hay autenticación real; el panel Admin es solo una
   vista (adecuado para el alcance académico actual).

---

## 8. Recuperación de la fábrica (turno final) — ✅ EXITOSA

Tras un corte por saldo que dejó dos subagentes a medias, se relanzaron **3
subagentes de recuperación en paralelo** (Backend, Data Lake y Frontend). Todos
completaron y sus entregables se verificaron en disco.

### 8.1 Backend — API terminada y CORS corregido
[`backend/main.py`](./backend/main.py) (965 líneas) quedó completo y validado
(`py_compile` OK). Endpoints confirmados:

`GET /` · `GET /api/oferta` · `POST /api/inscribir` ·
`GET /api/horario/{id}` · `POST /api/horario/automatico` ·
`GET /api/admin/reglas` · `PUT /api/admin/reglas`

- **CORS explícito** (ya no `"*"`): `http://localhost:3000`,
  `http://localhost:5173`, `http://localhost`, `http://localhost:80` y las
  variantes `127.0.0.1`. Admite ampliar con la variable `CORS_ORIGINS`.
  Esto **resuelve el bloqueo que provocaba el “Failed to fetch”** cuando la SPA
  se sirve desde Nginx en `http://localhost:80`.
- Admin con `upsert` por materia sobre `reglas_negocio`, normalización a
  mayúsculas, deduplicación y validación `422` si la materia viene vacía.
- `historial.codigo_curso` consistente con la ingesta.
- `backend/requirements.txt` incluye `pymongo`.

### 8.2 Data Lake — ingesta terminada
[`datalake/ingesta_datalake.py`](./datalake/ingesta_datalake.py) (668 líneas,
`py_compile` OK) + [`README_INGESTA.md`](./datalake/README_INGESTA.md)
(347 líneas) + CSV de ejemplo en [`datalake/ejemplos/`](./datalake/ejemplos/).

- Bug corregido de formato de rutas (`Path` con especificador `<28`).
- Validación temprana de CSV (faltantes/vacíos/columnas), UTF-8 → latin-1.
- Limpieza robusta: códigos ≤ 10, horas válidas, descarte avisado de filas
  inválidas y de NRC/Horario huérfanos (integridad referencial).
- Prueba funcional real en `--sin-bd`: 3 cursos / 4 NRC / 6 sesiones / 5
  aprobados; Parquet correcto; segunda ejecución idempotente.

### 8.3 Frontend — error de conexión solucionado y vista Admin
[`frontend/src/api.js`](./frontend/src/api.js) centraliza **todas** las
llamadas (`API_BASE = http://localhost:8000`), con `try/catch`, timeout de 20 s
(`AbortController`) y mensajes amigables: *“No se pudo conectar con el backend.
Verifica que la API esté en http://localhost:8000.”*

- [`App.jsx`](./frontend/src/App.jsx): pestañas **“Vista Estudiante”** y
  **“Vista Administrador”** en la cabecera, banner global con *Reintentar*.
- [`AdminReglas.jsx`](./frontend/src/components/AdminReglas.jsx): tabla que
  consume `GET /api/admin/reglas`, chips con “×” para quitar prerrequisitos y
  formulario que hace `PUT /api/admin/reglas` con el arreglo completo.
  Se deshabilita mientras carga para no sobrescribir reglas con datos parciales.
- [`GenerarHorario.jsx`](./frontend/src/components/GenerarHorario.jsx): botón
  **“Generar mi Horario Automático”**, aplana `sesiones` a filas y lista las no
  asignadas con su motivo.

### 8.4 Orquestación verificada
[`docker-compose.yml`](./docker-compose.yml) parsea como YAML válido y levanta
`backend` (8000) + `frontend` (80) + `postgres` (5432) + `mongo` (27017) +
`neo4j` (7474/7687). El comentario obsoleto de `seed_data.py` se eliminó.

---

## 9. Corrección de red Docker — «Failed to fetch» — ✅ RESUELTO

### 9.1 Causa raíz
El frontend se compilaba con la URL **absoluta** `http://localhost:8000`. Al
abrir la SPA desde otro origen (Harness, otro host/puerto o un túnel), el
navegador intentaba alcanzar *su propio* `localhost:8000`, no el backend del
contenedor, y la petición fallaba con `Failed to fetch`.

### 9.2 Solución: mismo origen + proxy interno de Nginx
Se eliminó la dependencia de una URL absoluta. Ahora el navegador solo habla con
el frontend y Nginx reenvía `/api/` al servicio `backend` por la red interna.

| Archivo | Cambio |
|---|---|
| [`frontend/src/api.js`](./frontend/src/api.js) | `API_BASE` se toma de `VITE_API_URL`; por defecto **`''`** → rutas relativas `/api/...` |
| [`frontend/nginx.conf`](./frontend/nginx.conf) | `location /api/` → `proxy_pass http://backend:8000` con `resolver 127.0.0.11` (DNS interno Docker) y `proxy_http_version 1.1` |
| [`frontend/vite.config.js`](./frontend/vite.config.js) | Proxy de desarrollo `/api` → `http://localhost:8000` (mismo comportamiento que Nginx) |
| [`frontend/Dockerfile`](./frontend/Dockerfile) | `ARG/ENV VITE_API_URL` (vacío por defecto) antes de `npm run build` |
| [`docker-compose.yml`](./docker-compose.yml) | Red explícita **`inscripcion_net`** para los 5 servicios; build arg `VITE_API_URL=""`; healthcheck del backend; `frontend` espera a `backend` sano |
| [`backend/Dockerfile`](./backend/Dockerfile) | Verificado: `uvicorn --host 0.0.0.0 --port 8000` (escucha en todas las interfaces) |

### 9.3 Por qué funciona ahora
```
Navegador ──(http://localhost, mismo origen)──▶ Nginx (frontend)
                                                   │  /api/*  (red interna)
                                                   ▼
                                             backend:8000 (FastAPI)
```
- No hay peticiones cross-origin → **no depende de CORS** ni de qué host use el usuario.
- `VITE_API_URL` permite, si algún día se despliega el frontend en otro dominio,
  apuntar a una API externa sin tocar el código (se define al construir la imagen).
- El `resolver` de Nginx evita el error `502` si el contenedor `backend` se
  reinicia y cambia de IP.

### 9.4 Cómo probar
```bash
cd /home/diego/Documentos/InscripcionMaterias
docker compose down          # limpia contenedores/red previos
docker compose up -d --build # reconstruye con la nueva config
docker compose ps
```
Luego abrir **http://localhost** (o `http://127.0.0.1`) y pulsar **Entrar** con
un ID. Verificación directa del proxy:
```bash
curl -i http://localhost/api/oferta
curl -i http://localhost:8000/api/oferta
```
Ambas deben responder `200` con JSON.

---

## 10. Flujo de negocio con carga de archivos — ✅ IMPLEMENTADO

La interfaz ahora alimenta el backend con **CSV reales** en lugar de depender de
scripts manuales. El flujo completo es: *Admin sube oferta → Estudiante sube su
matriz → se genera el horario automático*.

### 10.1 Nuevos endpoints

| Método | Ruta | Entrada | Salida |
|---|---|---|---|
| POST | `/api/admin/upload-oferta` | multipart `archivo` (CSV) | `{ok, archivo, cursos, nrcs, sesiones, salida}` |
| POST | `/api/estudiante/upload-matriz` | multipart `archivo` + `estudiante_id` (opcional) | `{ok, estudiante_id, carrera, semestre, aprobadas[], por_ver[], total_aprobadas, total_por_ver}` |
| GET | `/api/estudiante/{id}/matriz` | — | mismo resumen; `404` si no hay matriz |

**Upload de oferta:** guarda el CSV en `UPLOADS_DIR` (`/app/uploads` en Docker),
ejecuta `datalake/ingesta_datalake.py --oferta <archivo> --solo-oferta` y
devuelve los conteos. El modo `--solo-oferta` refresca `curso`/`nrc`/`horario`
pero **no toca `historial`** (no borra las materias aprobadas de los estudiantes).

**Upload de matriz:** crea si faltan `estudiante_perfil` y `matriz_por_ver`,
hace upsert del estudiante y **reemplaza solo** sus aprobadas (`historial`) y
pendientes (`matriz_por_ver`) dentro de una transacción.

### 10.2 Contratos CSV

`oferta_academica.csv` (una fila por sesión):

```csv
codigo_curso,nombre_curso,creditos,id_nrc,jornada,modalidad,cupo_max,numero_sesion,dia,hora_inicio,hora_fin
```

`matriz_estudiante.csv`:

```csv
estudiante_id,carrera,semestre,estado,codigo_curso,nombre_curso
```

`estado` ∈ `{aprobada, vista}` → historial; `{por_ver, pendiente}` → pendientes.

### 10.3 Reglas de negocio precargadas

[`seed_reglas.py`](./seed_reglas.py) siembra las **28 materias** de Ingeniería de
Sistemas en `reglas_negocio` con `$setOnInsert` (no sobrescribe ediciones del
Admin). El backend lo invoca en el **startup** de FastAPI con reintentos; si
Mongo no está listo, solo registra un warning y la API arranca igual.

El generador automático sigue leyendo las aprobadas de `historial` y los cupos
de `nrc`; si existe `matriz_por_ver`, prioriza esas materias.

### 10.4 Frontend

- **Admin:** [`SubirOferta.jsx`](./frontend/src/components/SubirOferta.jsx)
  (input `file`, POST multipart, tarjetas con cursos/NRC/sesiones) y
  [`AdminReglas.jsx`](./frontend/src/components/AdminReglas.jsx) ahora **solo
  edita/elimina prerrequisitos de materias existentes** (dos `<select>`, sin
  crear materias nuevas).
- **Estudiante:** [`MatrizEstudiante.jsx`](./frontend/src/components/MatrizEstudiante.jsx)
  sube la matriz y muestra carrera, semestre, aprobadas y pendientes. El botón
  **“Generar mi Horario Automático”** queda deshabilitado hasta que exista matriz
  (`matrizCargada`); al entrar se consulta `GET /api/estudiante/{id}/matriz`.
- **Subidas:** `apiUpload()` en [`api.js`](./frontend/src/api.js) usa `FormData`
  sin fijar `Content-Type` (el navegador pone el boundary). Nginx admite hasta
  10 MB (`client_max_body_size 10m`).

### 10.5 Persistencia y Docker

[`docker-compose.yml`](./docker-compose.yml):

- `backend` se construye desde la **raíz** (`context: .`) para copiar
  `datalake/ingesta_datalake.py` y `seed_reglas.py`.
- Variables `UPLOADS_DIR=/app/uploads`.
- Volúmenes persistentes: **`uploads_data`** (CSV subidos), **`lake_bronze`** y
  **`lake_silver`** (capas del Data Lake).
- `.dockerignore` evita enviar `node_modules`, capas del lake y cachés.

### 10.6 Prueba end-to-end (CSV incluidos)

Hay un juego de datos listo en [`pruebas/`](./pruebas/):

| Archivo | Contenido | Se sube desde |
|---|---|---|
| [`pruebas/oferta_academica.csv`](./pruebas/oferta_academica.csv) | 6 cursos / 6 NRC / 10 sesiones | Vista Administrador → “Subir Oferta Académica” |
| [`pruebas/matriz_estudiante.csv`](./pruebas/matriz_estudiante.csv) | Estudiante 1 con 3 aprobadas y 3 por ver | Vista Estudiante → “Cargar Matriz de Estudio” |

Orden: **levantar → subir oferta → entrar como estudiante 1 → subir matriz →
generar horario**. Resultado esperado: 3 materias asignadas (CAL-201, PRO-201,
FIS-101) sin choques, porque las 3 aprobadas satisfacen sus prerrequisitos.

---

## 11. Corrección de errores de BD y mejora UX/UI — ✅ RESUELTO

### 11.1 «relation estudiante does not exist»

**Causa raíz:** `schema.sql` está montado en `/docker-entrypoint-initdb.d` de
PostgreSQL, que **solo se ejecuta cuando el volumen de datos está vacío**. Si el
volumen `postgres_data` ya existía de una ejecución anterior, la base quedaba
sin tablas y el upload de la matriz fallaba.

**Solución implementada:**

- Nuevo [`backend/init_db.py`](./backend/init_db.py): DDL **idempotente**
  (`CREATE TABLE IF NOT EXISTS`) de las 8 tablas —
  `estudiante`, `curso`, `nrc`, `horario`, `inscripcion`, `historial`,
  `estudiante_perfil`, `matriz_por_ver` — más 7 índices. Reintenta la conexión
  hasta 60 s y **no borra datos**.
- **Entrypoint del contenedor** ([`backend/Dockerfile`](./backend/Dockerfile) y
  [`docker-compose.yml`](./docker-compose.yml)):
  `sh -c "python init_db.py && exec uvicorn main:app --host 0.0.0.0 --port 8000"`.
  Así las tablas existen **antes** de aceptar peticiones.
- El `startup` de FastAPI también invoca `inicializar_bd()` como red de
  seguridad (2 intentos acotados, sin bloquear el arranque).
- Si aun así falta una tabla, `upload-matriz` devuelve `503` con un mensaje
  claro en vez de un 500 genérico.

### 11.2 Ingesta: modos explícitos (no lee archivos no subidos)

[`datalake/ingesta_datalake.py`](./datalake/ingesta_datalake.py) ahora tiene tres
modos mutuamente excluyentes:

| Modo | Lee | Escribe | No toca |
|---|---|---|---|
| `--solo-oferta` | solo `oferta_academica.csv` | `curso`, `nrc`, `horario` (+ Bronze/Silver) | `historial`, `estudiante_perfil`, `matriz_por_ver` |
| `--solo-matriz` | solo `matriz_estudiante.csv` | `estudiante` (upsert), `historial`, `estudiante_perfil`, `matriz_por_ver` | `curso`, `nrc`, `horario` |
| completo | ambos | todo | — |

- `--solo-oferta` y `--solo-matriz` se declaran en un
  `add_mutually_exclusive_group()`; si falta el archivo del modo, error claro y
  `exit 1` **sin abrir el otro CSV**.
- `--solo-matriz` acepta el formato enriquecido
  (`estudiante_id,carrera,semestre,estado,codigo_curso,nombre_curso`) y el
  antiguo (`estudiante_id,codigo_curso`), crea el estudiante si no existe y
  reemplaza solo los datos de los estudiantes presentes, en una transacción.
- Ambos modos imprimen `RESUMEN_JSON: {...}` para que la API muestre conteos.

### 11.3 Mejora de interfaz (UX/UI)

- **Admin:** nueva tabla grande [`OfertaAdmin.jsx`](./frontend/src/components/OfertaAdmin.jsx)
  que consume `GET /api/oferta` (NRC, materia, créditos, jornada, horario,
  cupos con barra) y **se refresca automáticamente** al terminar la subida del
  CSV (`refreshKey`). Reglas rediseñadas como **tarjetas por materia** con chips
  y dos `<select>` (solo materias existentes).
- **Estudiante:** flujo en dos pasos visuales —
  **Paso 1 · Obligatorio** cargar matriz (con tarjetas de resumen: carrera,
  semestre, aprobadas, por ver) y **Paso 2** botón grande
  **“Generar Horario Automático”** habilitado solo con matriz.
- **Feedback:** componente [`Spinner.jsx`](./frontend/src/components/Spinner.jsx)
  y loaders en subidas, carga de reglas/oferta y generación; errores amigables
  con `Banner` y botón *Reintentar*.

### 11.4 Cómo verificar la corrección

```bash
cd /home/diego/Documentos/InscripcionMaterias
docker compose down
docker compose up -d --build
docker compose logs -f backend    # debe mostrar "Tablas verificadas: 8/8"
```

Luego: Admin sube [`pruebas/oferta_academica.csv`](./pruebas/oferta_academica.csv)
→ aparece la tabla de oferta; Estudiante 1 sube
[`pruebas/matriz_estudiante.csv`](./pruebas/matriz_estudiante.csv) → resumen y
botón habilitado → genera horario. Ya no debe aparecer
`relation "estudiante" does not exist`.





