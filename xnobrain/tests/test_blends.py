"""Adapter, service, and route tests for Model Blends (plan 012)."""

from __future__ import annotations

import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient
import yaml

from xnobrain.app import XNOBrainApplication
from xnobrain.integrations import AgentManager, GlobalConfigManager, NineRouterAPIError
from xnobrain.services.blends import BlendService
from xnobrain.services.platform import ServiceError
from xnobrain.tests.test_nine_router import FakeNineRouterManager


# --- adapter tests (responses-map fake) --------------------------------------

class BlendAdapterTests(unittest.IsolatedAsyncioTestCase):
    async def test_list_combos_normalizes_rows(self):
        manager = FakeNineRouterManager({
            ("GET", "/api/combos"): {"combos": [
                {"id": "c1", "name": "duo", "kind": "auto",
                 "models": ["cx/a", "cx/b"], "createdAt": "t0", "updatedAt": "t1"},
                "not-a-mapping",
            ]},
        })
        combos = await manager.list_combos()
        self.assertEqual(len(combos), 1)
        self.assertEqual(combos[0], {
            "id": "c1", "name": "duo", "kind": "auto",
            "models": ["cx/a", "cx/b"], "created_at": "t0", "updated_at": "t1"})

    async def test_update_combo_sends_only_provided_keys(self):
        manager = FakeNineRouterManager({("PUT", "/api/combos/c1"): {"id": "c1", "name": "duo"}})
        await manager.update_combo("c1", models=["cx/a"])
        self.assertIn(("PUT", "/api/combos/c1", {"models": ["cx/a"]}), manager.requests)

    async def test_set_combo_strategy_writes_whole_merged_map(self):
        manager = FakeNineRouterManager({
            ("GET", "/api/settings"): {"comboStrategies": {"other": {"fallbackStrategy": "fusion"}}},
            ("PATCH", "/api/settings"): {},
        })
        await manager.set_combo_strategy("duo", strategy="round-robin")
        self.assertIn((
            "PATCH", "/api/settings",
            {"comboStrategies": {"other": {"fallbackStrategy": "fusion"},
                                 "duo": {"fallbackStrategy": "round-robin"}}},
        ), manager.requests)

    async def test_set_smart_route_keeps_upstream_fallback_and_stores_policy(self):
        policy = {
            "quick": [{"model": "cx/a", "reasoning": "auto"}],
            "normal": [{"model": "cx/b", "reasoning": "medium"}],
            "difficult": [{"model": "cx/c", "reasoning": "high"}],
            "uncertainTier": "difficult",
        }
        manager = FakeNineRouterManager({
            ("GET", "/api/settings"): {"comboStrategies": {}},
            ("PATCH", "/api/settings"): {},
        })
        await manager.set_combo_strategy("smart", strategy="smart-route", smart_route=policy)
        patch_body = next(
            body for method, path, body in manager.requests
            if method == "PATCH" and path == "/api/settings"
        )
        self.assertEqual(
            patch_body["comboStrategies"]["smart"]["fallbackStrategy"], "fallback"
        )
        self.assertEqual(patch_body["comboStrategies"]["smart"]["smartRoute"], policy)

    async def test_resolve_smart_route_classifies_and_applies_reasoning(self):
        policy = {
            "quick": [{"model": "cx/a", "reasoning": "auto", "context_length": 32_000}],
            "normal": [{"model": "cx/b", "reasoning": "auto", "context_length": 128_000}],
            "difficult": [{
                "model": "cx/c", "reasoning": "auto", "context_length": 200_000,
                "reasoning_levels": ["medium", "high"],
            }],
            "uncertainTier": "difficult",
        }
        manager = FakeNineRouterManager({
            ("GET", "/api/settings"): {"comboStrategies": {"smart": {"smartRoute": policy}}},
            ("POST", "/v1/chat/completions"): {
                "choices": [{"message": {"content": "difficult"}}]
            },
        })
        route = await manager.resolve_smart_route("smart", "Design a distributed system")
        self.assertEqual(route, {
            "model": "cx/c", "reasoning": "high", "tier": "difficult", "route": "smart",
        })
        classifier_body = next(
            body for method, path, body in manager.requests
            if method == "POST" and path == "/v1/chat/completions"
        )
        self.assertEqual(classifier_body["max_tokens"], 32)
        self.assertEqual(classifier_body["reasoning_effort"], "low")

    async def test_resolve_smart_route_sends_obvious_greeting_to_quick_without_classifier(self):
        policy = {
            "quick": [{"model": "cx/a", "reasoning": "low"}],
            "normal": [{"model": "cx/b", "reasoning": "medium"}],
            "difficult": [{"model": "cx/c", "reasoning": "high"}],
        }
        manager = FakeNineRouterManager({
            ("GET", "/api/settings"): {"comboStrategies": {"smart": {"smartRoute": policy}}},
        })
        route = await manager.resolve_smart_route("smart", "hi em")
        self.assertEqual(route["model"], "cx/a")
        self.assertEqual(route["tier"], "quick")
        self.assertFalse(any(path == "/v1/chat/completions" for _, path, _ in manager.requests))

    async def test_model_metadata_reads_nine_router_capabilities(self):
        manager = FakeNineRouterManager({
            ("GET", "/api/providers"): {"connections": [
                {"id": "codex-1", "provider": "codex", "authType": "oauth"},
            ]},
            ("GET", "/v1/models?kind=llm"): {"data": [{
                "id": "cx/gpt", "owned_by": "cx",
                "capabilities": {"contextWindow": 372_000, "reasoning": True},
            }]},
            ("GET", "/api/combos"): {"combos": []},
            ("POST", "/api/combos"): {},
        })
        model = next(row for row in (await manager.list_models())["data"] if row["id"] == "cx/gpt")
        self.assertEqual(model["context_length"], 372_000)
        self.assertEqual(model["reasoning_levels"], ["low", "medium", "high"])

    async def test_resolve_smart_route_skips_models_with_too_little_context(self):
        policy = {
            "quick": [{"model": "cx/a", "context_length": 8_000}],
            "normal": [{"model": "cx/b", "context_length": 16_000}],
            "difficult": [{"model": "cx/c", "context_length": 128_000}],
        }
        manager = FakeNineRouterManager({
            ("GET", "/api/settings"): {"comboStrategies": {"smart": {"smartRoute": policy}}},
            ("POST", "/v1/chat/completions"): {
                "choices": [{"message": {"content": "quick"}}]
            },
        })
        route = await manager.resolve_smart_route(
            "smart", "Summarize this", required_context_tokens=64_000
        )
        self.assertEqual(route["model"], "cx/c")
        self.assertEqual(route["tier"], "difficult")

    async def test_combo_settings_whitelists_three_keys(self):
        manager = FakeNineRouterManager({
            ("GET", "/api/settings"): {
                "comboStrategy": "fallback", "comboStrategies": {"x": {}},
                "comboStickyRoundRobinLimit": 3, "password": "secret", "apiKey": "leak"},
        })
        settings = await manager.combo_settings()
        self.assertEqual(set(settings), {"combo_strategy", "combo_strategies", "combo_sticky_limit"})
        self.assertEqual(settings["combo_sticky_limit"], 3)

    async def test_list_models_surfaces_blend_and_auto_skips_it(self):
        manager = FakeNineRouterManager({
            ("GET", "/api/providers"): {"connections": [
                {"id": "codex-1", "provider": "codex", "authType": "oauth"}]},
            ("GET", "/v1/models?kind=llm"): {"data": [
                {"id": "cx/gpt", "owned_by": "cx"},
                {"id": "duo", "owned_by": "combo"}]},
            ("GET", "/api/combos"): {"combos": []},
            ("POST", "/api/combos"): {},
        })
        data = (await manager.list_models())["data"]
        ids = [(row["id"], row["provider"]) for row in data]
        self.assertEqual(ids[0], ("auto", "nine-router"))
        self.assertIn(("duo", "blend"), ids)
        self.assertLess(ids.index(("duo", "blend")), ids.index(("cx/gpt", "codex")))
        # the auto combo POST must not include the blend id (no "/")
        auto_post = next(body for method, path, body in manager.requests
                         if method == "POST" and path == "/api/combos")
        self.assertNotIn("duo", auto_post["models"])


