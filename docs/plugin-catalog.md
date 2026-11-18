# Agent plugin catalog

## Control-owned native provider packages

Discover > Plugins includes Sapo, SePay and KiotViet as `kind: native` catalog
entries. Control owns their definitions, tool names, input schemas, resources,
operation enums, connection-field metadata, skills and reference Markdown. Runtime
ships no provider package, provider identifier list, resource list or operation
catalog, and `runtime/optional-plugins/` and `xnobrain/assets/kiotviet_operations.json`
are removed. Installing a native provider is therefore possible only through the
Control-owned catalog and package endpoint.

Runtime reports `GET /xnobrain/api/runtime/v1/plugins/capabilities` with
`engine_capabilities` and `managed_provider_packages: {"schema_versions": [1]}`.
There is no `native_packages` fallback. The UI must require this capability for
all three native providers.

Every catalog `kind: native` entry exposes `native_package` with
`schema_version`, `definition_revision`, `package_version`, `package_sha256`
and declarative `connection_fields`. The catalog never exposes package bytes,
tool implementation details or credentials. A native install or update uses the
existing per-agent extension operations route with an explicit
`source: bundled:<provider>` and an idempotency key. Control attaches the signed
package intent described in the
[native provider package contract](contracts/native-provider-packages-v1.md);
Runtime rejects a bundled install/update without it.

Runtime downloads the exact package bytes from Control with the request's scoped
MCP grant before durably enqueueing the operation, verifies the SHA-256, schema,
provider version and revision, and caches the verified bytes until the worker
consumes them. The worker never resolves another revision. It builds
`plugin.yaml`, one Runtime-owned `__init__.py` bootstrap constant, `provider.json`,
and only the allowlisted skill/reference Markdown; Control packages contain no
Python, shell, scripts, credentials or arbitrary paths. Installation starts
disabled and unapproved. The existing native scan, approval, enable/disable,
snapshot, rollback, digest and cancellation lifecycle is unchanged.

Installed inventory adds
`native_package: {schema_version, definition_revision, package_sha256}` so the UI
can compare an installed package with the catalog. Updates revoke approval and
leave the new package disabled; a package that adds a write tool requires a fresh
approval. Old dev installs from the removed Runtime packages must be reinstalled
from Control before their tools or connection forms work. Runtime does not delete
running stack profile data automatically.

All three entries keep `provides_skills`; they have no MCP endpoint or MCP
connection requirement. Other entries retain their existing MCP contract.
Names, summaries, publisher labels, compatibility decisions, and URLs are
maintained in Control. Change provider content only through a Control release
that bumps the package revision.

Names, summaries, publisher labels, compatibility decisions, and URLs were
written for XNOBrain from the linked provider documentation and homepages; no
OpenBot descriptions, manifests, code, or assets are included.

Each entry may also carry `examples`: up to three example prompts each for
`en`, `vi` and `zh`, at most 160 characters each. The plugin page in the UI
shows them. They describe only what the entry's summary says the service can
do. Control maintains and validates the current entries and their examples.

The OpenBot catalog at commit
`21369e1eec420772a7db9f6bbfeae2ef9b3ab83a` is recorded solely as a coverage
checklist for the 31 entries. That repository is licensed under PolyForm
Noncommercial 1.0.0; its license terms do not cover commercial use. XNOBrain
does not distribute OpenBot catalog content or assets. See the
[upstream license](https://github.com/nightly-labs/openbot/blob/21369e1eec420772a7db9f6bbfeae2ef9b3ab83a/LICENSE).

Each catalog item links to the provider homepage and primary documentation
used to describe its integration and classify transport/authentication.
`publisher_kind` labels an official provider-maintained server as `official`,
and an integration maintained outside that provider as `community`. For
community entries the publisher field describes the publisher attribution
available from the linked project; it does not imply endorsement by the
service provider.

Each entry also has a required `iconUrl`: the provider's own logo (usually
its `apple-touch-icon` or site icon), hot-linked from the provider's website
rather than bundled. It must be a public HTTPS URL with no credentials or
query string, like the other links. The UI shows it on a white tile and falls
back to the name's first letter when the image fails to load. Logos remain
the trademarks of their owners.

Catalog availability is advisory metadata maintained by Control. Supported remote
HTTP integrations are marked `installable`; the unpinned Brave Search stdio package remains
`needs_review` until its version and package provenance can be pinned and
reviewed. Integrations requiring a desktop application and the Cobrain OAuth
integration are listed for coverage but are unsupported by the current
installer. Runtime validates every install request and its server endpoint.

`POST /agents-plugins/{agent_id}/install-catalog` never reads a catalog file or
trusts recipe fields from the browser. Control resolves the catalog ID against
its own catalog, discards browser-supplied recipe data, and forwards the
resolved `id`, `kind`, `package`, `mcp`, `installability`, and
`recipe_revision` in a principal-bound HMAC header signed with the private
service token. Runtime rejects a missing or invalid recipe with
`403 catalog_recipe_unavailable` and a mismatched ID with
`422 invalid_catalog_recipe`.

## Managed provider connections

The installed `provider.json` registers exactly the tools Control declares:
`sapo_read` (read), `sepay_read` (read), `kiotviet_read` (read) and
`kiotviet_write` (write). Runtime does not embed those names or their resource
and operation enums. Install, approve and enable the installed package for the
agent; enter credentials in the plugin detail connection form. Saving and
testing a connection do not bypass package approval. These tools never fall back
to profile environment credentials.

Control encrypts provider credentials in its existing vault. Runtime stores only
`provider_connections[plugin_id].connection_id` in extension state and receives
short-lived Control grants through trusted requests. Each call checks current
profile, enabled/approved package, mapping and grant. Control checks connection
owner, agent and provider. Organization connections are currently unsupported.

Every tool call reads one page (default 100 rows, maximum 100). SePay requires an
explicit sandbox/production environment and account allowlist; transactions also
require inclusive date bounds (at most 366 days). Test connection checks one
metadata page, not every API permission. KiotViet tokens are refreshed by
Control and never enter tool arguments/results or the profile.

KiotViet 0.3.0 adds `kiotviet_write` and operation-based `kiotviet_read` with
48 reads and 38 writes from the retail Public API. Control projects the operation
enums at startup from its domain catalog; Runtime receives them inside the
installed tool schema and validates every call against that installed schema, so
a later catalog change cannot grant an installed read-only package a write tool.
The English plugin API reference maps every operation to its method/path and
documents payloads, pagination and ambiguous write outcomes. No new credential
fields are required.
