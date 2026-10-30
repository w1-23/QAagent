# -*- coding: utf-8 -*-
"""附图 S1–S4 · 六个面板各自独立成文件（**不写面板字母**）

  图S1_检索三通道点图            纯中文 / 带锚点 / 英文原问 × 稠密·词法·融合
  图S2a/S2b_多比较森林图_*        A−B/A−C/A−D/A−E × D3–D10（逐判官）
  图S3_判官一致性点图             exact / qwk × D1–D10（D1/D2 加 *）
  图S4a_联网候选过滤漏斗          672 → 125 → 111
  图S4b_留存来源分级              全部 vs 留存 的 T1/T2/T3 结构

数据来自 ../数据/附图_核验_数据溯源.json；脚本内无硬编码数值。
"""
from __future__ import annotations

import json
import pathlib
import sys

HERE = pathlib.Path(__file__).resolve().parent
DESIGN_SYSTEM = pathlib.Path(
    r"E:\codex_WORKSPACES\Citrus_QA_Agent\CitrusQA_HA_Figures\01_design_system")
sys.path.insert(0, str(DESIGN_SYSTEM))
import fig_style_ha as S  # noqa: E402

import matplotlib as mpl  # noqa: E402
import matplotlib.pyplot as plt  # noqa: E402
from matplotlib.lines import Line2D  # noqa: E402
from audit_panel_alignment import require_matplotlib_panel_alignment  # noqa: E402

DATA = HERE.parent / "数据" / "附图_核验_数据溯源.json"
OUTDIR = HERE.parent / "绘图产物"

Q_LABEL = {"zh_Z": "ZH query (no anchor)", "zh_A": "ZH query + EN anchor", "raw": "EN query (reference)"}
Q_COLOR = {"zh_Z": S.CORE["blue"], "zh_A": S.CORE["green"], "raw": S.NEUTRAL["grey"]}
COMPARE = ["A − B", "A − C", "A − D", "A − E"]
C_COLOR = {"A − B": S.CORE["blue"], "A − C": S.CORE["green"],
           "A − D": S.CORE["orange"], "A − E": S.CORE["gold"]}


def explicit_rcparams() -> None:
    mpl.rcParams.update({
        "font.family": "Arial",
        "font.sans-serif": ["Arial", "DejaVu Sans"],
        "font.size": 8,
        "svg.fonttype": "none",
        "pdf.fonttype": 42,
    })


def export(fig, name: str) -> None:
    out = OUTDIR / name
    # 渲染期面板对齐门（nature-figure 必需；单面板 → NOT APPLICABLE）
    require_matplotlib_panel_alignment(
        fig, json_out=f"{out}.alignment.json", overlay_svg=f"{out}.alignment.svg",
        tolerance_pt=1.5, gutter_tolerance_pt=1.5, strict=True)
    fig.savefig(str(out) + ".svg", bbox_inches=None, pad_inches=0)
    fig.savefig(str(out) + ".pdf", bbox_inches=None, pad_inches=0)
    fig.savefig(str(out) + ".eps", bbox_inches=None, pad_inches=0)
    fig.savefig(str(out) + ".png", dpi=600, bbox_inches=None, pad_inches=0)
    fig.savefig(str(out) + ".tiff", dpi=600, bbox_inches=None, pad_inches=0)
    plt.close(fig)
    print("WROTE", str(out) + ".svg / .pdf / .eps / .tiff / .png")


def fig_s1(snap):
    S.use_style()
    s1 = snap["s1_channels"]
    chans = [("dense", "Dense"), ("bm25", "BM25"), ("fused", "Fused (RRF)")]
    qs = ["zh_Z", "zh_A", "raw"]
    fig = S.open_fig(174, 74)
    ax = fig.add_axes([0.085, 0.30, 0.875, 0.565])
    n = len(qs)
    w = 0.24
    for qi, q in enumerate(qs):
        for ci, (key, _) in enumerate(chans):
            x = ci + (qi - (n - 1) / 2) * w
            v = s1[q][key]
            col = Q_COLOR[q]
            ax.plot([x], [v], marker="o", ms=5.6, zorder=3,
                    markerfacecolor=col if qi == 1 else S.NEUTRAL["canvas"],
                    markeredgecolor=col, markeredgewidth=1.2)
            ax.text(x, v + 2.6, f"{v:.0f}", ha="center", va="bottom",
                    fontsize=S.FS["value"], color=S.NEUTRAL["text"])
    ax.set_xticks(range(len(chans)))
    ax.set_xticklabels([lab for _, lab in chans], fontsize=S.FS["value"], color=S.NEUTRAL["text"])
    ax.set_xlim(-0.5, len(chans) - 0.5)
    ax.set_ylim(0, 100)
    ax.set_yticks([0, 25, 50, 75, 100])
    ax.set_ylabel("Gold hit rate in top 20 (%)")
    S.finish(ax, ygrid_only=True)
    handles = [Line2D([], [], marker="o", ls="", ms=5.6,
                      markerfacecolor=Q_COLOR[q] if q == "zh_A" else S.NEUTRAL["canvas"],
                      markeredgecolor=Q_COLOR[q], markeredgewidth=1.2, label=Q_LABEL[q])
               for q in qs]
    ax.legend(handles=handles, loc="upper center", bbox_to_anchor=(0.5, -0.155), ncol=3,
              frameon=False, fontsize=S.FS["legend"], handletextpad=0.4, columnspacing=1.6)
    return fig


