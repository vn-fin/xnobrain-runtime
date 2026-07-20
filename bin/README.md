# Local build artifacts

`make build` writes the native `open-lumora` binary here and loads the Studio
image into the local container daemon. After the enterprise repository images
are built, `make bundle` exports all three application images here as a
checksum-protected `tar.gz` stream split into parts below 50 MB. Run
`make load-bundle` to verify and load the parts into another local Docker daemon.

The split format is `open-lumora-image-bundle-v1`; never concatenate or commit
an unsplit image tarball.
