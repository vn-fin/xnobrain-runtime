# Native CLI profile paths

Runtime discovers named profiles under `HERMES_PROFILES_ROOT`. The installed
CLI derives its profiles directory from its root `HERMES_HOME` plus `/profiles`.
For a newly provisioned native VM these are:

```text
Runtime: /srv/xnobrain-data/profiles
CLI:     /srv/xnobrain-data/root/profiles -> /srv/xnobrain-data/profiles
```

`scripts/prepare-service-data.sh` establishes a directory-level link before
seeding templates. The native installer installs `agent` through the same
`runtime/agent-cli.sh` wrapper used by containers, rather than linking directly
to the upstream executable. The wrapper anchors the CLI to the Runtime root,
preserves named-profile selection and explicit `-p` arguments, and retains the
existing per-invocation Router key loading.

Both `install-linux.sh` and `install-systemd-services.sh` must preserve that
wrapper. Source-based Incus image assembly installs it too, along with the
entrypoint's profile linker. Replacing `agent` with a direct engine link skips
the wrapper's strict checks and can create profiles outside Runtime discovery.
Existing unmigrated VMs can still have the `hermes/root` and `hermes/profiles`
prefixes; use their effective environment when checking a physical path.

The linker serializes first-use setup and handles missing or empty CLI profile
directories. The CLI may still print `root/profiles/math`; resolving that path
must yield the real Runtime profile directory. Profiles themselves are ordinary
directories, not per-profile symlinks.

Before `profile create`, `import`, or `rename`, a layout conflict stops the
command with a nonzero exit code. Read commands and help remain available for
diagnosis. Startup reports existing-data conflicts without moving data or
making the entire Runtime unavailable. Existing populated directories, wrong
symlinks and occupied files are never overwritten or automatically merged.

For an existing workspace with copies in both roots, compare and snapshot the
profiles before an explicitly planned migration. Updating this code alone does
not reconcile those copies or alter a running remote VM. After migration, verify
both CLI selection and Runtime agent inventory/detail before claiming recovery.

Source checks: `python -m unittest xnobrain.tests.test_native_profile_paths
xnobrain.tests.test_compiled_container xnobrain.tests.test_profile_inventory`.
The real-CLI test creates disposable profiles without aliases, skills or provider
calls, and verifies discovery through Runtime's agent manager.
