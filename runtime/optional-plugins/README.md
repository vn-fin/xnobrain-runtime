# Optional provider plugins

`sapo/` and `sepay/` are independent native directory plugins, each with one
English skill. They register no tool, hook, job, or startup API request. They
are not part of `required-plugins` or automatic profile provisioning.

These packages require XNOBrain Runtime's
`xnobrain.integrations.provider_reads.CONTRACT_VERSION == 1` from this change.
The scripts use that Runtime-owned transport; copying a plugin to an older
Runtime without this module is unsupported. No dependency on the other
provider plugin exists. Python 3.11+ and the standard library suffice for the
readers. Native plugin discovery also uses the Runtime's existing dependencies.

## Installation from the UI

The Skills library lists `sapo` and `sepay` under `integrations` as available,
not installed. Select **Install**, select the target agents, then apply. You
can also open an agent's **Skills → Add skill** dialog and choose one provider.
Installation copies the complete skill payload (`SKILL.md`, reader script, and
two references) into that profile's `skills/custom/<provider>/` and leaves it
disabled. Enable it with that agent's existing skill switch after configuring
the connection described in `references/setup.md`.

The library and its install action use the existing authenticated Skills API.
They do not call either provider or copy credentials. Installation merges only
the provider's environment variable names into `terminal.env_passthrough`,
preserving other configuration. It snapshots the prior configuration and skill
payload, publishes the directory atomically, rejects symlink escapes, and does
not overwrite an already installed skill. Existing local skills with the same
ID take precedence over the shipped catalog. A missing package is not listed.

UI installation exposes the flat skill (`sapo` or `sepay`) to the ordinary skill
index when enabled. It does not register a native plugin or change native
plugin enablement. The native directory-plugin workflow below remains a separate
installation option using the same authored skill files. Do not enable both
copies merely to make the UI entry appear.

The flat skills declare optional environment-discovery metadata so inherited
skills can register scoped terminal passthrough without interactive token
collection. The reader still requires every configured setting and reports a
missing connection. This metadata never supplies a token or chooses an account.
Removing a UI-installed skill removes its own directory; variable-name
allowlisting and provider credentials remain in that profile for reuse.

## Native plugin installation and removal

Use an authorized profile and the existing native plugin lifecycle. Copy only
the files enumerated in each `package-files.txt` into
`<profile>/plugins/<provider>/`, preserving their relative paths. Never copy
`.env`, caches, fixtures, session data, or a whole source store directory.
The profile root is resolved by Runtime, not supplied by a model as an account
owner. The manifest and script must be updated together, with a backup of the
previous package directory before replacement.

Enable with `agent -p PROFILE plugins enable sapo` or
`agent -p PROFILE plugins enable sepay`. Start a new session after changes.
Explicitly load `sapo:sapo` or `sepay:sepay` through `skill_view`. Native
plugin skills are opt-in loads, not entries in the flat system-prompt index.
Installation does not configure a connection or perform a provider request.

Disable the selected plugin with `agent -p PROFILE plugins disable PROVIDER`
before removing its own package directory. Leave other plugins and profile
configuration intact. Restoring the previous package directory rolls back the
package. Removing the package does not revoke a provider token.

## Configuration boundary

An administrator configures secrets in the existing active profile secret
facility (the native profile `.env` / secret source), never in an exported
plugin. In-process calls use the authoritative native secret scope without
falling back to another profile's process environment. CLI calls use the
Runtime terminal's scoped child environment. The reader does not discover
`.env` files, accept tokens on the command line, or accept a connection owner.

Configure the per-provider `terminal.env_passthrough` names listed in setup.
The pinned namespaced-skill loader bypasses the ordinary skill environment
registration path; declaring prerequisites in Markdown alone is insufficient.
The native terminal resolves configured names through the current secret scope.

Do not inject these provider credentials as shared workspace-wide variables
when several profiles have different access. This change adds no OAuth UI or
Control API. An agent with unrestricted shell access to its own environment
can read its token or modify a local client; the plugin is not a hostile-code
sandbox. Provider permissions and Runtime profile isolation remain necessary.
SePay additionally requires an explicit configured account allowlist; Sapo is
bound to one validated store slug. There is no shared outbound-IP limiter;
per-process delays and bounded HTTP 429 backoff do not establish a global limit.

## Output and composition

The readers emit JSON even without `--json`. Contract version 1 contains:

- `status`: `success`, `partial`, or `error`.
- `provider`, `environment`, and an array of projected `data` records.
- `coverage`: requested scope, retrieval time, record count, completeness,
  request count and limitations.
- `error`: a safe code and optional HTTP status, or null.

Exit codes are 0 for success, 1 for errors and 3 for partial reads. Options
`--timeout` (1–30 seconds, default 15), `--deadline` (1–300 seconds, default 90)
and `--max-pages` (1–100, default 20) bound work. The deadline bounds requests
and retry waits; DNS and a slow response can still be subject to the underlying
socket timeout. Each response is capped at 4 MB. Readers issue only GET, reject
redirects, disable inherited HTTP proxies and omit raw error bodies.

A future reconciliation skill must check both plugins and contract version 1,
use the installed script paths, and consume coverage before comparing orders
with payments. It must report missing dependencies without installing them or
changing credentials. Incomplete feeds cannot establish that an order is
unpaid. Do not copy clients or tokens into business skills.

## Provenance and verification

The Sapo command vocabulary and endpoint candidates were informed by the local
Petshop reference. This implementation is newly authored; no reference source,
private fixtures, credentials or shop-specific text is distributed. The
packages and shared Runtime module retain the repository's proprietary
license; each package includes its own copy of that notice.

Official references and provider limitations are linked from each skill's
`references/api.md`. Synthetic test results, actual native lifecycle checks,
and any live checks must be reported separately. Sapo inventory/lot pagination
is deliberately marked partial pending verification on an authorized store.
