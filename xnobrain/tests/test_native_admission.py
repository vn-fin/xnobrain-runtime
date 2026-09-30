"""Native dispatch waits before a claim; a started worker is never replayed."""

import os
import tempfile
import unittest
from contextlib import nullcontext
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, patch

from xnobrain.integrations.kanban_admission import dispatch_scope, take
from xnobrain.integrations.native_admission import NativeAdmission, cancel_waiting, reconcile_native
from xnobrain.repositories.custom_page_locks import acquire, release
from xnobrain.services.base import ServiceError


class NativeAdmissionTests(unittest.TestCase):
    pressure_reason = "pool_memory_pressure"

    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.root = Path(self.directory.name)
        self.response = {"id": "grant", "state": "waiting", "reason_code": self.pressure_reason}
        self.call = self.enterContext(
            patch("xnobrain.integrations.native_admission.call", side_effect=self.request)
        )

    def request(self, method, *args, **kwargs):
        if method == "claim":
            return {**self.response, "state": "starting", "nonce": "nonce", "runtime_boot": "boot"}
        return self.response

    def test_native_wait_leaves_dispatch_lock_free_and_reuses_identity(self):
        gate = NativeAdmission(self.root, "cron:occurrence", "cron")
        self.assertFalse(gate.poll())
        descriptor = acquire(self.root, ".capacity-" + gate.id + ".lock", shared=False)
        release(descriptor)
        self.response["state"] = "offered"
        self.assertTrue(gate.poll())
        gate.started()
        gate.finish()
        self.assertFalse(NativeAdmission(self.root, "cron:occurrence", "cron").poll())
        self.assertEqual([call.args[0] for call in self.call.call_args_list].count("claim"), 1)

    def test_lost_registration_response_is_durable_and_cancelled_by_run_identity(self):
        gate = NativeAdmission(self.root, "cron:lost-response", "cron")
        self.call.side_effect = ServiceError("unavailable", status=503)
        self.assertFalse(gate.poll())
        self.assertEqual(gate.store.native_get(gate.id)["state"], "waiting")
        self.call.side_effect = self.request
        self.assertTrue(cancel_waiting(self.root, gate.key))
        self.assertEqual(gate.store.native_get(gate.id)["state"], "settled")
        self.assertTrue(any(call.args == ("observe", gate.id) for call in self.call.call_args_list))

    def test_dead_started_worker_reconciles_without_second_claim(self):
        self.response["state"] = "offered"
        gate = NativeAdmission(self.root, "kanban:task:run", "kanban")
        self.assertTrue(gate.poll())
        gate.started()
        gate.close()  # Simulated process exit: no running receipt owner remains.
        reconcile_native(self.root)
        self.assertEqual(gate.store.native_get(gate.id)["state"], "settled")
        self.assertFalse(gate.poll())

    def test_cancel_waiting_and_deleted_native_sources_release_queue(self):
        gate = NativeAdmission(self.root, "kanban:board:task:None:ready", "kanban")
        self.assertFalse(gate.poll())
        self.assertTrue(cancel_waiting(self.root, gate.key))
        self.assertFalse(gate.poll())
        deleted = NativeAdmission(self.root, "cron:agent:removed:occurrence", "cron")
        self.assertFalse(deleted.poll())
        reconcile_native(self.root, lambda kind, key: key != deleted.key)
        self.assertEqual(deleted.store.native_get(deleted.id)["state"], "settled")

    def test_dead_waiting_cli_is_cancelled_but_live_owner_is_retained(self):
        gate = NativeAdmission(self.root, "cli-turn", "cli")
        owner = acquire(self.root, ".capacity-owner-" + gate.id + ".lock", shared=False)
        self.assertFalse(gate.poll())
        try:
            reconcile_native(self.root)
            self.assertEqual(gate.store.native_get(gate.id)["state"], "waiting")
        finally:
            release(owner)
        reconcile_native(self.root)
        self.assertEqual(gate.store.native_get(gate.id)["state"], "settled")

    def test_inherited_worker_descriptor_keeps_run_alive(self):
        self.response["state"] = "offered"
        gate = NativeAdmission(self.root, "kanban:live-worker", "kanban")
        self.assertTrue(gate.poll())
        gate.started()
        child = os.dup(gate.descriptor)
        gate.close()
        try:
            reconcile_native(self.root)
            self.assertEqual(gate.store.native_get(gate.id)["state"], "running")
        finally:
            os.close(child)
        reconcile_native(self.root)
        self.assertEqual(gate.store.native_get(gate.id)["state"], "settled")

    def test_cli_gates_each_root_turn_and_inherits_attached_work(self):
        from xnobrain.integrations.capacity_cli import install_agent_admission
        from xnobrain.integrations.run_admission import current_admission

        calls = []

        class Agent:
            def run_conversation(self, child=False):
                calls.append(current_admission())
                if not child:
                    self.run_conversation(child=True)

        with (
            patch("xnobrain.integrations.capacity_cli.managed", return_value=True),
            patch("xnobrain.integrations.capacity_cli.NativeAdmission") as gate,
            patch("xnobrain.integrations.capacity_cli.cli_activity", return_value=nullcontext()),
            patch("xnobrain.integrations.capacity_cli.time.sleep"),
            patch("xnobrain.integrations.capacity_cli.acquire", return_value=99),
            patch("xnobrain.integrations.capacity_cli.release"),
        ):
            gate.return_value.poll.side_effect = [False, True, True]
            gate.return_value.admission = {"id": "test"}
            install_agent_admission(Agent)
            Agent().run_conversation()
            Agent().run_conversation()
            self.assertEqual(gate.call_count, 2)
            self.assertEqual(len(calls), 4)
            self.assertTrue(all(calls))

    @patch("xnobrain.integrations.kanban_admission.managed", return_value=True)
    def test_kanban_pressure_does_not_claim_or_count_failure(self, _managed):
        task = SimpleNamespace(current_run_id=None, status="ready")
        original = Mock(return_value=task)
        kb = SimpleNamespace(
            get_task=Mock(return_value=task), claim_task=original, claim_review_task=Mock()
        )
        with dispatch_scope(kb, self.root, "board"):
            self.assertIsNone(kb.claim_task(None, "task"))
            original.assert_not_called()
        self.response["state"] = "offered"
        with dispatch_scope(kb, self.root, "board"):
            self.assertIs(kb.claim_task(None, "task"), task)
            gate = take("task")
            self.assertIsNotNone(gate)
            gate.abort()
        original.assert_called_once()


class CPUNativeAdmissionTests(NativeAdmissionTests):
    pressure_reason = "pool_cpu_pressure"
