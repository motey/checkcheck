#!/bin/bash
docker pull oven/bun
docker run --network=host -u $(id -u ${USER}):$(id -g ${USER}) -it -v ./CheckCheck/openapi.json:/openapi.json -v ./CheckCheck/frontend:/app oven/bun /bin/sh -c "cd /app && bun install && bun run build && bunx nuxi generate"