def fig_s2(snap, judge: str):
    S.use_style()
    data = snap["s2_forest"][judge]
    dims = [d for d in [f"D{i}" for i in range(3, 11)]]
    fig = S.open_fig(174, 92)
    ax = fig.add_axes([0.115, 0.245, 0.815, 0.66])
    ys = list(range(len(dims)))[::-1]
    m = len(COMPARE)
    off = [(-(m - 1) / 2 + i) * 0.17 for i in range(m)]
    for y, dim in zip(ys, dims):
        for k, label in enumerate(COMPARE):
            e = data.get(label, {}).get(dim)
            if not e:
                continue
            col = C_COLOR[label]
            d, lo, hi = e["delta"], e["delta"] - e["mdd"], e["delta"] + e["mdd"]
            yy = y + off[k]
            ax.plot([lo, hi], [yy, yy], color=col, lw=0.9, solid_capstyle="butt", zorder=3)
            for xx in (lo, hi):
                ax.plot([xx, xx], [yy - 0.06, yy + 0.06], color=col, lw=0.9, zorder=3)
            ax.plot([d], [yy], marker="o", ms=4.2, zorder=4, markerfacecolor=col, markeredgecolor=col)
    ax.axvline(0, color="#333333", lw=0.7, ls="--", zorder=1)
    ax.set_yticks(ys)
    ax.set_yticklabels(dims, fontsize=S.FS["value"], color=S.NEUTRAL["text"])
    ax.set_xlabel("Paired score difference vs Track A  (Δ)")
    ax.set_title(S.JUDGE_LABEL[judge], loc="left", fontsize=S.FS["title"], color=S.NEUTRAL["text"])
    lo_all = min(v["delta"] - v["mdd"] for lbl in data.values() for v in lbl.values())
    hi_all = max(v["delta"] + v["mdd"] for lbl in data.values() for v in lbl.values())
    ax.set_xlim(lo_all - 0.12, hi_all + 0.12)
    ax.set_ylim(-0.75, len(dims) - 0.35)
    S.finish(ax, ygrid_only=False)
    ax.grid(False)
    handles = [Line2D([], [], marker="o", ls="", ms=4.2, markerfacecolor=C_COLOR[l],
                      markeredgecolor=C_COLOR[l], label=l) for l in COMPARE]
    ax.legend(handles=handles, loc="upper center", bbox_to_anchor=(0.5, -0.105), ncol=4,
              frameon=False, fontsize=S.FS["legend"], handletextpad=0.4, columnspacing=1.6)
    return fig


def fig_s3(snap):
    S.use_style()
    ag = snap["s3_agreement"]
    dims = [f"D{i}" for i in range(1, 11)]
    fig = S.open_fig(174, 70)
    ax = fig.add_axes([0.075, 0.245, 0.90, 0.62])
    xs = list(range(len(dims)))
    for x, d in zip(xs, dims):
        e, q = ag[d]["exact"], ag[d]["qwk"]
        ax.plot([x, x], [e, q], color=S.NEUTRAL["border"], lw=0.9, zorder=1)
        ax.plot([x], [e], marker="o", ms=5.4, zorder=3, markerfacecolor=S.CORE["blue"],
                markeredgecolor=S.CORE["blue"])
        ax.plot([x], [q], marker="o", ms=5.4, zorder=3, markerfacecolor=S.CORE["green"],
                markeredgecolor=S.CORE["green"])
    ax.axhline(0, color="#333333", lw=0.6, ls="--", zorder=1)
    ax.set_xticks(xs)
    # D1/D2 可配对样本少 → 直接并入刻度标签，避免额外文本与刻度重叠
    ax.set_xticklabels([f"{d} *" if d in ("D1", "D2") else d for d in dims],
                       fontsize=S.FS["value"], color=S.NEUTRAL["text"])
    ax.set_xlim(-0.5, len(dims) - 0.5)
    ax.set_ylim(-0.12, 1.0)
    ax.set_yticks([0, 0.25, 0.5, 0.75, 1.0])
    ax.set_ylabel("Inter-judge agreement")
    S.finish(ax, ygrid_only=True)
    handles = [Line2D([], [], marker="o", ls="", ms=5.4, markerfacecolor=S.CORE["blue"],
                      markeredgecolor=S.CORE["blue"], label="Exact agreement"),
               Line2D([], [], marker="o", ls="", ms=5.4, markerfacecolor=S.CORE["green"],
                      markeredgecolor=S.CORE["green"], label="Quadratic weighted κ")]
    ax.legend(handles=handles, loc="upper center", bbox_to_anchor=(0.5, -0.135), ncol=2,
              frameon=False, fontsize=S.FS["legend"], handletextpad=0.4, columnspacing=1.8)
    return fig


