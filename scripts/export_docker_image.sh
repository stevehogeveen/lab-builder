#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT_DIR"

VERSION_VALUE="${LAB_BUILDER_VERSION:-$(tr -d '[:space:]' < VERSION)}"
IMAGE_NAME="${LAB_BUILDER_IMAGE:-lab-builder}"
IMAGE_TAG="${IMAGE_NAME}:${VERSION_VALUE}"
DIST_DIR="${DIST_DIR:-dist}"
ARCHIVE_PATH="${DIST_DIR}/${IMAGE_NAME}-${VERSION_VALUE}-image.tar.gz"

if ! command -v docker >/dev/null 2>&1; then
  echo "docker command not found. Install Docker or skip image export." >&2
  exit 1
fi

mkdir -p "$DIST_DIR"

if [[ "${LAB_BUILDER_FORCE_IMAGE_BUILD:-0}" == "1" ]] || ! docker image inspect "$IMAGE_TAG" >/dev/null 2>&1; then
  echo "Building image ${IMAGE_TAG}."
  LAB_BUILDER_VERSION="$VERSION_VALUE" docker compose -f docker-compose.yml -f docker-compose.build.yml build
fi

docker save "$IMAGE_TAG" | gzip -c > "$ARCHIVE_PATH"

echo "Exported Docker image: ${ARCHIVE_PATH}"
