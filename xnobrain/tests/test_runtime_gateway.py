"""Private Runtime gRPC gateway contracts."""

from __future__ import annotations

import os
import unittest
from unittest.mock import patch

import grpc
from aiohttp import web

from xnobrain.common.v1 import http_stream_pb2 as http_pb2
from xnobrain.integrations.runtime_gateway import (
    RuntimeGatewayService,
    start_runtime_gateway,
)
from xnobrain.runtime.v1 import runtime_gateway_pb2 as gateway_pb2
from xnobrain.runtime.v1 import runtime_gateway_pb2_grpc as gateway_grpc
from xnobrain.trusted_context import (
    conversation_context_signature,
    encode_conversation_context,
)

TOKEN = "runtime-internal-test-token"


def _head(*, headers=(), principal=True, path="/xnobrain/api/runtime/v1/test"):
    return gateway_pb2.RuntimeGatewayServiceProxyRequest(
        head=http_pb2.HttpRequestHead(
            request_id="request-1",
            method="POST",
            path=path,
            raw_query="mode=stream",
            headers=list(headers),
            principal=http_pb2.VerifiedPrincipal(
                user_id="user-1" if principal else "",
                tenant_id="tenant-1",
                organization_id="org-1",
            ),
        )
    )


async def _frames(*values):
    for value in values:
        yield value


class RuntimeGatewayTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self) -> None:
        self.received = {}

        async def relay_target(request: web.Request) -> web.StreamResponse:
            self.received["method"] = request.method
            self.received["path_qs"] = request.path_qs
            self.received["headers"] = list(request.headers.items())
            body = bytearray()
            async for chunk in request.content.iter_chunked(2):
                body.extend(chunk)
            self.received["body"] = bytes(body)

            response = web.StreamResponse(status=206)
            response.headers.add("x-runtime-result", "first")
            response.headers.add("x-runtime-result", "second")
            response.headers.add("connection", "close")
            await response.prepare(request)
            await response.write(b"stream-")
            await response.write(b"result")
            await response.write_eof()
            return response

        app = web.Application()
        app.router.add_route("*", "/{tail:.*}", relay_target)
        self.http_runner = web.AppRunner(app)
        await self.http_runner.setup()
        http_site = web.TCPSite(self.http_runner, "127.0.0.1", 0)
        await http_site.start()
        self.http_port = http_site._server.sockets[0].getsockname()[1]

        self.grpc_server = grpc.aio.server()
        relay = RuntimeGatewayService(TOKEN, self.http_port)
        gateway_grpc.add_RuntimeGatewayServiceServicer_to_server(relay, self.grpc_server)
        gateway_grpc.add_NodeGatewayServiceServicer_to_server(relay, self.grpc_server)
        grpc_port = self.grpc_server.add_insecure_port("127.0.0.1:0")
        await self.grpc_server.start()
        self.channel = grpc.aio.insecure_channel(f"127.0.0.1:{grpc_port}")
        self.stub = gateway_grpc.RuntimeGatewayServiceStub(self.channel)
        self.node_stub = gateway_grpc.NodeGatewayServiceStub(self.channel)

    async def asyncTearDown(self) -> None:
        await self.channel.close()
        await self.grpc_server.stop(grace=None)
        await self.http_runner.cleanup()

    async def test_accepts_node_gateway_frames_for_static_dev_runtime(self) -> None:
        call = self.node_stub.Proxy(
            _frames(
                gateway_pb2.NodeGatewayServiceProxyRequest(
                    head=gateway_pb2.WorkspaceRequestHead(
                        project="dev",
                        instance="source-runtime",
                        http=http_pb2.HttpRequestHead(
                            request_id="request-node",
                            method="GET",
                            path="/xnobrain/api/runtime/v1/health",
                            principal=http_pb2.VerifiedPrincipal(user_id="user-1"),
                        ),
                    )
                ),
                gateway_pb2.NodeGatewayServiceProxyRequest(end=http_pb2.StreamEnd()),
            ),
            metadata=(("x-xnobrain-internal-token", TOKEN),),
        )
        responses = [response async for response in call]
        self.assertEqual(responses[0].head.status_code, 206)
        self.assertEqual(self.received["path_qs"], "/xnobrain/api/runtime/v1/health")

    async def test_streams_body_and_strips_browser_credentials(self) -> None:
        request_headers = (
            http_pb2.Header(name="authorization", values=[b"Bearer browser-token"]),
            http_pb2.Header(name="cookie", values=[b"session=browser-secret"]),
            http_pb2.Header(name="proxy-authorization", values=[b"proxy-secret"]),
            http_pb2.Header(name="x-client-value", values=[b"one", b"two"]),
            http_pb2.Header(
                name="x-xnobrain-verified-conversation-context",
                values=[b"forged"],
            ),
        )
        call = self.stub.Proxy(
            _frames(
                _head(headers=request_headers),
                gateway_pb2.RuntimeGatewayServiceProxyRequest(
                    body_chunk=http_pb2.BodyChunk(sequence=0, data=b"upload-")
                ),
                gateway_pb2.RuntimeGatewayServiceProxyRequest(
                    body_chunk=http_pb2.BodyChunk(sequence=1, data=b"body")
                ),
                gateway_pb2.RuntimeGatewayServiceProxyRequest(end=http_pb2.StreamEnd()),
            ),
            metadata=(("x-xnobrain-internal-token", TOKEN),),
        )

        responses = [response async for response in call]

        self.assertEqual(self.received["method"], "POST")
        self.assertEqual(
            self.received["path_qs"],
            "/xnobrain/api/runtime/v1/test?mode=stream",
        )
        self.assertEqual(self.received["body"], b"upload-body")
        relayed_headers = [(name.lower(), value) for name, value in self.received["headers"]]
        self.assertNotIn("authorization", {name for name, _ in relayed_headers})
        self.assertNotIn("cookie", {name for name, _ in relayed_headers})
        self.assertNotIn("proxy-authorization", {name for name, _ in relayed_headers})
        self.assertEqual(
            [value for name, value in relayed_headers if name == "x-client-value"],
            ["one", "two"],
        )
        relayed = dict(relayed_headers)
        self.assertEqual(relayed["x-xnobrain-verified-subject"], "user-1")
        self.assertEqual(relayed["x-xnobrain-verified-tenant"], "tenant-1")
        self.assertEqual(relayed["x-xnobrain-verified-organization"], "org-1")
        self.assertRegex(relayed["x-xnobrain-principal-signature"], r"^[0-9a-f]{64}$")
        self.assertNotIn("x-xnobrain-verified-conversation-context", relayed)

        self.assertEqual(responses[0].WhichOneof("frame"), "head")
        self.assertEqual(responses[0].head.status_code, 206)
        response_headers = {
            header.name: [bytes(value).decode("latin-1") for value in header.values]
            for header in responses[0].head.headers
        }
        self.assertEqual(response_headers["x-runtime-result"], ["first", "second"])
        self.assertNotIn("connection", response_headers)
        body_frames = [
            response.body_chunk for response in responses if response.HasField("body_chunk")
        ]
        self.assertEqual([frame.sequence for frame in body_frames], list(range(len(body_frames))))
        self.assertEqual(b"".join(frame.data for frame in body_frames), b"stream-result")
        self.assertEqual(responses[-1].WhichOneof("frame"), "end")

    async def test_relays_only_control_signed_conversation_context_claims(self) -> None:
        context = {
            "schema_version": 1,
            "id": "cctx-1",
            "owner_kind": "organization",
            "organization_id": "org-1",
            "payer_kind": "personal",
            "state": "active",
        }
        encoded = encode_conversation_context(context)
        signature = conversation_context_signature(TOKEN, "user-1", "tenant-1", "org-1", encoded)
        headers = (
            http_pb2.Header(
                name="x-xnobrain-verified-conversation-context",
                values=[encoded.encode()],
            ),
            http_pb2.Header(
                name="x-xnobrain-conversation-context-signature",
                values=[signature.encode()],
            ),
        )
        call = self.stub.Proxy(
            _frames(
                _head(headers=headers),
                gateway_pb2.RuntimeGatewayServiceProxyRequest(end=http_pb2.StreamEnd()),
            ),
            metadata=(("x-xnobrain-internal-token", TOKEN),),
        )

        _ = [response async for response in call]

        relayed = {name.lower(): value for name, value in self.received["headers"]}
        self.assertEqual(relayed["x-xnobrain-verified-conversation-context"], encoded)
        self.assertEqual(relayed["x-xnobrain-conversation-context-signature"], signature)

    async def test_rejects_invalid_internal_token(self) -> None:
        call = self.stub.Proxy(
            _frames(
                _head(), gateway_pb2.RuntimeGatewayServiceProxyRequest(end=http_pb2.StreamEnd())
            ),
            metadata=(("x-xnobrain-internal-token", "wrong-token"),),
        )
        with self.assertRaises(grpc.aio.AioRpcError) as raised:
            await call.read()
        self.assertEqual(
            raised.exception.code(),
            grpc.StatusCode.UNAUTHENTICATED,
            raised.exception.details(),
        )

    async def test_rejects_missing_verified_principal(self) -> None:
        call = self.stub.Proxy(
            _frames(_head(principal=False)),
            metadata=(("x-xnobrain-internal-token", TOKEN),),
        )
        with self.assertRaises(grpc.aio.AioRpcError) as raised:
            await call.read()
        self.assertEqual(raised.exception.code(), grpc.StatusCode.INVALID_ARGUMENT)

    async def test_rejects_malformed_frame_order_and_sequence(self) -> None:
        malformed_calls = (
            self.stub.Proxy(
                _frames(gateway_pb2.RuntimeGatewayServiceProxyRequest(end=http_pb2.StreamEnd())),
                metadata=(("x-xnobrain-internal-token", TOKEN),),
            ),
            self.stub.Proxy(
                _frames(
                    _head(),
                    gateway_pb2.RuntimeGatewayServiceProxyRequest(
                        body_chunk=http_pb2.BodyChunk(sequence=1, data=b"out-of-order")
                    ),
                ),
                metadata=(("x-xnobrain-internal-token", TOKEN),),
            ),
        )
        for call in malformed_calls:
            with self.assertRaises(grpc.aio.AioRpcError) as raised:
                await call.read()
            self.assertEqual(raised.exception.code(), grpc.StatusCode.INVALID_ARGUMENT)

    async def test_optional_startup_requires_token_only_when_enabled(self) -> None:
        with patch.dict(os.environ, {"RUNTIME_GRPC_ENABLED": "false"}, clear=False):
            self.assertIsNone(await start_runtime_gateway())

        with patch.dict(os.environ, {"RUNTIME_GRPC_ENABLED": "true"}, clear=False):
            os.environ.pop("RUNTIME_INTERNAL_SERVICE_TOKEN", None)
            with self.assertRaisesRegex(RuntimeError, "RUNTIME_INTERNAL_SERVICE_TOKEN"):
                await start_runtime_gateway()


if __name__ == "__main__":
    unittest.main()
