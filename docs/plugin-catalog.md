# Agent plugin catalog

## Optional native provider plugins

Discover > Plugins includes Sapo, SePay and KiotViet as optional native plugin packages.
They are not seeded into the default Skills library. Their catalog entries have
`kind: native`, a server-owned `package` identifier, and `provides_skills`; they
have no MCP endpoint or MCP connection requirement. Other entries retain their
existing MCP contract. Missing package payloads are reported as unavailable.

Installation uses the existing per-agent extension operations route with
`kind: install`, `plugin_id: sapo|sepay|kiotviet`, `source: bundled:sapo|bundled:sepay|bundled:kiotviet`,
and an idempotency key. The backend accepts only these exact package/ID pairs;
local paths, custom references and subdirectories are rejected. Workspace
identity, profile ownership, lifecycle locking, scanning, snapshots, approval,
enable/disable and removal use the existing native plugin lifecycle. Installing
starts disabled and performs no provider request or credential mutation.
The installed skill remains owned by the plugin and is registered only when
the plugin is enabled. Connection setup is described in its English skill
references. Updates resolve the current shipped package and require fresh
artifact approval. The packages are independently authored by XNOBrain, not
official provider-maintained integrations.

The catalog itself is owned by Control at
`xnobrain-control/internal/services/mcpconnection/assets/plugin_catalog.json`
and served from `GET /xnobrain/api/control/v1/plugins/catalog`. Runtime does
not hold or read the catalog file. It reports only machine facts at
`GET /xnobrain/api/runtime/v1/plugins/capabilities` (`mcp_profile_isolation` and
native package availability), and the UI merges both responses. Changing
catalog metadata requires only a Control change and deploy.

Names, summaries, publisher labels, compatibility decisions, and URLs were
written for XNOBrain from the linked provider documentation and homepages; no
OpenBot descriptions, manifests, code, or assets are included.

Each entry may also carry `examples`: up to three example prompts each for
`en`, `vi` and `zh`, at most 160 characters each. The plugin page in the UI
shows them. They describe only what the entry's summary says the service can
do. All 31 MCP entries have three prompts per language.

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

Catalog availability is advisory metadata. Twenty-five remote HTTP integrations
are marked `installable`; the unpinned Brave Search stdio package remains
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

Sapo, SePay and KiotViet version 0.2.0 register `sapo_read`, `sepay_read` and
`kiotviet_read`. Install, approve and enable the native package for the agent;
enter credentials in the plugin detail connection form. Saving and testing a
connection do not bypass package approval. These tools never fall back to profile
environment credentials. Update and reapprove existing 0.1.0 packages to use them.

Control encrypts provider credentials in its existing vault. Runtime stores only
`provider_connections[plugin_id].connection_id` in extension state and receives
short-lived Control grants through trusted requests. Each call checks current
profile, enabled/approved package, mapping and grant. Control checks connection
owner, agent and provider. Organization connections are currently unsupported.

Every tool call reads one page (default 100 rows, maximum 100). SePay requires an
explicit sandbox/production environment and account allowlist; transactions also
require inclusive date bounds (at most 366 days). Test connection checks one
metadata page, not every API permission. No writes or webhook management are
provided by these tools. KiotViet tokens are refreshed by Control and never enter
tool arguments/results or the profile.

KiotViet 0.3.0 adds `kiotviet_write` and operation-based `kiotviet_read` with
48 reads and 38 writes from the retail Public API. The installed package manifest
must declare the write tool; old approved packages retain legacy read capability
until updated and approved through the existing lifecycle. The operation catalog
under `xnobrain/assets/kiotviet_operations.json` is the consumer contract copied
from Control's domain catalog; both must remain identical. The English plugin
API reference maps every operation to its method/path and documents payloads,
pagination and ambiguous write outcomes. No new credential fields are required.
