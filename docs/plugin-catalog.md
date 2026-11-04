# Agent plugin catalog

`xnobrain/assets/plugin_catalog.json` is XNOBrain-authored metadata. Names,
summaries, publisher labels, compatibility decisions, and URLs were written for
XNOBrain from the linked provider documentation and homepages; no OpenBot
descriptions, manifests, code, or assets are included.

Each entry may also carry `examples`: up to three example prompts each for
`en`, `vi` and `zh`, at most 160 characters each. The plugin page in the UI
shows them. They describe only what the entry's summary says the service can
do. All 31 checked-in entries have three prompts per language.

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

Catalog availability is advisory metadata. Only six remote HTTP integrations
are marked `installable`; the four stdio package candidates remain
`needs_review` until their versions and package provenance can be pinned and
reviewed. OAuth-dependent integrations and integrations requiring a desktop
application are listed for coverage but are unsupported by the current
installer. Runtime validates every install request and its server endpoint.
