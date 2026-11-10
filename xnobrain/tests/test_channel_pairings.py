"""Generic channel pairing management through the pinned Hermes store."""

from __future__ import annotations

import asyncio
import json
import multiprocessing
import os
import tempfile
import threading
import time
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

import yaml
from gateway.pairing import CODE_TTL_SECONDS, PairingStore
from pydantic import ValidationError

from xnobrain.integrations.hermes_pairings import (
    get_channel_pairing_store,
    install_pairing_constructor_locking,
    pairing_lock_path,
    pairing_lock_path_for_directory,
)
from xnobrain.models.channels import (
    ChannelPairingRequest,
    ChannelPairingRevokeRequest,
)
from xnobrain.repositories import file_lock as file_lock_module
from xnobrain.repositories.file_lock import FileLock, FileLockTimeout
from xnobrain.repositories.files import FileRepository
from xnobrain.services.base import ServiceError
from xnobrain.services.channels import ChannelsService
from xnobrain.trusted_context import TrustedRequestContext

TOKEN = "channel-pairing-test-token"


def _gateway_store(agent_id, root_profile_name):
    from gateway.pairing import PairingStore

    return PairingStore(profile="default" if agent_id == root_profile_name else agent_id)


def _worker_gateway_generate(agent_id, root_name, platform, user_id, barrier):
    from xnobrain.integrations.hermes_pairings import install_gateway_pairing_locking

    install_gateway_pairing_locking()
    store = _gateway_store(agent_id, root_name)
    barrier.wait(timeout=10)
    code = store.generate_code(platform, user_id, "Gateway User")
    if not code:
        raise RuntimeError("gateway generate_code failed")


def _worker_gateway_revoke(agent_id, root_name, platform, user_id, barrier):
    from xnobrain.integrations.hermes_pairings import install_gateway_pairing_locking

    install_gateway_pairing_locking()
    store = _gateway_store(agent_id, root_name)
    barrier.wait(timeout=10)
    if not store.revoke(platform, user_id):
        raise RuntimeError("gateway revoke failed")


def _worker_api_deny(agent_id, root_name, platform, request_id, barrier):
    from xnobrain.integrations.hermes_pairings import ChannelPairingStore

    store = ChannelPairingStore(agent_id, root_name)
    barrier.wait(timeout=10)
    status = store.deny(platform, request_id)
    if status != "denied":
        raise RuntimeError(f"api deny returned {status}")


def _worker_api_approve(agent_id, root_name, platform, request_id, barrier):
    from xnobrain.integrations.hermes_pairings import ChannelPairingStore

    store = ChannelPairingStore(agent_id, root_name)
    barrier.wait(timeout=10)
    if store.approve(platform, request_id) is None:
        raise RuntimeError("api approve failed")


class _Platform:
    def __init__(self, repository: FileRepository, profile: Path, root: Path):
        self.repository = repository
        self.profile = profile
        self.config = SimpleNamespace(root_profile=root)

    def _conversation_profile(self, agent_id: str) -> Path:
        if agent_id != "agent-1":
            raise ServiceError("agent profile not found", status=404, code="not_found")
        return self.profile

    def _snapshot_agent(self, agent_id, kind, target, content):
        return self.repository.snapshot(agent_id, kind, target, content)


