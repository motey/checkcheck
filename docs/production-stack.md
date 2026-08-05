# A full production stack

The CheckCheck image is only the app: a web UI backed by a REST API. A real
deployment needs more around it: TLS termination, single sign-on, and
scheduled database backups at a minimum. This document is one complete,
opinionated setup with all the trimmings, as a single Docker Compose project:

| Component | Image | Job |
|---|---|---|
| Traefik | `traefik:v3.5` | Reverse proxy; automatic HTTPS via Let's Encrypt |
| CheckCheck | `motey/checkcheck` | The app itself |
| PostgreSQL (CheckCheck) | `postgres:16` | Application database |
| Keycloak | `quay.io/keycloak/keycloak` | OpenID Connect identity provider |
| PostgreSQL (Keycloak) | `postgres:16` | Keycloak's own database |
| postgres-backup-local | `prodrigestivill/postgres-backup-local:16` | Scheduled `pg_dump` of both databases into `./backups` |

It is a reference, not a requirement: CheckCheck happily runs behind nginx,
Caddy, Authentik, or whatever you already operate (see
[deployment.md](deployment.md)). But if you are starting from a blank server,
this is a setup that works end to end.

```
Internet
   │ :80/:443
   ▼
Traefik ──── Let's Encrypt certificates
   ├── https://checklists.example.com ──► CheckCheck ──► PostgreSQL ─┐
   └── https://auth.example.com ────────► Keycloak ───► PostgreSQL ─┤
                                                                     ▼
                                          backup container ──► ./backups/
```

## Prerequisites

- A Linux host with Docker and the Compose plugin. Budget ~2 GB RAM; Keycloak
  is the hungry part.
- A domain, with two DNS records (A/AAAA) pointing at the host, e.g.
  `checklists.example.com` and `auth.example.com`.
- Ports 80 and 443 reachable from the internet (Let's Encrypt's TLS-ALPN
  challenge needs 443 inbound).
