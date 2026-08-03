PYTHON_BIN := $(if $(wildcard $(CURDIR)/.tools/python/bin/python),$(CURDIR)/.tools/python/bin/python,$(if $(wildcard $(HOME)/.local/lib/hermes-agent/venv/bin/python),$(HOME)/.local/lib/hermes-agent/venv/bin/python,python3))
CONTAINER_CLI ?= docker
IMAGE_TAG ?= local
BRAIN4ALL_FRONTEND_IMAGE ?= brain4all-frontend:$(IMAGE_TAG)
HERMES_RUNTIME_IMAGE ?= brain4all-hermes-runtime:$(IMAGE_TAG)
.PHONY: dev backend src test check smoke-api build run image frontend-image runtime-image bundle load-bundle install install-local dev-app win-app mac-app rpm-app

dev:
	bash ./scripts/dev.sh

backend:
	$(PYTHON_BIN) server.py

src:
	npm run dev:frontend

test:
	$(PYTHON_BIN) -m unittest discover -s brain4all/tests -t . -p 'test_*.py'
	npm test

check:
	$(PYTHON_BIN) -m unittest discover -s brain4all/tests -t . -p 'test_*.py'
	$(PYTHON_BIN) -m compileall -q brain4all server.py
	npm test
	npm run build

smoke-api:
	./scripts/SmokeAPI.sh

build:
	$(CONTAINER_CLI) compose build frontend runtime

run:
	$(CONTAINER_CLI) compose up -d --build

image: build

frontend-image:
	$(CONTAINER_CLI) compose build frontend

runtime-image:
	$(CONTAINER_CLI) compose build runtime

bundle:
	CONTAINER_CLI=$(CONTAINER_CLI) IMAGE_TAG=$(IMAGE_TAG) ./scripts/BundleImages.sh

load-bundle:
	CONTAINER_CLI=$(CONTAINER_CLI) ./scripts/LoadImages.sh

install: run

install-local:
	./scripts/install-linux.sh

# Brain4All installer and Full Managed App. All app implementation and release
# logic is isolated below app/; these targets are the stable repository entrypoints.
dev-app:
	$(MAKE) -C app dev

win-app:
	$(MAKE) -C app win

mac-app:
	$(MAKE) -C app mac

rpm-app:
	$(MAKE) -C app rpm
