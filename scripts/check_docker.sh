#!/usr/bin/env bash
set -euo pipefail

if ! command -v docker >/dev/null 2>&1; then
  echo "docker command not found. On Ubuntu, run: ./scripts/install_docker_ubuntu.sh" >&2
  exit 1
fi

docker --version
docker compose version
docker compose config >/dev/null
echo "Docker and Compose are available, and docker-compose.yml is valid."
