#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT_DIR"

BACKUP_DIR="${BACKUP_DIR:-backups}"
TIMESTAMP="$(date +%Y%m%d-%H%M%S)"
ARCHIVE_PATH="${BACKUP_DIR}/lab-builder-data-${TIMESTAMP}.tar.gz"

mkdir -p "$BACKUP_DIR"
mkdir -p config artifacts media

tar -czf "$ARCHIVE_PATH" \
  --exclude='media/**/*.iso' \
  --exclude='media/**/*.ova' \
  --exclude='media/**/*.ovf' \
  --exclude='media/**/*.vmdk' \
  --exclude='media/**/*.qcow2' \
  --exclude='media/**/*.img' \
  --exclude='media/**/*.bin' \
  --exclude='media/**/*.fw' \
  --exclude='media/**/*.fwpkg' \
  config artifacts media

echo "Created backup: ${ARCHIVE_PATH}"