class _ChannelPairingFixtureMixin:
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        root = Path(self.temporary.name)
        self.profiles = root / "agent"
        self.root_profile = self.profiles / "big-brother"
        self.profile = self.profiles / "agent-1"
        self.root_profile.mkdir(parents=True)
        self.profile.mkdir(parents=True)
        self.profile.joinpath("config.yaml").write_text(
            yaml.safe_dump(
                {
                    "platforms": {
                        "zalo": {
                            "enabled": True,
                            "extra": {"dm_policy": "pairing"},
                        },
                        "zalo_bot": {
                            "enabled": True,
                            "extra": {"dm_policy": "pairing"},
                        },
                    }
                }
            ),
            encoding="utf-8",
        )
        self.environment = mock.patch.dict(
            os.environ,
            {
                "FT_ENABLE_AGENT_CHANNELS": "true",
                "HERMES_HOME": str(self.root_profile),
                "HERMES_ROOT_PROFILE": str(self.root_profile),
                "HERMES_PROFILES_ROOT": str(self.profiles),
                "RUNTIME_INTERNAL_SERVICE_TOKEN": TOKEN,
                "DATA_DIR": str(root / "data"),
                "RUNTIME_CUSTOM_PAGE_STORAGE_MOUNT": "",
            },
        )
        self.environment.start()
        self.addCleanup(self.environment.stop)
        self.repository = FileRepository(
            root / "data", self.profiles, root_profile=self.root_profile
        )
        self.platform = _Platform(self.repository, self.profile, self.root_profile)
        self.service = ChannelsService(self.platform)
        self.service.repository.write_owner(
            self.profile,
            {
                "user_id": "user-1",
                "tenant_id": "personal-user-1",
                "organization_id": "",
                "agent_id": "agent-1",
                "bound_at": 1,
            },
        )

    def _trusted(self, subject="user-1", tenant="personal-user-1", organization=""):
        return TrustedRequestContext(
            subject=subject, tenant_id=tenant, organization_id=organization
        )

    def _store(self, agent_id="agent-1") -> PairingStore:
        return PairingStore(profile=agent_id)

    def _seed_pending(self, user_id="789", user_name="Nam", platform="zalo"):
        store = self._store()
        code = store.generate_code(platform, user_id, user_name)
        self.assertIsNotNone(code)
        return store, code

    def _request_id(self, user_id="789", platform="zalo"):
        for item in self._store().list_pending(platform):
            if str(item.get("user_id")) == user_id:
                return str(item["request_id"])
        raise AssertionError("pending request missing")

    def _seed_approved(self, user_id="789", user_name="Nam", platform="zalo"):
        store, code = self._seed_pending(user_id, user_name, platform)
        self.assertIsNotNone(store.approve_code(platform, code))
        return store


