"""Organization-owned devices use direct models, without member grants."""

import unittest

from xnobrain.integrations import organization_funding


class OrganizationWorkspaceFundingTests(unittest.TestCase):
    def test_direct_model_requires_control_workspace_funding(self):
        with self.assertRaises(ValueError):
            organization_funding.register("borrowed", "organization", "cx/gpt-5")
        organization_funding.register("owned", "organization", "cx/gpt-5", workspace=True)
        self.assertEqual(organization_funding.funding_for("owned")["model"], "cx/gpt-5")
        organization_funding.release("owned")

    def test_workspace_funding_keeps_the_agents_own_model(self):
        # The organization workspace's own Router key bills the organization
        # for any model, so Control sends no model and the agent keeps its own.
        organization_funding.register("owned-default", "organization", "", workspace=True)
        self.assertEqual(organization_funding.funding_for("owned-default")["model"], "")
        organization_funding.release("owned-default")
        with self.assertRaises(ValueError):
            organization_funding.register("borrowed-default", "organization", "")
