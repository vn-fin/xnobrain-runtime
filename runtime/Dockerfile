# syntax=docker/dockerfile:1.7
FROM node:22-bookworm AS nine-router-builder

ARG NINE_ROUTER_VERSION=v0.5.40
ARG NINE_ROUTER_NEXT_VERSION=16.2.10
RUN apt-get update \
    && apt-get install -y --no-install-recommends git python3 make g++ \
    && rm -rf /var/lib/apt/lists/* \
    && git clone --branch "${NINE_ROUTER_VERSION}" --depth 1 https://github.com/decolua/9router.git /src
WORKDIR /src
RUN --mount=type=cache,target=/root/.npm npm install --save-exact "next@${NINE_ROUTER_NEXT_VERSION}" \
    && NEXT_TELEMETRY_DISABLED=1 npm run build

FROM nousresearch/hermes-agent:latest

USER root
# Use 9router's supported standalone build layout. The npm CLI artifact starts
# but can return empty HTTP bodies when embedded into another base image.
COPY --from=nine-router-builder /src/public /opt/open-lumora/9router/public
COPY --from=nine-router-builder /src/.next/static /opt/open-lumora/9router/.next/static
COPY --from=nine-router-builder /src/.next/standalone/ /opt/open-lumora/9router/
COPY --from=nine-router-builder /src/open-sse /opt/open-lumora/9router/open-sse
COPY --from=nine-router-builder /src/src/mitm /opt/open-lumora/9router/src/mitm
COPY --from=nine-router-builder /src/node_modules/node-forge /opt/open-lumora/9router/node_modules/node-forge
COPY --from=nine-router-builder /src/node_modules/next /opt/open-lumora/9router/node_modules/next
COPY extensions/requirements.txt /opt/open-lumora/requirements.txt
RUN python3 -m ensurepip --upgrade \
    && python3 -m pip install --no-cache-dir -r /opt/open-lumora/requirements.txt
COPY extensions /opt/open-lumora/extensions
COPY runtime/hermes-custom-gateway /usr/local/bin/hermes-custom-gateway
COPY runtime/container-entrypoint.sh /usr/local/bin/open-lumora-runtime
COPY runtime/prepare-nine-router-auth.sh /usr/local/bin/open-lumora-prepare-nine-router-auth
RUN find /opt/open-lumora/extensions -type d -name __pycache__ -prune -exec rm -rf {} + \
    && find /opt/open-lumora/extensions -type f -name '*.pyc' -delete \
    && chmod 0755 /usr/local/bin/hermes-custom-gateway /usr/local/bin/open-lumora-runtime /usr/local/bin/open-lumora-prepare-nine-router-auth \
    && python3 -m py_compile /opt/open-lumora/extensions/api_extension.py /opt/open-lumora/extensions/hermes_custom_gateway.py /opt/open-lumora/extensions/hermes_api/*.py

ENV HERMES_HOME=/opt/data/open-lumora/root \
    HERMES_ROOT_PROFILE=/opt/data/open-lumora/root \
    HERMES_PROFILES_ROOT=/opt/data/open-lumora/profiles \
    HERMES_EXTENSION_ENTRYPOINT=/opt/open-lumora/extensions/hermes_custom_gateway.py \
    NINE_ROUTER_DATA_DIR=/opt/data/.9router \
    API_SERVER_ENABLED=true \
    API_SERVER_HOST=0.0.0.0 \
    API_SERVER_PORT=8642
VOLUME ["/opt/data"]
EXPOSE 8642 20128
HEALTHCHECK --interval=15s --timeout=3s --retries=10 CMD python3 -c "import os,urllib.request; token=os.environ.get('HERMES_RUNTIME_TOKEN',''); request=urllib.request.Request('http://127.0.0.1:8642/health',headers={'Authorization':'Bearer '+token} if token else {}); urllib.request.urlopen(request,timeout=2)" || exit 1
ENTRYPOINT ["/usr/local/bin/open-lumora-runtime"]
