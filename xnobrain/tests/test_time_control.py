"""Time Control request validation; no live settings changes."""

import unittest

from pydantic import ValidationError

from xnobrain.models.automation import CronCreate


class CronTimezoneContractTests(unittest.TestCase):
    def test_supported_iana_zones(self):
        for zone in ("UTC", "Etc/UTC", "Asia/Ho_Chi_Minh", "Europe/Berlin", "Australia/Lord_Howe"):
            request = CronCreate(agent_id="synthetic", name="Test", prompt="Test", timezone=zone)
            self.assertEqual(request.timezone, zone)

    def test_invalid_and_ambiguous_timezone_rejected(self):
        for zone in ("", "CST", "UTC+7", "/etc/localtime", "../UTC", "Asia/../UTC", "Unknown/Zone"):
            with self.subTest(zone=zone), self.assertRaises(ValidationError):
                CronCreate(agent_id="synthetic", name="Test", prompt="Test", timezone=zone)


class CalendarPreviewTests(unittest.TestCase):
    def test_random_and_hashed_fields_reject_before_evaluation(self):
        from datetime import datetime, timezone

        from xnobrain.integrations.schedule_preview import preview_calendar_runs

        cutoff = datetime(2026, 9, 9, tzinfo=timezone.utc)
        for expression in (
            "R 9 * * *",
            "r(0-30) 9 * * *",
            "R/5 9 * * *",
            "0,R 9 * * *",
            "R,0 9 * * *",
            "0,R,15 9 * * *",
            "H,0 9 * * *",
            "H 9 * * *",
            "H(0-30)/5 9 * * *",
        ):
            with (
                self.subTest(expression=expression),
                self.assertRaisesRegex(ValueError, "random or hashed"),
            ):
                preview_calendar_runs(expression, "UTC", cutoff)
        # Named months and weekdays remain ordinary deterministic calendar fields.
        result = preview_calendar_runs("0 9 * MAR THU", "UTC", cutoff, count=1)
        self.assertEqual(len(result), 1)

    def test_daily_vietnam_nine_is_two_utc(self):
        from datetime import datetime, timezone

        from xnobrain.app import XNOBrainApplication
        from xnobrain.integrations.schedule_preview import preview_calendar_runs

        self.assertIsNotNone(XNOBrainApplication)
        rows = preview_calendar_runs(
            "0 9 * * *", "Asia/Ho_Chi_Minh", datetime(2026, 9, 9, tzinfo=timezone.utc)
        )
        self.assertEqual(len(rows), 5)
        self.assertEqual(rows[0]["utc"], "2026-09-09T02:00:00Z")
        self.assertEqual(rows[-1]["utc"], "2026-09-13T02:00:00Z")

    def test_berlin_gap_skipped_and_fold_uses_earlier_offset(self):
        from datetime import datetime, timezone

        from xnobrain.app import XNOBrainApplication
        from xnobrain.integrations.schedule_preview import preview_calendar_runs

        self.assertIsNotNone(XNOBrainApplication)
        gap = preview_calendar_runs(
            "30 2 * * *", "Europe/Berlin", datetime(2026, 3, 28, 3, tzinfo=timezone.utc), count=1
        )
        self.assertEqual(gap[0]["utc"], "2026-03-30T00:30:00Z")
        fold = preview_calendar_runs(
            "30 2 * * *", "Europe/Berlin", datetime(2026, 10, 24, 3, tzinfo=timezone.utc), count=2
        )
        self.assertEqual(fold[0]["utc"], "2026-10-25T00:30:00Z")
        self.assertEqual(fold[1]["utc"], "2026-10-26T01:30:00Z")

    def test_lord_howe_half_hour_gap_and_fold(self):
        from datetime import datetime, timezone

        from xnobrain.app import XNOBrainApplication
        from xnobrain.integrations.schedule_preview import preview_calendar_runs

        self.assertIsNotNone(XNOBrainApplication)
        gap = preview_calendar_runs(
            "15 2 * * *",
            "Australia/Lord_Howe",
            datetime(2026, 10, 3, 16, tzinfo=timezone.utc),
            count=1,
        )
        self.assertEqual(gap[0]["utc"], "2026-10-04T15:15:00Z")
        fold = preview_calendar_runs(
            "45 1 * * *",
            "Australia/Lord_Howe",
            datetime(2026, 4, 4, 13, tzinfo=timezone.utc),
            count=2,
        )
        self.assertEqual(fold[0]["utc"], "2026-04-04T14:45:00Z")
        self.assertEqual(fold[1]["utc"], "2026-04-05T15:15:00Z")

    def test_leap_day_and_strict_cutoff(self):
        from datetime import datetime, timezone

        from xnobrain.app import XNOBrainApplication
        from xnobrain.integrations.schedule_preview import preview_calendar_runs

        self.assertIsNotNone(XNOBrainApplication)
        rows = preview_calendar_runs(
            "0 0 29 2 *", "UTC", datetime(2028, 2, 29, tzinfo=timezone.utc), count=2
        )
        self.assertEqual(
            [row["utc"] for row in rows],
            [
                "2032-02-29T00:00:00Z",
                "2036-02-29T00:00:00Z",
            ],
        )


