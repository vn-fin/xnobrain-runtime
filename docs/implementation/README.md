# Historical implementation specifications

The numbered files in this directory document the retired Go/Fiber,
PostgreSQL, multi-server implementation plan. They remain only as design and
versioned-contract history.

New work targets the single `xnobrain` Python package described in
`docs/architecture.md`:

- one FastAPI process extending the original Hermes CLI application;
- one 9router process;
- React routed through Traefik;
- atomic file-backed profile state and no application database;
- optional Enterprise features through `ENTERPRISE_API_URL`;
- Docker-only OSS packaging; managed/Incus packaging belongs to Enterprise.

Do not use old Go paths or old validation commands from the numbered files.
Cross-repository protocol changes still require an explicit version change in
`docs/contracts`.
