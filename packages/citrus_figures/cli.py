"""JSON-only CLI. All external paths and vendor identity are explicit."""
from __future__ import annotations
import argparse
from contextlib import redirect_stdout
import json
from pathlib import Path
import sys
from .verification import verify_current_figure


def _read_json(path):
    return json.loads(Path(path).read_text(encoding="utf-8")) if path else None


def main(argv=None):
    parser = argparse.ArgumentParser(prog="python -m citrus_figures")
    commands = parser.add_subparsers(dest="command", required=True)
    verify = commands.add_parser("verify")
    verify.add_argument("--artifact", action="append", default=[], metavar="FORMAT=PATH")
    verify.add_argument("--source")
    verify.add_argument("--layout")
    verify.add_argument("--backend", default="python", choices=["python", "r"])
    verify.add_argument("--mode", choices=["initial", "edit", "reaudit"], default="initial")
    verify.add_argument("--supersedes")
    for name in ("render", "bundle"):
        command = commands.add_parser(name)
        group = command.add_mutually_exclusive_group(required=True)
        group.add_argument("--spec")
        group.add_argument("--source")
        command.add_argument("--data", action="append", default=[], metavar="NAME=CSV_PATH")
        command.add_argument("--table", action="append", default=[], metavar="KIND:KEY=CSV_PATH")
        command.add_argument("--parameters")
        command.add_argument("--output", required=True)
    revise = commands.add_parser("revise")
    revise.add_argument("--bundle", required=True)
    revise.add_argument("--overrides", required=True)
    revise.add_argument("--output", required=True)
    for command in (verify, revise, *[commands.choices[n] for n in ("render", "bundle")]):
        command.add_argument("--vendor-root", required=True)
        command.add_argument("--checks", help="comma-separated check names; omitted means all")
        command.add_argument("--policy", help="JSON file with blocking_checks")
    try:
        # argparse diagnostics go to stderr; invalid command lines still return
        # the same one-object observation rather than an empty stdout.
        try:
            args = parser.parse_args(argv)
        except SystemExit as exc:
            if exc.code == 0:
                return 0
            raise ValueError("invalid command arguments") from exc
        checks = args.checks.split(",") if args.checks is not None else None
        policy = _read_json(args.policy)
        with redirect_stdout(sys.stderr):
            if args.command == "verify":
                artifacts = []
                for item in args.artifact:
                    fmt, path = item.split("=", 1)
                    artifacts.append({"format": fmt, "bytes": Path(path).read_bytes()})
                receipt = verify_current_figure(artifacts=artifacts,
                    source=Path(args.source).read_text(encoding="utf-8") if args.source else None,
                    layout_manifest=_read_json(args.layout), backend=args.backend,
                    mode=args.mode, supersedes=args.supersedes, checks=checks,
                    policy=policy, vendor_root=args.vendor_root)
                data = receipt
            else:
                from .bundle import render_figure, write_bundle, load_spec_bundle, revise_figure
                if args.command == "revise":
                    session = load_spec_bundle(args.bundle)
                    revise_figure(session, overrides=_read_json(args.overrides))
                else:
                    frames, tables = {}, {}
                    if args.data or args.table:
                        import pandas as pd
                        for item in args.data:
                            name, path = item.split("=", 1)
                            if name in frames:
                                raise ValueError("duplicate dataset name")
                            frames[name] = pd.read_csv(path)
                        for item in args.table:
                            reference, path = item.split("=", 1)
                            kind, key = reference.split(":", 1)
                            if kind not in {"account_file", "attachment"} or args.source:
                                raise ValueError("--table requires a spec and account_file or attachment reference")
                            binding = (kind, int(key) if kind == "attachment" else key)
                            if binding in tables:
                                raise ValueError("duplicate table binding")
                            tables[binding] = pd.read_csv(path)
                    session = render_figure(spec=_read_json(args.spec),
                        source=Path(args.source).read_text(encoding="utf-8") if args.source else None,
                        data=frames, tables=tables, parameters=_read_json(args.parameters))
                data = write_bundle(session, args.output, vendor_root=args.vendor_root,
                                    checks=checks, policy=policy)
                receipt = data["receipt"]
        conclusion = receipt["overall"]["conclusion"]
        observation = {"status": conclusion, "data": data, "errors": receipt["errors"],
                       "coverage": receipt["overall"]}
        code = 1 if conclusion in {"fail", "error"} else 0
    except Exception as exc:
        observation = {"status": "error", "data": None,
                       "errors": [{"code": "figure_error", "message": f"{type(exc).__name__}: {exc}"[:1500], "retryable": False}],
                       "coverage": {"execution": "not_started"}}
        code = 1
    print(json.dumps(observation, ensure_ascii=True, allow_nan=False))
    return code
