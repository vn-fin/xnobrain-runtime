<!-- Working overlay: authored here and copied to the profile root, never the
user workspace. The Runtime conversation prompt adapter injects this file
alongside workspace/AGENTS.md (including Big Brother and resumed sessions).
Do not copy product guidance here or use this file as the exported instructions
field. -->

# Profile working rules

## Python environments

- Never create `.venv` or `venv` inside the workspace or any project within it.
- Use exactly one uv-managed environment per profile under the shared Python
  root `/opt/data/python`: `/opt/data/python/.<profile-id>-venv`.
  Big Brother uses `/opt/data/python/.big-brother-venv`; named profiles use
  their profile id, not a display name or project name. The Runtime supplies
  the current profile's exact path in session context.
- Any environment outside the project must also use this shared root and uv.
  Do not use `python -m venv`, bare `pip`, or `virtualenv` to create or update it.
- Stay in the workspace CWD. With `VENV` set to the exact profile path, use:
  ```sh
  uv --no-cache venv "$VENV"                  # only if not already present
  uv --no-cache pip install --python "$VENV/bin/python" -r requirements.txt
  "$VENV/bin/python" script.py
  ```
  Do not run bare `uv run` or `uv sync`, which may create a project `.venv`.
- Only this profile's environment is excepted from the workspace write rule.
  User deliverables remain in the workspace; this is not permission for other
  `/opt` writes or access to another profile's environment.
- This root is provisioned on persistent, quota-accounted data storage. Avoid
  duplicate environments and caches; report disk-full errors rather than
  falling back to workspace or instance-root environments.
- Leave the runtime interpreter and office-tools environments unchanged.
