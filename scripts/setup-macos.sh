#!/usr/bin/env bash
# Install Docker Desktop, Docker Compose, and make for local use on macOS.
set -euo pipefail

if [[ "$(uname -s)" != "Darwin" ]]; then
  echo "This installer is for macOS. Use setup-linux.sh or setup-windows.ps1 instead." >&2
  exit 1
fi

if ! command -v brew >/dev/null; then
  /bin/bash -c "$(curl -fsSL https://raw.githubusercontent.com/Homebrew/install/HEAD/install.sh)"
fi

brew install make docker
if [[ ! -d "/Applications/Docker.app" ]]; then
  brew install --cask docker
fi

open -a Docker
printf 'Waiting for Docker Desktop to start'
for _ in $(seq 1 60); do
  if docker info >/dev/null 2>&1; then
    break
  fi
  printf '.'
  sleep 2
done
printf '\n'

if ! docker info >/dev/null 2>&1; then
  echo "Docker Desktop did not become ready. Finish its first-run setup, then run this script again." >&2
  exit 1
fi

if ! docker compose version >/dev/null 2>&1; then
  brew install docker-compose
  mkdir -p "$HOME/.docker/cli-plugins"
  ln -sf "$(brew --prefix)/bin/docker-compose" "$HOME/.docker/cli-plugins/docker-compose"
fi

docker --version
docker compose version
make --version | head -n 1
echo "Installation complete. Start Brain4All with: docker compose up -d --build"
