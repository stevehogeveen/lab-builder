#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT_DIR"

if [[ $# -ne 1 ]]; then
  echo "Usage: $0 backups/lab-builder-data-YYYYmmdd-HHMMSS.tar.gz" >&2
  exit 2
fi

BACKUP_PATH="$1"
if [[ ! -f "$BACKUP_PATH" ]]; then
  echo "Backup not found: ${BACKUP_PATH}" >&2
  exit 1
fi

mkdir -p config artifacts media
tar -xzf "$BACKUP_PATH"

echo "Restored backup: ${BACKUP_PATH}"
