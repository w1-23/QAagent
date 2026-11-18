#!/usr/bin/env python3
"""Operator-only recovery of an existing unknown offline Code Worker attempt.

Default: observe the exact container without stopping it or releasing references.
Apply: requires external confirmation that the original Worker/controller and all
its children have stopped and cannot resume. This CLI does not infer death from
PID/age/heartbeat or stop the Worker for you. It never replays user code. Run from
the existing deployment environment; this is not a Web or Agent capability.
"""
from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import stat
import sys


def read_evidence(path):
    path = Path(path)
    if not path.is_absolute():
        raise ValueError("absolute_operator_evidence_file_required")
    for part in (path, *path.parents):
        if part.is_symlink() or (hasattr(part, "is_junction") and part.is_junction()):
            raise ValueError("linked_operator_evidence_denied")
    fd = os.open(path, os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0)
                 | getattr(os, "O_NONBLOCK", 0) | getattr(os, "O_BINARY", 0))
    try:
        before = os.fstat(fd)
        if not stat.S_ISREG(before.st_mode) or before.st_nlink != 1 or before.st_size > 4096:
            raise ValueError("bounded_regular_operator_evidence_required")
        payload = os.read(fd, 4097)
        after = os.fstat(fd)
        if (len(payload) != before.st_size or len(payload) > 4096
                or any(getattr(before, k) != getattr(after, k) for k in
                       ("st_dev", "st_ino", "st_size", "st_mtime_ns", "st_ctime_ns", "st_nlink"))):
            raise ValueError("operator_evidence_changed")
        return payload.decode("utf-8")
    finally:
        os.close(fd)


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--runtime-root", type=Path, required=True)
    parser.add_argument("--staging-root", type=Path, required=True)
    parser.add_argument("--image-id")
    parser.add_argument("--backend", choices=("native", "wsl", "apptainer"))
    parser.add_argument("--sif-path")
    parser.add_argument("--apptainer-path", default="/usr/bin/apptainer")
    parser.add_argument("--host-python", default="/usr/bin/python3")
    parser.add_argument("--service-path")
    parser.add_argument("--namespace-sif-path", default="")
    parser.add_argument("--namespace-sif-sha256", default="")
    parser.add_argument("--execution-id", required=True)
    parser.add_argument("--owner", type=int, required=True)
    parser.add_argument("--session", required=True)
    parser.add_argument("--fence", type=int, required=True)
    parser.add_argument("--apply", action="store_true")
    parser.add_argument("--evidence-file", type=Path)
    parser.add_argument("--stop-container", action="store_true",
                        help="On apply only: request stop of this already verified owned container")
    parser.add_argument("--cleanup-resources", action="store_true",
                        help="On apply only: journal and resume stopped container/input staging removal")
    parser.add_argument("--resource-recovery", choices=("terminal", "preparation"),
                        help="Apply only: recover new-protocol terminal references or never-started preparation; no daemon actions")
    args = parser.parse_args(argv)
    if bool(args.namespace_sif_path) != bool(args.namespace_sif_sha256):
        parser.error("namespace SIF path and SHA-256 must be supplied together")
    if args.namespace_sif_path and args.backend != "apptainer":
        parser.error("namespace SIF requires the Apptainer backend")
    if args.resource_recovery and not args.apply:
        parser.error("--resource-recovery requires --apply")
    if args.resource_recovery and (args.stop_container or args.cleanup_resources):
        parser.error("resource recovery cannot issue container stop/removal")
    if args.cleanup_resources and not args.apply:
        parser.error("--cleanup-resources requires --apply")
    if args.apply and args.evidence_file is None:
        parser.error("--apply requires --evidence-file with external stopped-Worker confirmation")
    if args.stop_container and not args.apply:
        parser.error("--stop-container requires --apply")
    if args.evidence_file is not None and not args.apply:
        parser.error("--evidence-file is only used with --apply")
    if args.backend == "apptainer" and (sys.platform != "linux" or not args.sif_path):
        parser.error("Apptainer requires native Linux and --sif-path")
    if not args.resource_recovery and (args.image_id is None or args.backend is None):
        parser.error("container recovery requires --image-id and --backend")
    for root in (args.runtime_root, args.staging_root):
        if not root.is_absolute() or str(root).startswith(("\\\\", "//")):
            parser.error("runtime/staging roots must be absolute local paths")
        for part in (root, *root.parents):
            if part.is_symlink() or (hasattr(part, "is_junction") and part.is_junction()):
                parser.error("linked runtime/staging roots are not accepted")
    accounts = args.runtime_root / "state/accounts.sqlite3"
    queue_path = args.runtime_root / "state/code_executions.sqlite3"
    if not accounts.is_file() or not queue_path.is_file():
        parser.error("existing account and execution stores are required; nothing is initialized")
    os.environ["CITRUSQA_PROJECT_ROOT"] = str(args.runtime_root)
    os.environ["CITRUS_RUNTIME_ROLE"] = "code-worker"
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
    from src.code_execution.oci import OCIConfig, OfflineOCIBackend
    from src.code_execution.protocol import token
    from src.code_execution.reconciliation import ExecutionRecovery, WorkerStopEvidence
    from src.code_execution.repository import ExecutionRepository
    try:
        proof = WorkerStopEvidence.parse(read_evidence(args.evidence_file)) if args.apply else None
        repository = ExecutionRepository(queue_path)
        scope = dict(owner=args.owner, session=args.session, fence=args.fence)
        if args.resource_recovery:
            from src.code_execution.resources import ExecutionResources
            resources = ExecutionResources(repository, staging_root=args.staging_root)
            result = resources.reconcile(args.execution_id, proof=proof,
                preparation=args.resource_recovery == "preparation", **scope)
        else:
            if args.backend == "apptainer":
                from src.code_execution.apptainer import ApptainerConfig, OfflineApptainerBackend
                backend = OfflineApptainerBackend(ApptainerConfig(image_id=args.image_id,
                    staging_root=str(args.staging_root), sif_path=args.sif_path, runtime=args.apptainer_path,
                    host_python=args.host_python, namespace_sif_path=args.namespace_sif_path,
                    namespace_sif_sha256=args.namespace_sif_sha256,
                    **({"service_path": args.service_path} if args.service_path else {})))
            else:
                backend = OfflineOCIBackend(OCIConfig(image_id=args.image_id,
                    staging_root=str(args.staging_root), mode=args.backend))
            from src.code_execution.cleanup import ExecutionCleanup
            cleanup = (ExecutionCleanup(repository, backend, staging_root=args.staging_root)
                       if args.cleanup_resources else None)
            recovery = ExecutionRecovery(repository, backend, cleanup=cleanup)
            result = (recovery.reconcile_stopped(args.execution_id, proof=proof,
                        stop_container=args.stop_container, **scope) if args.apply
                      else recovery.snapshot(args.execution_id, **scope))
        # No script, host path, full log or private process token on stdout.
        print(json.dumps(result, ensure_ascii=True, separators=(",", ":")))
        return 0
    except Exception as exc:
        try:
            code = token(str(exc))
        except ValueError:
            code = "execution_recovery_unconfirmed"
        print(json.dumps({"status": "blocked", "error_type": type(exc).__name__, "code": code}))
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
