# syntax=docker/dockerfile:1.7
FROM node:22-bookworm-slim AS frontend
WORKDIR /src/frontend
COPY src/package.json src/package-lock.json ./
RUN npm ci
COPY src/ ./
RUN npm run build

FROM golang:1.26.5-bookworm AS backend
WORKDIR /src
COPY go.mod go.sum ./
RUN go mod download
COPY cmd ./cmd
COPY internal ./internal
COPY services ./services
RUN CGO_ENABLED=0 go build -trimpath -ldflags="-s -w" -o /out/open-lumora ./cmd

FROM debian:bookworm-slim
RUN apt-get update \
    && apt-get install -y --no-install-recommends ca-certificates curl \
    && rm -rf /var/lib/apt/lists/* \
    && useradd --system --uid 10001 --home /opt/open-lumora studio \
    && mkdir -p /opt/open-lumora/frontend /opt/data \
    && chown -R studio:studio /opt/open-lumora /opt/data
COPY --from=backend /out/open-lumora /usr/local/bin/open-lumora
COPY --from=frontend /src/frontend/dist /opt/open-lumora/frontend
USER studio
ENV START_MODE=local \
    HTTP_PORT=3000 \
    DATA_DIR=/opt/data/open-lumora \
    FRONTEND_DIR=/opt/open-lumora/frontend \
    HERMES_RUNTIME_MODE=remote \
    CONTROL_GATEWAY_URL=http://enterprise-gateway:3100
VOLUME ["/opt/data"]
EXPOSE 3000
HEALTHCHECK --interval=15s --timeout=3s --retries=10 CMD curl -fsS http://127.0.0.1:3000/api/v1/health || exit 1
ENTRYPOINT ["/usr/local/bin/open-lumora"]
