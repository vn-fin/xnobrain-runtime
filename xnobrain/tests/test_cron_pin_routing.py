"""Pinned cron jobs must authenticate through the LLM router, not local slugs."""

from __future__ import annotations

from unittest import TestCase
from unittest.mock import patch

from xnobrain.models.automation import CronCreate, CronUpdate
from xnobrain.services.cron import (
    CronService,
    _legacy_pin_needs_normalize,
    _pin_display_label,
    _router_pin_override,
)

ROUTER_URL = "http://llm-router:8090/v1"
ROUTER_PROVIDER = "custom:xnobrain"


def _router_patches(**overrides):
    values = {
        "LLM_ROUTER_BASE_URL": ROUTER_URL,
        "LLM_ROUTER_PROVIDER": ROUTER_PROVIDER,
        "ROUTER_PROVIDER_BY_MODEL_OWNER": {"cx": "codex", "gb": "grok-cli"},
    }
    values.update(overrides)
    return patch.multiple("xnobrain.integrations.llm_router_support", **values)


class RouterPinOverrideTests(TestCase):
    def test_empty_model_or_unset_router_is_unpin_semantics(self):
        self.assertIsNone(_router_pin_override(""))
        self.assertIsNone(_router_pin_override("   "))
        with _router_patches(LLM_ROUTER_BASE_URL=""):
            self.assertIsNone(_router_pin_override("cx/gpt-5.6-luna"))

    def test_override_uses_router_route_and_strips_api_key(self):
        with (
            _router_patches(),
            patch(
                "xnobrain.integrations.conversation_credentials.conversation_model_route",
                return_value={
                    "provider": ROUTER_PROVIDER,
                    "base_url": ROUTER_URL,
                    "model": "cx/gpt-5.6-luna",
                    "api_key": "must-not-persist",
                },
            ) as route,
        ):
            override = _router_pin_override("cx/gpt-5.6-luna")
        route.assert_called_once_with("cx/gpt-5.6-luna", ROUTER_URL)
        self.assertEqual(
            override,
            {"provider": ROUTER_PROVIDER, "base_url": ROUTER_URL, "model": "cx/gpt-5.6-luna"},
        )
        self.assertNotIn("api_key", override)

    def test_codex_slug_is_legacy_and_router_pin_is_not(self):
        with _router_patches():
            self.assertTrue(
                _legacy_pin_needs_normalize(
                    {"provider": "codex", "model": "cx/gpt-5.6-luna"}
                )
            )
            self.assertFalse(
                _legacy_pin_needs_normalize(
                    {
                        "provider": ROUTER_PROVIDER,
                        "model": "cx/gpt-5.6-luna",
                        "base_url": ROUTER_URL,
                        "xnobrain_pinned_label": "Codex · cx/gpt-5.6-luna",
                    }
                )
            )
        with _router_patches(LLM_ROUTER_BASE_URL=""):
            self.assertFalse(
                _legacy_pin_needs_normalize(
                    {"provider": "codex", "model": "cx/gpt-5.6-luna"}
                )
            )

    def test_display_label_uses_connector_name(self):
        self.assertEqual(_pin_display_label("codex", "cx/gpt-5.6-luna"), "Codex · cx/gpt-5.6-luna")
        self.assertEqual(_pin_display_label("grok-cli", "gb/grok-4.6"), "Grok · gb/grok-4.6")


