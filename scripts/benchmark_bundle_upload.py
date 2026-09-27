"""Opt-in, disposable HTTP upload/import benchmark; never uses workspace data.

Run in the source container with PYTHONPATH=/workspace. The default fixture is
1 GiB of random binary profile data. This measures local Runtime HTTP/storage,
not authenticated Control routing or browser throughput. Output contains only
synthetic fixture metadata and timings; all profile/archive data is removed.
"""

from __future__ import annotations

import argparse
import asyncio
import hashlib
import json
import os
import socket
import statistics
import tempfile
import time
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch
from zipfile import ZipFile

import httpx
import uvicorn
from fastapi import FastAPI, Request
from xnobrain.handlers.api import APIHandlers
from xnobrain.handlers.operations.portability import operations
from xnobrain.repositories.base import StoreError
from xnobrain.repositories.files import FileRepository
from xnobrain.services.portability import CHUNK_SIZE, UPLOAD_CHUNK_SIZE, PortabilityService
from xnobrain.services.portability_tasks import PortabilityTasks


def application(service):
    handlers = APIHandlers(
        SimpleNamespace(
            start_bundle_upload=service.start_upload,
            put_bundle_upload_part=service.put_upload_part,
            complete_bundle_upload=service.complete_upload,
        )
    )
    handlers._operation = lambda name, request, body: operations(handlers, request, body)[name]
    app = FastAPI()

    @app.post("/uploads", name="bundle_upload_start")
    @app.post("/uploads/{transfer_id}/complete", name="bundle_upload_complete")
    async def dispatch(request: Request, body: dict):
        return await handlers.dispatch(request, body)

    app.add_api_route(
        "/uploads/{transfer_id}/parts/{part_number}",
        handlers.bundle_part,
        methods=["PUT"],
        name="bundle_upload_part",
    )
    return app


def rss_bytes():
    # Linux source container: combined benchmark client + HTTP server + worker.
    return int(Path("/proc/self/statm").read_text().split()[1]) * os.sysconf("SC_PAGE_SIZE")


async def run_once(root, archive, expected, slots):
    archive_digest = PortabilityService._hash_file(archive)
    repository = FileRepository(root / "data", root / "profiles")
    service = PortabilityService(repository, root / "hermes")
    tasks = PortabilityTasks(service)
    repository.portability_task_store = tasks.store
    sock = socket.socket()
    sock.bind(("127.0.0.1", 0))
    server = uvicorn.Server(uvicorn.Config(application(service), log_level="error"))
    serving = asyncio.create_task(server.serve(sockets=[sock]))
    while not server.started:
        await asyncio.sleep(0.01)
    metrics = {
        "slots": slots,
        "retries": 0,
        "busy_responses": 0,
        "poll_retries": 0,
        "peak_rss_bytes": rss_bytes(),
    }

    async def sample():
        while True:
            metrics["peak_rss_bytes"] = max(metrics["peak_rss_bytes"], rss_bytes())
            await asyncio.sleep(0.02)

    sampler = asyncio.create_task(sample())
    try:
        async with httpx.AsyncClient(
            base_url=f"http://127.0.0.1:{sock.getsockname()[1]}", timeout=300
        ) as client:
            started = time.perf_counter()
            response = await client.post(
                "/uploads", json={"filename": "synthetic.zip", "size": archive.stat().st_size}
            )
            response.raise_for_status()
            descriptor = response.json()["data"]
            metrics.update(
                chunk_size=descriptor["chunk_size"], total_parts=descriptor["total_parts"]
            )
            admitted = time.perf_counter()
            upload = descriptor["upload_id"]
            indices = iter(range(descriptor["total_parts"]))

            async def send_parts():
                with archive.open("rb") as source:
                    for index in indices:
                        source.seek(index * descriptor["chunk_size"])
                        payload = source.read(descriptor["chunk_size"])
                        digest = hashlib.sha256(payload).hexdigest()
                        for attempt in range(4):
                            part = await client.put(
                                f"/uploads/{upload}/parts/{index}",
                                content=payload,
                                headers={
                                    "X-Part-SHA256": digest,
                                    "Idempotency-Key": f"{upload}:{index}:{digest}",
                                },
                            )
                            if part.status_code == 429:
                                metrics["busy_responses"] += 1
                            if (
                                part.status_code not in {408, 425, 429}
                                and part.status_code < 500
                                or attempt == 3
                            ):
                                part.raise_for_status()
                                break
                            metrics["retries"] += 1
                            await asyncio.sleep(0.5 * 2**attempt)

            await asyncio.gather(*(send_parts() for _ in range(slots)))
            uploaded = time.perf_counter()
            response = await client.post(f"/uploads/{upload}/complete", json={})
            response.raise_for_status()
            assert response.json()["data"]["sha256"] == archive_digest
            checked = time.perf_counter()
            task, _ = await asyncio.to_thread(
                tasks.create_import,
                {"upload_id": upload},
                scope="benchmark",
                actor="synthetic",
                key=upload,
            )
            await tasks.worker.start()
            async with asyncio.timeout(300):
                while True:
                    for attempt in range(4):
                        try:
                            current = await asyncio.to_thread(
                                tasks.get, task["task_id"], scope="benchmark", actor="synthetic"
                            )
                            break
                        except StoreError as error:
                            # The browser also retries transient task-read failures.
                            if error.status not in {429, 503} or attempt == 3:
                                raise
                            metrics["poll_retries"] += 1
                            await asyncio.sleep(0.5 * 2**attempt)
                    if current["status"] in {"COMPLETED", "FAILED"}:
                        break
                    await asyncio.sleep(0.05)
            finished = time.perf_counter()
            assert current["status"] == "COMPLETED", current
            target = current["result"]["agent_id_mappings"]["source"]
            destination = repository.profile_path(target) / "workspace"
            actual = {
                path.name: PortabilityService._hash_file(path) for path in destination.glob("*.bin")
            }
            assert actual == expected
            metrics.update(
                admission_s=admitted - started,
                upload_s=uploaded - admitted,
                checking_s=checked - uploaded,
                apply_s=finished - checked,
                total_s=finished - started,
                contents_equal=True,
                upload_mib_s=archive.stat().st_size / 2**20 / (uploaded - admitted),
            )
    finally:
        sampler.cancel()
        await asyncio.gather(sampler, return_exceptions=True)
        await tasks.worker.shutdown()
        server.should_exit = True
        await serving
        sock.close()
    return metrics


