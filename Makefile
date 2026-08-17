-include .env
-include ../.env
export

PYTHON_BIN := $(if $(wildcard $(CURDIR)/.tools/python/bin/python),$(CURDIR)/.tools/python/bin/python,$(if $(wildcard $(HOME)/.local/lib/hermes-agent/venv/bin/python),$(HOME)/.local/lib/hermes-agent/venv/bin/python,python3))
CONTAINER_CLI ?= docker
IMAGE_TAG ?= local
XNOBRAIN_RUNTIME_IMAGE ?= xnobrain-runtime:$(IMAGE_TAG)
.PHONY: dev backend test check smoke-api build run image runtime-image bundle load-bundle install install-local brain-app-image dev-app linux-app linux-app-local win-app mac-app rpm-app

dev:
	bash ./scripts/dev.sh

backend:
	HERMES_SERVE_HEADLESS=1 BROWSER=/bin/false DISPLAY= WAYLAND_DISPLAY= $(PYTHON_BIN) server.py

test:
	$(PYTHON_BIN) -m unittest discover -s xnobrain/tests -t . -p 'test_*.py'

check:
	$(PYTHON_BIN) -m unittest discover -s xnobrain/tests -t . -p 'test_*.py'
	$(PYTHON_BIN) -m compileall -q xnobrain server.py

smoke-api:
	./scripts/SmokeAPI.sh

build:
	$(CONTAINER_CLI) compose build runtime

run:
	$(CONTAINER_CLI) compose up -d --build

image: build

runtime-image:
	$(CONTAINER_CLI) compose build runtime

bundle:
	CONTAINER_CLI=$(CONTAINER_CLI) IMAGE_TAG=$(IMAGE_TAG) ./scripts/BundleImages.sh

load-bundle:
	CONTAINER_CLI=$(CONTAINER_CLI) ./scripts/LoadImages.sh

install: run

install-local:
	./scripts/install-linux.sh

# XNOBrain installer and Full Managed App. All app implementation and release
# logic is isolated below app/; these targets are the stable repository entrypoints.
dev-app:
	$(MAKE) -C app dev

brain-app-image:
	$(MAKE) -C app brain-image

linux-app:
	$(MAKE) -C app appimage

linux-app-local:
	$(MAKE) -C app appimage-local

win-app:
	$(MAKE) -C app win

mac-app:
	$(MAKE) -C app mac

rpm-app:
	$(MAKE) -C app rpm
