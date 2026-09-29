# Agent data paths

## Native VMs

New native VM installations use the existing persistent mount with these roots:

```text
RUNTIME_HERMES_HOME=/srv/xnobrain-data/root
RUNTIME_HERMES_PROFILES_ROOT=/srv/xnobrain-data/profiles
```

| Resource | Path |
| --- | --- |
| Big Brother profile | `/srv/xnobrain-data/root/` |
| Big Brother deliverables and file API | `/srv/xnobrain-data/root/workspace/` |
| Big Brother and common skill catalog | `/srv/xnobrain-data/root/skills/` |
| Named profile | `/srv/xnobrain-data/profiles/<agent-id>/` |
| Named profile deliverables and file API | `/srv/xnobrain-data/profiles/<agent-id>/workspace/` |
| Named profile skills | `/srv/xnobrain-data/profiles/<agent-id>/skills/` |

Each skill has a `SKILL.md`, optionally beneath a category, with its scripts and
references alongside it. Common-skill synchronization explicitly copies selected
root-profile skills into named profiles. Profiles keep their own skill files and
enablement in `config.yaml`. Skills are separate from workspace deliverables.
Blueprints remain at `<creator-profile>/.xnobrain/agent-blueprints/`.

## Docker development and alternative layouts

Docker development uses `RUNTIME_AGENT_DATA_ROOT=/opt/data/agent`:

| Resource | Path relative to the configured root |
| --- | --- |
| Big Brother profile | `big-brother/` |
| Big Brother deliverables and file API | `big-brother/workspace/` |
| Named profile | `<agent-id>/` |
| Named profile deliverables and file API | `<agent-id>/workspace/` |
| Creator's blueprint records | `<creator-id>/.xnobrain/agent-blueprints/` |

The root is operator configuration, never a browser parameter. The existing
`agent_layout.configure_layout` projects it into the embedded engine's internal
environment aliases. Engine dependencies and installed Python modules keep their
upstream names; those installation directories are separate from user data.

The file API and sidebar resolve profiles through the same Runtime manager.
Blueprints do not become profiles until scaffolding creates their assigned
`target_profile_id`. Do not infer a folder from a display name or claim that
certification failure proves no profile directory exists.

## Existing deployments

Setting the new root alone is not a migration. Native VM provisioning and image
preparation now agree on `root/` and `profiles/`. Credential refresh preserves
the existing guest's profile paths in both its environment file and cloud-init;
it refuses repair if no existing root can be determined. Existing VMs therefore
keep their old paths until an offline cutover. Incus application containers
still use their configured `/opt/data/hermes` layout. This source change does
not move running fleet data.

Prepare an offline cutover as follows:

1. Inventory the effective root profile, named profiles, legacy agents, API
   workspace and any divergent shell workspace such as `home/workspace`.
   Preserve each real profile ID and identify collisions before copying files.
2. Select the instance and stop all writers, including conversations, workers,
   schedules and native CLI processes. Take a restorable data and configuration
   backup; account for SQLite WAL files and permissions.
3. Run the existing read-only preflight for each source/destination pair:

   ```sh
   python -m xnobrain.agent_layout_audit --source /absolute/old/profile \
     --destination /absolute/new/profile
   ```

   It reports counts, digest, conflicts and free space without exposing content.
   Symlinks, special files, changed sources and collisions require explicit
   resolution. The preflight itself performs no copy or activation.
4. Copy to staging with permissions preserved; verify hashes and database
   integrity before activation. Merge divergent workspaces only after resolving
   duplicate relative paths. Do not overwrite files based on timestamps alone.
5. For native VMs, copy the complete old `hermes/root` to `root`, and old
   `hermes/profiles` to `profiles`, preserving all skills, history, databases and
   hidden files. Update `/etc/xnobrain/xnobrain.env` and the instance cloud-init
   environment to the two native roots shown above. Remove conflicting aliases;
   leave `RUNTIME_AGENT_DATA_ROOT` unset for this separate-root layout. Review
   absolute terminal CWDs, external skill directories and stored configuration
   references. Repoint a copied `root/profiles` CLI symlink to the new named
   profile root while writers remain stopped; never replace a populated real
   directory blindly. Verify the startup linker accepts that layout.
6. Restart the selected Runtime. Verify profile IDs and sidebar inventory,
   conversations, skills and blueprint state. In a new and a resumed Big Brother
   session, create a disposable deliverable and read it through the authenticated
   workspace file API. Verify named-profile isolation too.
7. Keep the original backup until verification and rollback policy are satisfied.
   If rollback is needed, fence writers before restoring data and configuration
   together, including any writes made after cutover.

An explicit environment selection and deployment authorization are required to
execute this procedure against a running installation.
