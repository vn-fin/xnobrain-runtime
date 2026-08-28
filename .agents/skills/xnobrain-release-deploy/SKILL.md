---
name: xnobrain-release-deploy
description: Build, publish, pull, and deploy an immutable XNOBrain release for the Python/FastAPI Hermes runtime and Incus/runtime image packaging repository through GHCR and Docker/Incus environments.
---

# XNOBrain release and deployment

Use this skill only for release-image, GHCR, Docker Compose deployment, Swarm, or Incus
simulation work involving `xnobrain-runtime`. Read the repository `AGENTS.md`, applicable rules,
and the root release/deployment documentation first. The workspace root is authoritative:
component repositories do not invent independent versions or deployment contracts.

## Immutable release workflow

1. Inspect all worktrees and confirm the intended coordinated version in the root `.version`
   and the matching release notes/manifest. Check the actual source pins; do not infer
   readiness from a mutable tag or an uncommitted working tree.
2. Validate the release before mutation: `make release-check-manifest` and, when relevant,
   `make release-check-tags ENV=.env.<env>`.
3. Build all release images from the root with the selected environment, not ad-hoc
   component tags: `make build ENV=.env.<env>`. This runs the release snapshot/build flow
   and tags the component images for `dev`, `staging`, or `prod`.
4. Immediately before publishing, verify that the target GHCR packages and immutable tags
   are intended. Export `GHCR_USERNAME` and `GHCR_TOKEN` in the shell or use the ignored
   selected env file; never put credentials in tracked files. Publish with
   `make push ENV=.env.<env>`.
5. Confirm digest artifact records were created under `xnobrain-release/artifacts/` and
   commit/push the generated release metadata according to `xnobrain-release` rules before
   a remote deployment consumes it.
6. Deploy only the exact published digest record. For a Docker + local Incus simulation,
   use `make deploy-compose ENV=.env.<env>`; for the Swarm environment use
   `make deploy-swarm ENV=.env.<env>`. These commands pull pinned images, validate Incus,
   and start the release-only stack without source mounts or builds.
7. Validate Traefik, control, router, AI, UI, gateway, and Incus runtime-image import;
   run the documented smoke test and inspect logs. Record the exact release, digests,
   environment, and checks. Roll back by selecting a previously verified immutable
   artifact, never by retagging an image.

Runtime owns the backend image and Incus/native packaging, but the root release scripts coordinate its tag and digest with all other components.

## Authorization and safety

- Building is local; pushing to GHCR and changing a real Incus/Swarm environment are
  external mutations. Ask for/confirm explicit authorization immediately before each
  mutation if it has not already been granted for this run.
- Never overwrite an existing semantic release, digest artifact, GHCR tag, or environment
  deployment. Use the explicit `make republish ENV=...` path only when replacing an
  environment artifact is intentionally authorized.
- The deployment script requires digest-pinned images, GHCR credentials for private
  environments, a stable `INTERNAL_SERVICE_TOKEN`, and a working Incus socket. It must
  fail before changing the stack when those prerequisites are absent.
- Do not log secrets or pass credentials as command-line arguments. Do not deploy from
  mutable `latest` tags or a dirty source tree. Report skipped Incus/GHCR checks honestly.
