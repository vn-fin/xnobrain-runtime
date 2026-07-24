#!/usr/bin/env bash
# Install the complete Brain4All development runtime on Linux without Docker.
#
# The layout is intentionally project-local for executable tooling:
#   .tools/hermes-agent/  upstream Hermes checkout and runtime
#   .tools/python        symlink to the Hermes Python environment
#   .tools/office-python office/document helpers
#   .tools/npm-global    9router and agent CLIs
#
# Hermes and 9router data remain in the user's home directory by default, so
# installing or removing this checkout never removes user profiles or keys.
set -euo pipefail

project_dir="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd)"
tools_dir="$project_dir/.tools"
node_root="$tools_dir/node"
npm_prefix="$tools_dir/npm-global"
hermes_install_dir="$tools_dir/hermes-agent"
office_python_dir="$tools_dir/office-python"
project_python_link="$tools_dir/python"

hermes_branch="${BRAIN4ALL_HERMES_BRANCH:-main}"
node_version="${BRAIN4ALL_NODE_VERSION:-22.23.1}"
nine_router_version="${BRAIN4ALL_NINE_ROUTER_VERSION:-0.5.40}"
codex_version="${BRAIN4ALL_CODEX_VERSION:-0.144.6}"
claude_version="${BRAIN4ALL_CLAUDE_CODE_VERSION:-2.1.216}"
agent_browser_version="${BRAIN4ALL_AGENT_BROWSER_VERSION:-0.26.0}"
hermes_home="${HERMES_HOME:-$HOME/.hermes}"
router_data_dir="${NINE_ROUTER_DATA_DIR:-$HOME/.9router}"
skip_system_packages=false
skip_office_tools=false
skip_browser=false
check_only=false

usage() {
  cat <<'EOF'
Install Brain4All locally on Linux (no Docker runtime required).

Usage: scripts/install-linux.sh [options]

Options:
  --skip-system-packages  Do not install the Dockerfile-derived apt/dnf packages
  --skip-office-tools     Do not install the large office/document Python set
  --skip-browser          Do not install Chromium for agent-browser
  --check                 Validate prerequisites and print the planned layout
  -h, --help              Show this help

Environment overrides:
  BRAIN4ALL_NODE_VERSION, BRAIN4ALL_HERMES_BRANCH,
  BRAIN4ALL_NINE_ROUTER_VERSION, BRAIN4ALL_CODEX_VERSION,
  BRAIN4ALL_CLAUDE_CODE_VERSION, BRAIN4ALL_AGENT_BROWSER_VERSION,
  HERMES_HOME, NINE_ROUTER_DATA_DIR
EOF
}

while [[ $# -gt 0 ]]; do
  case "$1" in
    --skip-system-packages) skip_system_packages=true; shift ;;
    --skip-office-tools) skip_office_tools=true; shift ;;
    --skip-browser) skip_browser=true; shift ;;
    --check) check_only=true; shift ;;
    -h|--help) usage; exit 0 ;;
    *) echo "Unknown option: $1" >&2; usage >&2; exit 2 ;;
  esac
done

if [[ "$(uname -s)" != "Linux" ]]; then
  echo "This installer is for Linux." >&2
  exit 1
fi
if [[ "${EUID}" -eq 0 ]]; then
  echo "Run this installer as your normal user; it requests sudo for system packages." >&2
  exit 1
fi

package_manager=""
if command -v apt-get >/dev/null 2>&1; then
  package_manager=apt
elif command -v dnf >/dev/null 2>&1; then
  package_manager=dnf
elif command -v pacman >/dev/null 2>&1; then
  package_manager=pacman
fi

if [[ "$skip_system_packages" == false && -z "$package_manager" ]]; then
  echo "Unsupported Linux package manager. Use --skip-system-packages after installing the prerequisites manually." >&2
  exit 1
fi
if [[ "$skip_system_packages" == false && ! -x "$(command -v sudo 2>/dev/null || true)" ]]; then
  echo "sudo is required to install system packages. Use --skip-system-packages only if they are already installed." >&2
  exit 1
fi

if [[ "$check_only" == true ]]; then
  printf 'Linux package manager: %s\n' "${package_manager:-not detected (system package step skipped)}"
  printf 'Project tools: %s\n' "$tools_dir"
  printf 'Project Python: %s\n' "$project_python_link/bin/python"
  printf 'Hermes home: %s\n' "$hermes_home"
  printf '9router data: %s\n' "$router_data_dir"
  printf 'Node: %s\n' "$node_version"
  printf '9router: %s\n' "$nine_router_version"
  exit 0
fi

