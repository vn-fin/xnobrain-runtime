"""Private streaming gRPC adapter for Control/node-gateway Runtime traffic."""

from __future__ import annotations

import asyncio
import hmac
import os
import re
from collections.abc import AsyncIterator

import aiohttp
import grpc

from xnobrain.common.v1 import http_stream_pb2 as http_pb2
from xnobrain.runtime.v1 import runtime_gateway_pb2 as gateway_pb2
from xnobrain.runtime.v1 import runtime_gateway_pb2_grpc as gateway_grpc


_MAX_CHUNK_BYTES = 64 * 1024
_METHOD_RE = re.compile(r"^[A-Z][A-Z0-9_-]{0,31}$")
_FORBIDDEN_REQUEST_HEADERS = {
    "authorization",
    "cookie",
    "host",
    "proxy-authorization",
    "connection",
    "keep-alive",
    "proxy-authenticate",
    "te",
    "trailer",
    "transfer-encoding",
    "upgrade",
    "content-length",
}
_FORBIDDEN_RESPONSE_HEADERS = {
    "connection",
    "keep-alive",
    "proxy-authenticate",
    "proxy-authorization",
    "te",
    "trailer",
    "transfer-encoding",
    "upgrade",
    "content-length",
}


def _metadata_value(context: grpc.aio.ServicerContext, name: str) -> str:
    lowered = name.lower()
    for item in context.invocation_metadata():
        if item.key.lower() == lowered:
            return str(item.value)
    return ""


def _request_headers(values) -> list[tuple[str, str]]:
    result: list[tuple[str, str]] = []
    for header in values:
        name = str(header.name or "").strip().lower()
        if not name or name in _FORBIDDEN_REQUEST_HEADERS:
            continue
        for value in header.values:
            result.append((name, bytes(value).decode("latin-1")))
    return result


def _response_headers(values) -> list[http_pb2.Header]:
    grouped: dict[str, list[bytes]] = {}
    for name, value in values:
        normalized = str(name or "").strip().lower()
        if not normalized or normalized in _FORBIDDEN_RESPONSE_HEADERS:
            continue
        grouped.setdefault(normalized, []).append(str(value).encode("latin-1"))
    return [
        http_pb2.Header(name=name, values=items)
        for name, items in grouped.items()
    ]


