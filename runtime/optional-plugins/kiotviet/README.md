# KiotViet

Optional Discover plugin for KiotViet retail Public API. Provides `kiotviet_read`
and `kiotviet_write` for the documented operation catalog, including product and
purchase creation, details, filters, bulk updates and webhook subscriptions.

Install/approve/enable for the intended agent. Enter retailer subdomain, client
ID and secret in the managed connection form. Credentials and cached access
tokens remain encrypted in Control; the plugin stores only a connection ID.

Read `skills/kiotviet/SKILL.md` and its API reference. Mutations require a user
request and are never automatically replayed. An uncertain outcome requires
read-back reconciliation. Provider account permissions and product availability
still apply. Subscription management does not add a webhook receiver.
