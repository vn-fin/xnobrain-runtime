"""Tests for human-readable runtime logging."""

from __future__ import annotations

import logging
import unittest
from unittest.mock import patch

from brain4all.logging_config import LOG_FORMAT, configure_logging


class LoggingConfigTests(unittest.TestCase):
    def test_configures_standard_human_readable_formatter(self):
        with patch("brain4all.logging_config.logging.basicConfig") as basic_config:
            configure_logging()

        handler = basic_config.call_args.kwargs["handlers"][0]
        record = logging.makeLogRecord({
            "name": "brain4all.http",
            "levelno": logging.INFO,
            "levelname": "INFO",
            "msg": "HTTP GET /api/v1/health -> 200",
        })
        rendered = handler.format(record)

        self.assertEqual(handler.formatter._fmt, LOG_FORMAT)
        self.assertIn("INFO brain4all.http: HTTP GET /api/v1/health -> 200", rendered)
        self.assertFalse(rendered.startswith("{"))