class CronPinServiceTests(TestCase):
    def setUp(self):
        self.service = CronService(repository=None, agents=_Agents())
        self.service._snapshot_store = lambda profile: None
        self.created = {
            "id": "job-1",
            "name": "Pinned",
            "prompt": "run",
            "schedule": {"kind": "interval", "minutes": 60, "display": "every 60m"},
            "provider": ROUTER_PROVIDER,
            "model": "cx/gpt-5.6-luna",
            "base_url": ROUTER_URL,
        }

    def test_create_job_persists_router_override_not_raw_slug(self):
        calls: list[tuple] = []

        def native(profile, function, *args, **kwargs):
            calls.append((profile, function, args, kwargs))
            if function == "create_job":
                merged = {**self.created, **kwargs}
                merged["schedule"] = self.created["schedule"]
                return merged
            if function == "update_job":
                return {**self.created, **args[1]}
            raise AssertionError(function)

        with _router_patches(), patch.object(self.service, "_native", side_effect=native):
            dto = self.service.create_job(
                {
                    "agent_id": "agent-1",
                    "name": "Pinned",
                    "prompt": "run",
                    "interval_minutes": 60,
                    "provider": "codex",
                    "model": "cx/gpt-5.6-luna",
                }
            )
        create = next(item for item in calls if item[1] == "create_job")
        self.assertEqual(create[3]["provider"], ROUTER_PROVIDER)
        self.assertEqual(create[3]["base_url"], ROUTER_URL)
        self.assertEqual(create[3]["model"], "cx/gpt-5.6-luna")
        update = next(item for item in calls if item[1] == "update_job")
        self.assertEqual(update[2][1]["xnobrain_pinned_connector"], "codex")
        self.assertEqual(update[2][1]["xnobrain_pinned_label"], "Codex · cx/gpt-5.6-luna")
        self.assertEqual(dto["pinned_provider"], ROUTER_PROVIDER)
        self.assertEqual(dto["pinned_model"], "cx/gpt-5.6-luna")
        self.assertEqual(dto["pinned_connector"], "codex")
        self.assertEqual(dto["pinned_label"], "Codex · cx/gpt-5.6-luna")
        self.assertTrue(dto["pinned"])

    def test_update_job_pin_and_unpin(self):
        job = {
            "id": "job-1",
            "name": "Pinned",
            "prompt": "run",
            "schedule": {"kind": "interval", "minutes": 60, "display": "every 60m"},
            "provider": "codex",
            "model": "cx/gpt-5.6-luna",
        }
        calls: list[tuple] = []

        def native(profile, function, *args, **kwargs):
            calls.append((profile, function, args, kwargs))
            if function == "list_jobs":
                return [job]
            if function == "update_job":
                job.update(args[1])
                return dict(job)
            if function in {"clear_drift_alerted", "clear_preflight_alerted"}:
                return True
            raise AssertionError(function)

        with _router_patches(), patch.object(self.service, "_native", side_effect=native):
            pinned = self.service.update_job(
                "job-1", {"provider": "codex", "model": "cx/gpt-5.6-luna"}, "agent-1"
            )
            unpinned = self.service.update_job("job-1", {"unpin": True}, "agent-1")
        pin_update = next(
            item
            for item in calls
            if item[1] == "update_job" and item[2][1].get("provider") == ROUTER_PROVIDER
        )
        self.assertEqual(pin_update[2][1]["base_url"], ROUTER_URL)
        self.assertEqual(pinned["pinned_provider"], ROUTER_PROVIDER)
        self.assertIsNone(unpinned["pinned_provider"])
        self.assertIsNone(unpinned["pinned_model"])
        self.assertFalse(unpinned["pinned"])
        self.assertTrue(any(item[1] == "clear_drift_alerted" for item in calls))

    def test_update_job_changes_interval_and_clears_calendar_binding(self):
        job = {
            "id": "job-1",
            "name": "Scheduled",
            "prompt": "run",
            "schedule": {"kind": "interval", "minutes": 60, "display": "every 60m"},
            "xnobrain_time_revision": 3,
            "xnobrain_time_revision_digest": "old",
        }
        calls: list[tuple] = []

        def native(profile, function, *args, **kwargs):
            calls.append((profile, function, args, kwargs))
            if function == "list_jobs":
                return [job]
            if function == "update_job":
                updates = dict(args[1])
                if isinstance(updates.get("schedule"), str):
                    updates["schedule"] = {
                        "kind": "interval",
                        "minutes": 15,
                        "display": "every 15m",
                    }
                job.update(updates)
                return dict(job)
            if function in {"clear_drift_alerted", "clear_preflight_alerted"}:
                return True
            raise AssertionError(function)

        with patch.object(self.service, "_native", side_effect=native):
            result = self.service.update_job(
                "job-1", {"interval_minutes": 15}, "agent-1"
            )

        schedule_update = next(
            item
            for item in calls
            if item[1] == "update_job" and item[2][1].get("schedule") == "every 15m"
        )
        self.assertEqual(schedule_update[0], "agent-1")
        self.assertEqual(result["schedule_kind"], "interval")
        self.assertEqual(result["interval_minutes"], 15)
        self.assertIsNone(job["xnobrain_time_revision"])
        self.assertIsNone(job["xnobrain_time_revision_digest"])

    def test_update_job_binds_calendar_timezone_and_revision(self):
        from cron import jobs

        from xnobrain.integrations.cron_timezone import install_timezone_computation

        install_timezone_computation(jobs)
        job = {
            "id": "job-1",
            "name": "Scheduled",
            "prompt": "run",
            "schedule": {"kind": "interval", "minutes": 60, "display": "every 60m"},
        }

        def native(_profile, function, *args, **_kwargs):
            if function == "list_jobs":
                return [job]
            if function == "update_job":
                updates = dict(args[1])
                if isinstance(updates.get("schedule"), str):
                    updates["schedule"] = jobs.parse_schedule(updates["schedule"])
                job.update(updates)
                return dict(job)
            if function in {"clear_drift_alerted", "clear_preflight_alerted"}:
                return True
            raise AssertionError(function)

        with patch.object(self.service, "_native", side_effect=native):
            result = self.service.update_job(
                "job-1",
                {"schedule": "0 9 * * *", "timezone": "Asia/Ho_Chi_Minh"},
                "agent-1",
            )

        self.assertEqual(result["schedule_kind"], "cron")
        self.assertEqual(result["timezone"], "Asia/Ho_Chi_Minh")
        self.assertEqual(result["dst_policy"], "skip_gap_earlier_fold")
        self.assertEqual(result["revision"], 1)

    def test_list_jobs_normalizes_legacy_codex_pin(self):
        job = {
            "id": "job-legacy",
            "name": "GET DATA VNSTOCLK",
            "prompt": "run",
            "schedule": {"kind": "interval", "minutes": 60, "display": "every 60m"},
            "provider": "codex",
            "model": "cx/gpt-5.6-luna",
        }
        calls: list[tuple] = []

        def native(profile, function, *args, **kwargs):
            calls.append((profile, function, args, kwargs))
            if function == "list_jobs":
                return [job]
            if function == "update_job":
                job.update(args[1])
                return dict(job)
            if function in {"clear_drift_alerted", "clear_preflight_alerted"}:
                return True
            raise AssertionError(function)

        with _router_patches(), patch.object(self.service, "_native", side_effect=native):
            rows = self.service.list_jobs("agent-1")
        self.assertEqual(job["provider"], ROUTER_PROVIDER)
        self.assertEqual(job["base_url"], ROUTER_URL)
        self.assertEqual(job["xnobrain_pinned_connector"], "codex")
        self.assertEqual(rows[0]["pinned_label"], "Codex · cx/gpt-5.6-luna")
        self.assertTrue(any(item[1] == "clear_drift_alerted" for item in calls))

    def test_dto_reports_router_pin_and_human_label(self):
        result = CronService._dto(
            "agent-1",
            {
                "id": "job-1",
                "schedule": {"kind": "interval", "minutes": 30, "display": "every 30m"},
                "provider": ROUTER_PROVIDER,
                "model": "gb/grok-4.6",
                "xnobrain_pinned_connector": "grok-cli",
                "xnobrain_pinned_label": "Grok · gb/grok-4.6",
            },
        )
        self.assertEqual(result["pinned_provider"], ROUTER_PROVIDER)
        self.assertEqual(result["pinned_model"], "gb/grok-4.6")
        self.assertEqual(result["pinned_connector"], "grok-cli")
        self.assertEqual(result["pinned_label"], "Grok · gb/grok-4.6")
        self.assertNotIn("xnobrain_pinned_label", result)