# --- service tests (stateful fake) -------------------------------------------

class FakeBlendRouter:
    def __init__(self, *, combos=None, models=None, settings=None):
        self._combos = combos or []
        self._models = models or []
        self._settings = settings or {"combo_strategy": "fallback", "combo_strategies": {}, "combo_sticky_limit": None}
        self.calls: list[tuple] = []
        self.fail_settings = False

    async def list_combos(self):
        return [dict(combo) for combo in self._combos]

    async def create_combo(self, name, models):
        self.calls.append(("create_combo", name, list(models)))
        row = {"id": f"cmb_{name}", "name": name, "kind": "", "models": list(models), "created_at": "", "updated_at": ""}
        self._combos.append(row)
        return dict(row)

    async def update_combo(self, combo_id, *, name=None, models=None):
        self.calls.append(("update_combo", combo_id, name, models))
        for combo in self._combos:
            if combo["id"] == combo_id:
                if name is not None:
                    combo["name"] = name
                if models is not None:
                    combo["models"] = list(models)
                return dict(combo)
        return {"id": combo_id, "name": name or "", "models": models or [], "kind": "", "created_at": "", "updated_at": ""}

    async def delete_combo(self, combo_id):
        self.calls.append(("delete_combo", combo_id))
        self._combos = [combo for combo in self._combos if combo["id"] != combo_id]
        return {"id": combo_id, "deleted": True}

    async def combo_settings(self):
        strategies = {k: dict(v) for k, v in self._settings["combo_strategies"].items()}
        return {**self._settings, "combo_strategies": strategies}

    async def set_combo_strategy(
        self, name, *, strategy, judge_model=None, fusion_tuning=None, smart_route=None,
    ):
        self.calls.append(("set_strategy", name, strategy, judge_model))
        if self.fail_settings:
            raise NineRouterAPIError("settings write failed", status=502)
        entry = {"fallbackStrategy": "fallback" if smart_route is not None else strategy}
        if judge_model is not None:
            entry["judgeModel"] = judge_model
        if smart_route is not None:
            entry["smartRoute"] = dict(smart_route)
        self._settings["combo_strategies"][name] = entry

    async def clear_combo_strategy(self, name):
        self.calls.append(("clear_strategy", name))
        self._settings["combo_strategies"].pop(name, None)

    async def set_combo_sticky_limit(self, limit):
        self.calls.append(("sticky", limit))
        self._settings["combo_sticky_limit"] = limit

    async def list_models(self):
        data = [{"id": "auto", "provider": "nine-router", "name": "Auto"}]
        for combo in self._combos:
            data.append({"id": combo["name"], "provider": "blend", "name": combo["name"]})
        for model in self._models:
            data.append({
                "id": model["id"],
                "provider": model.get("provider", "codex"),
                "name": model["id"],
                "context_length": model.get("context_length"),
                "reasoning_levels": model.get("reasoning_levels", []),
            })
        return {"data": data}


