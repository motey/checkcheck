# Deployment

CheckCheck ships as a single image, [`motey/checkcheck`](https://hub.docker.com/r/motey/checkcheck),
that serves both the web UI and the REST API on port `8181`. This page covers
running it in production: compose files, PostgreSQL, reverse proxies, and
backups. For the one-line quick start see the [README](../README.md); for
settings see [configuration.md](configuration.md).

## Image tags

| Tag | What it is |
|---|---|
| `latest` | Newest stable release. |
| `beta` | Newest pre-release. |
| `dev` | Latest build from `main` (bleeding edge). |
| `<version>` | A specific release, for example `0.1.0`. Pin this in production. |

## Data that must persist

Two things hold state you do not want to lose:

- The PostgreSQL database (see [the database section](#database)). Back its
  volume up.
- `/config/config.yml` is the optional config file. Mount it read-only if you
  use one; env vars work just as well.

Exports are written under the container's working directory (`EXPORT_CACHE_DIR`,
default `./export_cache`); mount a volume there too if you rely on exports.

## docker-compose

Run CheckCheck against PostgreSQL. PostgreSQL is the backend for any real
deployment (see [the database section](#database) below):

```yaml
services:
  checkcheck:
    image: motey/checkcheck:latest
    restart: unless-stopped
    ports:
      - "8181:8181"
    depends_on:
      - db
    environment:
      SERVER_SESSION_SECRET: "replace-with-64+-random-chars"
      AUTH_JWT_SECRET: "replace-with-a-different-64+-random-string"
      ADMIN_USER_PW: "pick-a-strong-password"
      SERVER_PUBLIC_URL: "https://checklists.example.com"
      SQL_DATABASE_URL: "postgresql+asyncpg://checkcheck:secret@db:5432/checkcheck"

  db:
    image: postgres:16
    restart: unless-stopped
    environment:
      POSTGRES_USER: checkcheck
      POSTGRES_PASSWORD: secret
      POSTGRES_DB: checkcheck
    volumes:
      - checkcheck-db:/var/lib/postgresql/data

volumes:
  checkcheck-db:
```

Generate each secret once with `openssl rand -hex 32`. The schema is created and
migrated automatically on start.

## Database

Point `SQL_DATABASE_URL` at PostgreSQL for any real deployment:

```yaml
SQL_DATABASE_URL: postgresql+asyncpg://checkcheck:secret@db:5432/checkcheck
```

The image also has a bundled SQLite fallback so it can boot with zero setup, but
that is meant for local development only, is single-process, and is on track to
be removed. Do not run a real instance on it. The developer setup covers it in
[CheckCheck/backend/README.md](../CheckCheck/backend/README.md).

## Reverse proxy and real-time sync

Clients learn about changes over a Server-Sent Events stream at `GET /api/sync`,
which the reverse proxy must not buffer. Response buffering breaks the live
stream and clients stop seeing updates until they reconnect. In nginx, disable
proxy buffering for that path (`proxy_buffering off;`) and allow long read
timeouts.

Real-time events fan out between server processes through a PostgreSQL
`pg_notify` channel, so you can run multiple workers safely.

Set `SERVER_PUBLIC_URL` to the external URL users reach the app on (scheme
included, e.g. `https://checklists.example.com`). Every absolute URL — OIDC
redirect URIs, the CORS origin — is built from it, and the session cookie's
`Secure` flag is derived from its scheme, so an HTTPS public URL needs no further
cookie tuning. The app binds internally on `SERVER_BIND_HOST`/`SERVER_BIND_PORT`
(`0.0.0.0:8181` in the image); point your proxy there.

The app also honours proxy headers (`X-Forwarded-Proto`, `X-Forwarded-For`) from
trusted upstreams (`SERVER_TRUSTED_PROXIES`, default `*`) for client-IP logging.
These no longer affect the OIDC redirect URI, which is pinned to
`SERVER_PUBLIC_URL` — so a spoofed forwarded header cannot divert a login. If the
container is ever reachable directly (not only through the proxy), narrow
`SERVER_TRUSTED_PROXIES` to your proxy's IP.

## Backups

Back up the database on a schedule (`pg_dump`, or snapshot the volume).

**Restoring a backup is not a neutral operation.** Clients track how far they
have synced with a monotonic counter. A restored database has that counter
rewound, so connected clients are ahead of the server. Reads self-heal (the
client detects the reset and does a full resync), but writes a client had queued
while offline can be partially dropped, and those users see a "server was reset"
notice. Plan restores for a quiet window, and never manually reset the internal
sync counter on a live database.

## Upgrading

Read [UPGRADING.md](UPGRADING.md) before pulling a new tag, and keep a backup.
The database schema is versioned with Alembic and migrated on start. Installs
that predate the 2.0 schema baseline must recreate their database once; this is
called out in the upgrade notes.

## Building the image yourself

From the repository root:

```bash
docker build -t checkcheck:local .
```

The build compiles the frontend and assembles the backend in one multi-stage
`Dockerfile`. `build_docker.sh` wraps this with the version stamping that CI
uses.
