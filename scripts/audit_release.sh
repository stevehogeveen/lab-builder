#!/usr/bin/env bash
set -euo pipefail

if [[ $# -ne 1 ]]; then
  echo "Usage: $0 dist/lab-builder-VERSION.tar.gz" >&2
  exit 2
fi

ARCHIVE_PATH="$1"
if [[ ! -f "$ARCHIVE_PATH" ]]; then
  echo "Release archive not found: ${ARCHIVE_PATH}" >&2
  exit 1
fi

LISTING="$(mktemp)"
trap 'rm -f "$LISTING"' EXIT
tar -tzf "$ARCHIVE_PATH" > "$LISTING"

failures=0

check_listing() {
  local label="$1"
  local pattern="$2"
  if rg -i "$pattern" "$LISTING" >/dev/null; then
    echo "Release audit failed: ${label}" >&2
    rg -i "$pattern" "$LISTING" >&2 || true
    failures=$((failures + 1))
  fi
}

check_listing "runtime data directory included" '(^|/)(config|artifacts|media|backups|dist|release)/'
check_listing "customer media or firmware file included" '\.(iso|ova|ovf|vmdk|qcow2|img|bin|fw|fwpkg|tgz)$'
check_listing "secret-like file included" '(^|/)(\.env|.*(password|passwd|secret|credential|customer|client).*)$'
check_listing "private key or certificate material included" '\.(key|pem|p12|pfx|kubeconfig)$'

HOST_PATH_PATTERN='/'"home"'/[^[:space:]"'\'']+'
SUPPLIED_PASSWORD_PATTERN='P@ss'"w0rd"
LOCAL_IDENTIFIER_PATTERN='Lab-'"Uplands|NS"''"WAN|DOP-"''"X70"
CONTENT_PATTERN="${HOST_PATH_PATTERN}|${SUPPLIED_PASSWORD_PATTERN}|${LOCAL_IDENTIFIER_PATTERN}"
if tar -xOf "$ARCHIVE_PATH" 2>/dev/null | rg -I -n "${CONTENT_PATTERN}" >/dev/null; then
  echo "Release audit failed: host path, supplied password, or known local/site identifier found in archive content" >&2
  tar -xOf "$ARCHIVE_PATH" 2>/dev/null | rg -I -n "${CONTENT_PATTERN}" >&2 || true
  failures=$((failures + 1))
fi

if [[ "$failures" -gt 0 ]]; then
  exit 1
fi

echo "Release audit passed: ${ARCHIVE_PATH}"
