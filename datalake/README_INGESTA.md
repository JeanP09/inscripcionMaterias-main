# README_INGESTA — Ingesta Data-Driven (CSV → Data Lake → PostgreSQL)

Este documento explica cómo el **administrador** debe preparar y cargar los CSV
que alimentan el Sistema de Inscripción de Materias, y qué hace el script
[`ingesta_datalake.py`](./ingesta_datalake.py).

> El proyecto dejó de usar datos ficticios (`seed_data.py`, `mongo_seed.js`).
> Ahora la fuente de verdad son **CSV reales** que entran por el Data Lake.

---

## 1. Qué hace el script

| Paso | Capa | Acción |
|---|---|---|
| 1 | **Bronze** | Copia 1:1 de los CSV crudos en `datalake/bronze/` (bytes originales) |
| 2 | **Silver** | Limpia/normaliza y guarda en Parquet en `datalake/silver/` |
| 3 | **PostgreSQL** | Carga idempotente de `curso`, `nrc`, `horario` y de la nueva tabla `historial` |

Archivos generados en `datalake/silver/`:

```
curso.parquet       # catálogo de materias (código, nombre, créditos)
nrc.parquet         # grupos/NRC ofertados (id, curso, jornada, modalidad, cupo)
horario.parquet     # sesiones de horario por NRC
historial.parquet   # materias aprobadas por estudiante
```

Cada ejecución **sobrescribe** los cuatro Parquet y las copias de `bronze/`
(no se acumulan versiones). El contrato exacto de columnas es:

| Archivo | Columnas, en orden | Tipos |
|---|---|---|
| `curso.parquet` | `codigo_alfanumerico`, `nombre`, `creditos` | texto, texto, entero |
| `nrc.parquet` | `id_nrc`, `curso_id`, `jornada`, `modalidad`, `cupo_max` | entero, texto, texto, texto, entero |
| `horario.parquet` | `nrc_id`, `numero_sesion`, `dia`, `hora_inicio`, `hora_fin` | entero, entero, entero, texto `HH:MM:SS`, texto `HH:MM:SS` |
| `historial.parquet` | `estudiante_id`, `codigo_curso` | entero, texto |

Los enteros usan el tipo nullable `Int64` de pandas (pueden ser nulos si el
origen venía incompleto, aunque las filas con llaves nulas se descartan).

---

## 2. Requisitos e instalación

Se requiere **Python 3.8 o superior**.

```bash
pip install pandas pyarrow psycopg2-binary
```

`psycopg2-binary` solo hace falta para cargar a PostgreSQL: el modo
`--sin-bd` funciona únicamente con `pandas` y `pyarrow`.

PostgreSQL debe tener aplicado el esquema de [`../schema.sql`](../schema.sql)
(las tablas `curso`, `nrc` y `horario`). La tabla `historial` la crea el propio
script automáticamente.

---

## 3. Configuración (variables de entorno)

```bash
export DB_HOST=localhost
export DB_PORT=5432
export DB_NAME=universidad
export DB_USER=postgres
export DB_PASSWORD=postgres
```

Estos son también los valores por defecto si no se exportan.

---

## 4. Ejecución

```bash
# Desde la raíz del proyecto, con las rutas por defecto
# (datalake/oferta_academica.csv y datalake/matriz_estudiante.csv):
python datalake/ingesta_datalake.py

# Con rutas explícitas:
python datalake/ingesta_datalake.py \
    --oferta /ruta/oferta_academica.csv \
    --matriz /ruta/matriz_estudiante.csv

# Solo procesar y escribir Bronze/Silver, sin tocar PostgreSQL (útil para probar):
python datalake/ingesta_datalake.py --sin-bd
```

### 4.1 Prueba sin PostgreSQL (`--sin-bd`)

El repositorio incluye dos CSV de ejemplo en `datalake/ejemplos/` que sirven
para validar el pipeline completo sin base de datos:

```bash
python datalake/ingesta_datalake.py --sin-bd \
    --oferta datalake/ejemplos/oferta_academica.csv \
    --matriz datalake/ejemplos/matriz_estudiante.csv
```

Salida esperada (resumida):

```
[1/3] BRONZE: copiando CSV crudos...
[2/3] SILVER: leyendo y limpiando...
   curso.parquet          OK (3 filas)
   nrc.parquet            OK (4 filas)
   horario.parquet        OK (6 filas)
   historial.parquet      OK (5 filas)
[3/3] Modo --sin-bd: no se conectó a PostgreSQL.
```

