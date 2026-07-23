"""Tests for stable JSON runtime logging metadata."""

from __future__ import annotations

import json
import logging
import os
import unittest
from unittest.mock import patch

from brain4all.json_logging import JsonFormatter


class JsonLoggingTests(unittest.TestCase):
    def test_formatter_adds_required_defaults_to_structured_events(self):
        record = logging.makeLogRecord({"name": "brain4all.http", "levelno": logging.INFO, "msg": '{"event":"http.request"}'})
        with patch.dict(os.environ, {}, clear=True):
            payload = json.loads(JsonFormatter().format(record))

        self.assertEqual(payload["event"], "http.request")
        self.assertEqual(payload["development_environment"], "dev")
        self.assertEqual(payload["service_name"], "runtime")
        self.assertTrue(payload["time"].endswith("Z"))

    def test_formatter_uses_configured_runtime_metadata(self):
        record = logging.makeLogRecord({"name": "brain4all.cron", "levelno": logging.WARNING, "msg": "scheduler warning"})
        with patch.dict(os.environ, {"DEVELOPMENT_ENVIRONMENT": "staging", "SERVICE_NAME": "brain4all"}, clear=True):
            payload = json.loads(JsonFormatter().format(record))

        self.assertEqual(payload["message"], "scheduler warning")
        self.assertEqual(payload["development_environment"], "staging")
        self.assertEqual(payload["service_name"], "brain4all")
