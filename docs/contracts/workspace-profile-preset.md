# Workspace profile preset v1

Control may pin an administrator-prepared S3 object and its SHA-256 in a new
workspace provisioning request. A fresh, short-lived HTTPS download URL is
generated for each attempt and passed privately as `RUNTIME_PROFILE_PRESET_URL`
with `RUNTIME_PROFILE_PRESET_SHA256`. No S3 credentials enter the guest.

Control may alternatively pass a pinned direct source URL, selected using
`CONTROL_WORKSPACE_PROFILE_PRESET_SOURCE_URL` plus the same SHA256. HTTPS and
literal RFC1918 IPv4 HTTP addresses are supported; public HTTP, localhost,
link-local targets and embedded credentials are rejected. HTTP sources cannot
carry query strings, bypass proxy environment variables and only follow redirects
on the same origin. HTTPS redirects cannot downgrade to HTTP. This path needs
no S3 and uses the same checksum, archive validation, atomic installation and
first-boot markers. Private HTTP is suitable only for shared administrator
presets on a trusted internal network, not private user data.

`scripts/prepare-service-data.sh` installs the preset before the API starts and
before marking the persistent volume initialized. Initialized volumes and fleet
rollout candidates skip seeding. Existing profiles are never overwritten; a
failed first boot can resume profiles already installed atomically. Failure to
download, validate, or install a configured preset fails preparation rather than
publishing an incomplete workspace as ready. This does not run or cancel existing
portability import jobs.

On first boot, the installer creates a missing root `config.yaml` from the
packaged `root/profile-template/config.yaml` before extracting profiles. The
file is published atomically with private permissions; an existing workspace
configuration always wins. A missing template fails preparation before profile
extraction. This allows preset installation before the API has ever started.

The prepared ZIP has `preset.json` (`format: xnobrain-profile-preset`, `version: 1`,
`profiles: [id, ...]`) and `profiles/<id>/...`. It is an administrator-distributed
template, not a general upload or full account backup. Prepare it once with:

```sh
python xnobrain/integrations/workspace_profile_preset.py prepare \
  --output /path/cfa-preset.zip /path/cfa-v6-*.zip
```

The command reads ordinary XNOBrain bundles without extracting them locally.
It keeps profile instructions, identity metadata, skills, and workspace files;
excludes credentials, hidden files, databases, logs, sessions, caches, binaries,
old configurations and scheduled jobs. Profile configuration is inherited from
the receiving workspace, not from the publisher. Review the instructional and
workspace content before publishing: path filtering is not a content malware or
secret scanner. Arbitrary client-selected archives cannot use this boot path.

Upload the output to a private immutable S3 key. Configure Control's
`CONTROL_WORKSPACE_PROFILE_PRESET_OBJECT_KEY` and
`CONTROL_WORKSPACE_PROFILE_PRESET_SHA256` (printed by preparation), along with
the existing `CONTROL_OBJECT_STORAGE_ENABLED`/`CONTROL_S3_*` settings. Empty pins
leave creation unchanged. Presets require a persistent-volume VM and a Runtime
image containing this contract; enable the pins only after that image is ready.

Each new VM downloads once and extracts once. This removes eight portability
upload/scan/import cycles, but still consumes each VM's network and disk I/O.
The archive is bounded to 8 GiB compressed, 12 GiB expanded and 100,000 entries.
URLs expire after two hours; a provisioning retry obtains a fresh URL. Rollout
does not seed old workspaces, and changing pins does not replace user profiles.

Example upload from an authenticated operator machine (AWS CLI uses credentials
from its normal profile/environment; do not put keys into shell history):

```sh
preset=/path/cfa-preset.zip
sha=$(shasum -a 256 "$preset" | cut -d ' ' -f 1)
key="workspace-presets/cfa/$sha.zip"
aws --endpoint-url "$CONTROL_S3_ENDPOINT" s3 cp "$preset" \
  "s3://$CONTROL_S3_BUCKET/$key" --content-type application/zip \
  --metadata "sha256=$sha" --no-progress
printf 'CONTROL_WORKSPACE_PROFILE_PRESET_OBJECT_KEY=%s\n' "$key"
printf 'CONTROL_WORKSPACE_PROFILE_PRESET_SHA256=%s\n' "$sha"
```

Do not set a public-read ACL. Use separate staging/prod buckets or prefixes and
retain old content-addressed objects while any queued provisioning request pins
them. Set those two values as GitHub environment variables for the selected
pipeline. The S3 endpoint used to sign downloads must be HTTPS and reachable
from the guest network; an internal Docker service hostname is not sufficient.
