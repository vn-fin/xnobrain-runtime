#!/usr/bin/env bash
# Install Docker Engine, the Docker Compose plugin, and make for local use.
set -euo pipefail

if [[ "$(uname -s)" != "Linux" ]]; then
  echo "This installer is for Linux. Use setup-macos.sh or setup-windows.ps1 instead." >&2
  exit 1
fi

if [[ "${EUID}" -eq 0 ]]; then
  echo "Run this installer as your normal user; it will request sudo when needed." >&2
  exit 1
fi

if ! command -v sudo >/dev/null; then
  echo "sudo is required to install Docker and make." >&2
  exit 1
fi

if command -v apt-get >/dev/null; then
  package_manager=apt
elif command -v dnf >/dev/null; then
  package_manager=dnf
elif command -v pacman >/dev/null; then
  package_manager=pacman
else
  echo "Unsupported Linux package manager. Install Docker Engine, Docker Compose v2, and make manually." >&2
  exit 1
fi

install_packages() {
  case "$package_manager" in
    apt) sudo apt-get update; sudo apt-get install -y "$@" ;;
    dnf) sudo dnf install -y "$@" ;;
    pacman) sudo pacman -Sy --needed --noconfirm "$@" ;;
  esac
}

if ! command -v make >/dev/null; then
  install_packages make
fi

if ! command -v curl >/dev/null; then
  install_packages curl
fi

if ! command -v docker >/dev/null; then
  installer_path="$(mktemp)"
  trap 'rm -f "$installer_path"' EXIT
  curl -fsSL https://get.docker.com -o "$installer_path"
  sudo sh "$installer_path"
fi

if ! docker compose version >/dev/null 2>&1; then
  case "$package_manager" in
    apt|dnf) install_packages docker-compose-plugin ;;
    pacman) install_packages docker-compose ;;
  esac
fi

if command -v systemctl >/dev/null; then
  sudo systemctl enable --now docker
fi

if ! id -nG "$USER" | tr ' ' '\n' | grep -qx docker; then
  sudo usermod -aG docker "$USER"
  group_added=true
else
  group_added=false
fi

docker --version
docker compose version
make --version | head -n 1

if [[ "$group_added" == true ]]; then
  echo "Installation complete. Sign out and sign back in before running Docker without sudo."
else
  echo "Installation complete. Start Brain4All with: docker compose up -d --build"
fi
