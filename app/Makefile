NPM ?= npm
UNAME_S := $(shell uname -s 2>/dev/null || echo Unknown)
APP_PACKAGE := package.json

.PHONY: dev win mac rpm check require-project require-windows require-macos require-linux

require-project:
	@test -f "$(APP_PACKAGE)" || { \
		echo "Brain4All app has not been scaffolded yet; implement Plan 013 Stage 0 under app/." >&2; \
		exit 2; \
	}

dev: require-project
	$(NPM) run tauri -- dev

win: require-project require-windows
	$(NPM) run tauri -- build --bundles nsis

mac: require-project require-macos
	$(NPM) run tauri -- build --bundles app,dmg

rpm: require-project require-linux
	$(NPM) run tauri -- build --bundles rpm

check: require-project
	$(NPM) run check
	cargo fmt --manifest-path src-tauri/Cargo.toml --all -- --check
	cargo clippy --manifest-path src-tauri/Cargo.toml --all-targets --all-features -- -D warnings
	cargo test --manifest-path src-tauri/Cargo.toml --all-features

require-windows:
	@case "$(OS):$(UNAME_S)" in \
		Windows_NT:*|*:MINGW*|*:MSYS*|*:CYGWIN*) ;; \
		*) echo "make win-app must run on a Windows build host." >&2; exit 2 ;; \
	esac

require-macos:
	@test "$(UNAME_S)" = "Darwin" || { \
		echo "make mac-app must run on a macOS build host." >&2; \
		exit 2; \
	}

require-linux:
	@test "$(UNAME_S)" = "Linux" || { \
		echo "make rpm-app must run on a Linux build host." >&2; \
		exit 2; \
	}
