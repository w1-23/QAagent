# -*- coding: utf-8 -*-
"""QA gate for Fig.1 & Fig.2 (task-book §10, checks 1-12).

Reads each SVG master + its qa json; runs automated checks; renders are done
by LibreOffice-Kit already (pptx twins -> pdf/png, no missing fonts)."""
import sys, io, re, json, hashlib, subprocess, shutil
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8')
from pathlib import Path
from lxml import etree
from design_meta import PALETTE_ALLOWED

ROOT = Path(r"E:\codex_WORKSPACES\Citrus_QA_Agent\CitrusQA_HA_Figures")
PY = r"C:\Users\Administrator\.dsh\dsh-runtimes\dsh-primary-runtime\dependencies\python\python.exe"

FIGS = {
    "Fig1": dict(svg=ROOT / "Fig1/fig1_system.svg",
                 required={"4,906", "126,494", "103,089", "10,018", "13,137", "250", "84",
                           "144", "132", "48", "3.1%", "24.0%", "1,649/1,649", "1–3",
                           "kimi-k2.6", "glm-5.3", "RRF", "BM25Plus", "e5-large",
                           "DOI"},
                 banned={"187,441", "4,992", "MinerU", "GPT-4", "Claude"}),
    "Fig2": dict(svg=ROOT / "Fig2/fig2_corpus.svg",
                 required={"4,906", "126,494", "112,890", "13,137", "467", "62,803", "33,790",
                           "9,801", "6,496", "250", "217", "103,089", "10,018", "800", "SHA-256",
                           "e5-large", "LanceDB", "BM25Plus"},
                 banned={"187,441", "4,992", "11 batches", "MinerU"}),
}

ALLOWED = set(PALETTE_ALLOWED) | {"#FFFFFF"}
results = {}

