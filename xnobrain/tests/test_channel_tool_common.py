"""Shared channel-tool helpers: external message shaping and thread scope."""

import json
import unittest

from xnobrain.integrations import channel_tool_common


class _Repository:
    def __init__(self, threads):
        self._threads = threads

    def threads(self, profile):
        return self._threads


class ExternalMessagesTests(unittest.TestCase):
    def test_caps_the_count_and_keeps_newest_last(self):
        records = [
            {"message_id": str(index), "sender_id": "s", "text": f"m{index}"}
            for index in range(5)
        ]

        result = channel_tool_common.external_messages(records, limit=2)

        self.assertEqual([item["message_id"] for item in result], ["3", "4"])
        self.assertTrue(all(item["trust"] == "untrusted_external" for item in result))

    def test_truncates_text_and_drops_unknown_keys(self):
        records = [
            {
                "message_id": "m1",
                "sender_id": "s1",
                "sender_name": "Nam",
                "text": "x" * 50,
                "ts": 1_700_000_000,
                "type": "text",
                "direction": "in",
                "secret": "must not leak",
            }
        ]

        result = channel_tool_common.external_messages(records, text_limit=10)

        self.assertEqual(len(result), 1)
        self.assertEqual(result[0]["text"], "x" * 10)
        self.assertEqual(
            set(result[0]),
            {
                "trust",
                "message_id",
                "sender_id",
                "sender_name",
                "text",
                "ts",
                "type",
                "direction",
            },
        )
        self.assertNotIn("secret", result[0])

    def test_missing_fields_become_empty_strings_or_none(self):
        result = channel_tool_common.external_messages([{"message_id": "m1"}])

        self.assertEqual(result[0]["sender_id"], "")
        self.assertEqual(result[0]["sender_name"], "")
        self.assertEqual(result[0]["text"], "")
        self.assertEqual(result[0]["type"], "")
        self.assertEqual(result[0]["direction"], "")
        self.assertIsNone(result[0]["ts"])

    def test_byte_limit_drops_the_oldest_entries(self):
        records = [
            {
                "message_id": str(index),
                "sender_id": "s",
                "text": "x" * 1000,
            }
            for index in range(6)
        ]

        result = channel_tool_common.external_messages(
            records,
            limit=200,
            text_limit=2000,
            max_bytes=1500,
        )

        self.assertTrue(result)
        self.assertLess(len(result), len(records))
        self.assertEqual(result[-1]["message_id"], "5")
        encoded = json.dumps(result, ensure_ascii=False).encode("utf-8")
        self.assertLessEqual(len(encoded), 1500)

    def test_invalid_limit_returns_no_messages(self):
        self.assertEqual(
            channel_tool_common.external_messages([{"text": "x"}], limit=0),
            [],
        )


class ChannelOriginTests(unittest.TestCase):
    def test_returns_the_bound_platform_and_thread(self):
        repository = _Repository(
            {"zalo": {"111": {"conversation_id": "conv-1"}}}
        )

        origin = channel_tool_common.channel_origin(repository, "profile", "conv-1")

        self.assertEqual(origin, ("zalo", "111"))

    def test_returns_none_for_an_unbound_conversation(self):
        repository = _Repository(
            {"zalo_bot": {"chat_1": {"conversation_id": "conv-1"}}}
        )

        self.assertIsNone(channel_tool_common.channel_origin(repository, "profile", "conv-2"))
        self.assertIsNone(channel_tool_common.channel_origin(repository, "profile", ""))


class ThreadScopeTests(unittest.TestCase):
    def test_web_run_is_allowed(self):
        self.assertIsNone(
            channel_tool_common.ensure_thread_in_scope(None, "zalo", "111")
        )

    def test_channel_run_allows_only_its_own_thread(self):
        origin = ("zalo", "111")

        self.assertIsNone(
            channel_tool_common.ensure_thread_in_scope(origin, "zalo", "111")
        )
        self.assertEqual(
            channel_tool_common.ensure_thread_in_scope(origin, "zalo", "222"),
            "channel_thread_out_of_scope",
        )
        self.assertEqual(
            channel_tool_common.ensure_thread_in_scope(origin, "zalo_bot", "111"),
            "channel_thread_out_of_scope",
        )


class ErrorEnvelopeTests(unittest.TestCase):
    def test_error_envelope_is_json_and_content_free(self):
        payload = json.loads(channel_tool_common.error("channel_send_failed", field="x"))

        self.assertEqual(
            payload,
            {
                "success": False,
                "error": {"code": "channel_send_failed", "field": "x"},
            },
        )


if __name__ == "__main__":
    unittest.main()