class ChannelPairingCase(_ChannelPairingFixtureMixin, unittest.IsolatedAsyncioTestCase):
    async def test_list_requires_verified_same_agent_personal_owner(self):
        for trusted in (None, TrustedRequestContext()):
            with self.subTest(trusted=trusted):
                with self.assertRaises(ServiceError) as raised:
                    await self.service.list_pairings("agent-1", "zalo", trusted)
                self.assertEqual(raised.exception.status, 401)
                self.assertEqual(raised.exception.code, "trusted_context_required")

        with self.assertRaises(ServiceError) as raised:
            await self.service.list_pairings(
                "agent-1", "zalo", self._trusted(subject="someone-else")
            )
        self.assertEqual(raised.exception.status, 403)
        self.assertEqual(raised.exception.code, "channel_owner_mismatch")

        with self.assertRaises(ServiceError) as raised:
            await self.service.list_pairings("agent-1", "zalo", self._trusted(organization="org-1"))
        self.assertEqual(raised.exception.status, 403)
        self.assertEqual(raised.exception.code, "channel_personal_required")

    async def test_feature_platform_and_enabled_channel_gate(self):
        with mock.patch.dict(os.environ, {"FT_ENABLE_AGENT_CHANNELS": "false"}):
            with self.assertRaises(ServiceError) as raised:
                await self.service.list_pairings("agent-1", "zalo", self._trusted())
        self.assertEqual(raised.exception.code, "feature_disabled")

        with self.assertRaises(ServiceError) as raised:
            await self.service.list_pairings("agent-1", "telegram", self._trusted())
        self.assertEqual(raised.exception.status, 422)
        self.assertEqual(raised.exception.code, "invalid_platform")

        config = yaml.safe_load(self.profile.joinpath("config.yaml").read_text())
        config["platforms"]["zalo"]["enabled"] = False
        self.profile.joinpath("config.yaml").write_text(yaml.safe_dump(config), encoding="utf-8")
        with self.assertRaises(ServiceError) as raised:
            await self.service.list_pairings("agent-1", "zalo", self._trusted())
        self.assertEqual(raised.exception.status, 403)
        self.assertEqual(raised.exception.code, "channel_disabled")

    async def test_list_omits_codes_and_hashes_and_shows_approved(self):
        store, code = self._seed_pending("789", "Nam")
        self._seed_approved("790", "Lan")
        result = await self.service.list_pairings("agent-1", "zalo", self._trusted())

        self.assertEqual(result["platform"], "zalo")
        self.assertEqual(result["dm_policy"], "pairing")
        self.assertEqual(result["pending"][0]["user_id"], "789")
        self.assertEqual(result["approved"][0]["user_id"], "790")
        payload = json.dumps(result)
        self.assertNotIn(code, payload)
        self.assertNotIn("hash", payload)
        self.assertNotIn("salt", payload)
        self.assertNotIn("approved.json", payload)
        self.assertGreater(result["pending"][0]["expires_at"], time.time())
        self.assertTrue(store.is_approved("zalo", "790"))

    async def test_approve_uses_real_store_and_core_recognizes_sender(self):
        self._seed_pending("789", "Nam")
        request_id = self._request_id("789")
        result = await self.service.approve_pairing("agent-1", "zalo", request_id, self._trusted())

        self.assertEqual(result["status"], "approved")
        self.assertEqual(result["user_id"], "789")
        self.assertEqual(self._store().list_pending("zalo"), [])
        self.assertTrue(self._store().is_approved("zalo", "789"))
        self.assertFalse(self._store("agent-2").is_approved("zalo", "789"))

        repeats = await self.service.list_pairings("agent-1", "zalo", self._trusted())
        self.assertEqual([item["user_id"] for item in repeats["approved"]], ["789"])

    async def test_deny_removes_only_one_request_without_approving(self):
        self._seed_pending("789", "Nam")
        self._seed_pending("790", "Lan")
        request_id = self._request_id("789")
        result = await self.service.deny_pairing("agent-1", "zalo", request_id, self._trusted())

        self.assertEqual(result["status"], "denied")
        remaining = self._store().list_pending("zalo")
        self.assertEqual([item["user_id"] for item in remaining], ["790"])
        self.assertFalse(self._store().is_approved("zalo", "789"))
        self.assertFalse(self._store().is_approved("zalo", "790"))

    async def test_revoke_is_scoped_and_idempotent(self):
        self._seed_approved("789", "Nam")
        self._store("agent-2").generate_code("zalo", "999", "Other")

        first = await self.service.revoke_pairing("agent-1", "zalo", "789", self._trusted())
        second = await self.service.revoke_pairing("agent-1", "zalo", "789", self._trusted())

        self.assertEqual(first["status"], "revoked")
        self.assertEqual(second["status"], "not_approved")
        self.assertFalse(self._store().is_approved("zalo", "789"))
        self.assertTrue(self._store("agent-2").list_pending("zalo"))

    async def test_expired_missing_and_policy_failures_are_distinct(self):
        store, _ = self._seed_pending("789", "Nam")
        request_id = self._request_id("789")
        with store._lock:
            path = store._pending_path("zalo")
            pending = store._load_json(path)
            pending[request_id]["created_at"] = time.time() - CODE_TTL_SECONDS - 1
            store._save_json(path, pending)

        with self.assertRaises(ServiceError) as raised:
            await self.service.deny_pairing("agent-1", "zalo", request_id, self._trusted())
        self.assertEqual(raised.exception.status, 404)
        self.assertEqual(raised.exception.code, "pairing_request_expired")

        with self.assertRaises(ServiceError) as raised:
            await self.service.approve_pairing("agent-1", "zalo", request_id, self._trusted())
        self.assertEqual(raised.exception.status, 404)

        with self.assertRaises(ServiceError) as raised:
            await self.service.approve_pairing(
                "agent-1", "zalo", "0123456789abcdef", self._trusted()
            )
        self.assertEqual(raised.exception.code, "pairing_request_not_found")

        config = yaml.safe_load(self.profile.joinpath("config.yaml").read_text())
        config["platforms"]["zalo"]["extra"]["dm_policy"] = "allowlist"
        self.profile.joinpath("config.yaml").write_text(yaml.safe_dump(config), encoding="utf-8")
        with self.assertRaises(ServiceError) as raised:
            await self.service.approve_pairing(
                "agent-1", "zalo", "0123456789abcdef", self._trusted()
            )
        self.assertEqual(raised.exception.code, "channel_pairing_policy_required")

    async def test_concurrent_approve_and_deny_have_one_winner(self):
        self._seed_pending("789", "Nam")
        request_id = self._request_id("789")
        results = await asyncio.gather(
            self.service.approve_pairing("agent-1", "zalo", request_id, self._trusted()),
            self.service.deny_pairing("agent-1", "zalo", request_id, self._trusted()),
            return_exceptions=True,
        )

        successes = [item for item in results if isinstance(item, dict)]
        failures = [item for item in results if isinstance(item, ServiceError)]
        self.assertEqual(len(successes), 1)
        self.assertEqual(len(failures), 1)
        self.assertIn(
            failures[0].code,
            {"pairing_request_not_found", "pairing_request_expired"},
        )
        self.assertEqual(self._store().list_pending("zalo"), [])

    async def test_other_platform_and_profile_remain_untouched(self):
        self._seed_pending("789", "Nam", "zalo")
        self._seed_pending("790", "Lan", "zalo_bot")
        self._store("agent-2").generate_code("zalo", "999", "Other")
        request_id = self._request_id("789", "zalo")

        await self.service.approve_pairing("agent-1", "zalo", request_id, self._trusted())

        self.assertEqual(
            [item["user_id"] for item in self._store().list_pending("zalo_bot")],
            ["790"],
        )
        self.assertEqual(
            [item["user_id"] for item in self._store("agent-2").list_pending("zalo")],
            ["999"],
        )

    async def test_cold_reload_persists_approved_state(self):
        self._seed_pending("789", "Nam")
        request_id = self._request_id("789")
        await self.service.approve_pairing("agent-1", "zalo", request_id, self._trusted())

        result = await self.service.list_pairings("agent-1", "zalo", self._trusted())
        self.assertEqual([item["user_id"] for item in result["approved"]], ["789"])
        self.assertTrue(self._store().is_approved("zalo", "789"))


