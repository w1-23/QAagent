#!/usr/bin/env python3
"""Independent offline development Worker; no model and no Web server.

Usage requires an existing isolated/app-initialized runtime and immutable local
image. Public arbitrary-code admission is not enabled by this entrypoint.
"""
from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import sys
import time


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--runtime-root", type=Path, required=True)
    parser.add_argument("--staging-root", type=Path, required=True)
    parser.add_argument("--image-id", required=True)
    parser.add_argument("--backend", choices=("native", "wsl", "apptainer"), required=True)
    parser.add_argument("--sif-path", help="Absolute local immutable SIF path for Apptainer")
    parser.add_argument("--apptainer-path", default="/usr/bin/apptainer")
    parser.add_argument("--host-python", default="/usr/bin/python3", help="Host-visible trusted Python for the systemd service")
    parser.add_argument("--service-path", help="Host-visible absolute apptainer_service.py path")
    parser.add_argument("--namespace-sif-path", default="", help="Optional pinned trusted namespace controller SIF")
    parser.add_argument("--namespace-sif-sha256", default="")
    parser.add_argument("--worker-id", default="code-worker-1")
    parser.add_argument("--development-oci", action="store_true", required=True,
                        help="Explicit offline development boundary; not public-user admission")
    parser.add_argument("--base-dependency-inventory", type=json.loads,
                        help="Verified base dependency versions from the trusted host launcher")
    parser.add_argument("--once", action="store_true")
    parser.add_argument("--hard-storage-root", type=Path)
    parser.add_argument("--hard-storage-limit-bytes", type=int)
    args = parser.parse_args()
    if bool(args.namespace_sif_path) != bool(args.namespace_sif_sha256):
        parser.error("namespace SIF path and SHA-256 must be supplied together")
    inventory = args.base_dependency_inventory
    if inventory is not None and (args.backend != "apptainer" or type(inventory) is not dict
                                  or len(inventory) > 256 or inventory and "python" not in inventory
                                  or any(type(version) is not str or not version for version in inventory.values())):
        parser.error("Invalid Apptainer base dependency inventory")
    if args.namespace_sif_path and args.backend != "apptainer":
        parser.error("namespace SIF requires the Apptainer backend")
    if (args.hard_storage_root is None) != (args.hard_storage_limit_bytes is None):
        parser.error("hard storage root and limit must be supplied together")
    if args.backend == "apptainer" and (sys.platform != "linux" or not args.sif_path):
        parser.error("Apptainer requires native Linux and --sif-path; --image-id is its sha256:<digest> pin")
    if args.hard_storage_root is not None and (sys.platform != "linux" or args.backend not in {"native", "apptainer"}):
        parser.error("hard storage admission requires native Linux; Windows/WSL bridge is unsupported")
    if not args.runtime_root.is_absolute() or not args.staging_root.is_absolute():
        parser.error("runtime and staging roots must be absolute local paths")
    account_db = args.runtime_root / "state" / "accounts.sqlite3"
    if not account_db.is_file():
        parser.error("runtime must already have an initialized account store")
    os.environ["CITRUSQA_PROJECT_ROOT"] = str(args.runtime_root)
    os.environ["CITRUS_RUNTIME_ROLE"] = "code-worker"
    os.environ.setdefault("CITRUSQA_ALLOW_EMPTY_DATA", "1")
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
    from src.code_execution.controller import ExecutionController
    from src.code_execution.repository import ExecutionRepository
    from src.code_execution.oci import OCIConfig, OfflineOCIBackend
    from src.code_execution.cleanup import ExecutionCleanup
    from src.code_execution.storage import HardStorage, VolumePolicy
    storage = (HardStorage(VolumePolicy(args.hard_storage_root, args.hard_storage_limit_bytes))
               if args.hard_storage_root is not None else None)
    if storage is not None:
        storage.check(args.runtime_root, args.staging_root)
    from src.code_execution.environments import EnvironmentStore
    from src.workspaces import TaskWorkspaceService
    def load_environment(request):
        workspace = TaskWorkspaceService(request.identity.owner_user_id, request.identity.session_id)
        return EnvironmentStore(workspace).read_overlay(request)
    base_dependency_versions = None
    if args.backend == "apptainer":
        from src.code_execution.apptainer import ApptainerConfig, OfflineApptainerBackend
        backend = OfflineApptainerBackend(ApptainerConfig(image_id=args.image_id,
            staging_root=str(args.staging_root), sif_path=args.sif_path, runtime=args.apptainer_path,
            host_python=args.host_python, namespace_sif_path=args.namespace_sif_path,
            namespace_sif_sha256=args.namespace_sif_sha256,
            **({"service_path": args.service_path} if args.service_path else {})),
            environment_loader=load_environment)
        base_dependency_versions = inventory
        if base_dependency_versions is None:
            try:
                base_dependency_versions = backend.base_dependency_versions()
            except (OSError, RuntimeError, ValueError):
                pass
        if not base_dependency_versions:
            print("Code Worker dependency inventory unavailable", file=sys.stderr)
    else:
        backend = OfflineOCIBackend(OCIConfig(image_id=args.image_id,
            staging_root=str(args.staging_root), mode=args.backend), environment_loader=load_environment)
    queue = ExecutionRepository(args.runtime_root / "state" / "code_executions.sqlite3")
    worker = ExecutionController(queue, backend, staging_root=args.staging_root, worker_id=args.worker_id,
                                 base_dependency_versions=base_dependency_versions)
    cleanup = ExecutionCleanup(queue, backend, staging_root=args.staging_root)
    # A heartbeat is an availability hint only. Queue fences, actual container
    # receipts and protected Host checkpoints remain the execution authorities.
    from src.code_execution.presence import worker_presence
    with worker_presence(queue.path) as presence_failed:
        while not presence_failed.is_set():
            worker.logs.expire()
            cleanup.maintain()
            if storage is not None:
                # Fresh kernel identity/capacity + low-water admission before claim.
                storage.check(args.runtime_root, args.staging_root)
            result = worker.run_one()
            if result is not None:
                print(json.dumps({"execution_id": result["execution_id"], "status": result["status"]}), flush=True)
            if args.once:
                return 0
            if result is None or result["status"] == "unknown":
                time.sleep(1)
        raise RuntimeError('code_worker_presence_unavailable')


if __name__ == "__main__":
    raise SystemExit(main())