class SchedulePreviewAPITests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        from xnobrain.tests.test_marketplace import MarketplaceExportAPITests

        self.fixture = MarketplaceExportAPITests()
        self.fixture.setUp()

    def tearDown(self):
        self.fixture.tearDown()

    async def test_preview_route_returns_five_times_without_schedule_mutation(self):
        from pathlib import Path

        from httpx import ASGITransport, AsyncClient

        root = Path(self.fixture.tmp.name)
        before = {p.relative_to(root): p.read_bytes() for p in root.rglob("*") if p.is_file()}
        async with AsyncClient(
            transport=ASGITransport(app=self.fixture.app), base_url="http://test"
        ) as client:
            response = await client.post(
                "/xnobrain/api/runtime/v1/cron/schedule-preview",
                json={
                    "schedule": "0 9 * * *",
                    "timezone": "Asia/Ho_Chi_Minh",
                    "after": "2026-09-09T00:00:00Z",
                },
            )
        self.assertEqual(response.status_code, 200, response.text)
        data = response.json()["data"]
        self.assertEqual(len(data["occurrences"]), 5)
        self.assertEqual(data["occurrences"][0]["utc"], "2026-09-09T02:00:00Z")
        self.assertFalse(data["executor_parity_verified"])
        self.assertEqual(
            before, {p.relative_to(root): p.read_bytes() for p in root.rglob("*") if p.is_file()}
        )

    async def test_preview_rejects_unsafe_or_ambiguous_request(self):
        from httpx import ASGITransport, AsyncClient

        async with AsyncClient(
            transport=ASGITransport(app=self.fixture.app), base_url="http://test"
        ) as client:
            for changes in (
                {"timezone": "CST"},
                {"after": "2026-09-09T09:00:00"},
                {"count": 21},
                {"organization_id": "foreign"},
            ):
                response = await client.post(
                    "/xnobrain/api/runtime/v1/cron/schedule-preview",
                    json={
                        "schedule": "0 9 * * *",
                        "timezone": "UTC",
                        **changes,
                    },
                )
                self.assertEqual(response.status_code, 422, response.text)

    async def test_invalid_calendar_has_safe_bounded_error(self):
        from httpx import ASGITransport, AsyncClient

        async with AsyncClient(
            transport=ASGITransport(app=self.fixture.app), base_url="http://test"
        ) as client:
            for expression in ("private-marker 9 * * *", "0 0 31 2 *"):
                response = await client.post(
                    "/xnobrain/api/runtime/v1/cron/schedule-preview",
                    json={
                        "schedule": expression,
                        "timezone": "UTC",
                        "after": "2026-09-09T00:00:00Z",
                    },
                )
                self.assertEqual(response.status_code, 400, response.text)
                self.assertNotIn("private-marker", response.text)

    async def test_preview_cutoff_overflow_is_safe_validation_error(self):
        from httpx import ASGITransport, AsyncClient

        async with AsyncClient(
            transport=ASGITransport(app=self.fixture.app), base_url="http://test"
        ) as client:
            response = await client.post(
                "/xnobrain/api/runtime/v1/cron/schedule-preview",
                json={
                    "schedule": "0 9 * * *",
                    "timezone": "Pacific/Kiritimati",
                    "after": "9999-12-31T23:59:00Z",
                },
            )
        self.assertEqual(response.status_code, 400, response.text)
        self.assertIn("supported calendar range", response.text)

    async def test_generated_occurrence_overflow_is_safe(self):
        from httpx import ASGITransport, AsyncClient

        async with AsyncClient(
            transport=ASGITransport(app=self.fixture.app), base_url="http://test"
        ) as client:
            response = await client.post(
                "/xnobrain/api/runtime/v1/cron/schedule-preview",
                json={
                    "schedule": "59 23 31 12 *",
                    "timezone": "Etc/GMT+12",
                    "after": "9999-12-30T00:00:00Z",
                    "count": 1,
                },
            )
        self.assertEqual(response.status_code, 400, response.text)
        self.assertIn("supported calendar range", response.text)

    async def test_preview_openapi_has_typed_response(self):
        schema = self.fixture.app.openapi()
        operation = schema["paths"]["/xnobrain/api/runtime/v1/cron/schedule-preview"]["post"]
        self.assertIn("200", operation["responses"])
        models = schema["components"]["schemas"]
        result = models["CronSchedulePreviewResult"]
        self.assertIn("executor_parity_verified", result["required"])
        self.assertEqual(result["properties"]["occurrences"]["maxItems"], 20)
        self.assertEqual(result["properties"]["occurrences"]["minItems"], 1)
        self.assertFalse(result["additionalProperties"])

    async def test_ambiguous_creation_rejects_without_native_mutation(self):
        from unittest.mock import patch

        from httpx import ASGITransport, AsyncClient

        cron = self.fixture.composition.service.cron
        async with AsyncClient(
            transport=ASGITransport(app=self.fixture.app), base_url="http://test"
        ) as client:
            with patch.object(cron, "create_job") as create:
                response = await client.post(
                    "/xnobrain/api/runtime/v1/cron/jobs",
                    json={
                        "agent_id": "synthetic",
                        "name": "Ambiguous",
                        "prompt": "Synthetic only",
                        "interval_minutes": 60,
                        "schedule": "0 9 * * *",
                        "timezone": "Asia/Ho_Chi_Minh",
                    },
                )
            self.assertEqual(response.status_code, 422)
            create.assert_not_called()

    async def test_naive_absolute_api_rejects_without_persisting_job(self):
        from httpx import ASGITransport, AsyncClient

        async with AsyncClient(
            transport=ASGITransport(app=self.fixture.app), base_url="http://test"
        ) as client:
            agent = await client.post(
                "/xnobrain/api/runtime/v1/agents", json={"name": "Synthetic one-shot"}
            )
            self.assertEqual(agent.status_code, 201)
            agent_id = agent.json()["data"]["id"]
            response = await client.post(
                "/xnobrain/api/runtime/v1/cron/jobs",
                json={
                    "agent_id": agent_id,
                    "name": "Ambiguous one-shot",
                    "prompt": "Synthetic only",
                    "schedule": "2099-10-25T02:30:00",
                    "timezone": "Europe/Berlin",
                },
            )
            self.assertEqual(response.status_code, 400, response.text)
            self.assertIn("explicit UTC offset", response.text)
            self.assertNotIn("2099-10-25", response.text)
            jobs = await client.get(f"/xnobrain/api/runtime/v1/cron/jobs?agent_id={agent_id}")
            self.assertEqual(jobs.status_code, 200)
            self.assertEqual(jobs.json()["data"], [])

    async def test_blueprint_creation_persists_explicit_timezone(self):
        from unittest.mock import patch

        from httpx import ASGITransport, AsyncClient

        cron = self.fixture.composition.service.cron
        spec = {
            "name": "Synthetic blueprint",
            "prompt": "Synthetic only",
            "schedule": "0 9 * * *",
            "deliver": "local",
        }
        async with AsyncClient(
            transport=ASGITransport(app=self.fixture.app), base_url="http://test"
        ) as client:
            agent = await client.post(
                "/xnobrain/api/runtime/v1/agents", json={"name": "Zone blueprint"}
            )
            self.assertEqual(agent.status_code, 201)
            agent_id = agent.json()["data"]["id"]
            body = {
                "agent_id": agent_id,
                "blueprint": "synthetic",
                "timezone": "Asia/Ho_Chi_Minh",
            }
            with patch.object(cron.delivery, "fill", return_value=spec) as fill:
                invalid = await client.post(
                    "/xnobrain/api/runtime/v1/cron/blueprints/instantiate",
                    json={**body, "timezone": "CST"},
                )
                self.assertEqual(invalid.status_code, 422)
                fill.assert_not_called()
                response = await client.post(
                    "/xnobrain/api/runtime/v1/cron/blueprints/instantiate", json=body
                )
            self.assertEqual(response.status_code, 201, response.text)
            job = response.json()["data"]
            self.assertEqual(job["timezone"], "Asia/Ho_Chi_Minh")
            self.assertIn("T02:00:00", job["next_run_at"])
            stored = cron._native(agent_id, "get_job", job["id"])
            self.assertEqual(
                stored["schedule"]["xnobrain_time"]["timezone"],
                "Asia/Ho_Chi_Minh",
            )

    async def test_calendar_creation_api_persists_explicit_zone(self):
        from unittest.mock import patch

        from httpx import ASGITransport, AsyncClient

        kanban = self.fixture.composition.service.cron.kanban
        self.assertIsNotNone(kanban)
        task_calls = patch.object(kanban, "create_task", wraps=kanban.create_task)
        observed = task_calls.start()
        self.addCleanup(task_calls.stop)
        async with AsyncClient(
            transport=ASGITransport(app=self.fixture.app), base_url="http://test"
        ) as client:
            agent = await client.post(
                "/xnobrain/api/runtime/v1/agents", json={"name": "Timezone Test"}
            )
            self.assertEqual(agent.status_code, 201, agent.text)
            agent_id = agent.json()["data"]["id"]
            created = await client.post(
                "/xnobrain/api/runtime/v1/cron/jobs",
                json={
                    "agent_id": agent_id,
                    "name": "Synthetic calendar",
                    "prompt": "Synthetic only",
                    "schedule": "0 9 * * *",
                    "timezone": "Asia/Ho_Chi_Minh",
                },
            )
            self.assertEqual(created.status_code, 201, created.text)
            job = created.json()["data"]
            self.assertEqual(job["timezone"], "Asia/Ho_Chi_Minh")
            self.assertEqual(job["dst_policy"], "skip_gap_earlier_fold")
            self.assertIn("T02:00:00", job["next_run_at"])
            stored = self.fixture.composition.service.cron._native(agent_id, "get_job", job["id"])
            self.assertEqual(stored["schedule"]["xnobrain_time"]["timezone"], "Asia/Ho_Chi_Minh")

        observed.assert_not_called()


