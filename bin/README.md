# Optional offline image bundles

Normal builds use Docker Compose and do not write binaries or images here.
The explicit `make bundle` release command exports the two application images
as a checksum-protected `tar.gz` stream split into parts below 50 MB. Run
`make load-bundle` to verify and load those parts into another Docker daemon.

The split format is `brain4all-image-bundle-v1`; never concatenate or commit
an unsplit image tarball.