class ChannelPairingMultiprocessTests(
    _ChannelPairingFixtureMixin, unittest.IsolatedAsyncioTestCase
):
    def _run_pair(self, first, first_args, second, second_args):
        context = multiprocessing.get_context("spawn")
        barrier = context.Barrier(2)
        processes = [
            context.Process(target=first, args=(*first_args, barrier)),
            context.Process(target=second, args=(*second_args, barrier)),
        ]
        for process in processes:
            process.start()
        for process in processes:
            process.join(20)
        self.assertEqual([process.exitcode for process in processes], [0, 0])

    async def test_gateway_generate_and_api_deny_keep_unrelated_requests(self):
        store = self._store()
        self._seed_pending("keep", "Keep", "zalo")
        self._seed_pending("bot-keep", "Bot Keep", "zalo_bot")
        self._store("agent-2").generate_code("zalo", "other-keep", "Other")
        request_id = self._request_id("keep", "zalo")

        self._run_pair(
            _worker_gateway_generate,
            ("agent-1", "big-brother", "zalo", "gateway-user"),
            _worker_api_deny,
            ("agent-1", "big-brother", "zalo", request_id),
        )

        zalo_pending = self._store().list_pending("zalo")
        self.assertEqual([item["user_id"] for item in zalo_pending], ["gateway-user"])
        self.assertEqual(
            [item["user_id"] for item in self._store().list_pending("zalo_bot")],
            ["bot-keep"],
        )
        self.assertEqual(
            [item["user_id"] for item in self._store("agent-2").list_pending("zalo")],
            ["other-keep"],
        )
        self.assertTrue(store.is_approved("zalo", "keep") is False)

    async def test_gateway_revoke_and_api_approve_do_not_restore_each_other(self):
        self._seed_approved("keep", "Keep")
        self._seed_pending("new", "New")
        request_id = self._request_id("new", "zalo")

        self._run_pair(
            _worker_gateway_revoke,
            ("agent-1", "big-brother", "zalo", "keep"),
            _worker_api_approve,
            ("agent-1", "big-brother", "zalo", request_id),
        )

        approved = self._store().list_approved("zalo")
        self.assertEqual([item["user_id"] for item in approved], ["new"])
        self.assertEqual(self._store().list_pending("zalo"), [])

    async def test_gateway_store_observes_api_approve_and_revoke_without_restart(self):
        gateway_store = self._store()
        self._seed_pending("789", "Nam")
        request_id = self._request_id("789", "zalo")
        self.assertFalse(gateway_store.is_approved("zalo", "789"))

        await self.service.approve_pairing("agent-1", "zalo", request_id, self._trusted())
        self.assertTrue(gateway_store.is_approved("zalo", "789"))

        await self.service.revoke_pairing("agent-1", "zalo", "789", self._trusted())
        self.assertFalse(gateway_store.is_approved("zalo", "789"))

    async def test_active_and_named_profile_lock_paths_match_gateway_store(self):
        named = get_channel_pairing_store("agent-1", "big-brother")
        named_gateway = _gateway_store("agent-1", "big-brother")
        self.assertEqual(
            pairing_lock_path(named.store),
            pairing_lock_path(named_gateway),
        )
        self.assertEqual(named.store._dir, named_gateway._dir)

        active = get_channel_pairing_store("big-brother", "big-brother")
        active_gateway = PairingStore(profile="default")
        self.assertEqual(
            pairing_lock_path(active.store),
            pairing_lock_path(active_gateway),
        )
        self.assertEqual(active.store._dir, active_gateway._dir)
        other = _gateway_store("agent-2", "big-brother")
        self.assertNotEqual(
            pairing_lock_path(named.store),
            pairing_lock_path(other),
        )