En este modo **no se importa psycopg2** y no se abre ninguna conexión. Para
comprobar los Parquet generados:

```bash
python - <<'PY'
import pandas as pd
for nombre in ("curso", "nrc", "horario", "historial"):
    df = pd.read_parquet(f"datalake/silver/{nombre}.parquet")
    print(nombre, df.shape, list(df.columns))
PY
```

> Los CSV de `ejemplos/` son solo material de prueba: no son datos reales y no
> se cargan por defecto (las rutas por defecto son `datalake/oferta_academica.csv`
> y `datalake/matriz_estudiante.csv`).

---

## 5. Formato de los CSV

Los archivos deben ser **UTF-8** (con o sin BOM); si la decodificación UTF-8
falla, el script reintenta automáticamente con **latin-1** (habitual en Excel de
Windows) y lo advierte. Deben estar separados por **coma (`,`)**, con
**primera fila de encabezados**. Los nombres de columna son exactos
(minúsculas y sin espacios); se recortan espacios accidentales alrededor del
nombre. Las columnas adicionales que no se usan se ignoran.

Convenciones generales:

- Un campo vacío se interpreta como **nulo**.
- `codigo_curso` se recorta, se pasa a **MAYÚSCULAS** y debe tener **10
  caracteres como máximo** (`VARCHAR(10)`); las filas con códigos más largos se
  descartan con aviso.
- Los campos enteros aceptan texto numérico (`"40"`); los valores no numéricos
  o con decimales (`"4.5"`) se consideran inválidos y pasan a nulo.
- Los CSV pueden contener filas de datos repetidas: se deduplican tras
  normalizar.

### 5.1 `oferta_academica.csv` — cursos, NRCs, cupos y horarios

**Una fila por sesión de horario.** Las columnas del curso y del NRC se repiten
en cada sesión de ese NRC (formato desnormalizado/plano).

| Columna | Tipo | Obligatoria | Descripción |
|---|---|---|---|
| `codigo_curso` | texto (máx 10) | sí | Código de la materia → `Curso.codigo_alfanumerico` |
| `nombre_curso` | texto | sí | Nombre de la materia → `Curso.nombre` |
| `creditos` | entero ≥ 0 | sí | Créditos → `Curso.creditos` |
| `id_nrc` | entero | sí | Identificador del grupo/NRC → `NRC.id_nrc` |
| `jornada` | texto | sí | `Diurna` / `Nocturna` / `Mixta` → `NRC.jornada` |
| `modalidad` | texto | sí | `Presencial` / `Virtual` / `Híbrida` → `NRC.modalidad` |
| `cupo_max` | entero ≥ 0 | sí | Cupo máximo del NRC → `NRC.cupo_max` |
| `numero_sesion` | entero | sí | Número de la sesión (1, 2, 3…) → `Horario.numero_sesion` |
| `dia` | entero 1–7 | sí | Día de la semana: **1=lunes … 7=domingo** → `Horario.dia` |
| `hora_inicio` | `HH:MM` o `HH:MM:SS` | sí | Hora de inicio → `Horario.hora_inicio` |
| `hora_fin` | `HH:MM` o `HH:MM:SS` | sí | Hora de fin → `Horario.hora_fin` |

**Ejemplo de filas** (misma materia `CAL-101` en dos NRC; el NRC 1001 tiene dos
sesiones):

```csv
codigo_curso,nombre_curso,creditos,id_nrc,jornada,modalidad,cupo_max,numero_sesion,dia,hora_inicio,hora_fin
CAL-101,Cálculo I,4,1001,Diurna,Presencial,40,1,1,07:00,08:30
CAL-101,Cálculo I,4,1001,Diurna,Presencial,40,2,3,07:00,08:30
CAL-101,Cálculo I,4,1002,Nocturna,Virtual,35,1,2,18:30,20:00
BDA-301,Bases de Datos I,4,1003,Diurna,Presencial,30,1,4,10:00,11:30
```

Reglas que aplica el script:

- Los códigos se pasan a **mayúsculas** y se recortan espacios.
- Las horas se normalizan a `HH:MM:SS` (`07:00` → `07:00:00`). Se aceptan
  valores entre `00:00:00` y `23:59:59`; una hora vacía, con formato inválido o
  fuera de rango hace que **se descarte la sesión** (con aviso).