def _service(fail_settings=False):
    router = FakeBlendRouter(
        combos=[{"id": "cmb_auto", "name": "auto", "kind": "", "models": ["cx/a"], "created_at": "", "updated_at": ""}],
        models=[
            {"id": "cx/a", "context_length": 32_000, "reasoning_levels": ["low", "medium"]},
            {"id": "cx/b", "context_length": 128_000, "reasoning_levels": ["low", "medium", "high"]},
            {"id": "cx/c", "context_length": 200_000, "reasoning_levels": ["medium", "high"]},
            {"id": "plainmodel"},
        ],
    )
    router.fail_settings = fail_settings
    return BlendService(router), router


class BlendServiceTests(unittest.IsolatedAsyncioTestCase):
    async def test_create_fallback_writes_no_strategy(self):
        service, router = _service()
        dto = await service.create_blend({"name": "duo", "models": ["cx/a", "cx/b"]})
        self.assertEqual(dto["strategy"], "fallback")
        self.assertTrue(any(c[0] == "create_combo" for c in router.calls))
        self.assertFalse(any(c[0] in ("set_strategy", "sticky") for c in router.calls))

    async def test_create_round_robin_writes_strategy_and_sticky(self):
        service, router = _service()
        dto = await service.create_blend({"name": "duo", "models": ["cx/a", "cx/b"], "strategy": "round-robin", "sticky_limit": 2})
        self.assertEqual(dto["strategy"], "round-robin")
        self.assertEqual(dto["sticky_limit"], 2)
        self.assertIn(("set_strategy", "duo", "round-robin", None), router.calls)
        self.assertIn(("sticky", 2), router.calls)

    async def test_fusion_requires_two_models_and_judge(self):
        service, _ = _service()
        with self.assertRaises(ServiceError):
            await service.create_blend({"name": "f", "models": ["cx/a"], "strategy": "fusion", "judge_model": "cx/a"})
        with self.assertRaises(ServiceError):
            await service.create_blend({"name": "f", "models": ["cx/a", "cx/b"], "strategy": "fusion"})
        dto = await service.create_blend({"name": "f", "models": ["cx/a", "cx/b"], "strategy": "fusion", "judge_model": "cx/a"})
        self.assertEqual(dto["judge_model"], "cx/a")

    async def test_judge_rejected_without_fusion(self):
        service, _ = _service()
        with self.assertRaises(ServiceError):
            await service.create_blend({"name": "d", "models": ["cx/a"], "judge_model": "cx/a"})

    async def test_name_collisions(self):
        service, _ = _service()
        with self.assertRaises(ServiceError) as auto:
            await service.create_blend({"name": "Auto", "models": ["cx/a"]})
        self.assertEqual(auto.exception.code, "blend_reserved")
        await service.create_blend({"name": "duo", "models": ["cx/a"]})
        with self.assertRaises(ServiceError) as dup:
            await service.create_blend({"name": "duo", "models": ["cx/a"]})
        self.assertEqual(dup.exception.code, "blend_name_conflict")
        with self.assertRaises(ServiceError) as shadow:  # matches a real model id
            await service.create_blend({"name": "plainmodel", "models": ["cx/a"]})
        self.assertEqual(shadow.exception.code, "blend_name_conflict")

    async def test_unknown_model_rejected(self):
        service, _ = _service()
        with self.assertRaises(ServiceError):
            await service.create_blend({"name": "d", "models": ["cx/zzz"]})

    async def test_compensating_delete_when_settings_write_fails(self):
        service, router = _service(fail_settings=True)
        with self.assertRaises(NineRouterAPIError):
            await service.create_blend({"name": "duo", "models": ["cx/a", "cx/b"], "strategy": "round-robin"})
        self.assertTrue(any(c[0] == "delete_combo" for c in router.calls))
        self.assertFalse(any(c["name"] == "duo" for c in router._combos))

    async def test_auto_is_immutable(self):
        service, router = _service()
        before = len(router.calls)
        with self.assertRaises(ServiceError) as edit:
            await service.update_blend("cmb_auto", {"models": ["cx/b"]})
        self.assertEqual(edit.exception.status, 403)
        with self.assertRaises(ServiceError) as remove:
            await service.delete_blend("cmb_auto")
        self.assertEqual(remove.exception.status, 403)
        # no mutating router call happened for auto
        self.assertFalse(any(c[0] in ("update_combo", "delete_combo", "set_strategy") for c in router.calls[before:]))

    async def test_rename_moves_strategy_entry(self):
        service, router = _service()
        await service.create_blend({"name": "duo", "models": ["cx/a", "cx/b"], "strategy": "round-robin"})
        combo_id = next(c["id"] for c in router._combos if c["name"] == "duo")
        router.calls.clear()
        await service.update_blend(combo_id, {"name": "trio"})
        self.assertIn(("clear_strategy", "duo"), router.calls)
        self.assertIn(("set_strategy", "trio", "round-robin", None), router.calls)

    async def test_available_models_excludes_blends_and_auto(self):
        service, _ = _service()
        await service.create_blend({"name": "duo", "models": ["cx/a"]})
        ids = {m["id"] for m in (await service.available_models())["data"]}
        self.assertNotIn("auto", ids)
        self.assertNotIn("duo", ids)
        self.assertIn("cx/a", ids)

    async def test_create_smart_route_groups_models_and_reports_context(self):
        service, router = _service()
        smart_route = {
            "quick": [{"model": "cx/a", "reasoning": "low"}],
            "normal": [{"model": "cx/b", "reasoning": "auto"}],
            "difficult": [{"model": "cx/c", "reasoning": "high"}],
            "uncertain_tier": "difficult",
        }
        dto = await service.create_blend({
            "name": "smart", "strategy": "smart-route", "smart_route": smart_route,
        })
        self.assertEqual(dto["strategy"], "smart-route")
        self.assertEqual(dto["models"], ["cx/a", "cx/b", "cx/c"])
        self.assertEqual(dto["guaranteed_context"], 32_000)
        self.assertEqual(dto["maximum_context"], 200_000)
        self.assertEqual(dto["smart_route"]["uncertain_tier"], "difficult")
        entry = router._settings["combo_strategies"]["smart"]
        self.assertEqual(entry["fallbackStrategy"], "fallback")
        self.assertIn("smartRoute", entry)

    async def test_smart_route_requires_every_group_and_unique_models(self):
        service, _ = _service()
        with self.assertRaises(ServiceError):
            await service.create_blend({
                "name": "smart", "strategy": "smart-route",
                "smart_route": {
                    "quick": [{"model": "cx/a"}],
                    "normal": [],
                    "difficult": [{"model": "cx/c"}],
                },
            })
        with self.assertRaises(ServiceError):
            await service.create_blend({
                "name": "smart", "strategy": "smart-route",
                "smart_route": {
                    "quick": [{"model": "cx/a"}],
                    "normal": [{"model": "cx/a"}],
                    "difficult": [{"model": "cx/c"}],
                },
            })


