GO_BIN := $(if $(wildcard $(CURDIR)/.tools/go/bin/go),$(CURDIR)/.tools/go/bin/go,go)
GOFMT_BIN := $(if $(wildcard $(CURDIR)/.tools/go/bin/gofmt),$(CURDIR)/.tools/go/bin/gofmt,gofmt)
PYTHON_BIN := $(if $(wildcard $(CURDIR)/.tools/python/bin/python),$(CURDIR)/.tools/python/bin/python,python3)
CONTAINER_CLI ?= docker
IMAGE_TAG ?= local
OPEN_LUMORA_BACKEND_IMAGE ?= open-lumora-backend:$(IMAGE_TAG)
OPEN_LUMORA_FRONTEND_IMAGE ?= open-lumora-frontend:$(IMAGE_TAG)
OPEN_LUMORA_HERMES_BASE_IMAGE ?= open-lumora-hermes-runtime:local
OPEN_LUMORA_HERMES_IMAGE ?= open-lumora-hermes-runtime:compatible
ENTERPRISE_CONTEXT ?= ../open-lumora-enterprise

.PHONY: dev backend src test check smoke-api binary build image backend-image frontend-image runtime-image support-images bundle load-bundle install

dev:
	./scripts/dev.sh

backend:
	$(GO_BIN) run cmd/main.go

src:
	cd src && npm run dev

test:
	$(GO_BIN) test ./...
	cd src && npm test

check:
	$(GOFMT_BIN) -w $$(find cmd internal services -name '*.go')
	$(GO_BIN) test ./...
	cd contracttest && $(GO_BIN) test ./...
	$(PYTHON_BIN) -m unittest extensions.hermes_api.test_extension extensions.hermes_api.test_nine_router
	$(PYTHON_BIN) -m py_compile extensions/api_extension.py extensions/hermes_api_extension.py extensions/hermes_custom_gateway.py extensions/hermes_api/*.py
	$(GO_BIN) vet ./...
	cd contracttest && $(GO_BIN) vet ./...
	cd src && npm test
	cd src && npm run build

smoke-api:
	./scripts/SmokeAPI.sh

binary:
	mkdir -p bin
	$(GO_BIN) build -o bin/open-lumora ./cmd

build: binary backend-image frontend-image runtime-image support-images
	$(MAKE) bundle

image: backend-image frontend-image

backend-image:
	test -x $(ENTERPRISE_CONTEXT)/bin/open-lumora-enterprise || { echo "Build the enterprise binary first: make -C $(ENTERPRISE_CONTEXT) binary" >&2; exit 2; }
	$(CONTAINER_CLI) build --build-context enterprise=$(ENTERPRISE_CONTEXT) -f Dockerfile.backend -t $(OPEN_LUMORA_BACKEND_IMAGE) .

frontend-image:
	$(CONTAINER_CLI) build -f Dockerfile.frontend -t $(OPEN_LUMORA_FRONTEND_IMAGE) .

runtime-image:
	$(CONTAINER_CLI) image inspect $(OPEN_LUMORA_HERMES_BASE_IMAGE) >/dev/null
	$(CONTAINER_CLI) build --build-arg RUNTIME_BASE_IMAGE=$(OPEN_LUMORA_HERMES_BASE_IMAGE) -f Dockerfile.runtime -t $(OPEN_LUMORA_HERMES_IMAGE) .

support-images:
	$(CONTAINER_CLI) pull traefik:v3.6.16
	$(CONTAINER_CLI) pull clickhouse/clickhouse-server:25.8-alpine

bundle:
	CONTAINER_CLI=$(CONTAINER_CLI) IMAGE_TAG=$(IMAGE_TAG) OPEN_LUMORA_HERMES_IMAGE=$(OPEN_LUMORA_HERMES_IMAGE) ./scripts/BundleImages.sh

load-bundle:
	CONTAINER_CLI=$(CONTAINER_CLI) ./scripts/LoadImages.sh

install: load-bundle
	$(CONTAINER_CLI) compose --profile local up -d --no-build
