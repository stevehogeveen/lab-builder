#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT_DIR"

VERSION_VALUE="$(tr -d '[:space:]' < VERSION)"
if [[ -z "$VERSION_VALUE" ]]; then
  echo "VERSION is empty" >&2
  exit 1
fi

DIST_DIR="${DIST_DIR:-dist}"
RELEASE_NAME="lab-builder-${VERSION_VALUE}"
ARCHIVE_PATH="${DIST_DIR}/${RELEASE_NAME}.tar.gz"
CHECKSUM_PATH="${DIST_DIR}/SHA256SUMS"
DEPENDENCY_SNAPSHOT_PATH="${DIST_DIR}/${RELEASE_NAME}-dependencies.yml"
INCLUDE_DOCKER_IMAGE="${INCLUDE_DOCKER_IMAGE:-auto}"

mkdir -p "$DIST_DIR"
rm -f "$ARCHIVE_PATH"
rm -f "$CHECKSUM_PATH"
rm -f "$DEPENDENCY_SNAPSHOT_PATH"

EXCLUDES=(
  "--exclude=.git"
  "--exclude=.venv"
  "--exclude=__pycache__"
  "--exclude=.pytest_cache"
  "--exclude=.mypy_cache"
  "--exclude=.ruff_cache"
  "--exclude=tests"
  "--exclude=PROJECT_FAILURES_AND_LESSONS.md"
  "--exclude=SESSION_COORDINATION.md"
  "--exclude=dist"
  "--exclude=release"
  "--exclude=backups"
  "--exclude=artifacts"
  "--exclude=config"
  "--exclude=media"
  "--exclude=*.iso"
  "--exclude=*.ova"
  "--exclude=*.ovf"
  "--exclude=*.vmdk"
  "--exclude=*.qcow2"
  "--exclude=*.img"
  "--exclude=*.bin"
  "--exclude=*.fw"
  "--exclude=*.fwpkg"
  "--exclude=*.tgz"
  "--exclude=*.key"
  "--exclude=*.pem"
  "--exclude=*.p12"
  "--exclude=*.pfx"
  "--exclude=.env"
  "--exclude=.env.*"
  "--exclude=*password*"
  "--exclude=*secret*"
  "--exclude=*credential*"
)

tar "${EXCLUDES[@]}" \
  --transform "s#^\\.#${RELEASE_NAME}#" \
  -czf "$ARCHIVE_PATH" \
  .

echo "Built release archive: ${ARCHIVE_PATH}"
./scripts/audit_release.sh "$ARCHIVE_PATH"

case "$INCLUDE_DOCKER_IMAGE" in
  1|true|yes)
    LAB_BUILDER_FORCE_IMAGE_BUILD=1 ./scripts/export_docker_image.sh
    ;;
  0|false|no)
    echo "Skipped Docker image export."
    ;;
  auto)
    if command -v docker >/dev/null 2>&1 && docker info >/dev/null 2>&1; then
      LAB_BUILDER_FORCE_IMAGE_BUILD=1 ./scripts/export_docker_image.sh
    else
      echo "Skipped Docker image export because Docker is not available to this shell."
    fi
    ;;
  *)
    echo "Unsupported INCLUDE_DOCKER_IMAGE value: ${INCLUDE_DOCKER_IMAGE}" >&2
    exit 2
    ;;
esac

IMAGE_ID="not-built"
if command -v docker >/dev/null 2>&1 && docker image inspect "lab-builder:${VERSION_VALUE}" >/dev/null 2>&1; then
  IMAGE_ID="$(docker image inspect "lab-builder:${VERSION_VALUE}" --format '{{.Id}}')"
fi

{
  echo "name: lab-builder"
  echo "version: \"${VERSION_VALUE}\""
  echo "generated_at: \"$(date -u +%Y-%m-%dT%H:%M:%SZ)\""
  echo "python_runtime_requirements: requirements-runtime.txt"
  echo "python_development_requirements: requirements.txt"
  echo "container_base_image: python:3.12-slim"
  echo "container_os_packages:"
  echo "  - ca-certificates"
  echo "  - openssh-client"
  echo "  - sshpass"
  echo "  - xorriso"
  echo "docker_image:"
  echo "  repository: lab-builder"
  echo "  tag: \"${VERSION_VALUE}\""
  echo "  id: \"${IMAGE_ID}\""
  echo "  archive: lab-builder-${VERSION_VALUE}-image.tar.gz"
} > "$DEPENDENCY_SNAPSHOT_PATH"

(
  cd "$DIST_DIR"
  for artifact in *; do
    [[ -f "$artifact" && "$artifact" != "SHA256SUMS" ]] || continue
    sha256sum "$artifact"
  done
) > "$CHECKSUM_PATH"

echo "Built checksums: ${CHECKSUM_PATH}"
echo "Review source archive before distribution: tar -tzf ${ARCHIVE_PATH}"
