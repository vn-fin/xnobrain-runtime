"""Disposable source Runtime for Control's opt-in example integration test.

Run in the development container with two unused HTTP/gRPC port arguments.
The fixture owns a TemporaryDirectory and never touches the mounted workspace.
"""

import argparse
import os
import shutil
import tempfile
from contextlib import asynccontextmanager
from pathlib import Path

import uvicorn
from fastapi import FastAPI

from xnobrain.app import XNOBrainApplication
from xnobrain.integrations import AgentManager, GlobalConfigManager
from xnobrain.integrations.runtime_gateway import start_runtime_gateway
from xnobrain.tests.test_fastapi import FakeRouter


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("http_port", type=int)
    parser.add_argument("grpc_port", type=int)
    args = parser.parse_args()
    with tempfile.TemporaryDirectory(prefix="example-integration-") as directory:
        root = Path(directory)
        os.environ.update(
            {
                "HERMES_HOME": str(root / "home"),
                "HERMES_ROOT_PROFILE": str(root / "home"),
                "HERMES_PROFILES_ROOT": str(root / "profiles"),
                "DATA_DIR": str(root / "data"),
                "RUNTIME_CUSTOM_PAGE_STORAGE_MOUNT": "",
                "RUNTIME_INCLUDE_PACKAGED_SKILLS": "false",
                "RUNTIME_GRPC_ENABLED": "true",
                "RUNTIME_INTERNAL_SERVICE_TOKEN": "example-integration-test-only",
                "RUNTIME_GRPC_PORT": str(args.grpc_port),
                "API_SERVER_PORT": str(args.http_port),
            }
        )
        agents = AgentManager(
            root_profile=root / "home",
            profiles_root=root / "profiles",
            legacy_agents_root=root / "legacy",
        )
        composition = XNOBrainApplication(
            agents, GlobalConfigManager(root_profile=root / "home"), FakeRouter()
        )
        app = FastAPI()
        composition.register(app)

        @asynccontextmanager
        async def lifespan(_app):
            gateway = await start_runtime_gateway()
            await composition.service.portability_tasks.worker.start()
            try:
                yield
            finally:
                await composition.service.portability_tasks.worker.shutdown()
                await gateway.stop(grace=1)
                # Uvicorn re-raises SIGTERM after lifespan shutdown; remove
                # owned fixture data before that signal can exit the process.
                shutil.rmtree(root)

        # Only the real bundle worker and authenticated relay are needed; no
        # provider, organization connector, scheduler, or default profile runs.
        app.router.lifespan_context = lifespan
        uvicorn.run(app, host="127.0.0.1", port=args.http_port, access_log=False)


if __name__ == "__main__":
    main()