class ChannelPairingLockTests(_ChannelPairingFixtureMixin, unittest.IsolatedAsyncioTestCase):
    def _try_other_thread(self, path):
        result = {}

        def acquire():
            try:
                with FileLock(path, timeout=0.5):
                    result["acquired"] = True
            except Exception as error:  # noqa: BLE001 - test observation
                result["error"] = error

        thread = threading.Thread(target=acquire)
        thread.start()
        thread.join(2)
        return result

    async def test_file_lock_is_thread_reentrant(self):
        path = self._store()._dir / ".reentrant-test.lock"
        with FileLock(path, timeout=0.2):
            with FileLock(path, timeout=0.2):
                pass
        self.assertEqual(self._try_other_thread(path), {"acquired": True})

    async def test_same_instance_nested_and_reused_releases_outer_lock(self):
        path = self._store()._dir / ".same-instance.lock"
        lock = FileLock(path, timeout=0.2)
        with lock:
            with lock:
                pass
        self.assertEqual(self._try_other_thread(path), {"acquired": True})

        with lock:
            pass
        self.assertEqual(self._try_other_thread(path), {"acquired": True})

    async def test_mixed_owner_borrower_nested_releases_every_count(self):
        path = self._store()._dir / ".mixed-nesting.lock"
        owner = FileLock(path, timeout=0.2)
        borrower = FileLock(path, timeout=0.2)
        with owner:
            with borrower:
                with borrower:
                    pass
        self.assertEqual(self._try_other_thread(path), {"acquired": True})

        with borrower:
            with borrower:
                pass
        self.assertEqual(self._try_other_thread(path), {"acquired": True})

    async def test_mixed_nested_exception_releases_every_count(self):
        path = self._store()._dir / ".mixed-exception.lock"
        owner = FileLock(path, timeout=0.2)
        borrower = FileLock(path, timeout=0.2)
        with self.assertRaises(RuntimeError):
            with owner:
                with borrower:
                    with borrower:
                        raise RuntimeError("synthetic nested failure")
        self.assertEqual(self._try_other_thread(path), {"acquired": True})

    async def test_canonical_alias_nesting_is_reentrant(self):
        directory = self._store()._dir / "alias-dir"
        directory.mkdir()
        canonical = directory / ".alias.lock"
        alias = directory / ".." / "alias-dir" / ".alias.lock"
        with FileLock(canonical, timeout=0.2):
            with FileLock(alias, timeout=0.2):
                pass
        self.assertEqual(self._try_other_thread(canonical), {"acquired": True})

    async def test_final_symlink_is_rejected(self):
        directory = self._store()._dir
        target = directory / ".real-lock"
        target.write_text("", encoding="utf-8")
        link = directory / ".linked-lock"
        link.symlink_to(target)
        with self.assertRaises(OSError):
            with FileLock(link, timeout=0.2):
                pass

    async def test_descriptor_is_closed_when_unlock_fails(self):
        path = self._store()._dir / ".unlock-failure.lock"
        original_flock = file_lock_module.fcntl.flock
        held_fd = {}

        def failing_unlock(descriptor, operation):
            if operation == file_lock_module.fcntl.LOCK_UN:
                held_fd["descriptor"] = descriptor
                raise OSError("unlock failed")
            return original_flock(descriptor, operation)

        lock = FileLock(path, timeout=0.2)
        with mock.patch.object(file_lock_module.fcntl, "flock", side_effect=failing_unlock):
            with self.assertRaises(OSError):
                with lock:
                    pass
        with self.assertRaises(OSError):
            os.fstat(held_fd["descriptor"])

    async def test_file_lock_timeout_is_fail_closed(self):
        path = self._store()._dir / ".timeout-test.lock"
        entered = threading.Event()
        release = threading.Event()

        def hold():
            with FileLock(path, timeout=0.2):
                entered.set()
                release.wait(5)

        holder = threading.Thread(target=hold)
        holder.start()
        self.assertTrue(entered.wait(2))
        try:
            with self.assertRaises(FileLockTimeout):
                with FileLock(path, timeout=0.1):
                    pass
        finally:
            release.set()
            holder.join(5)
        with FileLock(path, timeout=0.2):
            pass

    async def test_pairing_lock_path_rejects_symlink_escape(self):
        outside = Path(self.temporary.name) / "outside-pairing"
        outside.mkdir()
        link = Path(self.temporary.name) / "escaped-pairing"
        link.symlink_to(outside, target_is_directory=True)
        with self.assertRaises(ValueError):
            pairing_lock_path(SimpleNamespace(_dir=link))

    async def test_api_lock_timeout_returns_busy(self):
        self._seed_pending("789", "Nam")
        request_id = self._request_id("789", "zalo")
        adapter = get_channel_pairing_store("agent-1", "big-brother")
        path = pairing_lock_path(adapter.store)
        entered = threading.Event()
        release = threading.Event()

        def hold():
            with FileLock(path, timeout=1):
                entered.set()
                release.wait(5)

        holder = threading.Thread(target=hold)
        holder.start()
        self.assertTrue(entered.wait(2))
        try:
            with mock.patch(
                "xnobrain.integrations.hermes_pairings.PAIRING_LOCK_TIMEOUT_SECONDS",
                0.1,
            ):
                with self.assertRaises(ServiceError) as raised:
                    await self.service.approve_pairing(
                        "agent-1", "zalo", request_id, self._trusted()
                    )
            self.assertEqual(raised.exception.status, 503)
            self.assertEqual(raised.exception.code, "pairing_store_busy")
        finally:
            release.set()
            holder.join(5)
        self.assertEqual(
            [item["user_id"] for item in self._store().list_pending("zalo")],
            ["789"],
        )


