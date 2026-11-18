# Native provider packages v1

Control owns the Sapo, SePay and KiotViet native provider definitions, tool
schemas, connection-field metadata, operation enums, skills and reference
Markdown. Runtime owns only generic package verification, plugin generation,
registration and dispatch. No provider name, resource list or operation catalog
is embedded in Runtime source.

## Catalog projection

`GET /xnobrain/api/control/v1/plugins/catalog` keeps its envelope. Every
`kind: "native"` entry additionally exposes:

```json
"native_package": {
  "schema_version": 1,
  "definition_revision": 1,
  "package_version": "0.3.0",
  "package_sha256": "<64 lowercase hex>",
  "connection_fields": [
    {
      "name": "retailer",
      "kind": "text",
      "secret": false,
      "required": true,
      "max_length": 63,
      "label": {"en": "KiotViet retailer subdomain", "vi": "..."}
    }
  ]
}
```

The catalog never exposes package bytes, credentials or tool implementation
details. Runtime capability `managed_provider_packages.schema_versions`
contains `1`; there is no `native_packages` fallback.

## Package endpoint

```text
GET /xnobrain/api/control/internal/v1/provider-packages/:provider?agent_id=<agent>
Authorization: Bearer <existing scoped MCP grant>
Accept: application/json
```

Success is HTTP 200 with the exact deterministic package bytes and
`Content-Type: application/json`. The grant is the existing user-scoped MCP
grant; a provider connection is not required because installation precedes
credential configuration. The `agent_id` query is bounded
(`^[A-Za-z0-9][A-Za-z0-9._-]{0,127}$`) but is not authority: the signed intent
binds the agent, provider, route and digest. Stable failures are
`401 mcp_grant_invalid`, `404 native_provider_not_found`,
`422 native_provider_invalid_request` and
`503 native_provider_package_unavailable`.

The response body is:

```json
{
  "schema_version": 1,
  "definition": {
    "schema_version": 1,
    "revision": 1,
    "id": "kiotviet",
    "version": "0.3.0",
    "name": {"en": "KiotViet"},
    "description": {"en": "..."},
    "tools": [
      {
        "name": "kiotviet_read",
        "mode": "read",
        "description": "...",
        "input_schema": {"type": "object", "additionalProperties": false, "properties": {}}
      }
    ],
    "connection_fields": [],
    "skill": {
      "path": "skills/kiotviet/SKILL.md",
      "name": "kiotviet",
      "description": "..."
    },
    "references": [
      {"path": "skills/kiotviet/references/api.md", "title": "KiotViet retail Public API"}
    ]
  },
  "files": {
    "skills/kiotviet/SKILL.md": "<exact Markdown>",
    "skills/kiotviet/references/api.md": "<exact Markdown>"
  }
}
```

`package_sha256` is SHA-256 of these exact endpoint bytes. `revision` and the
digest are immutable for a deployed Control release. Runtime must reject a
package whose provider, version, revision or digest differs from the signed
intent.

## Signed intent

Control attaches the intent only to:

```text
POST /xnobrain/api/runtime/v1/agents-extension-operations/<agent>
```

with `kind` equal to `install` or `update`, `plugin_id` equal to the provider,
`source` exactly `bundled:<provider>`, and no `ref` or `path`. Headers:

```text
X-XNOBrain-Native-Provider-Intent: <base64url JSON without padding>
X-XNOBrain-Native-Provider-Signature: <lowercase hex HMAC-SHA256>
```

Payload:

```json
{
  "schema_version": 1,
  "provider": "kiotviet",
  "package_version": "0.3.0",
  "definition_revision": 1,
  "package_sha256": "<64 hex>",
  "agent_id": "research",
  "kind": "install",
  "path": "/xnobrain/api/runtime/v1/agents-extension-operations/research",
  "user_id": "<verified user>",
  "tenant_id": "<verified tenant>",
  "organization_id": "",
  "issued_at": 1763500000,
  "expires_at": 1763500300
}
```

Signature material is NUL-separated:

```text
native-provider-package-v1
POST
<request path>
<verified user id>
<verified tenant id>
<verified organization id>
<base64url intent payload>
```

