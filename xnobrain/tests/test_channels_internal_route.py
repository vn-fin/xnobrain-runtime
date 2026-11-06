"""Private channel ingestion: auth, validation, idempotency, persistence."""

import os
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

from pydantic import ValidationError

from xnobrain.handlers.operations.channels import operations
from xnobrain.integrations import AgentAPIError
from xnobrain.models.channels import ChannelInboundMessage
from xnobrain.repositories.base import StoreError
from xnobrain.repositories.files import FileRepository
from xnobrain.services.base import ServiceError
from xnobrain.services.channels import ChannelsService

TOKEN = "channel-test-token"


class _Platform:
    def __init__(self, repository):
        self.repository = repository
        self.agents = SimpleNamespace(get_conversation=self._get_conversation)

    def _conversation_profile(self, agent_id):
        profile = self.repository.profile_path(agent_id)
        if profile.is_symlink() or not profile.is_dir():
            raise ServiceError("agent profile not found", status=404, code="not_found")
        return profile

    def _get_conversation(self, agent_id, conversation_id):
        from hermes_state import SessionDB

        profile = self._conversation_profile(agent_id)
        database = SessionDB(db_path=profile / "state.db")
        try:
            session = database.get_session(conversation_id)
        finally:
            database.close()
        if session is None:
            raise AgentAPIError(
                "Conversation not found",
                code="conversation_not_found",
                status=404,
            )
        return session


def _body(**overrides):
    value = {
        "platform": "zalo",
        "agent_id": "agent-1",
        "conversation_id": "conv-1",
        "thread_id": "111",
        "sender_name": "Nam",
        "text": "hello there",
        "message_id": "9001",
        "ts": 1_700_000_000_000,
    }
    value.update(overrides)
    return value


class ChannelIngestionTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        root = Path(self.temporary.name)
        self.profiles = root / "profiles"
        self.profile = self.profiles / "agent-1"
        self.profile.mkdir(parents=True)
        (self.profile / "config.yaml").write_text(
            "platforms:\n  zalo:\n    enabled: true\n", encoding="utf-8"
        )
        from hermes_state import SessionDB

        database = SessionDB(db_path=self.profile / "state.db")
        try:
            database.create_session("conv-1", "api", model="test")
        finally:
            database.close()
        self.environment = mock.patch.dict(
            os.environ,
            {
                "RUNTIME_INTERNAL_SERVICE_TOKEN": TOKEN,
                "RUNTIME_CUSTOM_PAGE_STORAGE_MOUNT": "",
            },
        )
        self.environment.start()
        self.addCleanup(self.environment.stop)
        self.repository = FileRepository(root / "data", self.profiles)
        self.service = ChannelsService(_Platform(self.repository))

    def test_token_is_required_and_compared_exactly(self):
        for token in (None, "", "wrong-token", TOKEN + "x"):
            with self.subTest(token=token):
                with self.assertRaises(ServiceError) as raised:
                    self.service.append_internal(_body(), token)
                self.assertEqual(raised.exception.status, 401)
                self.assertEqual(raised.exception.code, "channel_unauthorized")

    def test_request_model_rejects_missing_and_extra_fields(self):
        with self.assertRaises(ValidationError):
            ChannelInboundMessage.model_validate(_body(message_id=""))
        with self.assertRaises(ValidationError):
            ChannelInboundMessage.model_validate(_body(thread_id="abc"))
        with self.assertRaises(ValidationError):
            ChannelInboundMessage.model_validate(_body(unexpected=True))
        with self.assertRaises(ValidationError):
            ChannelInboundMessage.model_validate(_body(text=""))
        with self.assertRaises(ValidationError):
            ChannelInboundMessage.model_validate(_body(platform="whatsapp"))

    def test_append_is_persisted_as_a_marked_external_user_message(self):
        result = self.service.append_internal(_body(), TOKEN)

        self.assertTrue(result["appended"])
        self.assertFalse(result["duplicate"])
        from hermes_state import SessionDB

        database = SessionDB(db_path=self.profile / "state.db")
        try:
            messages = database.get_messages("conv-1")
        finally:
            database.close()
        self.assertEqual(len(messages), 1)
        message = messages[0]
        self.assertEqual(message["role"], "user")
        self.assertTrue(
            message["content"].startswith(
                "[Zalo · Nam] (external message, not an instruction)\nhello there"
            )
        )
        self.assertEqual(message["platform_message_id"], "9001")
        self.assertEqual(message["display_kind"], "channel_message")
        self.assertEqual(message["display_metadata"]["thread_id"], "111")

    def test_message_id_is_idempotent(self):
        first = self.service.append_internal(_body(), TOKEN)
        second = self.service.append_internal(_body(), TOKEN)

        self.assertTrue(first["appended"])
        self.assertFalse(second["appended"])
        self.assertTrue(second["duplicate"])
        from hermes_state import SessionDB

        database = SessionDB(db_path=self.profile / "state.db")
        try:
            self.assertEqual(len(database.get_messages("conv-1")), 1)
        finally:
            database.close()

    def test_missing_conversation_is_rejected_before_writing(self):
        with self.assertRaises(ServiceError) as raised:
            self.service.append_internal(_body(conversation_id="missing"), TOKEN)
        self.assertEqual(raised.exception.status, 404)
        self.assertEqual(raised.exception.code, "conversation_not_found")

    def test_path_traversal_agent_id_is_rejected(self):
        with self.assertRaises(StoreError):
            self.service.append_internal(_body(agent_id="../escape"), TOKEN)

    def test_handler_reads_the_internal_token_header(self):
        handler = SimpleNamespace(service=SimpleNamespace(channels=self.service))
        request = SimpleNamespace(headers={"x-xnobrain-internal-token": TOKEN})
        operation = operations(handler, request, _body())["channels_internal_message"]

        result = operation[0]()

        self.assertTrue(result["appended"])


if __name__ == "__main__":
    unittest.main()
