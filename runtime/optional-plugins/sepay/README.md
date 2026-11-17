# SePay plugin

Install from Discover → Plugins, approve its package, then enable it for the selected agent. Configure the connection in its detail page using environment (sandbox or production), API token, and a comma-separated allowlist of bank account IDs.

The `sepay_read` tool reads one bounded page through Control. Credentials are encrypted in PostgreSQL; only a connection ID is stored in the agent profile. Organization connections are not supported yet. No environment-variable credential fallback, writes, or webhook management is provided.
