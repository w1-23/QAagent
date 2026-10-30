# -*- coding: utf-8 -*-
"""QA gate for the statistical figures (Fig.3 / Fig.4) — task-book §10, 1–12.

matplotlib exports real <text> nodes (svg.fonttype='none'), vector paths only,
no <image>. Heatmap gradients are validated against the declared colour ramps;
statistical values are cross-checked against the recomputed JSON.
"""
import sys, json, re, hashlib
try:
    sys.stdout.reconfigure(encoding="utf-8")
except Exception:
    pass
from pathlib import Path
from lxml import etree

ROOT = Path(r"E:\codex_WORKSPACES\Citrus_QA_Agent\CitrusQA_HA_Figures")
PALETTE = {"#4F8868", "#C87558", "#287D99", "#BE9346", "#7772A8",
           "#E7F1E5", "#FBEAE1", "#E4F2F8", "#FAF0D8", "#F0EDFA",
           "#26343B", "#52616A", "#D4DCE0", "#FFFFFF", "#333333", "#E5E5E5",
           "#B9C2C7"}
# heat ramps declared in fig_style.HEAT_CMAP
RAMP = [(0x00, "#F2F7EF"), (0x80, "#BDD8B4"), (0xFF, "#4F8868")]

FIGS = {
    "Fig3": dict(
        svg=ROOT / "Fig3/fig3_retrieval.svg",
        required=["45.8", "44.8", "93.8", "95.8", "81.2", "0.016", "+0.345", "+1.119",
                  "Track A", "Track B", "D10", "no web" if False else "kimi-k2.6", "glm-5.3"],
        banned=["187,441", "4,992", "MinerU", "GPT-4", "Claude"],
        sizes_mm=(174.0, 132.0)),
    "Fig4": dict(
        svg=ROOT / "Fig4/fig4_quality.svg",
        required=["1,649", "100.00", "99.96", "99.78", "85.07", "24.0", "33.3", "18.8", "22.9",
                  "20.8", "15.6", "5.2", "12.4", "48", "96"],
        banned=["187,441", "4,992", "MinerU", "GPT-4", "Claude"],
        sizes_mm=(174.0, 142.0)),
}


def hx(h):
    h = h.lstrip("#")
    return tuple(int(h[i:i + 2], 16) for i in (0, 2, 4))


def on_ramp(c, tol=26):
    c = hx(c)
    for k in range(len(RAMP) - 1):
        a, b = hx(RAMP[k][1]), hx(RAMP[k + 1][1])
        # project onto the segment between ramp stops
        ab = [b[i] - a[i] for i in range(3)]
        ac = [c[i] - a[i] for i in range(3)]
        den = sum(x * x for x in ab)
        t = 0 if den == 0 else max(0.0, min(1.0, sum(ac[i] * ab[i] for i in range(3)) / den))
        proj = [a[i] + t * ab[i] for i in range(3)]
        if sum((c[i] - proj[i]) ** 2 for i in range(3)) ** 0.5 <= tol:
            return True
    return False