- Si `hora_inicio >= hora_fin`, la sesión se conserva pero se emite una
  advertencia para que el administrador la revise.
- `dia` debe estar entre 1 y 7 (**1 = lunes**). Un valor fuera de rango se
  conserva (la columna de PostgreSQL es `INTEGER`) pero se advierte.
- Créditos o `cupo_max` negativos se conservan y se advierten.
- Cada `id_nrc` debe apuntar a **un único** `codigo_curso` y tener una sola
  combinación de `jornada`/`modalidad`/`cupo_max`. Si hay conflicto se conserva
  la primera aparición y se emite una advertencia.
- Si a un curso le falta `nombre_curso` o `creditos`, el curso se descarta y
  **sus NRC también**; si a un NRC le faltan datos, sus sesiones de horario
  también se descartan. Así se evitan violaciones de las claves foráneas de
  `schema.sql`.
- Las filas de sesión exactamente duplicadas se eliminan.
- `numero_sesion` identifica la sesión dentro del NRC (1, 2, 3…); un mismo NRC
  puede tener varias sesiones en días distintos.

### 5.2 `matriz_estudiante.csv` — materias ya aprobadas

**Una fila por (estudiante, materia aprobada).**

| Columna | Tipo | Obligatoria | Descripción |
|---|---|---|---|
| `estudiante_id` | entero | sí | ID del estudiante → `Historial.estudiante_id` |
| `codigo_curso` | texto (máx 10) | sí | Código de la materia aprobada → `Historial.codigo_curso` |

**Ejemplo de filas:**

```csv
estudiante_id,codigo_curso
1001,CAL-101
1001,ALG-101
1002,CAL-101
1002,PRO-101
1003,BDA-301
```

Reglas que aplica el script:

- `estudiante_id` se convierte a entero; si es vacío, no numérico o decimal, la
  fila se descarta. Un ID `<= 0` se conserva pero se advierte.
- `codigo_curso` se pasa a **mayúsculas**, se recortan espacios y debe tener
  **10 caracteres como máximo**; si es más largo, la fila se descarta.
- Los pares `(estudiante_id, codigo_curso)` duplicados se eliminan tras la
  normalización.
- El código **no necesita existir** en la oferta vigente: puede ser una materia
  aprobada en un semestre anterior (por eso `historial` no tiene FK a
  `curso`).

---

## 6. Tabla nueva `Historial` (materias aprobadas)

El esquema actual no tiene dónde guardar las materias aprobadas, así que el
script la crea con `CREATE TABLE IF NOT EXISTS`:

```sql
CREATE TABLE IF NOT EXISTS Historial (
    estudiante_id INTEGER NOT NULL,
    codigo_curso  VARCHAR(10) NOT NULL,
    PRIMARY KEY (estudiante_id, codigo_curso)
);
CREATE INDEX IF NOT EXISTS idx_historial_curso ON Historial (codigo_curso);
```

**Decisiones de diseño (documentadas):**

1. **Sin claves foráneas** a `Estudiante` ni a `Curso`. La matriz puede
   referenciar estudiantes o materias que aún no están en el catálogo/oferta
   vigente (p. ej. materias aprobadas en semestres pasados que hoy no se
   ofertan). Un FK haría fallar la ingesta. La integridad se valida a nivel de
   aplicación o en un paso posterior de reconciliación.
2. **PK compuesta** `(estudiante_id, codigo_curso)`: evita duplicados y permite
   carga idempotente.
3. **No se crean filas en `Estudiante`**: el CSV no trae `nombre` ni `plan_id`,
   e inventarlos sería fabricar datos. Si se desea, la carga de estudiantes se
   puede hacer con otro CSV/script dedicado.

---

## 7. Idempotencia de la carga en PostgreSQL

| Tabla | Estrategia | Motivo |
|---|---|---|
| `curso` | **UPSERT** (`ON CONFLICT ... DO UPDATE`) | Actualiza nombre/créditos sin romper referencias existentes |
| `nrc` | **UPSERT** (`ON CONFLICT ... DO UPDATE`) | Igual: preserva `inscripcion` que referencia a `nrc` |
| `horario` | **Refresco total** (`DELETE` + `INSERT`) | `horario` no tiene claves foráneas entrantes: borrar es seguro |
| `historial` | **Refresco total** (`DELETE` + `INSERT`) | La matriz es la fuente autoritativa del registro académico |

