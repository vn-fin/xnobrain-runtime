NPM ?= npm
CONTAINER_CLI ?= docker
ifeq ($(OS),Windows_NT)
UNAME_S := Windows
else
UNAME_S := $(shell uname -s)
endif
APP_PACKAGE := package.json
GENERATED_DIR := $(abspath generated)
GENERATED_MANIFEST := $(GENERATED_DIR)/runtime-manifest.json
GENERATED_TAURI_CONFIG := $(GENERATED_DIR)/tauri.release.conf.json
GENERATED_BRAIN_BUILD_ENV := $(GENERATED_DIR)/brain-ui-build.env
BINARY_DIR := $(abspath ../binary)
BRAIN_IMAGE_TAG ?= $(if $(XNOBRAIN_BRAIN_IMAGE_TAG),$(XNOBRAIN_BRAIN_IMAGE_TAG),xnobrain-brain:app)
export XNOBRAIN_RUNTIME_MANIFEST := $(GENERATED_MANIFEST)

-include .env
export

.DEFAULT_GOAL := setup

.PHONY: setup setup-unix setup-windows brain-config brain-image config config-local config-test dev win mac appimage appimage-local rpm deb linux check clean-generated require-project require-windows require-macos require-linux

setup: require-project
ifeq ($(OS),Windows_NT)
	@$(MAKE) setup-windows
else
	@case "$(OS):$(UNAME_S)" in \
		*:Darwin) $(MAKE) setup-unix SETUP_SCRIPT=scripts/setup-macos.sh ;; \
		*:Linux) $(MAKE) setup-unix SETUP_SCRIPT=scripts/setup-linux.sh ;; \
		*) echo "Unsupported development host: $(OS):$(UNAME_S)" >&2; exit 2 ;; \
	esac
endif

setup-unix:
	@"$(SETUP_SCRIPT)"

setup-windows:
	powershell.exe -NoProfile -ExecutionPolicy Bypass -File scripts/setup-windows.ps1

config: require-project
	@node scripts/generate-runtime-manifest.mjs "$(GENERATED_MANIFEST)"
	@node scripts/generate-tauri-config.mjs "$(GENERATED_TAURI_CONFIG)"
	@node scripts/generate-brain-build-env.mjs "$(GENERATED_BRAIN_BUILD_ENV)"

brain-config: require-project
	@node scripts/generate-brain-build-env.mjs "$(GENERATED_BRAIN_BUILD_ENV)"

brain-image: brain-config
	@set -a; . "$(GENERATED_BRAIN_BUILD_ENV)"; set +a; \
	cd ..; \
	$(CONTAINER_CLI) build --file Dockerfile.frontend --tag "$(BRAIN_IMAGE_TAG)" \
		--build-arg API_BASE_URL --build-arg AUTH_BASE_URL \
		--build-arg API_CONTROL_BASE_URL --build-arg APP_EDITION \
		--build-arg AUTH_MODE --build-arg AUTH_PROVIDER \
		--build-arg FIREBASE_API_KEY .

config-local: require-project
	@node scripts/generate-runtime-manifest.mjs "$(GENERATED_MANIFEST)" --allow-local
	@node scripts/generate-tauri-config.mjs "$(GENERATED_TAURI_CONFIG)"
	@node scripts/generate-brain-build-env.mjs "$(GENERATED_BRAIN_BUILD_ENV)"

config-test: require-project
	@node scripts/prepare-test-config.mjs "$(GENERATED_MANIFEST)"
	@node scripts/generate-tauri-config.mjs "$(GENERATED_TAURI_CONFIG)"

require-project:
ifeq ($(OS),Windows_NT)
	@powershell.exe -NoProfile -Command "if (-not (Test-Path '$(APP_PACKAGE)')) { throw 'XNOBrain app has not been scaffolded under app/.' }"
else
	@test -f "$(APP_PACKAGE)" || { \
		echo "XNOBrain app has not been scaffolded under app/." >&2; \
		exit 2; \
	}
endif

dev: require-project config-test
	$(NPM) run tauri -- dev --config "$(GENERATED_TAURI_CONFIG)"

win: require-project require-windows config
	powershell.exe -NoProfile -ExecutionPolicy Bypass -File scripts/preflight-windows.ps1
	$(NPM) run tauri -- build --bundles nsis --config "$(GENERATED_TAURI_CONFIG)"

mac: require-project require-macos config
	./scripts/preflight-macos.sh
	$(NPM) run tauri -- build --bundles dmg --config "$(GENERATED_TAURI_CONFIG)"

rpm: require-project require-linux config
	./scripts/preflight-linux.sh
	$(NPM) run tauri -- build --bundles rpm --config "$(GENERATED_TAURI_CONFIG)"

deb: require-project require-linux config
	./scripts/preflight-linux.sh
	$(NPM) run tauri -- build --bundles deb --config "$(GENERATED_TAURI_CONFIG)"

linux: appimage

appimage: require-project require-linux config
	./scripts/preflight-linux.sh
	$(NPM) run tauri -- build --bundles appimage --config "$(GENERATED_TAURI_CONFIG)"
	@mkdir -p "$(BINARY_DIR)"
	@source_path=$$(find src-tauri/target/release/bundle/appimage -maxdepth 1 -type f -name '*.AppImage' -print -quit); \
	test -n "$$source_path" || { echo 'Tauri did not produce an AppImage.' >&2; exit 2; }; \
	install -m 0755 "$$source_path" "$(BINARY_DIR)/XNOBrain-linux-$$(uname -m).AppImage"
	@echo "Standalone Linux app: $(BINARY_DIR)/XNOBrain-linux-$$(uname -m).AppImage"

appimage-local: require-project require-linux config-local
	./scripts/preflight-linux.sh
	$(NPM) run tauri -- build --bundles appimage --config "$(GENERATED_TAURI_CONFIG)"
	@mkdir -p "$(BINARY_DIR)"
	@source_path=$$(find src-tauri/target/release/bundle/appimage -maxdepth 1 -type f -name '*.AppImage' -print -quit); \
	test -n "$$source_path" || { echo 'Tauri did not produce an AppImage.' >&2; exit 2; }; \
	install -m 0755 "$$source_path" "$(BINARY_DIR)/XNOBrain-linux-$$(uname -m).AppImage"
	@echo "Local-validation Linux app: $(BINARY_DIR)/XNOBrain-linux-$$(uname -m).AppImage"

check: require-project config-test
	$(NPM) run check
	cargo fmt --manifest-path src-tauri/Cargo.toml --all -- --check
	cargo clippy --manifest-path src-tauri/Cargo.toml --all-targets --all-features -- -D warnings
	cargo test --manifest-path src-tauri/Cargo.toml --all-features

clean-generated:
	@node scripts/clean-generated.mjs

require-windows:
ifeq ($(OS),Windows_NT)
	@powershell.exe -NoProfile -Command "exit 0"
else
	@echo "make win-app must run on a Windows build host." >&2; exit 2
endif

require-macos:
	@test "$(UNAME_S)" = "Darwin" || { \
		echo "make mac-app must run on a macOS build host." >&2; \
		exit 2; \
	}

require-linux:
	@test "$(UNAME_S)" = "Linux" || { \
		echo "Linux app packaging must run on a Linux build host." >&2; \
		exit 2; \
	}
