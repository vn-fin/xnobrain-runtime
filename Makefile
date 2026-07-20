GO_BIN := $(if $(wildcard $(CURDIR)/.tools/go/bin/go),$(CURDIR)/.tools/go/bin/go,go)
GOFMT_BIN := $(if $(wildcard $(CURDIR)/.tools/go/bin/gofmt),$(CURDIR)/.tools/go/bin/gofmt,gofmt)
PYTHON_BIN := $(if $(wildcard $(CURDIR)/.tools/python/bin/python),$(CURDIR)/.tools/python/bin/python,python3)

.PHONY: dev backend src test check build

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

build:
	cd src && npm run build
	mkdir -p bin
	$(GO_BIN) build -o bin/open-lumora ./cmd
