PYTHON_BIN := $(if $(wildcard $(CURDIR)/.tools/python/bin/python),$(CURDIR)/.tools/python/bin/python,$(if $(wildcard $(HOME)/.local/lib/hermes-agent/venv/bin/python),$(HOME)/.local/lib/hermes-agent/venv/bin/python,python3))
CONTAINER_CLI ?= docker
IMAGE_TAG ?= local
BRAIN4ALL_FRONTEND_IMAGE ?= brain4all-frontend:$(IMAGE_TAG)
HERMES_RUNTIME_IMAGE ?= brain4all-hermes-runtime:$(IMAGE_TAG)
.PHONY: dev backend src test check smoke-api build run image frontend-image runtime-image bundle load-bundle install

dev:
	./scripts/dev.sh

backend:
	$(PYTHON_BIN) server.py

src:
	cd src && npm run dev

test:
	$(PYTHON_BIN) -m unittest discover -s brain4all/tests -t . -p 'test_*.py'
	cd src && npm test

check:
	$(PYTHON_BIN) -m unittest discover -s brain4all/tests -t . -p 'test_*.py'
	$(PYTHON_BIN) -m compileall -q brain4all server.py
	cd src && npm test
	cd src && npm run build

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