async def main(args):
    os.environ["RUNTIME_CUSTOM_PAGE_STORAGE_MOUNT"] = ""
    with tempfile.TemporaryDirectory(prefix="bundle-upload-benchmark-") as directory:
        root = Path(directory)
        repository = FileRepository(root / "fixture-data", root / "fixture-profiles")
        profile = repository.profiles_root / "source"
        (profile / "workspace").mkdir(parents=True)
        (profile / "config.yaml").write_text("model:\n  default: benchmark\n")
        (profile / "agent.json").write_text('{"name":"source","display_name":"Synthetic"}')
        expected = {}
        remaining = args.size_mib * 2**20
        index = 0
        while remaining:
            payload = os.urandom(min(8 * 2**20, remaining))
            name = f"part-{index:04d}.bin"
            (profile / "workspace" / name).write_bytes(payload)
            expected[name] = hashlib.sha256(payload).hexdigest()
            remaining -= len(payload)
            index += 1
        del payload
        archive = root / "synthetic.zip"
        PortabilityService(repository, root / "hermes")._export_to_path(
            {"agent_ids": ["source"]}, archive
        )
        with ZipFile(archive) as bundle:
            metadata = {
                "compressed_bytes": archive.stat().st_size,
                "expanded_bytes": sum(info.file_size for info in bundle.infolist()),
                "member_count": len(bundle.infolist()),
                "sha256": PortabilityService._hash_file(archive),
            }
        print(json.dumps({"fixture": metadata}), flush=True)
        results = []
        modes = (
            [(4, CHUNK_SIZE), (4, UPLOAD_CHUNK_SIZE)]
            if args.compare_part_sizes
            else [(1, UPLOAD_CHUNK_SIZE), (4, UPLOAD_CHUNK_SIZE)]
        )
        for pair in range(args.pairs):
            for slots, part_size in modes if pair % 2 == 0 else reversed(modes):
                with tempfile.TemporaryDirectory(dir=root, prefix="run-") as run_dir:
                    # Reproduce legacy admission descriptors without touching real
                    # workspace data or changing the production handler's bound.
                    with patch("xnobrain.services.portability.UPLOAD_CHUNK_SIZE", part_size):
                        result = await run_once(Path(run_dir), archive, expected, slots)
                    results.append({"pair": pair, **result})
                    args.output.write_text(
                        json.dumps(
                            {"fixture": metadata, "complete": False, "runs": results}, indent=2
                        )
                        + "\n"
                    )
                    print(json.dumps(results[-1]), flush=True)
        medians = {
            f"slots={slots},part={part_size}": {
                key: statistics.median(
                    row[key]
                    for row in results
                    if row["slots"] == slots and row["chunk_size"] == part_size
                )
                for key in (
                    "admission_s",
                    "upload_s",
                    "checking_s",
                    "apply_s",
                    "total_s",
                    "upload_mib_s",
                )
            }
            for slots, part_size in modes
        }
        report = {
            "complete": True,
            "fixture": metadata,
            "transport": "loopback HTTP; no injected latency; Control and browser excluded",
            "cache": "uncontrolled warm OS cache; same fixture; alternating paired order",
            "memory": "sampled combined process RSS every 20 ms",
            "medians": medians,
            "runs": results,
        }
        args.output.write_text(json.dumps(report, indent=2) + "\n")
        print(json.dumps({"medians": medians, "output": str(args.output)}), flush=True)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--size-mib", type=int, default=1024)
    parser.add_argument("--pairs", type=int, default=3)
    parser.add_argument("--compare-part-sizes", action="store_true")
    parser.add_argument(
        "--output", type=Path, default=Path("/tmp/bundle-upload-runtime-benchmark.json")
    )
    asyncio.run(main(parser.parse_args()))
