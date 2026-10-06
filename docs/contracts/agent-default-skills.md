# Default skills for new agents

When a new agent profile is created, Runtime copies enabled common-profile
skills and then invokes the installed engine's bundled-skill synchronizer in
an isolated child process with `HERMES_HOME` pointing to the new profile.
`HERMES_INSTALL_DIR` identifies that installation. Native installations also
resolve `.tools/hermes-agent` beneath the Runtime source root when the environment
override is absent. Source-only environments without an installation retain the
common-profile behavior.

The bundled catalog is installed in the profile's `skills/` directory, making
it visible to both the agent and the existing Skills API. Catalog installation
is independent of `RUNTIME_INCLUDE_PACKAGED_SKILLS`, which controls API
projection of the external installation directory. The existing default
activation policy enables broadly useful skills and disables the remaining
bundled entries; installation does not imply every skill is enabled.

The engine synchronizer owns manifest tracking and honors explicit
`.no-bundled-skills` markers. Existing common skills with user modifications
remain intact. An idempotent request for an existing agent does not reseed its
catalog or reset enabled/disabled choices. This creation path does not migrate
BigBrother or other existing profiles.

If the configured synchronizer fails or exceeds 60 seconds, agent creation
fails with `default_skills_initialization_failed`; the newly created profile
is removed by the existing creation rollback. Subprocess output is not exposed
or logged.