class CronTimezoneAdapterTests(unittest.TestCase):
    def test_bound_schedule_uses_zone_and_unbound_calls_native(self):
        from types import SimpleNamespace
        from unittest.mock import Mock

        from xnobrain.app import XNOBrainApplication
        from xnobrain.integrations.cron_timezone import install_timezone_computation

        self.assertIsNotNone(XNOBrainApplication)
        native = Mock(return_value="native-result")
        jobs = SimpleNamespace(compute_next_run=native)
        install_timezone_computation(jobs)
        installed = jobs.compute_next_run
        install_timezone_computation(jobs)
        self.assertIs(installed, jobs.compute_next_run)
        legacy = {"kind": "interval", "minutes": 60}
        self.assertEqual(jobs.compute_next_run(legacy, "cutoff"), "native-result")
        native.assert_called_once_with(legacy, "cutoff")
        schedule = {
            "kind": "cron",
            "expr": "0 9 * * *",
            "xnobrain_time": {
                "schema_version": 1,
                "timezone": "Asia/Ho_Chi_Minh",
                "dst_policy": "skip_gap_earlier_fold",
            },
        }
        self.assertEqual(
            jobs.compute_next_run(schedule, "2026-09-09T00:00:00Z"), "2026-09-09T02:00:00Z"
        )
        native.assert_called_once()

    def test_invalid_persisted_binding_never_falls_back_to_native(self):
        from types import SimpleNamespace
        from unittest.mock import Mock

        from xnobrain.integrations.cron_timezone import install_timezone_computation

        native = Mock(return_value="unsafe-fallback")
        jobs = SimpleNamespace(compute_next_run=native)
        install_timezone_computation(jobs)
        valid = {
            "schema_version": 1,
            "timezone": "UTC",
            "dst_policy": "skip_gap_earlier_fold",
        }
        invalid = [
            None,
            {},
            {**valid, "schema_version": True},
            {**valid, "schema_version": 2},
            {**valid, "timezone": None},
            {**valid, "timezone": "CST"},
            {**valid, "timezone": "Unknown/Zone"},
            {**valid, "future_policy": True},
        ]
        for binding in invalid:
            with self.subTest(binding=binding), self.assertRaises(ValueError):
                jobs.compute_next_run(
                    {
                        "kind": "cron",
                        "expr": "0 9 * * *",
                        "xnobrain_time": binding,
                    },
                    "2026-09-09T00:00:00Z",
                )
        native.assert_not_called()

    def test_native_dst_advancement_and_completion_skip_gap_and_second_fold(self):
        import tempfile
        from datetime import datetime, timezone
        from pathlib import Path
        from unittest.mock import patch

        from cron import jobs

        from xnobrain.app import XNOBrainApplication
        from xnobrain.integrations.cron_timezone import install_timezone_computation

        self.assertIsNotNone(XNOBrainApplication)
        original_compute = jobs.compute_next_run
        original_parse = jobs.parse_schedule
        cases = [
            (
                "Europe/Berlin",
                "30 2 * * *",
                datetime(2026, 3, 28, 1, 31, tzinfo=timezone.utc),
                "2026-03-30T00:30:00Z",
            ),
            (
                "Europe/Berlin",
                "30 2 * * *",
                datetime(2026, 10, 25, 0, 31, tzinfo=timezone.utc),
                "2026-10-26T01:30:00Z",
            ),
            (
                "Europe/Berlin",
                "30 2 * * *",
                datetime(2026, 10, 25, 1, 15, tzinfo=timezone.utc),
                "2026-10-26T01:30:00Z",
            ),
            (
                "Australia/Lord_Howe",
                "15 2 * * *",
                datetime(2026, 10, 2, 16, 0, tzinfo=timezone.utc),
                "2026-10-04T15:15:00Z",
            ),
            (
                "Australia/Lord_Howe",
                "45 1 * * *",
                datetime(2026, 4, 4, 14, 46, tzinfo=timezone.utc),
                "2026-04-05T15:15:00Z",
            ),
            (
                "Australia/Lord_Howe",
                "45 1 * * *",
                datetime(2026, 4, 4, 15, 5, tzinfo=timezone.utc),
                "2026-04-05T15:15:00Z",
            ),
        ]
        try:
            install_timezone_computation(jobs)
            for zone, expression, cutoff, expected in cases:
                with (
                    self.subTest(zone=zone, cutoff=cutoff),
                    tempfile.TemporaryDirectory() as directory,
                ):
                    with jobs.use_cron_store(Path(directory)):
                        jobs.save_jobs(
                            [
                                {
                                    "id": "synthetic-dst",
                                    "enabled": True,
                                    "next_run_at": cutoff.isoformat(),
                                    "schedule": {
                                        "kind": "cron",
                                        "expr": expression,
                                        "xnobrain_time": {
                                            "schema_version": 1,
                                            "timezone": zone,
                                            "dst_policy": "skip_gap_earlier_fold",
                                        },
                                    },
                                }
                            ]
                        )
                        with patch.object(jobs, "_hermes_now", return_value=cutoff):
                            self.assertTrue(jobs.advance_next_run("synthetic-dst"))
                            self.assertEqual(jobs.get_job("synthetic-dst")["next_run_at"], expected)
                            self.assertEqual(jobs.get_due_jobs(), [])
                            jobs.mark_job_run("synthetic-dst", True)
                            self.assertEqual(jobs.get_due_jobs(), [])
                        self.assertEqual(jobs.get_job("synthetic-dst")["next_run_at"], expected)
        finally:
            jobs.compute_next_run = original_compute
            jobs.parse_schedule = original_parse

    def test_named_calendar_fields_create_through_native_store(self):
        import tempfile
        from datetime import datetime, timezone
        from pathlib import Path
        from unittest.mock import patch

        from cron import jobs

        from xnobrain.app import XNOBrainApplication
        from xnobrain.integrations.cron_timezone import (
            cron_creation_timezone,
            install_timezone_computation,
        )

        self.assertIsNotNone(XNOBrainApplication)
        original_compute = jobs.compute_next_run
        original_parse = jobs.parse_schedule
        try:
            install_timezone_computation(jobs)
            with tempfile.TemporaryDirectory() as directory:
                with jobs.use_cron_store(Path(directory)):
                    with patch("xnobrain.integrations.cron_timezone.datetime") as clock:
                        clock.now.return_value = datetime(2026, 9, 9, tzinfo=timezone.utc)
                        with cron_creation_timezone("Asia/Ho_Chi_Minh"):
                            job = jobs.create_job(
                                "Synthetic named cron", "0 9 * SEP THU", deliver="local"
                            )
                    self.assertEqual(job["next_run_at"], "2026-09-10T02:00:00Z")
                    stored = jobs.get_job(job["id"])
                    self.assertEqual(stored["schedule"]["expr"], "0 9 * SEP THU")
                    self.assertEqual(
                        stored["schedule"]["xnobrain_time"]["timezone"],
                        "Asia/Ho_Chi_Minh",
                    )
            with self.assertRaises(ValueError):
                jobs.parse_schedule("0 9 * SEP THU")
        finally:
            jobs.compute_next_run = original_compute
            jobs.parse_schedule = original_parse

    def test_scoped_naive_absolute_schedule_rejects_without_native_parsing(self):
        from types import SimpleNamespace
        from unittest.mock import Mock

        from xnobrain.app import XNOBrainApplication

        self.assertIsNotNone(XNOBrainApplication)
        from xnobrain.integrations.cron_timezone import (
            cron_creation_timezone,
            install_timezone_computation,
        )

        parser = Mock(return_value={"kind": "once"})
        jobs = SimpleNamespace(compute_next_run=Mock(), parse_schedule=parser)
        install_timezone_computation(jobs)
        with cron_creation_timezone("Europe/Berlin"):
            for text in (
                "2026-10-25T02:30:00",
                "2026-03-29T02:30:00",
                "2026-09-09",
                "2026-10-25X02:30:00",
                "2026-10-25t02:30:00",
            ):
                with (
                    self.subTest(text=text),
                    self.assertRaisesRegex(ValueError, "explicit UTC offset"),
                ):
                    jobs.parse_schedule(text)
        parser.assert_not_called()
        jobs.parse_schedule("2026-09-09T09:00:00")
        parser.assert_called_once()

    def test_timezone_scope_does_not_reinterpret_interval_or_absolute_once(self):
        from datetime import datetime, timezone
        from unittest.mock import patch

        from cron import jobs

        from xnobrain.app import XNOBrainApplication
        from xnobrain.integrations.cron_timezone import (
            cron_creation_timezone,
            install_timezone_computation,
        )

        self.assertIsNotNone(XNOBrainApplication)
        original_compute = jobs.compute_next_run
        original_parse = jobs.parse_schedule
        now = datetime(2026, 3, 29, 0, 45, tzinfo=timezone.utc)
        try:
            install_timezone_computation(jobs)
            for zone in ("Europe/Berlin", "Asia/Ho_Chi_Minh", "Australia/Lord_Howe"):
                with (
                    self.subTest(zone=zone),
                    patch.object(jobs, "_hermes_now", return_value=now),
                    cron_creation_timezone(zone),
                ):
                    interval = jobs.parse_schedule("every 60m")
                    once = jobs.parse_schedule("2026-03-29T03:15:00+02:00")
                    self.assertNotIn("xnobrain_time", interval)
                    self.assertNotIn("xnobrain_time", once)
                    next_interval = datetime.fromisoformat(jobs.compute_next_run(interval))
                    next_once = datetime.fromisoformat(jobs.compute_next_run(once))
                    self.assertEqual((next_interval - now).total_seconds(), 3600)
                    self.assertEqual(
                        next_once.astimezone(timezone.utc).isoformat(),
                        "2026-03-29T01:15:00+00:00",
                    )
        finally:
            jobs.compute_next_run = original_compute
            jobs.parse_schedule = original_parse

    def test_native_pause_resume_preserves_zone_and_chooses_future_occurrence(self):
        import tempfile
        from datetime import datetime, timezone
        from pathlib import Path
        from unittest.mock import patch

        from cron import jobs

        from xnobrain.app import XNOBrainApplication
        from xnobrain.integrations.cron_timezone import install_timezone_computation

        self.assertIsNotNone(XNOBrainApplication)
        original_compute = jobs.compute_next_run
        original_parse = jobs.parse_schedule
        try:
            install_timezone_computation(jobs)
            with tempfile.TemporaryDirectory() as directory:
                with jobs.use_cron_store(Path(directory)):
                    binding = {
                        "schema_version": 1,
                        "timezone": "Asia/Ho_Chi_Minh",
                        "dst_policy": "skip_gap_earlier_fold",
                    }
                    jobs.save_jobs(
                        [
                            {
                                "id": "synthetic-resume",
                                "enabled": True,
                                "state": "scheduled",
                                "next_run_at": "2026-09-01T02:00:00Z",
                                "schedule": {
                                    "kind": "cron",
                                    "expr": "0 9 * * *",
                                    "xnobrain_time": binding,
                                },
                            }
                        ]
                    )
                    paused = jobs.pause_job("synthetic-resume")
                    self.assertFalse(paused["enabled"])
                    self.assertEqual(paused["schedule"]["xnobrain_time"], binding)
                    now = datetime(2026, 9, 9, 3, tzinfo=timezone.utc)
                    with patch("xnobrain.integrations.cron_timezone.datetime") as clock:
                        clock.now.return_value = now
                        resumed = jobs.resume_job("synthetic-resume")
                    self.assertTrue(resumed["enabled"])
                    self.assertEqual(resumed["next_run_at"], "2026-09-10T02:00:00Z")
                    stored = jobs.get_job("synthetic-resume")
                    self.assertEqual(stored["schedule"]["xnobrain_time"], binding)
                    with patch.object(jobs, "_hermes_now", return_value=now):
                        self.assertEqual(jobs.get_due_jobs(), [])
        finally:
            jobs.compute_next_run = original_compute
            jobs.parse_schedule = original_parse

    def test_native_advancement_and_completion_keep_job_timezone(self):
        import tempfile
        from datetime import datetime, timezone
        from pathlib import Path
        from unittest.mock import patch

        from cron import jobs

        from xnobrain.app import XNOBrainApplication
        from xnobrain.integrations.cron_timezone import install_timezone_computation

        self.assertIsNotNone(XNOBrainApplication)
        original = jobs.compute_next_run
        original_parse = jobs.parse_schedule
        try:
            install_timezone_computation(jobs)
            with tempfile.TemporaryDirectory() as directory, jobs.use_cron_store(Path(directory)):
                schedule = {
                    "kind": "cron",
                    "expr": "0 9 * * *",
                    "xnobrain_time": {
                        "schema_version": 1,
                        "timezone": "Asia/Ho_Chi_Minh",
                        "dst_policy": "skip_gap_earlier_fold",
                    },
                }
                jobs.save_jobs(
                    [
                        {
                            "id": "synthetic-zone-job",
                            "schedule": schedule,
                            "next_run_at": "2026-09-09T02:00:00Z",
                            "enabled": True,
                        }
                    ]
                )
                clock = datetime(2026, 9, 9, 2, 1, tzinfo=timezone.utc)
                with patch.object(jobs, "_hermes_now", return_value=clock):
                    self.assertTrue(jobs.advance_next_run("synthetic-zone-job"))
                    self.assertEqual(
                        jobs.get_job("synthetic-zone-job")["next_run_at"], "2026-09-10T02:00:00Z"
                    )
                    jobs.mark_job_run("synthetic-zone-job", True)
                reloaded = jobs.get_job("synthetic-zone-job")
                self.assertEqual(reloaded["next_run_at"], "2026-09-10T02:00:00Z")
                self.assertEqual(
                    reloaded["schedule"]["xnobrain_time"]["timezone"], "Asia/Ho_Chi_Minh"
                )
        finally:
            jobs.compute_next_run = original
            jobs.parse_schedule = original_parse

    def test_concurrent_native_creations_keep_independent_zones(self):
        import tempfile
        from concurrent.futures import ThreadPoolExecutor
        from pathlib import Path
        from threading import Barrier

        from cron import jobs

        from xnobrain.app import XNOBrainApplication
        from xnobrain.integrations.cron_timezone import (
            cron_creation_timezone,
            install_timezone_computation,
        )

        self.assertIsNotNone(XNOBrainApplication)
        original_compute = jobs.compute_next_run
        original_parse = jobs.parse_schedule
        zones = ["Asia/Ho_Chi_Minh", "Europe/Berlin", "UTC", "Australia/Lord_Howe"]
        barrier = Barrier(len(zones))
        try:
            install_timezone_computation(jobs)
            with tempfile.TemporaryDirectory() as directory:

                def create(zone):
                    # Each worker must select the same actual native store itself:
                    # context-local state is intentionally not inherited by threads.
                    with jobs.use_cron_store(Path(directory)):
                        with cron_creation_timezone(zone):
                            barrier.wait(timeout=10)
                            return jobs.create_job(
                                "Synthetic concurrent test",
                                "0 9 * * *",
                                deliver="local",
                            )

                with ThreadPoolExecutor(max_workers=len(zones)) as pool:
                    created = list(pool.map(create, zones))
                with jobs.use_cron_store(Path(directory)):
                    persisted = {job["id"]: job for job in jobs.load_jobs()}
                self.assertEqual(len(persisted), len(zones))
                for zone, job in zip(zones, created, strict=True):
                    stored = persisted[job["id"]]
                    self.assertEqual(stored["schedule"]["xnobrain_time"]["timezone"], zone)
                    from datetime import datetime
                    from zoneinfo import ZoneInfo

                    local = datetime.fromisoformat(
                        stored["next_run_at"].replace("Z", "+00:00")
                    ).astimezone(ZoneInfo(zone))
                    self.assertEqual((local.hour, local.minute), (9, 0))
                self.assertNotIn("xnobrain_time", jobs.parse_schedule("0 9 * * *"))
        finally:
            jobs.compute_next_run = original_compute
            jobs.parse_schedule = original_parse

    def test_fresh_process_reloads_and_advances_persisted_timezone(self):
        import subprocess
        import sys
        import tempfile
        import textwrap

        script = textwrap.dedent("""
            import sys
            from datetime import datetime, timezone
            from pathlib import Path
            from unittest.mock import patch

            from xnobrain.app import XNOBrainApplication
            from xnobrain.integrations.cron_timezone import (
                cron_creation_timezone,
                install_timezone_computation,
            )
            from cron import jobs

            install_timezone_computation(jobs)
            with jobs.use_cron_store(Path(sys.argv[1])):
                if sys.argv[2] == "create":
                    with patch("xnobrain.integrations.cron_timezone.datetime") as clock:
                        clock.now.return_value = datetime(
                            2026, 9, 9, tzinfo=timezone.utc
                        )
                        with cron_creation_timezone("Asia/Ho_Chi_Minh"):
                            job = jobs.create_job(
                                "Synthetic restart test", "0 9 * * *", deliver="local"
                            )
                    assert job["next_run_at"] == "2026-09-09T02:00:00Z"
                else:
                    stored = jobs.load_jobs()
                    assert len(stored) == 1
                    job = stored[0]
                    assert job["schedule"]["xnobrain_time"]["timezone"] == (
                        "Asia/Ho_Chi_Minh"
                    )
                    with patch.object(jobs, "_hermes_now", return_value=datetime(
                        2026, 9, 9, 2, 1, tzinfo=timezone.utc
                    )):
                        assert jobs.advance_next_run(job["id"])
                    assert jobs.get_job(job["id"])["next_run_at"] == (
                        "2026-09-10T02:00:00Z"
                    )
        """)
        with tempfile.TemporaryDirectory() as directory:
            for phase in ("create", "advance"):
                result = subprocess.run(
                    [sys.executable, "-c", script, directory, phase],
                    capture_output=True,
                    text=True,
                    timeout=30,
                    check=False,
                )
                self.assertEqual(result.returncode, 0, result.stderr)

    def test_native_creation_persists_zone_before_first_run_and_resets_scope(self):
        import tempfile
        from datetime import datetime, timezone
        from pathlib import Path
        from unittest.mock import patch

        from cron import jobs

        from xnobrain.app import XNOBrainApplication
        from xnobrain.integrations.cron_timezone import (
            cron_creation_timezone,
            install_timezone_computation,
        )

        self.assertIsNotNone(XNOBrainApplication)
        original_compute, original_parse = jobs.compute_next_run, jobs.parse_schedule
        try:
            install_timezone_computation(jobs)
            with tempfile.TemporaryDirectory() as directory, jobs.use_cron_store(Path(directory)):
                with patch("xnobrain.integrations.cron_timezone.datetime") as clock:
                    clock.now.return_value = datetime(2026, 9, 9, tzinfo=timezone.utc)
                    with cron_creation_timezone("Asia/Ho_Chi_Minh"):
                        created = jobs.create_job("Synthetic task", "0 9 * * *", deliver="local")
                    with cron_creation_timezone("Europe/Berlin"):
                        second = jobs.create_job("Synthetic task", "0 9 * * *", deliver="local")
                self.assertEqual(created["next_run_at"], "2026-09-09T02:00:00Z")
                self.assertEqual(second["next_run_at"], "2026-09-09T07:00:00Z")
                self.assertEqual(
                    jobs.get_job(created["id"])["schedule"]["xnobrain_time"]["timezone"],
                    "Asia/Ho_Chi_Minh",
                )
                self.assertNotIn("xnobrain_time", jobs.parse_schedule("0 9 * * *"))
        finally:
            jobs.compute_next_run, jobs.parse_schedule = original_compute, original_parse


