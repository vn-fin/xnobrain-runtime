"""Timing logs retain correlation and omit user data."""

import asyncio
import unittest

from xnobrain.diagnostic_timing import timed, timing


class TimingTests(unittest.IsolatedAsyncioTestCase):
    async def test_concurrent_requests_have_separate_nested_correlation(self):
        @timed("total")
        async def operation():
            await asyncio.sleep(0)
            with timing("child"):
                pass

        with self.assertLogs("xnobrain.diagnostic_timing", level="INFO") as logs:
            await asyncio.gather(operation(), operation())
        children = [r for r in logs.records if r.phase == "child"]
        totals = [r for r in logs.records if r.phase == "total"]
        self.assertEqual(len({r.diagnostic_id for r in totals}), 2)
        self.assertEqual({r.diagnostic_id for r in children}, {r.diagnostic_id for r in totals})
        self.assertTrue(all(r.duration_ms >= 0 for r in logs.records))

    async def test_failure_and_cancellation_are_logged_without_messages(self):
        for error in [ValueError("secret"), asyncio.CancelledError("secret")]:

            @timed("failure")
            async def operation():
                raise error

            with self.assertLogs("xnobrain.diagnostic_timing", level="INFO") as logs:
                with self.assertRaises(type(error)):
                    await operation()
            self.assertEqual(logs.records[0].error, type(error).__name__)
            self.assertNotIn("secret", str(logs.records[0].__dict__))


class ModelsTransportTimingTests(unittest.IsolatedAsyncioTestCase):
    async def test_real_http_success_and_error_record_phases(self):
        from aiohttp import web

        from xnobrain.integrations.llm_router_support import LLMRouterAPIError
        from xnobrain.integrations.llm_router_transport import LLMRouterTransportMixin

        class Client(LLMRouterTransportMixin):
            def _request_headers(self):
                return {"Authorization": "Bearer secret"}

        status = 200

        async def models(request):
            return web.json_response({"data": [], "error": "secret"}, status=status)

        app = web.Application()
        app.router.add_get("/models", models)
        runner = web.AppRunner(app)
        await runner.setup()
        site = web.TCPSite(runner, "127.0.0.1", 0)
        await site.start()
        client = Client()
        client.base_url = f"http://127.0.0.1:{site._server.sockets[0].getsockname()[1]}"
        try:
            for status in [200, 503]:
                with self.assertLogs("xnobrain.diagnostic_timing", level="INFO") as logs:
                    if status == 200:
                        self.assertEqual((await client._request("GET", "/models"))["data"], [])
                    else:
                        with self.assertRaises(LLMRouterAPIError):
                            await client._request("GET", "/models")
                self.assertEqual(
                    {r.phase for r in logs.records},
                    {
                        "router.models.credentials",
                        "router.models.headers",
                        "router.models.body",
                        "router.models.total",
                    },
                )
                self.assertEqual(len({r.diagnostic_id for r in logs.records}), 1)
                self.assertNotIn("secret", str([r.__dict__ for r in logs.records]))
        finally:
            await runner.cleanup()