class RuntimeGatewayService(
    gateway_grpc.RuntimeGatewayServiceServicer,
    gateway_grpc.NodeGatewayServiceServicer,
):
    """Relay authenticated gRPC frames into the existing local HTTP app.

    The NodeGateway-compatible method lets the explicit four-container dev
    topology connect Control directly to this Runtime without an extra gateway
    process. Workspace project/instance fields are discarded at this trusted
    development boundary.
    """

    def __init__(self, token: str, http_port: int):
        self._token = token
        self._http_port = http_port

    async def Proxy(self, request_iterator, context):  # noqa: N802
        try:
            first = await anext(request_iterator)
        except StopAsyncIteration:
            await context.abort(grpc.StatusCode.INVALID_ARGUMENT, "request head is required")
        supplied = _metadata_value(context, "x-xnobrain-internal-token")
        if not supplied or not hmac.compare_digest(supplied, self._token):
            # grpc.aio requires a bidirectional handler to read its initial
            # frame before aborting reliably. No request content is relayed
            # until this service identity check succeeds.
            await context.abort(
                grpc.StatusCode.UNAUTHENTICATED,
                "invalid internal service identity",
            )
        if first.WhichOneof("frame") != "head":
            await context.abort(grpc.StatusCode.INVALID_ARGUMENT, "request head must be first")

        received_head = first.head
        head = received_head.http if hasattr(received_head, "http") else received_head
        method = str(head.method or "").strip().upper()
        path = str(head.path or "").strip()
        if not _METHOD_RE.fullmatch(method) or not path.startswith("/") or "://" in path:
            await context.abort(grpc.StatusCode.INVALID_ARGUMENT, "invalid HTTP request head")
        if not str(head.principal.user_id or "").strip():
            await context.abort(grpc.StatusCode.INVALID_ARGUMENT, "verified principal is required")

        async def request_body() -> AsyncIterator[bytes]:
            sequence = 0
            async for frame in request_iterator:
                kind = frame.WhichOneof("frame")
                if kind == "end":
                    return
                if kind != "body_chunk":
                    await context.abort(grpc.StatusCode.INVALID_ARGUMENT, "invalid request frame order")
                chunk = frame.body_chunk
                if chunk.sequence != sequence or len(chunk.data) > _MAX_CHUNK_BYTES:
                    await context.abort(grpc.StatusCode.INVALID_ARGUMENT, "invalid request body chunk")
                sequence += 1
                if chunk.data:
                    yield bytes(chunk.data)
            await context.abort(grpc.StatusCode.INVALID_ARGUMENT, "request end frame is required")

        target = f"http://127.0.0.1:{self._http_port}{path}"
        raw_query = str(head.raw_query or "").lstrip("?")
        if raw_query:
            target += "?" + raw_query
        timeout = aiohttp.ClientTimeout(total=None, connect=10, sock_connect=10)
        async with aiohttp.ClientSession(
            timeout=timeout,
            trust_env=False,
            auto_decompress=False,
        ) as session:
            try:
                async with session.request(
                    method,
                    target,
                    headers=_request_headers(head.headers),
                    data=request_body(),
                    allow_redirects=False,
                ) as response:
                    yield gateway_pb2.RuntimeGatewayServiceProxyResponse(
                        head=http_pb2.HttpResponseHead(
                            status_code=response.status,
                            headers=_response_headers(response.headers.items()),
                        )
                    )
                    sequence = 0
                    async for data in response.content.iter_chunked(_MAX_CHUNK_BYTES):
                        yield gateway_pb2.RuntimeGatewayServiceProxyResponse(
                            body_chunk=http_pb2.BodyChunk(sequence=sequence, data=data)
                        )
                        sequence += 1
                    yield gateway_pb2.RuntimeGatewayServiceProxyResponse(end=http_pb2.StreamEnd())
            except asyncio.CancelledError:
                raise
            except (aiohttp.ClientError, TimeoutError):
                await context.abort(grpc.StatusCode.UNAVAILABLE, "Runtime HTTP service is unavailable")


async def start_runtime_gateway():
    """Start the optional private gRPC listener in the FastAPI process."""

    enabled = os.getenv("RUNTIME_GRPC_ENABLED", "").strip().lower() in {
        "1", "true", "yes", "on",
    }
    if not enabled:
        return None
    token = os.getenv("RUNTIME_INTERNAL_SERVICE_TOKEN", "").strip()
    if not token:
        raise RuntimeError("RUNTIME_INTERNAL_SERVICE_TOKEN is required when Runtime gRPC is enabled")
    grpc_port = int(os.getenv("RUNTIME_GRPC_PORT", "3001"))
    http_port = int(os.getenv("API_SERVER_PORT", "3000"))
    if grpc_port < 1 or grpc_port > 65535 or http_port < 1 or http_port > 65535:
        raise RuntimeError("Runtime HTTP and gRPC ports must be between 1 and 65535")

    server = grpc.aio.server(options=(
        ("grpc.max_receive_message_length", 128 * 1024),
        ("grpc.max_send_message_length", 128 * 1024),
    ))
    relay = RuntimeGatewayService(token, http_port)
    gateway_grpc.add_RuntimeGatewayServiceServicer_to_server(relay, server)
    gateway_grpc.add_NodeGatewayServiceServicer_to_server(relay, server)
    if server.add_insecure_port(f"0.0.0.0:{grpc_port}") == 0:
        raise RuntimeError("could not bind Runtime gRPC listener")
    await server.start()
    return server


__all__ = ["RuntimeGatewayService", "start_runtime_gateway"]
