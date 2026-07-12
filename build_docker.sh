#!/bin/bash
# Build the CheckCheck container image locally, mirroring how CI stamps the
# version (setuptools_scm from the latest git tag, default 0.0.1 with no tag).
set -euo pipefail

docker_tag="${1:-checkcheck:latest}"

APP_VERSION="$(python -c "from setuptools_scm import get_version; print(get_version(root='.', fallback_version='0.0.1'))" 2>/dev/null || echo 0.0.1)"
LOG_LEVEL="${LOG_LEVEL:-INFO}"

echo "Build docker image '$docker_tag' (version=$APP_VERSION, LOG_LEVEL=$LOG_LEVEL)"
docker build . -t "$docker_tag" -f Dockerfile \
    --build-arg APP_VERSION="$APP_VERSION" \
    --build-arg LOG_LEVEL="$LOG_LEVEL"

echo "Docker image produced: $docker_tag"
echo "Run with:"
echo "     docker run $docker_tag"