- SMTP relay credentials if you want email notifications (see
  [What is deliberately left out](#what-is-deliberately-left-out)).

Substitute the example domains and addresses below with your own everywhere.

## 1. Directory layout and secrets

```
checkcheck-stack/
├── .env                # all secrets and hostnames; gitignored
├── docker-compose.yml
├── config/
│   └── config.yml      # CheckCheck config; includes the OIDC client secret
└── backups/            # database dumps land here
```

Compose reads `.env` from the project directory automatically, both for
`${...}` interpolation in the compose file and as values you pass into
containers. Generate the secrets once:

```bash
openssl rand -hex 32   # one per secret below
```

`.env`:

```ini
# Domains
CHECKCHECK_DOMAIN=checklists.example.com
KEYCLOAK_DOMAIN=auth.example.com
ACME_EMAIL=you@example.com

# CheckCheck secrets
SERVER_SESSION_SECRET=<openssl rand -hex 32>
AUTH_JWT_SECRET=<a different openssl rand -hex 32>
ADMIN_USER_PW=<a strong password>

# PostgreSQL for CheckCheck
POSTGRES_DB=checkcheck
POSTGRES_USER=checkcheck
POSTGRES_PASSWORD=<openssl rand -hex 16>

# PostgreSQL for Keycloak
KEYCLOAK_DB_PASSWORD=<openssl rand -hex 16>

# Keycloak admin console
KEYCLOAK_ADMIN_USERNAME=admin
KEYCLOAK_ADMIN_PASSWORD=<a strong password>

# SMTP: only when EMAIL_ENABLED is true in config/config.yml
EMAIL_SMTP_PASSWORD=<from your mail provider>
```

## 2. docker-compose.yml

```yaml
services:
  traefik:
    image: traefik:v3.5
    restart: unless-stopped
    command:
      - --providers.docker=true
      - --providers.docker.exposedbydefault=false
      - --entrypoints.web.address=:80
      - --entrypoints.web.http.redirections.entrypoint.to=websecure
      - --entrypoints.web.http.redirections.entrypoint.scheme=https
      - --entrypoints.websecure.address=:443
      - --certificatesresolvers.letsencrypt.acme.email=${ACME_EMAIL}
      - --certificatesresolvers.letsencrypt.acme.storage=/letsencrypt/acme.json
      - --certificatesresolvers.letsencrypt.acme.tlschallenge=true
    ports:
      - "80:80"
      - "443:443"
    volumes:
      - /var/run/docker.sock:/var/run/docker.sock:ro
      - traefik-letsencrypt:/letsencrypt
    networks:
      proxy:
        ipv4_address: 172.28.0.10

  checkcheck:
    image: motey/checkcheck:0.4.0    # pin a release tag; see deployment.md#image-tags
    restart: unless-stopped
    depends_on:
      db:
        condition: service_healthy
    environment:
      SERVER_SESSION_SECRET: ${SERVER_SESSION_SECRET}
      AUTH_JWT_SECRET: ${AUTH_JWT_SECRET}
      ADMIN_USER_PW: ${ADMIN_USER_PW}
      SERVER_PUBLIC_URL: https://${CHECKCHECK_DOMAIN}
      SQL_DATABASE_URL: postgresql+asyncpg://${POSTGRES_USER}:${POSTGRES_PASSWORD}@db:5432/${POSTGRES_DB}
      # Only Traefik may set X-Forwarded-* headers (its fixed address above).
      SERVER_TRUSTED_PROXIES: "172.28.0.10"
      # Uncomment when you configure SMTP in config/config.yml:
      # EMAIL_SMTP_PASSWORD: ${EMAIL_SMTP_PASSWORD}
    volumes:
      - ./config/config.yml:/config/config.yml:ro
      - checkcheck-export-cache:/data/export_cache
    networks: [proxy, internal]
    labels:
      - traefik.enable=true
      - traefik.docker.network=proxy
      - traefik.http.routers.checkcheck.rule=Host(`${CHECKCHECK_DOMAIN}`)
      - traefik.http.routers.checkcheck.entrypoints=websecure
      - traefik.http.routers.checkcheck.tls=true
      - traefik.http.routers.checkcheck.tls.certresolver=letsencrypt
      - traefik.http.services.checkcheck.loadbalancer.server.port=8181

  db:
    image: postgres:16
    restart: unless-stopped
    environment:
      POSTGRES_DB: ${POSTGRES_DB}
      POSTGRES_USER: ${POSTGRES_USER}
      POSTGRES_PASSWORD: ${POSTGRES_PASSWORD}
    volumes:
      - checkcheck-db:/var/lib/postgresql/data
    networks: [internal]
    healthcheck:
      test: ["CMD-SHELL", "pg_isready -U $${POSTGRES_USER} -d $${POSTGRES_DB}"]
      interval: 10s
      timeout: 5s
      retries: 5

  keycloak:
    image: quay.io/keycloak/keycloak:26.3   # check for the current release
    restart: unless-stopped
    command: ["start"]
    environment:
      KC_BOOTSTRAP_ADMIN_USERNAME: ${KEYCLOAK_ADMIN_USERNAME}
      KC_BOOTSTRAP_ADMIN_PASSWORD: ${KEYCLOAK_ADMIN_PASSWORD}
      KC_DB: postgres
      KC_DB_URL_HOST: keycloak-db
      KC_DB_URL_DATABASE: keycloak
      KC_DB_USERNAME: keycloak
      KC_DB_PASSWORD: ${KEYCLOAK_DB_PASSWORD}
      KC_HOSTNAME: https://${KEYCLOAK_DOMAIN}
      KC_PROXY_HEADERS: xforwarded
      KC_HTTP_ENABLED: "true"
      KC_HEALTH_ENABLED: "true"
    depends_on:
      keycloak-db:
        condition: service_healthy
    networks: [proxy, internal]
    healthcheck:
      test: ["CMD-SHELL", "bash -c ':> /dev/tcp/127.0.0.1/9000' || exit 1"]
      interval: 15s
      timeout: 5s
      retries: 10
      start_period: 90s
    labels:
      - traefik.enable=true
      - traefik.docker.network=proxy
      - traefik.http.routers.keycloak.rule=Host(`${KEYCLOAK_DOMAIN}`)
      - traefik.http.routers.keycloak.entrypoints=websecure
      - traefik.http.routers.keycloak.tls=true
      - traefik.http.routers.keycloak.tls.certresolver=letsencrypt
      - traefik.http.services.keycloak.loadbalancer.server.port=8080

  keycloak-db:
    image: postgres:16
    restart: unless-stopped
    environment:
      POSTGRES_DB: keycloak
      POSTGRES_USER: keycloak
      POSTGRES_PASSWORD: ${KEYCLOAK_DB_PASSWORD}
    volumes:
      - keycloak-db:/var/lib/postgresql/data
    networks: [internal]
    healthcheck:
      test: ["CMD-SHELL", "pg_isready -U keycloak -d keycloak"]
      interval: 10s
      timeout: 5s
      retries: 5

  db-backup:
    image: prodrigestivill/postgres-backup-local:16
    restart: unless-stopped
    environment:
      POSTGRES_HOST: db
      POSTGRES_DB: ${POSTGRES_DB}
      POSTGRES_USER: ${POSTGRES_USER}
      POSTGRES_PASSWORD: ${POSTGRES_PASSWORD}
      SCHEDULE: "@daily"
      BACKUP_KEEP_DAYS: 7
      BACKUP_KEEP_WEEKS: 4
      BACKUP_KEEP_MONTHS: 6
    volumes:
      - ./backups/checkcheck:/backups
    networks: [internal]

  keycloak-db-backup:
    image: prodrigestivill/postgres-backup-local:16
    restart: unless-stopped
    environment:
      POSTGRES_HOST: keycloak-db
      POSTGRES_DB: keycloak
      POSTGRES_USER: keycloak
      POSTGRES_PASSWORD: ${KEYCLOAK_DB_PASSWORD}
      SCHEDULE: "@daily"
      BACKUP_KEEP_DAYS: 7
      BACKUP_KEEP_WEEKS: 4
      BACKUP_KEEP_MONTHS: 6
    volumes:
      - ./backups/keycloak:/backups
    networks: [internal]

networks:
  proxy:
    ipam:
      config:
        - subnet: 172.28.0.0/24
  internal:

volumes:
  traefik-letsencrypt:
  checkcheck-db:
  checkcheck-export-cache:
  keycloak-db:
```

Notes on the choices:

- **No published ports for CheckCheck or Keycloak.** They are only reachable
  through Traefik on the `proxy` network; databases and backups sit on the
  `internal` network only.
- **`SERVER_TRUSTED_PROXIES` is pinned to Traefik's fixed address**, so a
  spoofed `X-Forwarded-*` header cannot fake a client IP even if something
  else ever reaches the container directly.
- **Traefik does not buffer responses**, so CheckCheck's Server-Sent Events
  stream (`/api/sync`) works unchanged, no equivalent of nginx's
  `proxy_buffering off` needed.
- **The image already carries a healthcheck** (an unauthenticated
  `/api/health` probe that includes a database round-trip), so
  `docker compose ps` shows real readiness. That URL is also a good target
  for an uptime monitor: `https://checklists.example.com/api/health`.
- Both backup containers dump into `./backups` on the host, where any
  off-site sync tool of yours (rsync, restic, a cloud drive) can pick them up.

## 3. CheckCheck config/config.yml

Most secrets (session/JWT secrets, admin password, SMTP password) come from
`.env` as environment variables, which win over the file; the non-secret
shape lives here. The OIDC client secret is the exception: an environment
variable cannot override a single field inside the provider list, so it
lives in this file. The file never leaves the host. Treat it like a secret
and never commit it. See [configuration.md](configuration.md) for precedence
and every field.

```yaml
APP_NAME: CheckCheck

# Local login stays on as a break-glass for the bootstrap admin account.
# Flip to false once you trust the SSO path completely.
AUTH_BASIC_LOGIN_IS_ENABLED: true

AUTH_OIDC_PROVIDERS:
  - ENABLED: true
    PROVIDER_DISPLAY_NAME: Keycloak        # slug "keycloak" → see callback URI below
    CONFIGURATION_ENDPOINT: https://auth.example.com/realms/checkcheck/.well-known/openid-configuration
    CLIENT_ID: checkcheck
    CLIENT_SECRET: <paste from the Keycloak client's Credentials tab>
    SCOPES: [openid, profile, email, offline_access]
    AUTO_CREATE_AUTHORIZED_USER: true
    AUTO_LOGIN: true                       # go straight to Keycloak, skip the login form
    # Needs the groups mapper described in step 4:
    # ROLE_MAPPING:
    #   checkcheck-admins: [admin]

# Email notifications: point at any SMTP relay you trust.
# Delete this block (or set EMAIL_ENABLED: false) to keep mail off;
# in-app and push notifications work without it.
EMAIL_ENABLED: true
EMAIL_FROM_ADDRESS: checkcheck@example.com
EMAIL_SMTP_HOST: smtp.example.com
EMAIL_SMTP_PORT: 587
EMAIL_SMTP_SECURITY: starttls
EMAIL_SMTP_USER: checkcheck
# EMAIL_SMTP_PASSWORD is injected from .env

# Keep exports on a volume so they survive container recreation.
EXPORT_CACHE_DIR: /data/export_cache
```

If you do not want a config file at all, the same settings also fit into the
checkcheck service's `environment` block: scalar settings one variable each,
the provider list as one JSON value. Drop the `./config/config.yml` volume
mount in that case. This is what that looks like, commented out because the
YAML file above is a lot more readable:

```yaml
    # environment:
    #   APP_NAME: CheckCheck
    #   AUTH_BASIC_LOGIN_IS_ENABLED: "true"
    #   AUTH_OIDC_PROVIDERS: '[{"ENABLED": true, "PROVIDER_DISPLAY_NAME": "Keycloak", "CONFIGURATION_ENDPOINT": "https://auth.example.com/realms/checkcheck/.well-known/openid-configuration", "CLIENT_ID": "checkcheck", "CLIENT_SECRET": "<paste from the Keycloak client Credentials tab>", "SCOPES": ["openid", "profile", "email", "offline_access"], "AUTO_CREATE_AUTHORIZED_USER": true, "AUTO_LOGIN": true}]'
    #   EMAIL_ENABLED: "true"
    #   EMAIL_FROM_ADDRESS: checkcheck@example.com
    #   EMAIL_SMTP_HOST: smtp.example.com
    #   EMAIL_SMTP_PORT: "587"
    #   EMAIL_SMTP_SECURITY: starttls
    #   EMAIL_SMTP_USER: checkcheck
    #   EMAIL_SMTP_PASSWORD: ${EMAIL_SMTP_PASSWORD}
    #   EXPORT_CACHE_DIR: /data/export_cache
```

`SERVER_PUBLIC_URL` is set in the compose file, not here. It must match the
hostname in `CONFIGURATION_ENDPOINT` and the one you register in Keycloak:
all three are `https`, scheme included, or OIDC logins fail as a
redirect-URI mismatch.

## 4. Boot and set up Keycloak

CheckCheck refuses to start while a provider entry lacks its client secret,
so bring up the pieces Keycloak needs first, create the client, and start
the rest afterwards:

```bash
mkdir -p config backups
docker compose up -d traefik keycloak-db keycloak
docker compose ps      # give Keycloak a minute or two on first boot
```

Then configure the identity provider at `https://auth.example.com` (log in
with `KEYCLOAK_ADMIN_USERNAME` / `KEYCLOAK_ADMIN_PASSWORD`):

1. **Create a realm** named `checkcheck`.
2. **Create your users** in that realm (username + email; they set their own
   passwords on first login).
3. **Create a client**: Clients → Create client, Client ID `checkcheck`.
   - *Capability config*: enable **Client authentication** (Standard flow is
     already on; leave everything else off).
   - *Login settings*: Valid redirect URIs →
     `https://checklists.example.com/api/auth/oidc/callback/keycloak`,
     Web origins → `https://checklists.example.com`.
     The callback path is `/api/auth/oidc/callback/` plus the slug of the
     provider's display name (`"Keycloak"` becomes `keycloak`).
   - Save, open the **Credentials** tab, and paste the client secret into
     `CLIENT_SECRET` in `config/config.yml`.
4. (Optional, for `ROLE_MAPPING`) In the realm, create a group such as
   `checkcheck-admins`, put your admin in it, then add a groups claim:
   Client scopes → `checkcheck-dedicated` → Add mapper → *Group membership*,
   Token Claim Name `groups`, added to both ID and access token.
5. Start the rest of the stack: `docker compose up -d`.

No extra work is needed for `offline_access`: Keycloak grants it out of the
box, which is what keeps sessions alive without re-login (see
[configuration.md](configuration.md) for the symptoms when a provider does
not grant it).

## 5. Verify

- `https://checklists.example.com` serves a certificate from Let's Encrypt
  and lands you at Keycloak (AUTO_LOGIN), then back in the app with a fresh
  account.
- The break-glass login still works: after a logout the login form appears,
  and `admin` / `ADMIN_USER_PW` gets you in.
- With `EMAIL_ENABLED`, open the avatar menu → **Notifications** →
  **Send test email** ([configuration.md](configuration.md)).
- Install the PWA on a phone: HTTPS is all the secure-context requirement
  needs ([pwa-install.md](pwa-install.md)). Web Push subscriptions work for
  the same reason; the instance generated its own VAPID keys on first boot.
- Trigger a first backup without waiting for the schedule:
  `docker compose exec db-backup /backup.sh`, then look in
  `backups/checkcheck/`.

## Backups and restore

The backup containers write gzip-compressed `pg_dump` output on a daily
schedule with 7 days / 4 weeks / 6 months retention; the newest dump is also
kept as a `-latest` copy. Copy `./backups` off the host with whatever tool
you trust: a backup that lives next to the server it protects is not a
backup. Back up `config/` and `.env` too; losing the secrets logs everyone
out and, for `AUTH_JWT_SECRET`, invalidates API tokens.

To restore CheckCheck's database:

```bash
docker compose stop checkcheck
gunzip -c backups/checkcheck/checkcheck-latest.sql.gz \
  | docker compose exec -T db psql -U ${POSTGRES_USER} -d ${POSTGRES_DB}
docker compose start checkcheck
```

**Read the restore warning in [deployment.md](deployment.md#backups) first.**
A restored database has its sync counter rewound: clients heal their reads
automatically, but writes a client had queued offline can be partially
dropped. Restore in a quiet window.

## Upgrading

```bash
docker compose pull
docker compose up -d
```

Let the backup schedule run before you pull, and read
[UPGRADING.md](UPGRADING.md). The schema migrates automatically on start,
but some releases carry notes. Deliberate version bumps beat auto-updaters
here; see below.

## What is deliberately left out

- **A mail server.** Email notifications only need an SMTP relay; running
  your own mail server (with SPF/DKIM deliverability) is a separate project.
  Point the `EMAIL_*` settings at your hoster's relay or a transactional mail
  service. Push and in-app notifications work with no mail at all.
- **Automatic updates (Watchtower and friends).** CheckCheck migrates its
  schema on start and occasionally carries upgrade notes; updates should be a
  deliberate `pull` after a backup, not something that happens to you.
- **High availability.** CheckCheck targets personal use and small trusted
  groups ([Limitations](../README.md#limitations)). One solid host with
  backups is the right shape for it.
