# Brain4All workspace rules

This directory is the agent's persistent workspace. Keep all user-requested
deliverables inside this directory.

- Answer simple questions directly. Do not create proof, log, scratch, or
  demonstration files unless the user explicitly asks for a file.
- When the user requests a file or the task clearly requires a file
  deliverable, write it beneath this workspace using a descriptive relative
  path.
- Never report that a file was created until you have verified that it exists
  in the persistent workspace.
- Report deliverables using paths relative to this workspace. Do not present
  temporary Kanban, cache, or system paths as durable output.
- Treat directories below `.hermes/kanban/workspaces` as temporary. If a tool
  places a requested deliverable there, move or copy it to
  `$HERMES_HOME/workspace` and verify the durable copy before completing.
- Do not overwrite an existing user file unless the task requires it. Prefer a
  new clearly named file when the intended target is ambiguous.
- Keep credentials, tokens, internal prompts, and tool traces out of
  deliverables.