class CronPinContractTests(TestCase):
    def test_create_and_update_accept_router_style_model_ids(self):
        created = CronCreate(
            agent_id="agent-1",
            name="Pinned",
            prompt="run",
            interval_minutes=30,
            provider="codex",
            model="cx/gpt-5.6-luna",
        )
        self.assertEqual(created.provider, "codex")
        self.assertEqual(created.model, "cx/gpt-5.6-luna")
        updated = CronUpdate(provider="grok-cli", model="gb/grok-4.6")
        self.assertEqual(updated.model, "gb/grok-4.6")

    def test_update_accepts_one_schedule_shape(self):
        interval = CronUpdate(interval_minutes=15)
        calendar = CronUpdate(schedule="0 9 * * *", timezone="Asia/Ho_Chi_Minh")
        self.assertEqual(interval.interval_minutes, 15)
        self.assertEqual(calendar.timezone, "Asia/Ho_Chi_Minh")
        with self.assertRaises(ValueError):
            CronUpdate(interval_minutes=15, schedule="0 9 * * *", timezone="Etc/UTC")
        with self.assertRaises(ValueError):
            CronUpdate(schedule="0 9 * * *")


class _Agents:
    def describe_agent(self, agent_id, include_memory=False):
        return {"id": agent_id}

    def list_agent_names(self):
        return ["agent-1"]

    def profile_path(self, agent_id):
        from pathlib import Path

        return Path("/tmp/xnobrain-cron-pin-tests") / agent_id
