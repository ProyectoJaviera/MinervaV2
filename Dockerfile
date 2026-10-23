# Minerva -- imagen Docker (Fase 6, docs/historial/FASE6_PLAN.md). PAPER TRADING: nunca ordenes reales.
#
# Tres etapas: (1) compila el frontend, (2) instala las dependencias de Python en un venv con las
# versiones EXACTAS de docker/requirements.lock, (3) imagen final sin Node, sin compiladores y sin cache.
#
# Esta imagen NO lleva secretos: `.env` queda fuera (.dockerignore) y se monta de solo lectura en
# /app/.env en tiempo de ejecucion (ver docker-compose.yml). Tampoco lleva datos (data/, logs/) ni docs/.
#
#   docker compose build            # construir
#   docker compose up -d            # arrancar (ver docs/GUIA_INSTALACION.md antes del primer uso)

ARG NODE_VERSION=24
ARG PYTHON_VERSION=3.11

# ---------------------------------------------------------------------------------------------------
# Etapa 1: build del frontend (React + Vite). `npm run build` = tsc --noEmit && vite build.
# ---------------------------------------------------------------------------------------------------
FROM node:${NODE_VERSION}-slim AS frontend
WORKDIR /frontend
COPY frontend/package.json frontend/package-lock.json ./
RUN npm ci --no-audit --no-fund
COPY frontend/ ./
RUN npm run build

# ---------------------------------------------------------------------------------------------------
# Etapa 2: dependencias de Python. Solo ruedas binarias (--only-binary): si alguna version fijada no
# tuviera rueda para Linux, el build falla aqui en vez de intentar compilar algo.
# ---------------------------------------------------------------------------------------------------
FROM python:${PYTHON_VERSION}-slim AS python-deps
ENV PIP_DISABLE_PIP_VERSION_CHECK=1 \
    PIP_NO_CACHE_DIR=1
RUN python -m venv /opt/venv
COPY docker/requirements.lock /tmp/requirements.lock
RUN /opt/venv/bin/pip install --only-binary=:all: -r /tmp/requirements.lock

# ---------------------------------------------------------------------------------------------------
# Etapa 3: imagen final. El codigo vive en /app porque `app/main.py` y `app/trading/verdict_record.py`
# resuelven `frontend/dist` y `docs/` RELATIVO a la ubicacion del paquete: el proyecto NO se instala en
# site-packages, se ejecuta desde /app.
# ---------------------------------------------------------------------------------------------------
FROM python:${PYTHON_VERSION}-slim AS runtime

ARG APP_UID=10001
ARG APP_GID=10001

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PATH="/opt/venv/bin:${PATH}" \
    DATABASE_PATH=/app/data/minerva_estabilidad.db \
    LOG_DIR=/app/logs

# Certificados raiz opcionales (p. ej. la inspeccion TLS de una red corporativa): todo `*.crt` de
# docker/certs/ se agrega al almacen de confianza del sistema, que es el que lee `truststore`
# (app/core/http.py). Sin certificados es un no-op. Ver docker/certs/README.md.
COPY docker/certs/ /tmp/extra-certs/
RUN find /tmp/extra-certs -maxdepth 1 -name '*.crt' -exec cp {} /usr/local/share/ca-certificates/ \; \
    && update-ca-certificates \
    && rm -rf /tmp/extra-certs

# Usuario sin privilegios. SOLO /app/data y /app/logs son suyos (un volumen vacio hereda ese dueno);
# el codigo de /app pertenece a root y es de solo lectura para el: un proceso comprometido no puede
# reescribir la aplicacion. Hallazgo de la prueba real de permisos (Fase 7): antes todo /app era del
# usuario y el proceso podia modificar su propio codigo.
RUN groupadd --system --gid ${APP_GID} minerva \
    && useradd --system --uid ${APP_UID} --gid minerva --home-dir /app --shell /usr/sbin/nologin minerva \
    && mkdir -p /app/data /app/logs /app/docs \
    && chown minerva:minerva /app/data /app/logs

COPY --from=python-deps /opt/venv /opt/venv

WORKDIR /app
COPY app/ ./app/
COPY scripts/ ./scripts/
COPY docker/healthcheck.py ./docker/healthcheck.py
COPY --from=frontend /frontend/dist ./frontend/dist

USER minerva

# Dentro del contenedor el servidor escucha en 0.0.0.0 (asi funciona el port-forwarding de Docker); el
# aislamiento real lo da el enlace "127.0.0.1:" del lado del host en docker-compose.yml.
EXPOSE 8000

# `python -m uvicorn` pone /app en sys.path (importa `app` desde aqui). La forma "exec" hace que uvicorn sea
# PID 1 y reciba SIGTERM de `docker stop`: el cierre ordenado del lifespan (tareas, base de datos).
CMD ["python", "-m", "uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]

# Sano = /health responde y el scheduler avanza (o esta en pausa deliberada). start-period largo: el primer
# arranque descubre el universo y baja velas, y /health sale "stale" hasta el primer ciclo.
HEALTHCHECK --interval=60s --timeout=10s --start-period=300s --retries=3 \
    CMD ["python", "docker/healthcheck.py"]
