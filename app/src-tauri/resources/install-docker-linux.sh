#!/bin/sh
set -eu

if [ "$(id -u)" -ne 0 ]; then
  echo 'Docker installation requires administrator privileges.' >&2
  exit 2
fi

. /etc/os-release
case "${ID:-}:${ID_LIKE:-}" in
  ubuntu:*|debian:*|pop:*|*:debian*)
    case "${ID:-}:${ID_LIKE:-}" in
      ubuntu:*|pop:*|linuxmint:*|elementary:*|*:ubuntu*) repository_distribution=ubuntu ;;
      *) repository_distribution=debian ;;
    esac
    apt-get update
    apt-get install -y ca-certificates curl
    install -m 0755 -d /etc/apt/keyrings
    curl -fsSL "https://download.docker.com/linux/${repository_distribution}/gpg" -o /etc/apt/keyrings/docker.asc
    chmod a+r /etc/apt/keyrings/docker.asc
    architecture="$(dpkg --print-architecture)"
    codename="${UBUNTU_CODENAME:-${VERSION_CODENAME:-}}"
    [ -n "$codename" ] || { echo 'Could not determine the distribution codename.' >&2; exit 2; }
    printf '%s\n' \
      'Types: deb' \
      "URIs: https://download.docker.com/linux/${repository_distribution}" \
      "Suites: ${codename}" \
      'Components: stable' \
      "Architectures: ${architecture}" \
      'Signed-By: /etc/apt/keyrings/docker.asc' \
      > /etc/apt/sources.list.d/docker.sources
    apt-get update
    apt-get install -y docker-ce docker-ce-cli containerd.io docker-buildx-plugin docker-compose-plugin
    ;;
  fedora:*|*:fedora*)
    dnf install -y ca-certificates curl
    curl -fsSL https://download.docker.com/linux/fedora/docker-ce.repo -o /etc/yum.repos.d/docker-ce.repo
    dnf install -y docker-ce docker-ce-cli containerd.io docker-buildx-plugin docker-compose-plugin
    ;;
  centos:*|rhel:*|*:rhel*)
    dnf install -y ca-certificates curl
    curl -fsSL https://download.docker.com/linux/centos/docker-ce.repo -o /etc/yum.repos.d/docker-ce.repo
    dnf install -y docker-ce docker-ce-cli containerd.io docker-buildx-plugin docker-compose-plugin
    ;;
  *)
    echo "Unsupported Linux distribution: ${ID:-unknown}. Install Docker Engine and Compose from Docker's official repository." >&2
    exit 2
    ;;
esac

systemctl enable --now docker
if [ -n "${PKEXEC_UID:-}" ]; then
  invoking_user="$(getent passwd "$PKEXEC_UID" | cut -d: -f1)"
  if [ -n "$invoking_user" ]; then
    usermod -aG docker "$invoking_user"
  fi
fi