class CronTimezoneProjectionTests(unittest.TestCase):
    def test_interval_duration_comes_from_persisted_schedule(self):
        from xnobrain.services.cron import CronService

        for minutes in (60, True, -1, "60", None):
            with self.subTest(minutes=minutes):
                result = CronService._dto(
                    "synthetic",
                    {
                        "schedule": {
                            "kind": "interval",
                            "minutes": minutes,
                            "display": "Human readable label",
                        },
                    },
                )
                self.assertEqual(
                    result["interval_minutes"],
                    minutes if type(minutes) is int and minutes > 0 else None,
                )

    def test_schedule_kind_is_authoritative_or_unknown(self):
        from xnobrain.services.cron import CronService

        for kind in ("cron", "interval", "once", "future", None, [], {}):
            with self.subTest(kind=kind):
                result = CronService._dto(
                    "synthetic",
                    {
                        "schedule": {"kind": kind, "display": "Human readable schedule"},
                    },
                )
                self.assertEqual(
                    result["schedule_kind"],
                    kind
                    if isinstance(kind, str) and kind in {"cron", "interval", "once"}
                    else None,
                )

    def test_invalid_binding_is_not_presented_as_known_timezone(self):
        from xnobrain.services.cron import CronService

        for version, policy, zone in (
            (True, "skip_gap_earlier_fold", "UTC"),
            (1, "unknown", "UTC"),
            (1, "skip_gap_earlier_fold", "CST"),
        ):
            with self.subTest(version=version, policy=policy, zone=zone):
                result = CronService._dto(
                    "synthetic",
                    {
                        "schedule": {
                            "kind": "cron",
                            "expr": "0 9 * * *",
                            "xnobrain_time": {
                                "schema_version": version,
                                "dst_policy": policy,
                                "timezone": zone,
                            },
                        },
                    },
                )
                self.assertIsNone(result["timezone"])
                self.assertIsNone(result["dst_policy"])

    def test_projection_preserves_binding_and_does_not_guess_legacy_zone(self):
        from xnobrain.app import XNOBrainApplication
        from xnobrain.services.cron import CronService

        self.assertIsNotNone(XNOBrainApplication)
        job = {"id": "synthetic", "schedule": {"kind": "cron", "expr": "0 9 * * *"}}
        legacy = CronService._dto("synthetic", job)
        self.assertIsNone(legacy["timezone"])
        job["schedule"]["xnobrain_time"] = {
            "schema_version": 1,
            "timezone": "Asia/Ho_Chi_Minh",
            "dst_policy": "skip_gap_earlier_fold",
        }
        bound = CronService._dto("synthetic", job)
        self.assertEqual(bound["timezone"], "Asia/Ho_Chi_Minh")
        self.assertEqual(bound["dst_policy"], "skip_gap_earlier_fold")


