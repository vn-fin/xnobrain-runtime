# syntax=docker/dockerfile:1.7
FROM node:22-bookworm-slim AS frontend
WORKDIR /src/frontend
COPY frontend/package.json frontend/package-lock.json ./
RUN npm ci
COPY frontend/ ./
RUN npm run build

FROM golang:1.26.5-bookworm AS backend
WORKDIR /src
COPY go.mod go.sum ./
RUN go mod download
COPY cmd ./cmd
COPY internal ./internal
COPY services ./services
RUN CGO_ENABLED=0 go build -trimpath -ldflags="-s -w" -o /out/open-lumora ./cmd

# The runtime image supplies the Hermes CLI and its Python/tooling environment.
FROM nousresearch/hermes-agent:latest
USER root
RUN npm install -g --include=optional 9router@latest
COPY extensions/requirements.txt /opt/open-lumora/requirements.txt
RUN python3 -m pip install --no-cache-dir -r /opt/open-lumora/requirements.txt
COPY --from=backend /out/open-lumora /usr/local/bin/open-lumora
COPY --from=frontend /src/frontend/dist /opt/open-lumora/frontend
COPY extensions /opt/open-lumora/hermes
COPY scripts/hermes-custom-gateway /usr/local/bin/hermes-custom-gateway
COPY scripts/container-entrypoint.sh /usr/local/bin/open-lumora-container
RUN find /opt/open-lumora/hermes -type d -name __pycache__ -prune -exec rm -rf {} + \
    && find /opt/open-lumora/hermes -type f -name '*.pyc' -delete \
    && chmod 0755 /usr/local/bin/hermes-custom-gateway /usr/local/bin/open-lumora-container \
    && python3 -m py_compile /opt/open-lumora/hermes/api_extension.py /opt/open-lumora/hermes/hermes_custom_gateway.py /opt/open-lumora/hermes/hermes_api/*.py
ENV HTTP_PORT=3000 \
    DATA_DIR=/opt/data/open-lumora \
    FRONTEND_DIR=/opt/open-lumora/frontend \
    HERMES_BIN=hermes \
    HERMES_GATEWAY_BIN=hermes-custom-gateway \
    HERMES_RUNTIME_MODE=gateway \
    NINE_ROUTER_URL=http://127.0.0.1:20128 \
    NINE_ROUTER_DATA_DIR=/opt/data/.9router
VOLUME ["/opt/data"]
EXPOSE 3000
CMD ["/usr/local/bin/open-lumora-container"]