class ChannelPairingLinkedProfilesTests(unittest.TestCase):
    """Managed layouts link ``<root>/profiles`` to the shared profiles root."""

    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.agents = Path(self.temporary.name) / "agents"
        self.root = self.agents / "big-brother"
        self.root.mkdir(parents=True)
        self.profile = self.agents / "plugin-qa"
        self.profile.mkdir()
        self.linked_profiles = self.root / "profiles"
        self.linked_profiles.symlink_to(self.agents, target_is_directory=True)
        self.environment = mock.patch.dict(
            os.environ,
            {
                "HERMES_HOME": str(self.root),
                "HERMES_ROOT_PROFILE": str(self.root),
                "HERMES_PROFILES_ROOT": str(self.agents),
            },
        )
        self.environment.start()
        self.addCleanup(self.environment.stop)

    def test_linked_profile_pairing_dir_lock_is_under_profiles_root(self):
        directory = self.linked_profiles / "plugin-qa" / "platforms" / "pairing"
        lock = pairing_lock_path_for_directory(directory)
        self.assertEqual(
            lock,
            self.profile.resolve() / "platforms" / "pairing" / ".xnobrain-pairing.lock",
        )

    def test_directory_outside_both_bases_is_rejected(self):
        outside = Path(self.temporary.name) / "elsewhere" / "pairing"
        outside.mkdir(parents=True)
        with self.assertRaises(ValueError):
            pairing_lock_path_for_directory(outside)

    def test_linked_profile_store_constructs_under_profiles_root(self):
        install_pairing_constructor_locking()
        store = PairingStore(profile="plugin-qa")
        self.assertTrue(store._dir.resolve().is_relative_to(self.profile.resolve()))


class ChannelPairingModelTests(unittest.TestCase):
    def test_models_forbid_extra_fields(self):
        request = ChannelPairingRequest.model_validate({"request_id": "0123456789abcdef"})
        self.assertEqual(request.request_id, "0123456789abcdef")
        for payload in (
            {"request_id": "0123456789abcdef", "user_id": "789"},
            {"request_id": "not-a-request-id"},
        ):
            with self.subTest(payload=payload):
                with self.assertRaises(ValidationError):
                    ChannelPairingRequest.model_validate(payload)

        revoke = ChannelPairingRevokeRequest.model_validate({"user_id": "789"})
        self.assertEqual(revoke.user_id, "789")
        with self.assertRaises(ValidationError):
            ChannelPairingRevokeRequest.model_validate({"user_id": "789", "tenant_id": "attacker"})


if __name__ == "__main__":
    unittest.main()
