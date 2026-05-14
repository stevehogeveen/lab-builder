#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT_DIR"

VERSION_VALUE="${LAB_BUILDER_VERSION:-$(tr -d '[:space:]' < VERSION)}"
IMAGE_NAME="${LAB_BUILDER_IMAGE:-lab-builder}"
DEFAULT_ARCHIVE="dist/${IMAGE_NAME}-${VERSION_VALUE}-image.tar.gz"
ARCHIVE_PATH="${1:-$DEFAULT_ARCHIVE}"

if ! command -v docker >/dev/null 2>&1; then
  echo "docker command not found. Install Docker before loading the image." >&2
  exit 1
fi

if [[ ! -f "$ARCHIVE_PATH" ]]; then
  echo "Docker image archive not found: ${ARCHIVE_PATH}" >&2
  exit 1
fi

gzip -dc "$ARCHIVE_PATH" | docker load

echo "Loaded Docker image from: ${ARCHIVE_PATH}"
