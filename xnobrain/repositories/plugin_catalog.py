"""Load and validate the independently authored public plugin catalog."""

from __future__ import annotations

import copy
import ipaddress
import json
import re
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit

CATALOG_PATH = Path(__file__).resolve().parents[1] / "assets" / "plugin_catalog.json"
CATALOG_IDS = (
    "aave",
    "canva",
    "linear",
    "notion",
    "obsidian",
    "atlassian",
    "todoist",
    "trello",
    "cobrain",
    "figma",
    "paper",
    "apple-apps",
    "sentry",
    "vercel",
    "cloudflare",
    "supabase",
    "neon",
    "prisma",
    "netlify",
    "railway",
    "render",
    "context7",
    "huggingface",
    "stripe",
    "posthog",
    "airtable",
    "firecrawl",
    "brave-search",
    "resend",
    "composio",
    "clickup",
)
OAUTH_INSTALLABLE_IDS = {
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
}
INSTALLABLE_IDS = {
    "aave",
    "context7",
    "huggingface",
    "render",
    "posthog",
    "composio",
    "firecrawl",
    *OAUTH_INSTALLABLE_IDS,
}
NEEDS_REVIEW_IDS = {"brave-search"}
SUMMARY_LOCALES = {"en", "vi", "es", "fr", "de", "ja", "zh"}
# Example prompts follow the UI languages.
EXAMPLE_LOCALES = {"en", "vi", "zh"}
DESKTOP_IDS = {"obsidian", "figma", "paper", "apple-apps"}
OAUTH_IDS = {
    "canva",
    "linear",
    "notion",
    "atlassian",
    "todoist",
    "trello",
    "airtable",
    "resend",
    "cobrain",
    "sentry",
    "vercel",
    "cloudflare",
    "supabase",
    "neon",
    "prisma",
    "netlify",
    "railway",
    "stripe",
    "clickup",
}
_ENVIRONMENT_NAME = re.compile(r"^[A-Z][A-Z0-9_]{1,63}$")
_SLUG = re.compile(r"^[a-z0-9]+(?:-[a-z0-9]+)*$")


def validate_plugin_catalog(payload: Any) -> None:
    """Raise ``ValueError`` when the catalog violates its published contract."""
    if not isinstance(payload, dict) or payload.get("schema_version") != 1:
        raise ValueError("plugin catalog schema version is invalid")
    if not isinstance(payload.get("catalog_version"), str) or not payload["catalog_version"]:
        raise ValueError("plugin catalog version is missing")

    reference = payload.get("coverage_reference")
    if not isinstance(reference, dict) or reference != {
        "repository": "nightly-labs/openbot",
        "commit": "21369e1eec420772a7db9f6bbfeae2ef9b3ab83a",
        "path": "marketplace/plugin-catalog/catalog.json",
        "use": "coverage-checklist-only",
    }:
        raise ValueError("plugin catalog coverage reference is invalid")

    entries = payload.get("entries")
    if not isinstance(entries, list) or len(entries) < len(CATALOG_IDS):
        raise ValueError("plugin catalog must contain at least the complete 31-entry coverage set")
    ids = [entry.get("id") if isinstance(entry, dict) else None for entry in entries]
    if any(not isinstance(entry_id, str) for entry_id in ids):
        raise ValueError("plugin catalog entry ids are invalid")
    if len(ids) != len(set(ids)) or not set(CATALOG_IDS).issubset(ids):
        raise ValueError("plugin catalog entries are missing or duplicated")

    for entry in entries:
        _validate_entry(entry)


def _validate_examples(entry_id: str, examples: Any) -> None:
    """Optional example prompts: up to three short strings per UI language."""
    if not isinstance(examples, dict) or set(examples) != EXAMPLE_LOCALES:
        raise ValueError(f"{entry_id}: examples must provide en, vi and zh prompts")
    for prompts in examples.values():
        if (
            not isinstance(prompts, list)
            or not 1 <= len(prompts) <= 3
            or any(
                not isinstance(text, str) or not text.strip() or len(text) > 160 for text in prompts
            )
        ):
            raise ValueError(f"{entry_id}: example prompts are invalid")


