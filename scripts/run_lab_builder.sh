#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT_DIR"

mkdir -p config artifacts media

VERSION_VALUE="${LAB_BUILDER_VERSION:-$(tr -d '[:space:]' < VERSION)}"
export LAB_BUILDER_VERSION="$VERSION_VALUE"
IMAGE_NAME="${LAB_BUILDER_IMAGE:-lab-builder}"
IMAGE_TAG="${IMAGE_NAME}:${VERSION_VALUE}"
IMAGE_ARCHIVE="${LAB_BUILDER_IMAGE_ARCHIVE:-dist/${IMAGE_NAME}-${VERSION_VALUE}-image.tar.gz}"

compose_cmd=()
if docker compose version >/dev/null 2>&1; then
  compose_cmd=(docker compose)
elif command -v docker-compose >/dev/null 2>&1; then
  compose_cmd=(docker-compose)
else
  echo "Docker Compose is required. Install Docker with the compose plugin, then rerun this script." >&2
  exit 1
fi

if ! docker image inspect "$IMAGE_TAG" >/dev/null 2>&1; then
  if [[ -f "$IMAGE_ARCHIVE" ]]; then
    ./scripts/load_docker_image.sh "$IMAGE_ARCHIVE"
  elif [[ "${LAB_BUILDER_ALLOW_BUILD:-1}" == "1" && -f Dockerfile ]]; then
    exec "${compose_cmd[@]}" -f docker-compose.yml -f docker-compose.build.yml up --build
  else
    echo "Docker image ${IMAGE_TAG} is not available and no image archive was found at ${IMAGE_ARCHIVE}." >&2
    echo "Load an image with ./scripts/load_docker_image.sh or enable source builds with LAB_BUILDER_ALLOW_BUILD=1." >&2
    exit 1
  fi
fi

exec "${compose_cmd[@]}" up