Se evita `TRUNCATE ... CASCADE` para **no borrar la tabla `inscripcion`**
(inscripciones operativas del backend) ni otros datos.

Detalles de la idempotencia:

- Ejecutar el script dos veces con el mismo CSV produce el mismo estado final
  en `curso`, `nrc`, `horario` y `historial`.
- Los Parquet de `silver/` y las copias de `bronze/` se **sobrescriben** en
  cada corrida.
- El refresco de `horario` conserva el contenido lógico, pero los valores de la
  columna `id` (`SERIAL`) se regeneran porque se hace `DELETE` + `INSERT`.
  Ninguna tabla del esquema referencia `horario.id`, así que no se rompe nada.
- `historial` es la **fuente autoritativa**: las filas que no estén en el CSV
  se eliminan. Si se quiere conservar historial acumulado, hay que incluir todas
  las materias aprobadas en el CSV.
- `curso` y `nrc` se insertan/actualizan, pero **no se borran** los que ya no
  vienen en el CSV: podrían tener inscripciones asociadas en `inscripcion`.
  `horario` sí se refresca por completo, así que un NRC retirado conserva su
  fila en `nrc` pero se queda sin sesiones. Para sincronizar totalmente el
  catálogo hace falta un paso de reconciliación aparte.
- Toda la carga (DDL + DML) ocurre dentro de **una sola transacción**: ante
  cualquier error se ejecuta `ROLLBACK` y la base queda como estaba.

---

## 8. Archivos de ejemplo

`datalake/ejemplos/` contiene un fixture mínimo para validar el pipeline sin
PostgreSQL:

| Archivo | Contenido |
|---|---|
| [`ejemplos/oferta_academica.csv`](./ejemplos/oferta_academica.csv) | 3 cursos, 4 NRC y 6 sesiones de horario |
| [`ejemplos/matriz_estudiante.csv`](./ejemplos/matriz_estudiante.csv) | 5 pares (estudiante, materia aprobada) |

```bash
python datalake/ingesta_datalake.py --sin-bd \
    --oferta datalake/ejemplos/oferta_academica.csv \
    --matriz datalake/ejemplos/matriz_estudiante.csv
```

Resultado esperado: `curso.parquet` (3), `nrc.parquet` (4), `horario.parquet`
(6) e `historial.parquet` (5). Estos archivos son **solo de prueba**; no se
cargan por defecto.

---

## 9. Relación con el pipeline anterior

[`etl_pipeline.py`](./etl_pipeline.py) era el pipeline previo que **leía de
PostgreSQL** para construir el medallón (Bronze/Silver/Gold). Con el nuevo
enfoque Data-Driven, **la ingesta va en sentido contrario**: los CSV entran al
Data Lake (`ingesta_datalake.py`) y desde aquí se alimenta PostgreSQL.

`etl_pipeline.py` puede mantenerse únicamente para la capa **Gold** (agregados
analíticos como `demanda_cursos.parquet`) si se desea conservar ese reporte.

---

## 10. Solución de problemas

| Mensaje / síntoma | Causa y solución |
|---|---|
| `No se encontraron los CSV de entrada` | Revise `--oferta` / `--matriz` o deje los archivos en `datalake/oferta_academica.csv` y `datalake/matriz_estudiante.csv`. |
| `no tiene las columnas requeridas` | El encabezado debe tener exactamente los nombres de la sección 5 (minúsculas, sin espacios). |
| `está vacío o no tiene encabezados` | El CSV no tiene filas o está en blanco. |
| `La oferta académica no produjo cursos válidos` | Todos los cursos quedaron sin `codigo_curso`, `nombre_curso` o `creditos` válidos. Revise los avisos previos. |
| `psycopg2 no está instalado` | `pip install psycopg2-binary` o ejecute con `--sin-bd`. |
| `could not connect to server` | PostgreSQL no está levantado o `DB_HOST`/`DB_PORT`/`DB_USER`/`DB_PASSWORD` son incorrectos. |
| `violates foreign key constraint` | No debería ocurrir: el script filtra NRC/horarios huérfanos. Verifique que `schema.sql` esté aplicado y que no haya cambios manuales en las tablas. |

Validación rápida de sintaxis y ayuda:

```bash
python3 -m py_compile datalake/ingesta_datalake.py
python datalake/ingesta_datalake.py --help
```
