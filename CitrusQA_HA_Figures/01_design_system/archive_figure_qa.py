# -*- coding: utf-8 -*-
"""把 nature-figure 的三个 QA 结果整理成一份机器可读汇总（供交付附带）。

- 源码预检：读取各绘图脚本旁的 <script>.preflight.json（由 validate_figure.py --json 生成）
- PDF 字形审计：调用 audit_pdf_text.py --json
- 渲染碰撞审计：调用 audit_figure_collisions.py --json-out（写文件后读取）

产出：../图件QA_机器可读汇总.csv
"""
from __future__ import annotations

import csv
import json
import pathlib
import subprocess
import sys

SKILL = pathlib.Path(r"C:\Users\Administrator\.dsh\skills\nature-figure\scripts")
PY = sys.executable
BASE = pathlib.Path(r"E:\codex_WORKSPACES\Citrus_QA_Agent\实验结果data")
GROUPS = ["图4_联网混池风险与交付可靠性", "图3_候选_语言补偿与能力比较", "附图_S1-S4"]


def json_from_stdout(text: str):
    """过滤审计器的可读表头，只取 JSON 主体。"""
    i = text.find("{")
    return json.loads(text[i:]) if i >= 0 else None


def run(*args):
    r = subprocess.run([PY, "-X", "utf8", *map(str, args)], capture_output=True, text=True,
                       encoding="utf-8", errors="replace")
    return r.stdout or "", r.returncode


def counts_of(summary: dict) -> tuple:
    """兼容 {fail,warn} 与 {FAIL,WARN} 两种键名。"""
    if not summary:
        return (None, None)
    f = summary.get("fail", summary.get("FAIL"))
    w = summary.get("warn", summary.get("WARN"))
    return (f, w)


def main() -> int:
    rows = []
    for g in GROUPS:
        outdir = BASE / g / "绘图产物"
        for pdf in sorted(outdir.glob("*.pdf")):
            name = pdf.stem
            t_out, _ = run(SKILL / "audit_pdf_text.py", pdf, "--json")
            t = json_from_stdout(t_out) or {}
            tverdict = ("PASS" if t.get("auditable") and (t.get("below_minimum_count") == 0)
                        else ("NOT AUDITABLE" if not t.get("auditable") else "FAIL"))
            cfile = outdir / f"{name}.collision-audit.json"
            run(SKILL / "audit_figure_collisions.py", pdf, "--json-out", cfile)
            c = json.loads(cfile.read_text(encoding="utf-8")) if cfile.exists() else {}
            cf, cw = counts_of(c.get("summary") or {})
            rows.append({
                "图组": g, "面板": name,
                "PDF字形下限_pt": t.get("minimum_found_pt"),
                "PDF低于下限数": t.get("below_minimum_count"),
                "PDF文本审计": tverdict,
                "碰撞审计": c.get("verdict"),
                "碰撞FAIL": cf, "碰撞WARN": cw,
                "碰撞JSON": cfile.name,
            })
    # 源码预检：由本脚本重新运行并写 UTF-8 JSON（避免 PowerShell 重定向写成 UTF-16）
    pre = []
    for g in GROUPS:
        for s in sorted((BASE / g / "绘图脚本").glob("绘图_*.py")):
            if "公共" in s.name:      # 库模块（无 rcParams/导出），不是绘图入口，不参与源码预检
                continue
            p_out, _ = run(SKILL / "validate_figure.py", s, "--json")
            j = json_from_stdout(p_out) or {}
            pf = s.with_suffix(s.suffix + ".preflight.json")
            pf.write_text(json.dumps(j, ensure_ascii=False, indent=2), encoding="utf-8")
            cnt = (j.get("summary") or {}).get("counts") or {}
            pre.append({"图组": g, "脚本": s.name, "ready": (j.get("summary") or {}).get("ready"),
                        "PASS": cnt.get("PASS"), "WARN": cnt.get("WARN"), "FAIL": cnt.get("FAIL")})
    out = BASE / "图件QA_机器可读汇总.csv"
    with out.open("w", newline="", encoding="utf-8-sig") as f:
        w = csv.DictWriter(f, fieldnames=list(rows[0].keys()))
        w.writeheader(); w.writerows(rows)
    out2 = BASE / "图件QA_源码预检汇总.csv"
    with out2.open("w", newline="", encoding="utf-8-sig") as f:
        w = csv.DictWriter(f, fieldnames=list(pre[0].keys()))
        w.writeheader(); w.writerows(pre)
    print(f"面板数 {len(rows)}  源码数 {len(pre)}")
    for r in rows:
        print(f"  {r['面板']:<32} text={r['PDF文本审计']} {r['PDF字形下限_pt']}pt  "
              f"collision={r['碰撞审计']} fail={r['碰撞FAIL']} warn={r['碰撞WARN']}")
    for p in pre:
        print(f"  {p['脚本']:<34} ready={p['ready']} PASS={p['PASS']} WARN={p['WARN']} FAIL={p['FAIL']}")
    print("WROTE", out)
    print("WROTE", out2)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
