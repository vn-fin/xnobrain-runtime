"""Channel repository tests for the stored Zalo Bot conversation files."""

from __future__ import annotations

import json
import os
import tempfile
import unittest
from pathlib import Path

from xnobrain.repositories.base import StoreError
from xnobrain.repositories.channels import ChannelsRepository
from xnobrain.repositories.files import FileRepository


class ZaloBotHistoryRepositoryTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        self.profiles = self.root / "profiles"
        self.profile = self.profiles / "agent-1"
        self.profile.mkdir(parents=True)
        self.repository = FileRepository(
            self.root / "data", self.profiles, root_profile=self.profile
        )
        self.channels = ChannelsRepository(self.repository)

    # ── Fixtures ──────────────────────────────────────────────────────

    def _history_dir(self) -> Path:
        directory = self.profile / "zalo_bot" / "history"
        directory.mkdir(parents=True, exist_ok=True)
        return directory

    @staticmethod
    def _write(path: Path, records) -> None:
        with path.open("w", encoding="utf-8") as handle:
            for record in records:
                handle.write(json.dumps(record, ensure_ascii=False) + "\n")

    def _record(self, ts: float, text: str = "", chat_type: str = "group") -> dict:
        return {
            "message_id": f"m-{ts}",
            "chat_id": "chat1",
            "chat_type": chat_type,
            "direction": "in",
            "sender_id": "sender",
            "sender_name": "Sender",
            "type": "text",
            "text": text or f"message-{ts}",
            "ts": ts,
            "trust": "untrusted_external",
        }

    # ── Reads ─────────────────────────────────────────────────────────

    def test_bot_history_is_oldest_first_with_since_and_limit(self):
        self._write(
            self._history_dir() / "chat1.jsonl",
            [self._record(1), self._record(2), self._record(3)],
        )

        records = self.channels.bot_history(self.profile, "chat1")

        self.assertEqual([record["ts"] for record in records], [1.0, 2.0, 3.0])
        self.assertEqual(
            [record["ts"] for record in self.channels.bot_history(self.profile, "chat1", limit=2)],
            [2.0, 3.0],
        )
        self.assertEqual(
            [record["ts"] for record in self.channels.bot_history(self.profile, "chat1", since=2)],
            [2.0, 3.0],
        )

    def test_bot_history_rejects_invalid_chat_ids(self):
        self._write(self._history_dir() / "chat1.jsonl", [self._record(1)])

        for chat_id in ("", "../chat1", "chat/1", "a" * 129, None):
            with self.subTest(chat_id=chat_id):
                self.assertEqual(self.channels.bot_history(self.profile, chat_id), [])

    def test_bot_history_ignores_malformed_lines(self):
        path = self._history_dir() / "chat1.jsonl"
        path.write_text("not json\n\n" + json.dumps(self._record(1)) + "\n", encoding="utf-8")

        records = self.channels.bot_history(self.profile, "chat1")

        self.assertEqual(len(records), 1)

    def test_bot_history_chats_summarizes_and_sorts(self):
        directory = self._history_dir()
        self._write(
            directory / "chat1.jsonl",
            [self._record(1), self._record(3)],
        )
        self._write(
            directory / "chat2.jsonl",
            [self._record(5, chat_type="dm")],
        )
        self._write(directory / "notes.txt", [])

        chats = self.channels.bot_history_chats(self.profile)

        self.assertEqual([chat["chat_id"] for chat in chats], ["chat2", "chat1"])
        self.assertEqual(chats[0]["chat_type"], "dm")
        self.assertEqual(chats[0]["last_ts"], 5.0)
        self.assertEqual(chats[0]["message_count"], 1)
        self.assertEqual(chats[1]["chat_type"], "group")
        self.assertEqual(chats[1]["last_ts"], 3.0)
        self.assertEqual(chats[1]["message_count"], 2)

    def test_missing_history_directory_is_empty(self):
        self.assertEqual(self.channels.bot_history(self.profile, "chat1"), [])
        self.assertEqual(self.channels.bot_history_chats(self.profile), [])
        self.assertEqual(self.channels.delete_bot_history(self.profile), 0)

    # ── Deletes ───────────────────────────────────────────────────────

    def test_delete_bot_history_one_or_all(self):
        directory = self._history_dir()
        self._write(directory / "chat1.jsonl", [self._record(1)])
        self._write(directory / "chat2.jsonl", [self._record(2)])

        self.assertEqual(self.channels.delete_bot_history(self.profile, "chat1"), 1)
        self.assertFalse((directory / "chat1.jsonl").exists())
        self.assertTrue((directory / "chat2.jsonl").exists())

        self.assertEqual(self.channels.delete_bot_history(self.profile, "../escape"), 0)
        self.assertEqual(self.channels.delete_bot_history(self.profile), 1)
        self.assertFalse((directory / "chat2.jsonl").exists())

    # ── Path safety ───────────────────────────────────────────────────

    def test_symlinked_history_directory_is_refused(self):
        bot_dir = self.profile / "zalo_bot"
        bot_dir.mkdir()
        outside = self.root / "outside"
        outside.mkdir()
        (outside / "chat1.jsonl").write_text(
            json.dumps(self._record(1)) + "\n", encoding="utf-8"
        )
        os.symlink(outside, bot_dir / "history")

        with self.assertRaises(StoreError) as raised:
            self.channels.bot_history(self.profile, "chat1")
        self.assertEqual(raised.exception.code, "channel_path_unsafe")

        with self.assertRaises(StoreError) as raised:
            self.channels.delete_bot_history(self.profile)
        self.assertEqual(raised.exception.code, "channel_path_unsafe")
        self.assertTrue((outside / "chat1.jsonl").exists())

    def test_symlinked_chat_file_is_not_read_or_deleted(self):
        directory = self._history_dir()
        outside = self.root / "outside.jsonl"
        outside.write_text(json.dumps(self._record(1)) + "\n", encoding="utf-8")
        os.symlink(outside, directory / "chat1.jsonl")

        self.assertEqual(self.channels.bot_history(self.profile, "chat1"), [])
        self.assertEqual(self.channels.delete_bot_history(self.profile, "chat1"), 0)
        self.assertTrue(outside.exists())


if __name__ == "__main__":
    unittest.main()
