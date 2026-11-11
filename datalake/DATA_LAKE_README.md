# Data Lake — Pipeline ETL (arquitectura Medallón)

Este directorio contiene el pipeline analítico del **Sistema de Inscripción de
Materias**. Sigue la arquitectura **Medallón (Bronze → Silver → Gold)**, el
patrón estándar de los data lakes modernos para separar datos crudos,
depurados y agregados.

## Estructura de carpetas

```
datalake/
├── etl_pipeline.py        # pipeline ETL (pandas + pyarrow + psycopg2)
├── DATA_LAKE_README.md    # este documento
├── bronze/                # datos crudos (JSON), tal cual vienen de PostgreSQL
│   ├── curso.json
│   ├── estudiante.json
│   ├── nrc.json
│   ├── horario.json
│   └── inscripcion.json
├── silver/                # datos limpios y tipados (Parquet)
│   ├── curso.parquet
│   ├── estudiante.parquet
│   ├── nrc.parquet
│   ├── horario.parquet
│   └── inscripcion.parquet
└── gold/                  # datos agregados listos para analítica
    └── demanda_cursos.parquet
```

## Qué hace cada capa

| Capa | Formato | Contenido | Objetivo |
|---|---|---|---|
| **Bronze** | JSON | Copia 1:1 de las 5 tablas de PostgreSQL (`curso`, `estudiante`, `nrc`, `horario`, `inscripcion`) | Preservar el dato crudo e inmutable, como respaldo y auditoría |
| **Silver** | Parquet | Mismos datos **limpios**: textos sin espacios, columnas numéricas tipadas (`Int64`), fechas como `datetime` y horas normalizadas a `HH:MM:SS` | Obtener datos consistentes y tipados, listos para consultas analíticas |
| **Gold** | Parquet | `demanda_cursos.parquet`: una fila por NRC con el número de inscripciones, enriquecida con curso (código, nombre, créditos) y jornada/modalidad/cupo | Agregaciones de negocio listas para dashboards y reporting |

## La tabla Gold: `demanda_cursos.parquet`

| Columna | Descripción |
|---|---|
| `nrc_id` | Identificador del NRC |
| `curso_id` | Código del curso (`CAL-101`, `BDA-401`, ...) |
| `nombre` | Nombre de la materia |
| `creditos` | Créditos del curso |
| `jornada` / `modalidad` | Jornada (Diurna/Nocturna/Mixta) y modalidad (Presencial/Virtual/Híbrida) |
| `cupo_max` | Cupo máximo del NRC |
| `total_inscripciones` | **Cuántas inscripciones recibió el NRC** (la métrica de demanda) |

Está ordenada de mayor a menor demanda, lo que permite responder directamente
"¿cuáles NRCs son los más demandados?".

## Requisitos

```bash
pip install pandas pyarrow psycopg2-binary
```

## Configuración

El pipeline lee la conexión a PostgreSQL de variables de entorno (con valores
por defecto para desarrollo):

```bash
export DB_HOST=localhost
export DB_PORT=5432
export DB_NAME=universidad
export DB_USER=postgres
export DB_PASSWORD=postgres
```

## Ejecución

```bash
# Desde la raíz del proyecto:
python datalake/etl_pipeline.py
```

Las carpetas `bronze/`, `silver/` y `gold/` se crean automáticamente. Ejecuta
`python seed_data.py` primero para poblar PostgreSQL (y, si deseas datos de
demanda, activa `GENERAR_INSCRIPCIONES = True` en `seed_data.py`).