install_apt_packages() {
  # This list follows Dockerfile.backend's Ubuntu office/engineering image.
  local packages=(
    antiword apt-transport-https bash-completion build-essential ca-certificates
    catdoc csvkit curl default-jre-headless dnsutils docx2txt ffmpeg file
    fontconfig fonts-crosextra-caladea fonts-crosextra-carlito fonts-dejavu
    fonts-freefont-ttf fonts-liberation fonts-liberation2 fonts-noto
    fonts-noto-cjk fonts-noto-color-emoji fonts-urw-base35 ghostscript git
    gnumeric gnupg graphicsmagick htop hunspell-en-us hunspell-vi hyphen-en-us
    imagemagick img2pdf iproute2 iputils-ping jq less libffi-dev
    libimage-exiftool-perl libreoffice libreoffice-base libreoffice-calc
    libreoffice-draw libreoffice-impress libreoffice-java-common libreoffice-math
    libreoffice-script-provider-python libreoffice-writer locales lsb-release
    lsof man-db manpages mtr-tiny mythes-en-us nano net-tools netcat-openbsd
    ocrmypdf odt2txt openssh-client pandoc pdftk-java pkg-config poppler-utils
    procps psmisc python3 python3-dev python3-pip python3-uno python3-venv qpdf
    redis-tools ripgrep rsync socat software-properties-common sqlite3 strace
    sudo tar tcpdump tesseract-ocr tesseract-ocr-eng tesseract-ocr-vie tmux
    traceroute tree ttf-mscorefonts-installer unoconv unzip vim weasyprint
    wget wkhtmltopdf wv xlsx2csv xz-utils zip
  )
  sudo debconf-set-selections <<'EOF'
ttf-mscorefonts-installer msttcorefonts/accepted-mscorefonts-eula select true
EOF
  sudo apt-get update
  sudo DEBIAN_FRONTEND=noninteractive apt-get install -y --no-install-recommends "${packages[@]}"
}

install_dnf_packages() {
  sudo dnf install -y \
    ca-certificates curl gcc gcc-c++ git make openssh-clients \
    python3 python3-devel python3-pip python3-tkinter ripgrep \
    rsync sqlite tar unzip util-linux-user wget xz zip
}

install_pacman_packages() {
  sudo pacman -Sy --needed --noconfirm \
    base-devel ca-certificates curl git make openssh python python-pip \
    ripgrep rsync sqlite tar unzip wget xz zip
}

if [[ "$skip_system_packages" == false ]]; then
  case "$package_manager" in
    apt) install_apt_packages ;;
    dnf) install_dnf_packages ;;
    pacman) install_pacman_packages ;;
  esac
fi

mkdir -p "$tools_dir" "$npm_prefix/bin" "$hermes_home" "$router_data_dir/auth"

arch="$(uname -m)"
case "$arch" in
  x86_64|amd64) node_arch=x64 ;;
  aarch64|arm64) node_arch=arm64 ;;
  armv7l|armv7) node_arch=armv7l ;;
  *) echo "Unsupported Linux architecture for the Node runtime: $arch" >&2; exit 1 ;;
esac

system_node="$(command -v node 2>/dev/null || true)"
use_system_node=false
if [[ -n "$system_node" ]]; then
  system_node_major="$($system_node -p 'process.versions.node.split(".")[0]' 2>/dev/null || true)"
  [[ "$system_node_major" == "22" ]] && use_system_node=true
fi

if [[ "$use_system_node" == false ]]; then
  node_archive="node-v${node_version}-linux-${node_arch}"
  node_install_dir="$tools_dir/$node_archive"
  if [[ ! -x "$node_install_dir/bin/node" ]]; then
    archive_path="$tools_dir/$node_archive.tar.xz"
    curl -fsSL "https://nodejs.org/dist/v${node_version}/${node_archive}.tar.xz" -o "$archive_path"
    tar -xJf "$archive_path" -C "$tools_dir"
    rm -f "$archive_path"
  fi
  if [[ -e "$node_root" && ! -L "$node_root" ]]; then
    echo "$node_root exists and is not the installer-managed Node symlink." >&2
    exit 1
  fi
  ln -sfn "$(basename "$node_install_dir")" "$node_root"
fi

if [[ "$use_system_node" == true ]]; then
  node_bin_dir="$(dirname "$system_node")"
else
  node_bin_dir="$node_root/bin"
fi
export PATH="$node_bin_dir:$npm_prefix/bin:$tools_dir/python/bin:$office_python_dir/bin:$PATH"
node --version
npm --version

if [[ ! -d "$hermes_install_dir/.git" || ! -x "$hermes_install_dir/venv/bin/python" ]]; then
  installer_file="$(mktemp)"
  trap 'rm -f "$installer_file"' EXIT
  curl -fsSL https://hermes-agent.nousresearch.com/install.sh -o "$installer_file"
  hermes_args=(--branch "$hermes_branch" --skip-setup --non-interactive --dir "$hermes_install_dir" --hermes-home "$hermes_home")
  if [[ "$skip_browser" == true ]]; then
    hermes_args+=(--skip-browser)
  fi
  HERMES_HOME="$hermes_home" HERMES_INSTALL_DIR="$hermes_install_dir" \
    bash "$installer_file" "${hermes_args[@]}"
  rm -f "$installer_file"
  trap - EXIT
fi