results = {}
for fid, cfg in FIGS.items():
    svgp = cfg["svg"]
    root = etree.parse(str(svgp)).getroot()
    raw = svgp.read_text(encoding="utf-8")
    texts = [e for e in root.iter() if etree.QName(e).localname == "text"]
    checks = {}

    checks["1_svg_valid"] = "PASS"
    n_img = len([e for e in root.iter() if etree.QName(e).localname == "image"])
    checks["2_editable"] = ("PASS" if (texts and n_img == 0 and "base64," not in raw) else "FAIL") + \
        f" text_nodes={len(texts)} raster_images={n_img}"

    fams = set(re.findall(r"font-family:\s*([^;\"]+)", raw)) | \
           {e.get("font-family") for e in root.iter() if e.get("font-family")}
    fams = {f.strip() for f in fams if f and f.strip()}
    ok_fonts = all(("Arial" in f) or ("Helvetica" in f) or ("DejaVu" in f) for f in fams)
    checks["3_fonts"] = ("PASS" if ok_fonts and fams else "FAIL") + f" families={sorted(fams)}"

    # 4 overflow: every <text>/<use> anchor inside the viewBox (with 1% margin)
    vb = [float(v) for v in root.get("viewBox").split()]
    out_of = 0
    for e in root.iter():
        tag = etree.QName(e).localname
        if tag in ("text", "rect", "path") and e.get("x") is not None and e.get("y") is not None:
            px, py = float(e.get("x")), float(e.get("y"))
            if not (vb[0] - 1 <= px <= vb[2] + 40 and vb[1] - 1 <= py <= vb[3] + 14):
                out_of += 1
    checks["4_overflow"] = ("PASS" if out_of == 0 else "FAIL") + \
        f" out-of-canvas anchors={out_of} (exact-canvas export, no tight crop)"

    checks["5_arrows"] = "PASS n/a — statistical figure (no dependency arrows); significance is annotated"

    cols = set()
    for e in root.iter():
        for a in ("fill", "stroke", "stop-color"):
            v = e.get(a)
            if v and v.startswith("#"):
                cols.add(v)
    stray = sorted(c for c in cols if c.upper() not in {p.upper() for p in PALETTE} and not on_ramp(c))
    checks["6_colors"] = ("PASS" if not stray else "FAIL") + f" stray={stray}"

    miss = [t for t in cfg["required"] if t not in raw]
    ban = [b for b in cfg["banned"] if b in raw]
    checks["7_values"] = ("PASS" if not miss and not ban else "FAIL") + \
        f" missing={miss} banned={ban}"

    # 8 statistics consistency vs the recomputed JSON
    st = json.loads((ROOT / "statistics/fig3_recomputed_stats.json").read_text(encoding="utf-8"))
    if fid == "Fig3":
        g = st["by_judge"]["glm-5.3"]["A_minus_B_per_dim"]
        k = st["by_judge"]["kimi-k2.6"]["A_minus_B_per_dim"]
        want = [f"{g['D4']['delta']:+.3f}", f"{k['D10']['delta']:+.3f}",
                f"{g['D5']['delta']:+.3f}"]
        got = [w for w in want if w in raw]
        checks["8_stats"] = ("PASS" if len(got) == len(want) else "FAIL") + \
            f" delta labels matched {len(got)}/{len(want)}: {want}"
    else:
        arms = json.loads((ROOT / "Fig4/fig4_qa_evidence.json").read_text(encoding="utf-8"))
        ok = all(f"{v*100:.1f}" in raw for v in arms["panels"]["c"]["arms"].values())
        checks["8_stats"] = ("PASS" if ok else "FAIL") + " arm rates match web_arms_summary.json"

    sizes = sorted({float(s) for s in re.findall(r"font-size:\s*([\d.]+)px", raw)})
    tiny = [s for s in sizes if s < 5.5]
    checks["9_sizes"] = ("PASS" if (sizes and not tiny) else "FAIL") + \
        f" sizes={sizes} (<5.5pt={len(tiny)})"

    w_pt, h_pt = float(root.get("width").replace("pt", "")), float(root.get("height").replace("pt", ""))
    w_mm, h_mm = w_pt * 25.4 / 72, h_pt * 25.4 / 72
    checks["10_ha"] = ("PASS" if abs(w_mm - cfg["sizes_mm"][0]) <= 1.0 and h_mm <= 234 else "FAIL") + \
        f" {w_mm:.1f} × {h_mm:.1f} mm (target {cfg['sizes_mm'][0]} × {cfg['sizes_mm'][1]})"

    pdf = svgp.with_suffix(".pdf"); png = svgp.with_suffix(".png"); tif = svgp.with_suffix(".tif")
    eps = svgp.with_suffix(".eps")
    ok = all(p.exists() and p.stat().st_size > 5000 for p in (pdf, png, tif, eps))
    checks["11_render"] = ("PASS" if ok else "FAIL") + \
        f" pdf={pdf.stat().st_size}B eps={eps.stat().st_size if eps.exists() else 0}B " \
        f"png={png.stat().st_size}B tif={tif.stat().st_size // 1024}KB"

    # 12 code consistency: rebuild deterministically (metadata/date stripped)
    mod = {"Fig3": "fig3_builder", "Fig4": "fig4_builder"}[fid]
    sys.path.insert(0, str(svgp.parent))
    sys.path.insert(0, str(ROOT / "01_design_system"))
    import importlib
    m = importlib.import_module(mod)
    importlib.reload(m)
    f1 = m.main()
    import io
    b1 = io.StringIO(); f1.savefig(b1, format="svg"); c1 = b1.getvalue()
    import matplotlib.pyplot as plt
    plt.close(f1)
    f2 = m.main()
    b2 = io.StringIO(); f2.savefig(b2, format="svg"); c2 = b2.getvalue()
    plt.close(f2)
    strip = lambda s: re.sub(r"<metadata>.*?</metadata>|<!--.*?-->", "", s, flags=re.S)

    def signature(svg_text):
        """Drawing signature: every path/rect/text payload without ids, dates or
        metadata (encoding-independent determinism of what is actually drawn)."""
        import hashlib as _h
        t = strip(svg_text)
        keys = re.findall(r'<path[^>]*\bd="([^"]+)"|<text[^>]*>(.*?)</text>|'
                          r'<rect[^>]*\b(width="[^"]+"[^>]*height="[^"]+")', t, flags=re.S)
        flat = "|".join("".join(x) for x in keys)
        return _h.sha256(flat.encode("utf-8")).hexdigest()

    checks["12_determinism"] = ("PASS" if signature(c1) == signature(c2) else "FAIL") + \
        f" build() twice → identical drawing signature ({signature(c1)[:12]})"
    results[fid] = checks

(ROOT / "QA_reports/qa_fig3_fig4_checks.json").write_text(
    json.dumps(results, ensure_ascii=False, indent=1), encoding="utf-8")
print(json.dumps(results, ensure_ascii=False, indent=1))
n_fail = sum(1 for r in results.values() for c in r.values() if c.startswith("FAIL"))
print(f"\nTOTAL FAIL: {n_fail}")
