#!/bin/bash
# Run inside the Runtime dependency image with /src read-only and /out writable.
set -euo pipefail
python=/usr/local/lib/hermes-agent/venv/bin/python
export PYTHONDONTWRITEBYTECODE=1
export PYTHONPATH=/src:/usr/local/lib/hermes-agent
uv pip install --python "$python" 'Nuitka==4.1.3' 'ordered-set==4.1.0' 'zstandard==0.25.0'
cd /src
"$python" -m unittest xnobrain.tests.test_runtime_data_volume xnobrain.tests.test_runtime_storage_memory_state
mkdir -p /out/payload /out/checks
"$python" -m nuitka --module xnobrain --include-package=xnobrain \
  --nofollow-import-to=xnobrain.tests --nofollow-import-to=xnobrain.assets \
  --python-flag=no_docstrings --jobs="${NUITKA_JOBS:-2}" --lto=no \
  --assume-yes-for-downloads --output-dir=/out/payload --report=/out/compilation-report.xml
cp xnobrain/tests/test_runtime_data_volume.py xnobrain/tests/test_runtime_storage_memory_state.py /out/checks/
cd /out/payload
export PYTHONPATH=/out/payload:/out/checks:/usr/local/lib/hermes-agent
"$python" -c 'import importlib.util; assert importlib.util.find_spec("xnobrain").origin.endswith(".so"); import xnobrain; assert xnobrain.__compiled__.no_docstrings'
"$python" -m unittest test_runtime_data_volume test_runtime_storage_memory_state
# Assets and callable launch helpers remain readable by design. This is a payload,
# not an Incus image: the VM recipe must install the matching Hermes dependencies.
bundle=$(mktemp -d /out/bundle.XXXXXX)
trap 'rm -rf "$bundle"' EXIT
mkdir -p "$bundle/xnobrain"
extension_suffix=$("$python" -c 'import sysconfig; print(sysconfig.get_config_var("EXT_SUFFIX"))')
cp "/out/payload/xnobrain${extension_suffix}" "$bundle/"
cp -a /src/xnobrain/assets "$bundle/xnobrain/"
cp -a /src/runtime "$bundle/"
find "$bundle" -type d -name __pycache__ -prune -exec rm -rf {} +
tar --sort=name --mtime=@0 --owner=0 --group=0 --numeric-owner \
  -cf /out/runtime-payload.tar -C "$bundle" .
