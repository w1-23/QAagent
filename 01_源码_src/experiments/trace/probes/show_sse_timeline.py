# -*- coding: utf-8 -*-
"""把 probe_loop_stall_e2e.json 的 SSE 时间线打出来，看空档夹在哪些事件之间。"""
import json
import pathlib
import sys

path = pathlib.Path(sys.argv[1] if len(sys.argv) > 1 else
                    "experiments/trace/runs/20261030/probe_loop_stall_e2e.json")
data = json.loads(path.read_text(encoding="utf-8"))
turn = data["turn"]
print(f"elapsed={turn['elapsed_s']}s frames={turn['frames']} max_gap={turn['max_gap_s']}s")
prev = 0.0
for f in turn["timeline"]:
    gap = f["t"] - prev
    mark = "  <<< 空档" if gap >= 1.0 else ""
    print(f"{f['t']:7.3f}  (+{gap:5.2f})  {f['event']}{mark}")
    prev = f["t"]
print("\nstalls:")
for s in (data["watchdog_after"].get("stalls") or []):
    print(f"  {s['stall_ms']}ms")
    for line in s["frames"][:6]:
        print(f"    {line}")