class ConcurrentCronTimezoneTests(unittest.TestCase):
    def test_creation_scopes_are_thread_local_and_restore_after_failure(self):
        from concurrent.futures import ThreadPoolExecutor
        from threading import Barrier
        from types import SimpleNamespace

        from xnobrain.app import XNOBrainApplication
        from xnobrain.integrations.cron_timezone import (
            cron_creation_timezone,
            install_timezone_computation,
        )

        self.assertIsNotNone(XNOBrainApplication)
        jobs = SimpleNamespace(
            compute_next_run=lambda schedule, last=None: "native",
            parse_schedule=lambda expression: {"kind": "cron", "expr": expression},
        )
        install_timezone_computation(jobs)
        barrier = Barrier(2)

        def parse_in_zone(zone):
            with cron_creation_timezone(zone):
                barrier.wait(timeout=5)
                return jobs.parse_schedule("0 9 * * *")["xnobrain_time"]["timezone"]

        zones = ["Asia/Ho_Chi_Minh", "Europe/Berlin"]
        with ThreadPoolExecutor(max_workers=2) as pool:
            self.assertEqual(list(pool.map(parse_in_zone, zones)), zones)
        with self.assertRaises(RuntimeError):
            with cron_creation_timezone("Asia/Ho_Chi_Minh"):
                raise RuntimeError("synthetic")
        self.assertNotIn("xnobrain_time", jobs.parse_schedule("0 9 * * *"))

    def test_adapter_installation_is_idempotent_across_threads(self):
        from concurrent.futures import ThreadPoolExecutor
        from types import SimpleNamespace

        from xnobrain.app import XNOBrainApplication
        from xnobrain.integrations.cron_timezone import install_timezone_computation

        self.assertIsNotNone(XNOBrainApplication)
        native = lambda schedule, last=None: "native"
        parser = lambda expression: {"kind": "cron", "expr": expression}
        jobs = SimpleNamespace(compute_next_run=native, parse_schedule=parser)
        with ThreadPoolExecutor(max_workers=8) as pool:
            list(pool.map(lambda _: install_timezone_computation(jobs), range(32)))
        self.assertIs(jobs.compute_next_run.__wrapped__, native)
        self.assertIs(jobs.parse_schedule.__wrapped__, parser)


class RuntimeTimeControlAdapterTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        import os

        from xnobrain.tests.test_marketplace import MarketplaceExportAPITests

        self.previous_token = os.environ.get("RUNTIME_INTERNAL_SERVICE_TOKEN")
        os.environ["RUNTIME_INTERNAL_SERVICE_TOKEN"] = "time-control-test-token"
        self.fixture = MarketplaceExportAPITests()
        self.fixture.setUp()

    def tearDown(self):
        import os

        self.fixture.tearDown()
        if self.previous_token is None:
            os.environ.pop("RUNTIME_INTERNAL_SERVICE_TOKEN", None)
        else:
            os.environ["RUNTIME_INTERNAL_SERVICE_TOKEN"] = self.previous_token

    @staticmethod
    def _workspace():
        return {
            "UserID": "owner",
            "TenantID": "personal-owner",
            "InstanceName": "runtime-one",
            "InstanceType": "container",
            "Project": "default",
        }

    @staticmethod
    def _hash(marker="1"):
        return "sha256:" + marker * 64

    async def _client(self):
        from httpx import ASGITransport, AsyncClient

        from xnobrain.trusted_context import principal_signature

        return AsyncClient(
            transport=ASGITransport(app=self.fixture.app),
            base_url="http://test",
            headers={
                "X-XNOBrain-Time-Token": "time-control-test-token",
                "X-XNOBrain-Verified-Subject": "owner",
                "X-XNOBrain-Verified-Tenant": "personal-owner",
                "X-XNOBrain-Principal-Signature": principal_signature(
                    "time-control-test-token",
                    "owner",
                    "personal-owner",
                ),
            },
        )

    async def _calendar_job(self, client, name="Managed calendar"):
        agent = await client.post(
            "/xnobrain/api/runtime/v1/agents",
            json={"name": name},
        )
        self.assertEqual(agent.status_code, 201, agent.text)
        created = await client.post(
            "/xnobrain/api/runtime/v1/cron/jobs",
            json={
                "agent_id": agent.json()["data"]["id"],
                "name": name,
                "prompt": "Synthetic only",
                "schedule": "0 9 * * *",
                "timezone": "Etc/UTC",
            },
        )
        self.assertEqual(created.status_code, 201, created.text)
        return created.json()["data"]

    async def test_service_token_required_and_observation_reads_persisted_config(self):
        from pathlib import Path

        from httpx import ASGITransport, AsyncClient

        path = "/xnobrain/api/runtime/v1/system/time-control"
        async with AsyncClient(
            transport=ASGITransport(app=self.fixture.app),
            base_url="http://test",
        ) as unauthenticated:
            rejected = await unauthenticated.get(path)
        self.assertEqual(rejected.status_code, 401, rejected.text)

        async with await self._client() as client:
            observed = await client.get(path)
        self.assertEqual(observed.status_code, 200, observed.text)
        data = observed.json()["data"]
        self.assertEqual(data["effective_timezone"], "Etc/UTC")
        self.assertEqual(data["scheduler_timezone"], "Etc/UTC")
        self.assertTrue(all(item["supported"] for item in data["capabilities"]))
        self.assertFalse(Path(self.fixture.tmp.name, "data", "time-control", "fence.json").exists())

    async def test_apply_validates_revision_fence_and_is_idempotent_with_readback(self):
        from pathlib import Path

        body = {
            "workspace": self._workspace(),
            "operation_id": "time_apply_1",
            "fence": 7,
            "plan_hash": self._hash("a"),
            "target_timezone": "Asia/Ho_Chi_Minh",
            "expected_revision": 0,
            "cutoff": "0001-01-01T00:00:00Z",
            "items": [],
        }
        async with await self._client() as client:
            first = await client.post(
                "/xnobrain/api/runtime/v1/system/time-control/apply",
                json=body,
            )
            replay = await client.post(
                "/xnobrain/api/runtime/v1/system/time-control/apply",
                json=body,
            )
            stale = await client.post(
                "/xnobrain/api/runtime/v1/system/time-control/apply",
                json={**body, "operation_id": "time_apply_stale", "fence": 6},
            )
            forged = await client.post(
                "/xnobrain/api/runtime/v1/system/time-control/apply",
                json={**body, "plan_hash": self._hash("b")},
            )
        self.assertEqual(first.status_code, 200, first.text)
        self.assertEqual(first.json()["data"], replay.json()["data"])
        self.assertTrue(first.json()["data"]["verified"])
        self.assertEqual(stale.status_code, 409, stale.text)
        self.assertEqual(forged.status_code, 409, forged.text)
        config = Path(self.fixture.tmp.name, "data", "time-control", "timezone.json")
        persisted = __import__("json").loads(config.read_text())
        self.assertEqual(persisted["timezone"], "Asia/Ho_Chi_Minh")
        self.assertEqual(persisted["revision"], 1)
        self.assertNotIn("TZ", persisted)

    async def test_schedule_inventory_is_revision_authority_and_preview_is_read_only(self):
        from pathlib import Path

        async with await self._client() as client:
            job = await self._calendar_job(client)
            inventory = await client.get("/xnobrain/api/runtime/v1/cron/jobs")
            time_inventory = self.fixture.composition.service.time_control.schedules(
                time_token="time-control-test-token"
            )
            before = {
                path.relative_to(Path(self.fixture.tmp.name)): path.read_bytes()
                for path in Path(self.fixture.tmp.name).rglob("*")
                if path.is_file()
            }
            preview = await client.post(
                "/xnobrain/api/runtime/v1/system/time-control/schedule-migration-preview",
                json={
                    "workspace": self._workspace(),
                    "target_timezone": "Asia/Ho_Chi_Minh",
                    "cutoff": "2026-09-13T00:00:00Z",
                    "items": [
                        {
                            "id": job["id"],
                            "label": "",
                            "old_timezone": None,
                            "new_timezone": "Asia/Ho_Chi_Minh",
                            "revision": 1,
                            "status": "",
                            "next_occurrences": None,
                        }
                    ],
                },
            )
        self.assertEqual(inventory.status_code, 200)
        wire_job = next(item for item in inventory.json()["data"] if item["id"] == job["id"])
        self.assertEqual(wire_job["revision"], 1)
        self.assertTrue(wire_job["editable"])
        self.assertEqual(wire_job["restriction"], "")
        self.assertEqual(time_inventory[0]["revision"], 1)
        self.assertTrue(time_inventory[0]["editable"])
        self.assertEqual(preview.status_code, 200, preview.text)
        item = preview.json()["data"]["items"][0]
        self.assertEqual(item["status"], "ready")
        self.assertEqual(item["next_occurrences"][0]["utc"], "2026-09-13T02:00:00Z")
        self.assertEqual(
            before,
            {
                path.relative_to(Path(self.fixture.tmp.name)): path.read_bytes()
                for path in Path(self.fixture.tmp.name).rglob("*")
                if path.is_file()
            },
        )

    async def test_migration_preserves_history_reports_stale_and_replays_exact_result(self):
        from datetime import datetime, timezone

        async with await self._client() as client:
            job = await self._calendar_job(client, "History retained")
            cron = self.fixture.composition.service.cron
            profile, stored = cron._find_job(job["id"])
            cron._native(
                profile,
                "update_job",
                job["id"],
                {"last_run_at": "2026-09-12T09:00:00+00:00", "last_status": "ok"},
            )
            body = {
                "workspace": self._workspace(),
                "operation_id": "time_migration_1",
                "fence": 11,
                "plan_hash": self._hash("c"),
                "target_timezone": "Asia/Ho_Chi_Minh",
                "expected_revision": 0,
                "cutoff": "2026-09-13T00:00:00Z",
                "items": [
                    {
                        "id": job["id"],
                        "label": "History retained",
                        "old_timezone": "Etc/UTC",
                        "new_timezone": "Asia/Ho_Chi_Minh",
                        "revision": 1,
                        "status": "ready",
                        "next_occurrences": [],
                    },
                    {
                        "id": "missing-job",
                        "label": "Missing",
                        "old_timezone": "Etc/UTC",
                        "new_timezone": "Asia/Ho_Chi_Minh",
                        "revision": 1,
                        "status": "ready",
                        "next_occurrences": [],
                    },
                ],
            }
            result = await client.post(
                "/xnobrain/api/runtime/v1/system/time-control/schedule-migrate",
                json=body,
            )
            replay = await client.post(
                "/xnobrain/api/runtime/v1/system/time-control/schedule-migrate",
                json=body,
            )
        self.assertEqual(result.status_code, 200, result.text)
        data = result.json()["data"]
        self.assertTrue(data["partial"])
        self.assertFalse(data["verified"])
        self.assertEqual(
            [item["status"] for item in data["item_results"]],
            ["succeeded", "stale"],
        )
        self.assertEqual(data, replay.json()["data"])
        migrated = cron._native(profile, "get_job", job["id"])
        self.assertEqual(migrated["last_run_at"], "2026-09-12T09:00:00+00:00")
        self.assertEqual(migrated["last_status"], "ok")
        self.assertEqual(
            migrated["schedule"]["xnobrain_time"]["timezone"],
            "Asia/Ho_Chi_Minh",
        )
        self.assertEqual(migrated["xnobrain_time_revision"], 2)
        self.assertEqual(
            migrated["next_run_at"],
            "2026-09-13T02:00:00Z",
        )
        self.assertLess(
            datetime.fromisoformat("2026-09-13T00:00:00+00:00"),
            datetime.fromisoformat(migrated["next_run_at"].replace("Z", "+00:00")),
        )

    async def test_restart_recovers_committed_settings_and_incomplete_migration_retry(self):
        service = self.fixture.composition.service.time_control
        request = {
            "workspace": self._workspace(),
            "operation_id": "recover_migration_1",
            "fence": 15,
            "plan_hash": self._hash("d"),
            "target_timezone": "UTC",
            "expected_revision": 0,
            "cutoff": "2026-09-13T00:00:00Z",
            "items": [
                {
                    "id": "missing-recovery-job",
                    "new_timezone": "UTC",
                    "revision": 1,
                }
            ],
        }
        service._begin(request, "migration")

        from xnobrain.services.time_control import TimeControlService

        recovered = TimeControlService(self.fixture.composition.service)
        result = await recovered.migrate(
            request,
            time_token="time-control-test-token",
        )
        self.assertTrue(result["partial"])
        self.assertEqual(result["item_results"][0]["status"], "stale")

    async def test_failed_operation_uses_failed_terminal_state(self):
        service = self.fixture.composition.service.time_control
        request = {
            "workspace": self._workspace(),
            "operation_id": "failed_terminal_state",
            "fence": 16,
            "plan_hash": self._hash("failed"),
            "target_timezone": "UTC",
            "expected_revision": 0,
            "items": [],
        }
        service._begin(request, "settings")

        result = service._finish(
            request,
            "settings",
            verified=False,
            partial=False,
            error_code="time_readback_mismatch",
            observations=[],
        )

        self.assertFalse(result["verified"])
        self.assertFalse(result["partial"])
        self.assertEqual(
            service.repository.operation(request["operation_id"])["state"],
            "failed",
        )

    async def test_mutations_require_matching_relay_verified_workspace(self):
        from httpx import ASGITransport, AsyncClient

        from xnobrain.trusted_context import principal_signature

        body = {
            "workspace": self._workspace(),
            "operation_id": "time_identity_1",
            "fence": 18,
            "plan_hash": self._hash("f"),
            "target_timezone": "UTC",
            "expected_revision": 0,
            "items": [],
        }
        headers = {
            "X-XNOBrain-Time-Token": "time-control-test-token",
            "X-XNOBrain-Verified-Subject": "foreign",
            "X-XNOBrain-Verified-Tenant": "personal-foreign",
            "X-XNOBrain-Principal-Signature": principal_signature(
                "time-control-test-token",
                "foreign",
                "personal-foreign",
            ),
        }
        async with AsyncClient(
            transport=ASGITransport(app=self.fixture.app),
            base_url="http://test",
            headers=headers,
        ) as client:
            response = await client.post(
                "/xnobrain/api/runtime/v1/system/time-control/apply",
                json=body,
            )
        self.assertEqual(response.status_code, 403, response.text)

    async def test_contract_rejects_shell_fields_and_cross_operation_shapes(self):
        settings = {
            "workspace": self._workspace(),
            "operation_id": "time_invalid_1",
            "fence": 20,
            "plan_hash": self._hash("e"),
            "target_timezone": "UTC",
            "expected_revision": 0,
            "items": [],
        }
        async with await self._client() as client:
            shell = await client.post(
                "/xnobrain/api/runtime/v1/system/time-control/apply",
                json={**settings, "command": "date -s tomorrow"},
            )
            settings_with_items = await client.post(
                "/xnobrain/api/runtime/v1/system/time-control/apply",
                json={
                    **settings,
                    "items": [
                        {
                            "id": "job-one",
                            "new_timezone": "UTC",
                            "revision": 1,
                        }
                    ],
                },
            )
            migration_without_cutoff = await client.post(
                "/xnobrain/api/runtime/v1/system/time-control/schedule-migrate",
                json=settings,
            )
            preview_authority = await client.post(
                "/xnobrain/api/runtime/v1/system/time-control/schedule-migration-preview",
                json={
                    "workspace": self._workspace(),
                    "target_timezone": "UTC",
                    "cutoff": "2026-09-13T00:00:00Z",
                    "fence": 1,
                    "items": [{"id": "job-one", "new_timezone": "UTC", "revision": 1}],
                },
            )
        for response in (
            shell,
            settings_with_items,
            migration_without_cutoff,
            preview_authority,
        ):
            self.assertEqual(response.status_code, 422, response.text)

    async def test_openapi_declares_all_fixed_typed_paths(self):
        schema = self.fixture.app.openapi()
        paths = schema["paths"]
        expected = {
            "/xnobrain/api/runtime/v1/system/time-control": "get",
            "/xnobrain/api/runtime/v1/system/time-control/schedule-migration-preview": "post",
            "/xnobrain/api/runtime/v1/system/time-control/apply": "post",
            "/xnobrain/api/runtime/v1/system/time-control/schedule-migrate": "post",
        }
        for path, method in expected.items():
            with self.subTest(path=path):
                self.assertIn(method, paths[path])
                self.assertIn("200", paths[path][method]["responses"])