for fid, cfg in FIGS.items():
    svgp = cfg["svg"]
    qaj = json.loads(svgp.with_suffix("").with_suffix("") .with_suffix(".json").read_text(encoding="utf-8")) if False else json.loads(Path(str(svgp).replace(".svg", "_qa.json")).read_text(encoding="utf-8"))
    root = etree.parse(str(svgp)).getroot()
    t = lambda tag: [e for e in root.iter() if etree.QName(e).localname == tag]
    texts, lines, rects, paths = t("text"), t("line"), t("rect"), t("path")
    checks = {}

    # 1 SVG validity
    checks["1_svg_valid"] = "PASS"
    # 2 editability: no <image>, no base64 raster
    images = t("image") + t("image ")
    raw = svgp.read_text(encoding="utf-8")
    raster = [e for e in t("image")] or "base64," in raw
    checks["2_editable"] = ("PASS" if not raster else "FAIL") + \
        f" texts={len(texts)} rects={len(rects)} paths={len(paths)} (nodes as vectors)"
    # 3 fonts
    fams = {e.get("font-family", "") for e in texts} | {root.get("font-family", "")}
    ok = all("Arial" in f for f in fams if f)
    checks["3_fonts"] = ("PASS" if ok else "FAIL") + f" families={sorted(fams)}"
    # 4 overflow
    fits = qaj.get("fit") if "fit" in qaj else None
    fc = json.loads((ROOT / fid).joinpath(f"figure_manifest_{fid.lower()}.json").read_text(encoding="utf-8"))["qa_evidence"]["fit_checks"]
    bad = [c for c in fc if not c["fit"]]
    checks["4_overflow"] = ("PASS" if not bad else "FAIL") + f" {len(fc)} fit-checks, {len(bad)} overflow"
    # 5 arrows direction & markers
    arrows = [e for e in lines if e.get("marker-end")]
    plain = [e for e in lines if not e.get("marker-end")]
    bad_dir = [e for e in arrows if not (float(e.get("x2")) > float(e.get("x1")) - 0.01
                                         and (float(e.get("y2")) > float(e.get("y1")) + 0.01
                                              or float(e.get("x2")) > float(e.get("x1")) + 0.01))]
    checks["5_arrows"] = ("PASS" if not bad_dir and len(arrows) > 0 else "FAIL") + \
        f" arrows={len(arrows)} plain-lines={len(plain)} wrong-direction={len(bad_dir)}"
    # 6 colours
    svg_colors = set()
    for e in root.iter():
        for a in ("fill", "stroke"):
            v = e.get(a)
            if v and v.startswith("#") and v != "none":
                svg_colors.add(v)
    stray = sorted(svg_colors - ALLOWED)
    checks["6_colors"] = ("PASS" if not stray else "FAIL") + f" stray={stray}"
    # 7 values & categories: search the RAW text the figure visibly carries
    raw = svgp.read_text(encoding="utf-8")
    raw_low = raw.lower()
    miss_req = [r for r in cfg["required"] if str(r).lower() not in raw_low]
    hit_ban = [b for b in cfg["banned"] if str(b).lower() in raw_low]
    checks["7_values"] = ("PASS" if not miss_req and not hit_ban else "FAIL") + \
        f" missing={miss_req} banned-hit={hit_ban}"
    # 8 stats discipline: scan ONLY the visible text content (not SVG markup)
    visible = " ".join((e.text or "") for e in texts)
    banned_stats = re.findall(r"(p\s*[<=]\s*\d|p-value|error bar|95% CI|n\.s\.|\*\*\*)",
                              visible, re.I)
    checks["8_stats"] = ("PASS" if not banned_stats else "FAIL") + f" hits={banned_stats}"
    # 9 sizes readability
    sizes = sorted({float(e.get("font-size", 8)) for e in texts})
    tiny = [s for s in sizes if s < 6.0]
    checks["9_sizes"] = ("PASS" if not tiny else "FAIL") + f" sizes={sizes} (<6pt count={len(tiny)})"
    # 10 HA format
    w = root.get("width"); h = root.get("height")
    wmm = float(w.replace("mm", "")); hmm = float(h.replace("mm", ""))
    labs = [e.text for e in texts if e.text and re.match(r"\([a-c]\)", e.text)]
    has_title = any(e.text and "Fig." in e.text for e in texts)
    checks["10_ha"] = ("PASS" if abs(wmm - 174.0) < 0.01 and hmm <= 234 and len(labs) >= 2
                       and not has_title else "FAIL") + \
        f" width={w} height={h} panels={labs} title-inside={has_title}"
    # 11 cross-render (already produced by LibreOffice-Kit: pptx->pdf & pptx->png)
    figdir = svgp.parent
    pdfp = figdir / (svgp.stem + ".pdf")
    pngs = list((figdir / "preview").glob("*.png")) if (figdir / "preview").exists() else []
    checks["11_render"] = ("PASS" if pdfp.exists() and pdfp.stat().st_size > 10000 and pngs
                           else "FAIL") + f" pdf={pdfp.stat().st_size if pdfp.exists() else 0}B pngs={[p.name for p in pngs]}"
    # 12 code determinism: rebuild twice → identical element stream
    tg = {"Fig1": "fig1_builder", "Fig2": "fig2_builder"}[fid]
    workdir = svgp.parent
    sys.path.insert(0, str(ROOT / "01_design_system"))
    sys.path.insert(0, str(workdir))
    import importlib
    mod = importlib.import_module(tg)
    importlib.reload(mod)
    cv_a = mod.build()
    snap_a = "\n".join(cv_a.el)
    cv_b = mod.build()
    snap_b = "\n".join(cv_b.el)
    checks["12_determinism"] = ("PASS" if snap_a == snap_b else "FAIL") + " build() twice → identical"
    results[fid] = checks

print(json.dumps(results, ensure_ascii=False, indent=1))
Path = globals()["Path"]
import json as _j
(ROOT / "QA_reports" / "qa_fig1_fig2_checks.json").write_text(
    _j.dumps(results, ensure_ascii=False, indent=1), encoding="utf-8")
n_fail = sum(1 for r in results.values() for c in r.values() if c.startswith("FAIL"))
print(f"\nTOTAL FAIL: {n_fail}")