def _validate_entry(entry: dict[str, Any]) -> None:
    entry_id = entry.get("id")
    if not isinstance(entry_id, str) or not _SLUG.fullmatch(entry_id):
        raise ValueError("plugin catalog entry id is invalid")
    if not isinstance(entry.get("name"), str) or not entry["name"].strip():
        raise ValueError(f"{entry_id}: name is missing")
    if entry.get("publisher_kind") not in {"official", "community", "unverified"}:
        raise ValueError(f"{entry_id}: publisher kind is invalid")
    if not isinstance(entry.get("publisher"), str) or not entry["publisher"].strip():
        raise ValueError(f"{entry_id}: publisher is missing")
    recipe_revision = entry.get("recipe_revision")
    if (
        isinstance(recipe_revision, bool)
        or not isinstance(recipe_revision, int)
        or recipe_revision < 1
    ):
        raise ValueError(f"{entry_id}: recipe revision must be a positive integer")

    summary = entry.get("summary")
    if not isinstance(summary, dict) or set(summary) != SUMMARY_LOCALES:
        raise ValueError(f"{entry_id}: summaries must provide all seven supported locales")
    if any(
        not isinstance(text, str) or not text.strip() or len(text) > 240
        for text in summary.values()
    ):
        raise ValueError(f"{entry_id}: summary text is invalid")

    examples = entry.get("examples")
    if examples is not None:
        _validate_examples(entry_id, examples)

    for link_name in ("homepage", "documentation"):
        _validate_public_https_url(entry.get(link_name), entry_id, link_name)
    # The UI loads this image directly from the provider, so it gets the same
    # public-HTTPS rule as the other provider links.
    _validate_public_https_url(entry.get("iconUrl"), entry_id, "icon")

    mcp = entry.get("mcp")
    if not isinstance(mcp, dict) or mcp.get("transport") not in {"http", "stdio"}:
        raise ValueError(f"{entry_id}: MCP transport is invalid")
    auth = mcp.get("auth")
    if not isinstance(auth, dict) or auth.get("kind") not in {"none", "api_key", "oauth", "local"}:
        raise ValueError(f"{entry_id}: authentication declaration is invalid")
    if auth["kind"] == "api_key":
        _validate_api_key_auth(entry_id, auth)

    if entry_id in NEEDS_REVIEW_IDS and entry["publisher_kind"] == "official":
        raise ValueError(f"{entry_id}: unreviewed package publisher cannot be marked official")

    status = entry.get("installability")
    expected_status = (
        "installable"
        if entry_id in INSTALLABLE_IDS
        else "needs_review"
        if entry_id in NEEDS_REVIEW_IDS
        else "unsupported"
    )
    if status != expected_status:
        raise ValueError(f"{entry_id}: installability must be {expected_status}")

    reason = entry.get("reason_code")
    if status == "installable":
        _validate_installable_entry(entry_id, mcp, auth, reason)
    elif entry_id in NEEDS_REVIEW_IDS:
        package = mcp.get("package")
        if (
            reason != "package_version_unpinned"
            or mcp["transport"] != "stdio"
            or auth["kind"] != "api_key"
            or not isinstance(package, dict)
            or not isinstance(package.get("name"), str)
            or package.get("version") is not None
        ):
            raise ValueError(f"{entry_id}: unpinned stdio packages must remain needs_review")
    elif entry_id in DESKTOP_IDS:
        if status != "unsupported" or reason != "desktop_application_required":
            raise ValueError(f"{entry_id}: desktop integrations must remain unsupported")
    elif entry_id in OAUTH_IDS - OAUTH_INSTALLABLE_IDS:
        if status != "unsupported" or reason != "oauth_not_supported" or auth["kind"] != "oauth":
            raise ValueError(f"{entry_id}: OAuth integrations must remain unsupported")
    else:
        if (
            status != "unsupported"
            or reason != "not_reviewed"
            or entry["publisher_kind"] == "official"
        ):
            raise ValueError(
                f"{entry_id}: additional entries must remain unreviewed and unsupported"
            )


def _validate_installable_entry(
    entry_id: str,
    mcp: dict[str, Any],
    auth: dict[str, Any],
    reason: Any,
) -> None:
    if mcp["transport"] != "http" or auth["kind"] not in {"none", "api_key", "oauth"}:
        raise ValueError(
            f"{entry_id}: only remote HTTP without auth or with an API key is installable"
        )

    endpoint_mode = mcp.get("endpoint_mode")
    if entry_id in OAUTH_INSTALLABLE_IDS:
        url = mcp.get("url")
        if endpoint_mode != "static" or auth["kind"] != "oauth" or reason != "compatible":
            raise ValueError(f"{entry_id}: OAuth endpoints must be static and compatible")
        _validate_public_https_url(url, entry_id, "mcp.url")
    else:
        if endpoint_mode != "static" or reason != "compatible":
            raise ValueError(
                f"{entry_id}: compatible endpoints must be static and marked compatible"
            )
        _validate_public_https_url(mcp.get("url"), entry_id, "MCP endpoint")


def _validate_api_key_auth(entry_id: str, auth: dict[str, Any]) -> None:
    if auth.get("field") not in {"token", "api_key"}:
        raise ValueError(f"{entry_id}: API-key field identifier is invalid")
    delivery = auth.get("delivery")
    if delivery == "header":
        if not isinstance(auth.get("header"), str) or not auth["header"].strip():
            raise ValueError(f"{entry_id}: API-key header is missing")
        if not isinstance(auth.get("prefix"), str):
            raise ValueError(f"{entry_id}: API-key header prefix is invalid")
    elif delivery == "env":
        if not isinstance(auth.get("environment_name"), str) or not _ENVIRONMENT_NAME.fullmatch(
            auth["environment_name"]
        ):
            raise ValueError(f"{entry_id}: API-key environment variable is invalid")
    else:
        raise ValueError(f"{entry_id}: API-key delivery must be header or environment")


def _validate_public_https_url(value: Any, entry_id: str, field: str) -> None:
    if not isinstance(value, str):
        raise ValueError(f"{entry_id}: {field} URL is missing")
    parsed = urlsplit(value)
    if (
        parsed.scheme != "https"
        or not parsed.hostname
        or parsed.username is not None
        or parsed.password is not None
        or parsed.query
        or parsed.fragment
    ):
        raise ValueError(
            f"{entry_id}: {field} must be a public HTTPS URL without credentials or query"
        )
    try:
        address = ipaddress.ip_address(parsed.hostname)
    except ValueError:
        if (
            parsed.hostname.lower() in {"localhost", "localhost.localdomain"}
            or "." not in parsed.hostname
        ):
            raise ValueError(f"{entry_id}: {field} host is not public") from None
    else:
        if not address.is_global:
            raise ValueError(f"{entry_id}: {field} host is not public")


def load_plugin_catalog() -> dict[str, object]:
    """Load the checked-in catalog, refusing invalid data at startup/use time."""
    try:
        payload = json.loads(CATALOG_PATH.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise ValueError("plugin catalog could not be loaded") from exc
    validate_plugin_catalog(payload)
    return copy.deepcopy(payload)
