---
name: debug-server-setup
description: How to start a self-contained debug backend + rebuild the frontend for interactive debugging in this project
metadata:
  type: reference
---

# Debug server + frontend rebuild

## Backend debug server

Start a fresh isolated backend on port 8282 (avoids colliding with the user's running instance on 8181):

```bash
cd /home/tim/Repos/github.com/motey/checkcheck/CheckCheck/backend
SQL_DATABASE_URL="sqlite+aiosqlite:///./debug_test.sqlite" \
FRONTEND_FILES_DIR="../frontend/.output/public" \
SERVER_LISTENING_PORT=8282 \
pdm run ./checkcheckserver/main.py > /tmp/checkcheck_debug.log 2>&1 &
sleep 4 && curl -s http://localhost:8282/api/health
```

- Uses a separate `debug_test.sqlite` so it doesn't corrupt the user's `muchdata.sqlite`
- All env vars come from `CheckCheck/backend/checkcheckserver/.env` (admin pw = `password123`)
- Logs go to `/tmp/checkcheck_debug.log`
- Kill with: `kill $(lsof -ti:8282)`

## Login and API testing

```bash
TOKEN=$(curl -s -X POST http://localhost:8282/api/auth/basic/login/token \
  -d "username=admin&password=password123" | python3 -c "import sys,json; print(json.load(sys.stdin)['access_token'])")
curl -s -H "Authorization: Bearer $TOKEN" http://localhost:8282/api/checklist?limit=5
```

Note: login is **form data** (`-d "username=..."`), NOT JSON. The endpoint is `/api/auth/basic/login/token`.

## Frontend rebuild

The frontend is built with Bun inside Docker (no local Node/Bun needed):

```bash
cd /home/tim/Repos/github.com/motey/checkcheck
docker run --network=host \
  -u $(id -u ${USER}):$(id -g ${USER}) \
  -v ./CheckCheck/openapi.json:/openapi.json \
  -v ./CheckCheck/frontend:/app \
  oven/bun /bin/sh -c "cd /app && bun install --frozen-lockfile 2>/dev/null && bun run build && bunx nuxi generate"
```

Output goes to `CheckCheck/frontend/.output/public/` which is the `FRONTEND_FILES_DIR` the backend serves.

**Why not `./build_frontend.sh`**: the script uses `-it` (interactive TTY) which fails non-interactively. Remove the `-it` flag to run without a terminal.

## Demo data

The `debug_test.sqlite` is pre-populated with demo data (users, checklists, items) from `provisioning_data/demo_data/demo_data.yaml` on first start. Login as `admin`/`password123` or demo users.

## Access

Open `http://localhost:8282` in the browser. The backend serves the built frontend.