hermes_python="$hermes_install_dir/venv/bin/python"
if [[ ! -x "$hermes_python" ]] || ! "$hermes_python" -c 'import hermes_cli' >/dev/null 2>&1; then
  echo "Hermes installation did not provide a working venv: $hermes_python" >&2
  exit 1
fi

if [[ -e "$project_python_link" && ! -L "$project_python_link" ]]; then
  project_python="$project_python_link"
else
  ln -sfn "hermes-agent/venv" "$project_python_link"
  project_python="$project_python_link"
fi
project_python="$project_python/bin/python"

# Reuse an existing project venv when present, but make sure it also contains
# the freshly installed Hermes package instead of silently falling back to a
# user's unrelated global installation.
if ! "$project_python" -c 'import hermes_cli' >/dev/null 2>&1; then
  "$project_python" -m pip install -e "$hermes_install_dir"
fi

"$project_python" -m pip install --upgrade pip setuptools wheel
"$project_python" -m pip install -r "$project_dir/requirements.txt"
"$project_python" -m pip install 'edge-tts==7.2.7'

if [[ "$skip_office_tools" == false ]]; then
  if [[ ! -x "$office_python_dir/bin/python" ]]; then
    python3 -m venv "$office_python_dir"
  fi
  "$office_python_dir/bin/python" -m pip install --upgrade pip setuptools wheel
  "$office_python_dir/bin/python" -m pip install --upgrade \
    'markitdown[pdf,docx,pptx,xlsx,xls,outlook]' \
    beautifulsoup4 docx2python extract-msg lxml mammoth markdown numpy odfpy \
    openpyxl pandas pdfplumber pikepdf pillow pymupdf pypdf pyxlsb pytesseract \
    python-docx python-magic python-pptx pyyaml reportlab xlrd xlwt xlsxwriter
fi

# npm 11 can deny package lifecycle scripts by policy. These three packages use
# postinstall only to fetch/build their runnable CLI payloads; opt them in
# explicitly when the installed npm supports the flag (older npm runs scripts
# by default).
npm_script_args=()
if npm install --help 2>&1 | grep -q -- '--allow-scripts'; then
  npm_script_args+=(
    --allow-scripts=9router
    --allow-scripts=agent-browser
    --allow-scripts=@anthropic-ai/claude-code
  )
fi
npm install --global --prefix "$npm_prefix" --no-audit --no-fund --include=optional \
  "${npm_script_args[@]}" \
  "9router@${nine_router_version}" \
  "@openai/codex@${codex_version}" \
  "@anthropic-ai/claude-code@${claude_version}" \
  "agent-browser@${agent_browser_version}" \
  pnpm

if [[ "$skip_browser" == false ]]; then
  "$npm_prefix/bin/agent-browser" install --with-deps
fi

frontend_npmrc="$project_dir/src/.npmrc"
frontend_npmrc_created=false
if npm install --help 2>&1 | grep -q -- '--allow-scripts' && [[ ! -e "$frontend_npmrc" ]]; then
  # npm only accepts allow-scripts for a project install through .npmrc.
  printf '%s\n' 'allow-scripts=esbuild,msw' > "$frontend_npmrc"
  frontend_npmrc_created=true
fi
cleanup_frontend_npmrc() {
  if [[ "$frontend_npmrc_created" == true ]]; then
    rm -f "$frontend_npmrc"
  fi
}
if [[ "$frontend_npmrc_created" == true ]]; then
  trap cleanup_frontend_npmrc EXIT
fi
npm --prefix "$project_dir/src" ci --no-audit --no-fund
cleanup_frontend_npmrc
trap - EXIT

# Match the Docker runtime's private 9router identity. The API adapter and
# router process read the same files, while credentials remain outside git.
if [[ ! -s "$router_data_dir/machine-id" ]]; then
  machine_id="$(cat /etc/machine-id 2>/dev/null || hostname)"
  printf '%s' "$machine_id" > "$router_data_dir/machine-id"
fi
if [[ ! -s "$router_data_dir/auth/cli-secret" ]]; then
  "$project_python" -c 'import secrets, sys; print(secrets.token_hex(32), end="")' > "$router_data_dir/auth/cli-secret"
fi
"$project_python" - "$router_data_dir" <<'PY'
import hashlib
from pathlib import Path
import sys

root = Path(sys.argv[1])
machine = (root / "machine-id").read_text().strip()
secret = (root / "auth" / "cli-secret").read_text().strip()
(root / "auth" / "cli-token").write_text(
    hashlib.sha256(f"{machine}9r-cli-auth{secret}".encode()).hexdigest()[:16]
)
PY
chmod 700 "$hermes_home" "$router_data_dir" "$router_data_dir/auth"
chmod 600 "$router_data_dir/machine-id" "$router_data_dir/auth/cli-secret" "$router_data_dir/auth/cli-token"

cat <<EOF

Brain4All local installation complete.

Project Python: $project_python
Hermes:         $hermes_install_dir/venv/bin/hermes
9router:        $npm_prefix/bin/9router
Data:           $hermes_home
Router data:    $router_data_dir

Start development with:
  cd "$project_dir"
  npm run dev
or:
  make dev
EOF