def fig_s4a(snap):
    S.use_style()
    w = snap["s4_web"]
    fig = S.open_fig(174, 58)
    ax = fig.add_axes([0.05, 0.20, 0.92, 0.66])
    ax.set_axis_off()
    stages = [("Fetched", w["n_items"], S.NEUTRAL["grey_light"]),
              ("Content gate", w["content_out"], S.CORE["orange"]),
              ("Metadata gate", w["meta_out"], S.CORE["green"])]
    total = w["n_items"]
    x0, x1 = 0.02, 0.72
    hgt = 0.17
    for i, (name, val, col) in enumerate(stages):
        y = 0.80 - i * 0.29
        ax.add_patch(plt.Rectangle((x0, y - hgt / 2), (x1 - x0), hgt,
                                   facecolor=col, edgecolor="none", transform=ax.transAxes))
        ax.text(x0 + 0.012, y, name, ha="left", va="center", fontsize=S.FS["label"],
                color="#FFFFFF" if i > 0 else S.NEUTRAL["text"], transform=ax.transAxes)
        ax.text(x1 + 0.02, y, f"{val}", ha="left", va="center", fontsize=S.FS["value"] + 2,
                fontweight="bold", color=S.NEUTRAL["text"], transform=ax.transAxes)
    ax.text(x0, 0.055, f"48 questions · {total} web candidates → {w['meta_out']} kept "
                       f"(content gate −{w['drop_content']}, metadata gate −{w['drop_meta']})",
            ha="left", va="center", fontsize=S.FS["note"], color=S.NEUTRAL["text2"],
            transform=ax.transAxes)
    return fig


def fig_s4b(snap):
    S.use_style()
    w = snap["s4_web"]
    tiers = ["T1", "T2", "T3"]
    colors = [S.NEUTRAL["grey"], S.NEUTRAL["grey_light"], S.CORE["orange"]]
    sets = [("All candidates", w["tier_all"]), ("Kept", w["tier_kept"])]
    fig = S.open_fig(174, 56)
    ax = fig.add_axes([0.20, 0.30, 0.70, 0.50])
    for i, (name, dist) in enumerate(sets):
        tot = sum(dist.values())
        left = 0.0
        for t, c in zip(tiers, colors):
            v = dist.get(t, 0) / tot * 100
            # 白色细分隔线：T1/T2 两个中性灰 ΔE 仅 9.1，靠分隔线保证相邻段可分辨
            ax.add_patch(plt.Rectangle((left, i - 0.28), v, 0.56, facecolor=c,
                                       edgecolor="#FFFFFF", linewidth=0.8))
            if v > 7:
                ax.text(left + v / 2, i, f"{dist.get(t, 0)}", ha="center", va="center",
                        fontsize=S.FS["value"], color="#FFFFFF" if c == S.CORE["orange"] else S.NEUTRAL["text"])
            left += v
    ax.set_yticks([0, 1])
    ax.set_yticklabels([n for n, _ in sets], fontsize=S.FS["value"], color=S.NEUTRAL["text"])
    ax.set_xlim(0, 100)
    ax.set_ylim(-0.6, 1.6)
    ax.set_xticks([0, 25, 50, 75, 100])
    ax.set_xlabel("Source tier composition (%)")
    ax.tick_params(length=0)
    ax.grid(False)
    for sp in ("top", "right", "bottom", "left"):
        ax.spines[sp].set_visible(False)
    handles = [Line2D([], [], marker="s", ls="", ms=6.5, markerfacecolor=c, markeredgecolor=c,
                      label=t) for t, c in zip(tiers, colors)]
    ax.legend(handles=handles, loc="lower center", bbox_to_anchor=(0.5, -0.62), ncol=3,
              frameon=False, fontsize=S.FS["legend"], handletextpad=0.4, columnspacing=1.6)
    return fig


def main() -> int:
    snap = json.loads(DATA.read_text(encoding="utf-8"))
    explicit_rcparams()
    export(fig_s1(snap), "图S1_检索三通道点图")
    export(fig_s2(snap, "glm-5.3"), "图S2a_多比较森林图_GLM")
    export(fig_s2(snap, "kimi-k2.6"), "图S2b_多比较森林图_Kimi")
    export(fig_s3(snap), "图S3_判官一致性点图")
    export(fig_s4a(snap), "图S4a_联网候选过滤漏斗")
    export(fig_s4b(snap), "图S4b_留存来源分级")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
