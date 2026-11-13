"""Regression checks for the independently authored integration catalog."""

from __future__ import annotations

import copy
import unittest

from xnobrain.repositories.plugin_catalog import load_plugin_catalog, validate_plugin_catalog

SUMMARY_LOCALES = {"en", "vi", "es", "fr", "de", "ja", "zh"}


class PluginCatalogTests(unittest.TestCase):
    def test_checked_in_catalog_has_full_coverage_and_safe_installable_subset(self) -> None:
        catalog = load_plugin_catalog()
        entries = catalog["entries"]
        self.assertEqual(len(entries), 31)
        by_id = {entry["id"]: entry for entry in entries}
        self.assertEqual(
            {
                entry_id
                for entry_id, entry in by_id.items()
                if entry["installability"] == "installable"
            },
            {
                "aave",
                "context7",
                "huggingface",
                "render",
                "posthog",
                "composio",
                "notion",
                "linear",
                "atlassian",
                "sentry",
                "canva",
                "vercel",
                "cloudflare",
                "supabase",
                "neon",
                "stripe",
                "todoist",
                "clickup",
                "netlify",
                "prisma",
                "railway",
                "trello",
                "airtable",
                "resend",
                "firecrawl",
                "cobrain",
            },
        )
        self.assertEqual(
            {
                entry_id
                for entry_id, entry in by_id.items()
                if entry["installability"] == "needs_review"
            },
            {"brave-search"},
        )
        for entry in entries:
            self.assertEqual(set(entry["summary"]), SUMMARY_LOCALES)
            self.assertTrue(all(value.strip() for value in entry["summary"].values()))
            self.assertEqual(
                entry["recipe_revision"],
                2
                if entry["id"]
                in {
                    "notion",
                    "linear",
                    "atlassian",
                    "sentry",
                    "canva",
                    "vercel",
                    "cloudflare",
                    "supabase",
                    "neon",
                    "stripe",
                    "todoist",
                    "clickup",
                    "netlify",
                    "prisma",
                    "railway",
                    "trello",
                    "airtable",
                    "resend",
                    "firecrawl",
                    "composio",
                    "cobrain",
                }
                else 1,
            )
            self.assertIn(entry["publisher_kind"], {"official", "community", "unverified"})
            self.assertEqual(set(entry["examples"]), {"en", "vi", "zh"})
            self.assertTrue(all(len(prompts) == 3 for prompts in entry["examples"].values()))
        self.assertNotEqual(by_id["brave-search"]["publisher_kind"], "official")
        validate_plugin_catalog(catalog)

    def test_rejects_invalid_example_prompts(self) -> None:
        for examples in (
            {"en": ["Ask"], "vi": ["Hỏi"]},
            {"en": [""], "vi": ["Hỏi"], "zh": ["问"]},
            {"en": ["Ask"] * 4, "vi": ["Hỏi"], "zh": ["问"]},
            {"en": ["x" * 161], "vi": ["Hỏi"], "zh": ["问"]},
        ):
            catalog = load_plugin_catalog()
            catalog["entries"][0]["examples"] = examples
            with self.assertRaisesRegex(ValueError, "example"):
                validate_plugin_catalog(catalog)

    def test_every_entry_has_a_public_https_icon(self) -> None:
        for entry in load_plugin_catalog()["entries"]:
            self.assertTrue(entry["iconUrl"].startswith("https://"), entry["id"])

        for icon in (
            None,
            "http://aave.com/icon.png",
            "https://localhost/icon.png",
            "https://10.0.0.1/icon.png",
            "https://aave.com/icon.png?v=1",
            "https://user:secret@aave.com/icon.png",
        ):
            catalog = load_plugin_catalog()
            catalog["entries"][0]["iconUrl"] = icon
            with self.assertRaisesRegex(ValueError, "icon"):
                validate_plugin_catalog(catalog)

    def test_load_returns_a_copy(self) -> None:
        first = load_plugin_catalog()
        first["entries"].clear()
        self.assertEqual(len(load_plugin_catalog()["entries"]), 31)

    def test_rejects_missing_coverage_and_unsupported_install_paths(self) -> None:
        catalog = load_plugin_catalog()
        cases = []

        missing = copy.deepcopy(catalog)
        missing["entries"].pop()
        cases.append(missing)

        oauth_install = copy.deepcopy(catalog)
        oauth_install["entries"][1]["installability"] = "installable"
        oauth_install["entries"][1]["mcp"].pop("url", None)
        cases.append(oauth_install)

        unpinned_stdio_install = copy.deepcopy(catalog)
        unpinned_stdio_install["entries"][27]["installability"] = "installable"
        cases.append(unpinned_stdio_install)

        missing_recipe_revision = copy.deepcopy(catalog)
        missing_recipe_revision["entries"][0].pop("recipe_revision")
        cases.append(missing_recipe_revision)

        missing_locale = copy.deepcopy(catalog)
        missing_locale["entries"][0]["summary"].pop("ja")
        cases.append(missing_locale)

        for invalid in cases:
            with self.subTest(catalog=invalid):
                with self.assertRaises(ValueError):
                    validate_plugin_catalog(invalid)

    def test_additional_entries_are_allowed_only_in_fail_closed_state(self) -> None:
        catalog = load_plugin_catalog()
        extra = copy.deepcopy(catalog["entries"][-1])
        extra.update(
            id="new-integration",
            publisher_kind="unverified",
            installability="unsupported",
            reason_code="not_reviewed",
        )
        catalog["entries"].append(extra)
        validate_plugin_catalog(catalog)

        catalog["entries"][-1]["installability"] = "installable"
        with self.assertRaises(ValueError):
            validate_plugin_catalog(catalog)

    def test_oauth_catalog_entries_have_public_static_endpoints(self) -> None:
        catalog = load_plugin_catalog()
        ids = {
            "notion",
            "linear",
            "atlassian",
            "sentry",
            "canva",
            "vercel",
            "cloudflare",
            "supabase",
            "neon",
            "stripe",
            "todoist",
            "clickup",
            "netlify",
            "prisma",
            "railway",
            "trello",
            "airtable",
            "resend",
            "cobrain",
        }
        by_id = {entry["id"]: entry for entry in catalog["entries"]}
        for entry_id in ids:
            entry = by_id[entry_id]
            self.assertEqual(entry["installability"], "installable")
            self.assertEqual(entry["recipe_revision"], 2)
            self.assertEqual(entry["mcp"]["auth"]["kind"], "oauth")
            self.assertTrue(entry["mcp"]["url"].startswith("https://"))
        self.assertEqual(by_id["cobrain"]["installability"], "installable")

    def test_composio_uses_the_static_connect_endpoint(self) -> None:
        catalog = load_plugin_catalog()
        by_id = {entry["id"]: entry for entry in catalog["entries"]}
        entry = by_id["composio"]
        self.assertEqual(entry["installability"], "installable")
        self.assertEqual(entry["reason_code"], "compatible")
        self.assertEqual(entry["recipe_revision"], 2)
        self.assertEqual(entry["mcp"]["transport"], "http")
        self.assertEqual(entry["mcp"]["endpoint_mode"], "static")
        self.assertEqual(entry["mcp"]["url"], "https://connect.composio.dev/mcp")
        self.assertEqual(
            entry["mcp"]["auth"],
            {
                "kind": "api_key",
                "field": "api_key",
                "delivery": "header",
                "header": "x-consumer-api-key",
                "prefix": "",
            },
        )
        self.assertNotIn("url_policy", entry["mcp"])

    def test_unreviewed_stdio_package_publishers_cannot_be_official(self) -> None:
        catalog = load_plugin_catalog()
        by_id = {entry["id"]: entry for entry in catalog["entries"]}
        for entry_id in {"brave-search"}:
            with self.subTest(entry=entry_id):
                by_id[entry_id]["publisher_kind"] = "official"
                with self.assertRaises(ValueError):
                    validate_plugin_catalog(catalog)
                by_id[entry_id]["publisher_kind"] = "unverified"


if __name__ == "__main__":
    unittest.main()