So `HMAC-SHA256(token, "\x00".join(...))` where the token is the workspace
Runtime token after the node gateway re-signs, and the private Runtime service
token on a direct Control-to-Runtime relay. Control proxy paths strip any
browser-supplied intent headers on every request and attach a fresh signature
only for the exact route. The node gateway also strips both headers on every
relayed request, verifies Control's signature and claim bindings, then replaces
the signature with one derived from the workspace Runtime token.

Runtime enforces exactly one value for each header, the route and `POST` method,
the signature, the payload schema, provider slug, package version, positive
revision, 64 lowercase hex digest, agent ID and path, install/update kind, exact
verified subject/tenant/organization, non-empty user and tenant, `issued_at > 0`,
`expires_at > issued_at`, expiry of at most 300 seconds, and a small clock skew.
A missing, malformed, expired, cross-provider, cross-agent or cross-route intent
is rejected; query `agent_id` is never authority.

`source` values other than the exact `bundled:<provider>` pair remain on the
normal Git/upload path. A source-less update whose persisted source is
`bundled:<provider>` also requires a valid intent and never falls back to a
Runtime package.

## Install and update behavior

Runtime validates the intent and downloads the exact bytes before durable
enqueue, using only the configured `RUNTIME_CONTROL_URL` origin, no redirects,
`trust_env=False`, a short timeout and the 1 MiB package bound. It verifies the
digest and parses the schema before caching the verified bytes under
`DATA_DIR/extension-staging/native-packages/<digest>.json`. The worker rechecks
the cached digest and never downloads another revision.

Idempotency compares stable provider/source/profile-generation identity rather
than the expiring intent or package pin. Repeating an accepted request returns
the existing operation even after Control publishes a different package. A new
operation with a different stable identity and the same key conflicts.

Runtime generates `plugin.yaml`, one constant `__init__.py` bootstrap that reads
sibling `provider.json`, and the allowlisted skill/reference Markdown. The
installed definition supplies tool names, descriptions, modes and input schemas.
An optional `request.endpoint` is `read`, `execute` or `auto`; `auto` chooses
`/execute` when the call includes an `operation` argument and `/read` otherwise.
Without `request`, Runtime applies that same generic rule and adds `mode` only
for operation calls. Argument keys, required fields and enum values are validated
against the installed schema. A catalog change cannot alter an installed package
or add a write tool without a new install/update and fresh approval.

Installed plugin status adds:

```json
"native_package": {
  "schema_version": 1,
  "definition_revision": 1,
  "package_sha256": "<64 hex>"
}
```

## Limits

- Package response and total Markdown: 1 MiB.
- Files: 1 to 32; each non-empty file at most 256 KiB.
- Definitions: 1 to 16 tools, 1 to 16 connection fields, 0 to 32 references.
- Tool input schema: a JSON object with `additionalProperties: false`, at most
  64 KiB, and no `$ref`, `$dynamicRef` or `$recursiveRef`.
- Provider ID: `^[a-z0-9][a-z0-9_-]{0,63}$`.
- Package version: `^[A-Za-z0-9][A-Za-z0-9._+-]{0,63}$`.
- Tool name: `^[a-z][a-z0-9_]{0,127}$`; field name:
  `^[a-z][a-z0-9_]{0,63}$`.
- Reference file: `skills/<provider>/references/<name>.md` with a name matching
  `^[A-Za-z0-9][A-Za-z0-9._-]{0,127}\.md$`.
- Localized text: 1 to 8 locales, English required, each non-empty; names and
  field labels at most 200 bytes, descriptions and skill descriptions at most
  2000 bytes, tool descriptions at most 4000 bytes, option labels and reference
  titles at most 200 bytes, and help at most 512 bytes.
- Connection field `max_length`: required for `text` fields (1 to 8192) and
  absent or zero for `select` fields; select values are bounded to 1 to 32
  unique options with version-shaped values.

## Failure codes

Runtime operation creation uses stable public codes before enqueueing:
`native_provider_intent_required`, `native_provider_intent_invalid`,
`native_provider_grant_required`, `native_provider_grant_invalid`,
`native_provider_not_found`, `native_provider_invalid_request`,
`native_provider_package_unavailable`,
`native_provider_package_invalid` and
`native_provider_package_digest_mismatch`. None include package bytes, request
headers, credentials or Control response content.