# --- ASGI route integration --------------------------------------------------

class BlendRouteTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        base = Path(self.temporary.name)
        root = base / "root"
        profiles = base / "profiles"
        root.mkdir(parents=True)
        profiles.mkdir(parents=True)
        (root / "config.yaml").write_text(yaml.safe_dump({
            "model": {"provider": "custom:nine-router", "default": "auto"},
            "providers": {}, "agent": {"reasoning_effort": "medium"},
            "approvals": {"mode": "manual"}, "terminal": {"backend": "local"},
        }), encoding="utf-8")
        self.environment = patch.dict(os.environ, {
            "HERMES_HOME": str(root), "HERMES_ROOT_PROFILE": str(root),
            "HERMES_PROFILES_ROOT": str(profiles), "DATA_DIR": self.temporary.name,
        })
        self.environment.start()
        self.router = FakeBlendRouter(
            combos=[{"id": "cmb_auto", "name": "auto", "kind": "", "models": ["cx/a"], "created_at": "", "updated_at": ""}],
            models=[{"id": "cx/a"}, {"id": "cx/b"}, {"id": "cx/c"}])
        app = FastAPI()
        XNOBrainApplication(
            AgentManager(root_profile=root, profiles_root=profiles, legacy_agents_root=base / "legacy"),
            GlobalConfigManager(root_profile=root), self.router).register(app)
        self.app = app

    def tearDown(self):
        self.environment.stop()
        self.temporary.cleanup()

    def client(self):
        return AsyncClient(transport=ASGITransport(app=self.app), base_url="http://test")

    async def test_crud_and_guards(self):
        async with self.client() as client:
            created = await client.post("/xnobrain/api/runtime/v1/blends", json={"name": "duo", "models": ["cx/a", "cx/b"]})
            self.assertEqual(created.status_code, 201)
            blend_id = created.json()["data"]["id"]

            blends = (await client.get("/xnobrain/api/runtime/v1/blends")).json()["data"]["blends"]
            self.assertEqual(blends[0]["name"], "auto")
            self.assertTrue(blends[0]["system"])

            models = (await client.get("/xnobrain/api/runtime/v1/blends/available-models")).json()["data"]["data"]
            ids = {m["id"] for m in models}
            self.assertNotIn("auto", ids)
            self.assertNotIn("duo", ids)

            auto_id = next(b["id"] for b in blends if b["name"] == "auto")
            self.assertEqual((await client.delete(f"/xnobrain/api/runtime/v1/blends/{auto_id}")).status_code, 403)
            self.assertEqual(
                (await client.post("/xnobrain/api/runtime/v1/blends", json={"name": "duo", "models": ["cx/a"]})).status_code, 409)
            self.assertEqual((await client.delete(f"/xnobrain/api/runtime/v1/blends/{blend_id}")).status_code, 200)

    async def test_unavailable_router_returns_503(self):
        async def boom():
            raise NineRouterAPIError("9Router is unavailable", code="nine_router_unavailable", status=503)
        self.router.list_combos = lambda: boom()
        async with self.client() as client:
            self.assertEqual((await client.get("/xnobrain/api/runtime/v1/blends")).status_code, 503)

    async def test_smart_route_contract_round_trips(self):
        smart_route = {
            "quick": [{"model": "cx/a", "reasoning": "low"}],
            "normal": [{"model": "cx/b", "reasoning": "medium"}],
            "difficult": [{"model": "cx/a", "reasoning": "high"}],
            "uncertain_tier": "difficult",
        }
        # The same model cannot be put in two task groups.
        async with self.client() as client:
            invalid = await client.post("/xnobrain/api/runtime/v1/blends", json={
                "name": "smart-invalid",
                "models": ["cx/a", "cx/b"],
                "strategy": "smart-route",
                "smart_route": smart_route,
            })
            self.assertEqual(invalid.status_code, 400)

            smart_route["difficult"] = [{"model": "cx/c", "reasoning": "high"}]
            created = await client.post("/xnobrain/api/runtime/v1/blends", json={
                "name": "smart-valid",
                "models": ["cx/a", "cx/b", "cx/c"],
                "strategy": "smart-route",
                "smart_route": smart_route,
            })
            self.assertEqual(created.status_code, 201)
            data = created.json()["data"]
            self.assertEqual(data["strategy"], "smart-route")
            self.assertEqual(data["smart_route"]["uncertain_tier"], "difficult")


if __name__ == "__main__":
    unittest.main()
