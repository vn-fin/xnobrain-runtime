"""Personal/org exposure follows the coordinated stable feature convention."""

import os
import unittest
from unittest.mock import patch

from xnobrain.feature_flags import FEATURE_PERSONAL_ORG_WORKSPACE_EXPERIENCE, enabled


class PersonalOrgFeatureTests(unittest.TestCase):
    def test_default_and_invalid_values(self):
        for value, expected in ((None, True), ("true", True), ("false", False), ("bad", False)):
            environment = (
                {} if value is None else {"FT_ENABLE_PERSONAL_ORG_WORKSPACE_EXPERIENCE": value}
            )
            with self.subTest(value=value), patch.dict(os.environ, environment, clear=True):
                self.assertEqual(enabled(FEATURE_PERSONAL_ORG_WORKSPACE_EXPERIENCE), expected)
