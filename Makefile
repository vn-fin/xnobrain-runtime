GO_BIN := $(if $(wildcard $(CURDIR)/.tools/go/bin/go),$(CURDIR)/.tools/go/bin/go,go)
GOFMT_BIN := $(if $(wildcard $(CURDIR)/.tools/go/bin/gofmt),$(CURDIR)/.tools/go/bin/gofmt,gofmt)
PYTHON_BIN := $(if $(wildcard $(CURDIR)/.tools/python/bin/python),$(CURDIR)/.tools/python/bin/python,python3)
CONTAINER_CLI ?= docker
IMAGE_TAG ?= local
OPEN_LUMORA_BACKEND_IMAGE ?= open-lumora-backend:$(IMAGE_TAG)
OPEN_LUMORA_FRONTEND_IMAGE ?= open-lumora-frontend:$(IMAGE_TAG)
OPEN_LUMORA_CONTROL_NETWORK ?= open-lumora-control

.PHONY: dev backend src test check smoke-api binary build image backend-image frontend-image bundle load-bundle network install

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

build: binary backend-image frontend-image
	$(MAKE) bundle

image: backend-image frontend-image

backend-image:
	$(CONTAINER_CLI) build -f Dockerfile.backend -t $(OPEN_LUMORA_BACKEND_IMAGE) .

frontend-image:
	$(CONTAINER_CLI) build -f Dockerfile.frontend -t $(OPEN_LUMORA_FRONTEND_IMAGE) .

bundle:
	CONTAINER_CLI=$(CONTAINER_CLI) IMAGE_TAG=$(IMAGE_TAG) ./scripts/BundleImages.sh

load-bundle:
	CONTAINER_CLI=$(CONTAINER_CLI) ./scripts/LoadImages.sh

network:
	$(CONTAINER_CLI) network inspect $(OPEN_LUMORA_CONTROL_NETWORK) >/dev/null 2>&1 || $(CONTAINER_CLI) network create --internal $(OPEN_LUMORA_CONTROL_NETWORK)

install: load-bundle network
	$(CONTAINER_CLI) compose --profile local up -d --no-build
