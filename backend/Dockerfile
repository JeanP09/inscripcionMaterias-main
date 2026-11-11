# =============================================================================
# Imagen del backend (FastAPI + psycopg2 + ingesta del Data Lake).
#
# IMPORTANTE: el contexto de build es la RAÍZ del repo, no backend/.
# docker-compose.yml lo declara así:
#
#   build:
#     context: .
#     dockerfile: backend/Dockerfile
#
# Equivalente manual:
#   docker build -f backend/Dockerfile -t inscripcion-backend .
# =============================================================================
FROM python:3.12-slim

WORKDIR /app

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1

# 1. Dependencias primero (mejor uso de la caché de capas).
COPY backend/requirements.txt ./requirements.txt
RUN pip install --no-cache-dir -r requirements.txt

# 2. Código de la API, inicialización idempotente del esquema y seed de reglas
#    (raíz del repo -> /app). init_db.py crea las tablas si el volumen de
#    PostgreSQL ya existía y schema.sql no llegó a ejecutarse.
COPY backend/main.py ./main.py
COPY backend/init_db.py ./init_db.py
COPY seed_reglas.py ./seed_reglas.py

# 3. Script de ingesta en /app/datalake/ (la API lo localiza desde __file__:
#    /app/datalake/ingesta_datalake.py).
RUN mkdir -p /app/datalake
COPY datalake/ingesta_datalake.py ./datalake/ingesta_datalake.py

# 4. Directorios de escritura: uploads de CSV y capas Bronze/Silver.
#    docker-compose monta volúmenes persistentes sobre ellos.
RUN mkdir -p /app/uploads /app/datalake/bronze /app/datalake/silver

EXPOSE 8000

# 5. El esquema se crea/verifica ANTES de aceptar peticiones. Si init_db.py
#    falla (BD caída tras los reintentos) el contenedor no arranca, en lugar de
#    exponer una API con "relation ... does not exist".
CMD ["sh", "-c", "python init_db.py && exec uvicorn main:app --host 0.0.0.0 --port 8000"]
