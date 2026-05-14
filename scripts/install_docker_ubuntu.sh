#!/usr/bin/env bash
set -euo pipefail

if [[ "$(id -u)" -eq 0 ]]; then
  SUDO=""
else
  SUDO="sudo"
fi

if ! command -v apt >/dev/null 2>&1; then
  echo "This installer requires apt and is intended for Ubuntu hosts." >&2
  exit 2
fi

if [[ ! -r /etc/os-release ]]; then
  echo "Cannot read /etc/os-release; refusing to configure Docker apt sources." >&2
  exit 2
fi

. /etc/os-release
if [[ "${ID:-}" != "ubuntu" ]]; then
  echo "Unsupported OS '${ID:-unknown}'. Use this script only on Ubuntu hosts." >&2
  exit 2
fi

CODENAME="${UBUNTU_CODENAME:-${VERSION_CODENAME:-}}"
if [[ -z "$CODENAME" ]]; then
  echo "Cannot determine Ubuntu codename from /etc/os-release." >&2
  exit 2
fi

echo "Installing Docker Engine for Ubuntu ${VERSION_ID:-unknown} (${CODENAME})."
echo "This follows Docker's official apt repository installation path."

$SUDO apt update
$SUDO apt install -y ca-certificates curl
$SUDO install -m 0755 -d /etc/apt/keyrings
$SUDO curl -fsSL https://download.docker.com/linux/ubuntu/gpg -o /etc/apt/keyrings/docker.asc
$SUDO chmod a+r /etc/apt/keyrings/docker.asc

$SUDO tee /etc/apt/sources.list.d/docker.sources >/dev/null <<EOF
Types: deb
URIs: https://download.docker.com/linux/ubuntu
Suites: ${CODENAME}
Components: stable
Architectures: $(dpkg --print-architecture)
Signed-By: /etc/apt/keyrings/docker.asc
EOF

$SUDO apt update
$SUDO apt install -y docker-ce docker-ce-cli containerd.io docker-buildx-plugin docker-compose-plugin

if command -v systemctl >/dev/null 2>&1; then
  $SUDO systemctl enable --now docker
fi

if getent group docker >/dev/null 2>&1; then
  $SUDO usermod -aG docker "$USER"
fi

echo
echo "Docker installed."
echo "Verify now with: sudo docker run hello-world"
echo "For non-sudo docker commands, log out and back in so the docker group membership applies."
