# syntax=docker/dockerfile:1

# ---------------------------------------------------------------------------
# FRONTEND BUILD STAGE
# Nuxt static generate — the resulting files are served by the backend.
# ---------------------------------------------------------------------------
FROM oven/bun AS frontend-build
WORKDIR /frontend_build
COPY CheckCheck/frontend /frontend_build
# open-fetch reads the API schema from ../openapi.json (see nuxt.config.ts). With
# the frontend at /frontend_build that resolves to /openapi.json, so the schema
# must land there or type generation fails with ENOENT '/openapi.json'.
COPY CheckCheck/openapi.json /openapi.json
# Bake the build version into the client bundle so the sidebar can show the
# actually-running client version (Nuxt maps NUXT_PUBLIC_CLIENT_VERSION →
# runtimeConfig.public.clientVersion, inlined at `nuxi generate` time). Same
# APP_VERSION build-arg the backend stage uses; CI overrides it (git/tag version).
ARG APP_VERSION=0.0.1
ENV NUXT_PUBLIC_CLIENT_VERSION=$APP_VERSION
RUN bun install && bun run build && bunx nuxi generate

# ---------------------------------------------------------------------------
# BACKEND BUILD + RUNTIME STAGE
# ---------------------------------------------------------------------------
FROM python:3.13 AS backend

ARG APPNAME=DZDCheckCheck
ARG MODULENAME=checkcheckserver

# Injected by CI (see .github/workflows). APP_VERSION defaults to 0.0.1 so a
# plain `docker build` still produces a valid version; CI overrides it with the
# git-derived (dev) or release-tag version. LOG_LEVEL becomes the image default
# and is read at runtime via the LOG_LEVEL env var.
ARG APP_VERSION=0.0.1
ARG LOG_LEVEL=INFO

RUN python3 -m pip install --upgrade pip pip-tools

# Static frontend produced by the stage above.
COPY --from=frontend-build /frontend_build/.output/public /app
ENV DOCKER_MODE=1
ENV FRONTEND_FILES_DIR=/app

RUN mkdir -p /opt/$APPNAME/$MODULENAME
WORKDIR /opt/$APPNAME

# Resolve + install backend dependencies from pyproject.toml.
COPY CheckCheck/backend/pyproject.toml /opt/$APPNAME/$MODULENAME/
RUN pip-compile -o /opt/$APPNAME/requirements.txt /opt/$APPNAME/$MODULENAME/pyproject.toml
RUN pip install -U -r /opt/$APPNAME/requirements.txt

# Install the application.
COPY CheckCheck/backend/checkcheckserver /opt/$APPNAME/$MODULENAME

# Alembic migration scripts. They live beside the package in the repo
# (backend/migrations). At runtime _init_db.py resolves them relative to the
# package parent — /opt/$APPNAME/migrations in this image — so they must be
# copied there or start-up migrations crash with "Path doesn't exist".
COPY CheckCheck/backend/migrations /opt/$APPNAME/migrations
# Alembic config file. DB_MIGRATION_ALEMBIC_CONFIG_FILE defaults to /alembic.ini
# in this image layout (script_location and sqlalchemy.url are overridden at
# runtime; this mainly supplies the logging configuration).
COPY alembic.ini /alembic.ini

# Stamp the version instead of shipping the .git folder. BOTH names are
# required: checkcheckserver/__init__.py also imports __version_git_branch__ and
# only guards ModuleNotFoundError, so a partial file would crash on boot.
RUN printf "__version__ = '%s'\n__version_git_branch__ = None\n" "$APP_VERSION" \
    > /opt/$APPNAME/$MODULENAME/__version__.py

# Default provisioning data (nothing is loaded unless
# APP_PROVISIONING_DATA_YAML_FILES points at a file here).
COPY CheckCheck/backend/provisioning_data /provisioning

RUN mkdir -p /data/db

WORKDIR /opt/$APPNAME/$MODULENAME

# Runtime configuration.
# Bind on all interfaces so a reverse proxy on the docker network can reach the
# app (the in-container healthcheck hits 127.0.0.1, so a localhost bind would
# still report healthy while Traefik gets a 502).
ENV SERVER_BIND_HOST=0.0.0.0
ENV LOG_LEVEL=${LOG_LEVEL}
ENV APP_PROVISIONING_DATA_YAML_FILES='[]'
ENV SQL_DATABASE_URL=sqlite+aiosqlite:////data/db/local.sqlite
# Optional YAML config. Mount a file here (or set the settings as env vars); a
# missing file is ignored, and env vars override anything in it.
ENV CHECKCHECK_CONFIG_FILE=/config/config.yml

LABEL org.opencontainers.image.title="CheckCheck" \
      org.opencontainers.image.version="${APP_VERSION}" \
      org.opencontainers.image.source="https://github.com/motey/checkcheck"

# Container health probe. Hits the unauthenticated /api/health endpoint, which
# performs a real DB round-trip. That endpoint returns HTTP 200 even when the DB
# is down (with {"healthy": false}), so we parse the body and require healthy ==
# true — a green status here means "app is up AND the database is reachable".
# The port is read from SERVER_BIND_PORT (default 8181) so the probe follows
# a reconfigured port. Uses stdlib urllib only — no curl dependency. start-period
# covers first-boot schema creation + migrations; tune it up for slow databases.
HEALTHCHECK --interval=30s --timeout=10s --start-period=40s --retries=3 \
    CMD ["python", "-c", "import os,sys,json,urllib.request; p=os.environ.get('SERVER_BIND_PORT','8181'); r=urllib.request.urlopen('http://127.0.0.1:%s/api/health' % p, timeout=8); sys.exit(0 if json.load(r).get('healthy') is True else 1)"]

ENTRYPOINT ["python", "./main.py"]
