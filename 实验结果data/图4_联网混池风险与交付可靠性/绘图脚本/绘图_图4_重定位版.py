# -*- coding: utf-8 -*-
"""Fig. 4 重绘（计划 v2）· 两个面板各自独立成文件，**不写面板字母**

  图4a_旧架构联网混入风险：旧混合仲裁架构（2026-09-23/24）五臂，
      左列绝对「judge-flagged hallucination rate」、右列相对不联网基线的差值、最右 n/N
  图4b_新架构结构核验：v11（2026-09-26）两组审计指标
      Source-boundary compliance（回答级 / 次数级）＋ Citation integrity（标记级）

数据来源：
  4a：../数据/图4_核验_数据溯源.json（五臂逐题复算，与冻结件逐位一致）
  4b：07_交付与文档_delivery_docs\04_数据_data\审计_audit\fig4_v11_structural_audit.csv
"""
from __future__ import annotations

import csv
import json
import pathlib
import sys

HERE = pathlib.Path(__file__).resolve().parent
DESIGN_SYSTEM = pathlib.Path(
    r"E:\codex_WORKSPACES\Citrus_QA_Agent\CitrusQA_HA_Figures\01_design_system")
SKILL_SCRIPTS = pathlib.Path(r"C:\Users\Administrator\.dsh\skills\nature-figure\scripts")
for p in (str(DESIGN_SYSTEM), str(SKILL_SCRIPTS)):
    if p not in sys.path:
        sys.path.insert(0, p)
import fig_style_ha as S  # noqa: E402
from audit_panel_alignment import require_matplotlib_panel_alignment  # noqa: E402

import matplotlib as mpl  # noqa: E402
import matplotlib.pyplot as plt  # noqa: E402
from matplotlib.patches import Ellipse, Rectangle  # noqa: E402

DATA_A = HERE.parent / "数据" / "图4_核验_数据溯源.json"
AUDIT_CSV = (pathlib.Path(r"E:\codex_WORKSPACES\Citrus_QA_Agent\07_交付与文档_delivery_docs")
             / "04_数据_data" / "审计_audit" / "fig4_v11_structural_audit.csv")
OUTDIR = HERE.parent / "绘图产物"

ARMS = [("A", "Local-only"), ("B", "Unfiltered"), ("C", "Receipt-level filtering"),
        ("C'", "Model-visible filtering"), ("D", "Excerpt-only filtering")]
DARK_ORANGE = "#A96049"


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


# ────────────────────────────────────────────── Panel a
def panel_a(snap):
    S.use_style()
    arms = snap["arms"]
    fig = S.open_fig(174, 76)
    ax = fig.add_axes([0.02, 0.04, 0.96, 0.90])
    ax.set_axis_off()
    # 数据坐标：左条区 0–3.6（映射 0–40%）、右条区 4.5–7.5（映射 0–35pp）、计数列 8.7
    LX0, LX1 = 0.0, 3.6
    RX0, RX1 = 4.5, 7.5
    RATE_MAX, DELTA_MAX = 40.0, 35.0
    ax.set_xlim(-3.05, 10.2)
    ax.set_ylim(6.75, -1.85)

    for r, (tag, label) in enumerate(ARMS):
        y = r
        d = arms[tag]
        rate = d["rate"]
        col = S.NEUTRAL["grey"] if tag == "A" else (DARK_ORANGE if tag == "C" else S.CORE["orange"])
        ax.add_patch(Rectangle((LX0, y - 0.22), (LX1 - LX0) * rate / RATE_MAX, 0.44,
                               facecolor=col, edgecolor="none"))
        ax.text(LX1 + 0.14, y, f"{rate:.1f}%", ha="left", va="center",
                fontsize=S.FS["value"], color=S.NEUTRAL["text"])

        if tag == "A":
            ax.text(RX0 + 0.06, y, "Reference", ha="left", va="center",
                    fontsize=S.FS["value"], color=S.NEUTRAL["text2"])
        else:
            delta = rate - arms["A"]["rate"]
            ax.add_patch(Rectangle((RX0, y - 0.22), (RX1 - RX0) * delta / DELTA_MAX, 0.44,
                                   facecolor=col, edgecolor="none"))
            ax.text(RX1 + 0.14, y, f"{delta:+.1f} pp", ha="left", va="center",
                    fontsize=S.FS["value"], color=S.NEUTRAL["text"])

        ax.text(8.75, y, f"{d['h']}/{d['n']}", ha="left", va="center",
                fontsize=S.FS["value"], color=S.NEUTRAL["text2"])
        ax.text(-0.12, y, f"{tag} · {label}", ha="right", va="center",
                fontsize=S.FS["value"], color=S.NEUTRAL["text"])

    # A 与其余四组的细分隔线
    ax.plot([LX0, 8.75 + 0.9], [0.62, 0.62], color=S.NEUTRAL["border"], lw=0.7)

    # 组表头
    ax.text(LX0, -0.98, "Judge-flagged hallucination rate (%)", ha="left", va="center",
            fontsize=S.FS["legend"], color=S.NEUTRAL["text2"])
    ax.text(RX0, -0.98, "Difference vs local-only (pp)", ha="left", va="center",
            fontsize=S.FS["legend"], color=S.NEUTRAL["text2"])
    ax.text(8.75, -0.98, "Events / verdicts", ha="left", va="center",
            fontsize=S.FS["legend"], color=S.NEUTRAL["text2"])

    # 两条小刻度尺
    for x0, x1, vmax, step in ((LX0, LX1, RATE_MAX, 10), (RX0, RX1, DELTA_MAX, 5)):
        ax.plot([x0, x1], [4.62, 4.62], color=S.NEUTRAL["border"], lw=0.7)
        v = 0
        while v <= vmax:
            xx = x0 + (x1 - x0) * v / vmax
            ax.plot([xx, xx], [4.62, 4.74], color=S.NEUTRAL["border"], lw=0.7)
            ax.text(xx, 4.86, f"{v}", ha="center", va="top",
                    fontsize=S.FS["tick"], color=S.NEUTRAL["text2"])
            v += step

    ax.text(-0.12, 5.55, "Old architecture (pre-separation) · 2026-09-23/24",
            ha="left", va="center", fontsize=S.FS["note"], color=S.NEUTRAL["text2"])
    ax.text(-0.12, 6.15, "48-question subset · two independent judges · 96 verdicts per arm "
                         "(differences are descriptive)",
            ha="left", va="center", fontsize=S.FS["note"], color=S.NEUTRAL["text2"])
    return fig


# ────────────────────────────────────────────── Panel b
def panel_b():
    S.use_style()
    with AUDIT_CSV.open(encoding="utf-8-sig") as f:
        rows = list(csv.DictReader(f))

    def find(group, key):
        for r in rows:
            if r["指标组"].startswith(group) and key in r["指标名"]:
                return r
        raise KeyError(key)

    left = [
        ("Local-section markers · answers", find("A", "本地段出现联网编号的回答数"), True),
        ("Local-section markers · occurrences", find("A", "本地段联网编号出现次数"), False),
        ("Web-section markers · occurrences", find("A", "联网段联网编号出现次数"), False),
        ("Pool C answers meeting the rule", find("A", "Pool C 分段引用合规的回答数"), True),
        ("Pool C answers with a web citation", find("A", "联网段携带联网编号的回答数"), "dots"),
    ]
    right = [
        ("Citation markers", find("B", "正文引用标记总数"), False),
        ("Resolvable", find("B", "可解析标记数"), True),
        ("Out-of-pool", find("B", "交付文本内池外引用数"), True),
    ]

    fig = S.open_fig(174, 78)
    ax = fig.add_axes([0.02, 0.05, 0.96, 0.88])
    ax.set_axis_off()
    ax.set_xlim(0, 174)          # 数据坐标 = 毫米
    ax.set_ylim(80, -22)

    # 圆点在数据坐标下会被拉成椭圆（x/y 比例不同），故按轴的实际 mm/单位换算成正圆
    FIG_W_MM, FIG_H_MM = 174.0, 78.0
    AX_X, AX_Y, AX_W, AX_H = 0.02, 0.05, 0.96, 0.88
    Y_SPAN = 80 - (-22)
    DOT_ASPECT = ((FIG_H_MM * AX_H) / Y_SPAN) / ((FIG_W_MM * AX_W) / 174.0)

    def dot_grid(x0, x1, y, n_filled, n_total, per_row=16):
        """单位图：每个点 = 一条回答（Pool C 共 48 条）。实心＝该答联网段带编号；
        空心＝未带编号（末位）。点序：47 条带编号者在前，未带编号的 1 条置于末位。"""
        pitch = (x1 - x0) / per_row
        r = pitch * 0.40
        for k in range(n_total):
            row, col = divmod(k, per_row)
            cx = x0 + pitch * (col + 0.5)
            cy = y + (row - (n_total - 1) // per_row / 2.0) * (pitch * 1.25)
            filled = k < n_filled
            ax.add_patch(Ellipse((cx, cy), 2 * r, 2 * r * DOT_ASPECT,
                                 facecolor=S.CORE["orange"] if filled else S.NEUTRAL["grey_light"],
                                 edgecolor="none"))

    def block(lab_r, x0, xw, items, header, unit_note, val_fmt):
        ax.text(lab_r - 21.0, -17.0, header, ha="left", va="center",
                fontsize=S.FS["title"], color=S.NEUTRAL["text"])
        ax.text(lab_r - 21.0, -11.0, unit_note, ha="left", va="center",
                fontsize=S.FS["note"], color=S.NEUTRAL["text2"])
        ax.plot([x0, x0 + xw + 14], [-6.0, -6.0], color=S.NEUTRAL["border"], lw=0.7)
        for i, (label, row, kind) in enumerate(items):
            y = i * 9.0
            if kind == "dots":
                dot_grid(x0, x0 + xw, y, int(row["分子"]), int(row["分母"]))
            else:
                ax.add_patch(Rectangle((x0, y - 2.0), xw, 4.0,
                                       facecolor=S.NEUTRAL["na"], edgecolor="none"))
                if kind is True:
                    pct = float(row["结果"].rstrip("%"))
                    ax.add_patch(Rectangle((x0, y - 2.0), xw * pct / 100.0, 4.0,
                                           facecolor=S.CORE["green"], edgecolor="none"))
            ax.text(x0 + xw + 2.0, y, val_fmt(row), ha="left", va="center",
                    fontsize=S.FS["value"], color=S.NEUTRAL["text"])
            ax.text(lab_r, y, label, ha="right", va="center",
                    fontsize=S.FS["value"], color=S.NEUTRAL["text"])
        return len(items)

    val = lambda r: f"{r['分子']}/{r['分母']}" if str(r["分母"]) not in ("", "—") else str(r["分子"])  # noqa: E731
    block(46.0, 48.0, 28.0, left, "Source-boundary compliance",
          "answers · occurrences (144 answers)", val)
    block(118.0, 120.0, 28.0, right, "Citation integrity",
          "citation markers (84 answers)", val)

    ax.text(0, 52.0, "New separated output · v11 · 2026-09-26.",
            ha="left", va="center", fontsize=S.FS["note"], color=S.NEUTRAL["text2"])
    ax.text(0, 59.0, "Structural traceability only: resolvable markers are linked to pool sources; "
                     "this does not establish semantic support.",
            ha="left", va="center", fontsize=S.FS["note"], color=S.NEUTRAL["text2"])
    ax.text(0, 66.0, "The two groups use different statistical units and must not be combined "
                     "into one compliance score.",
            ha="left", va="center", fontsize=S.FS["note"], color=S.NEUTRAL["text2"])
    ax.text(0, 73.0, "Pool C dots: one per answer (48); the unfilled one corrected the premise "
                     "(C48-new-33) and has no numbered citation.",
            ha="left", va="center", fontsize=S.FS["note"], color=S.NEUTRAL["text2"])
    return fig


def main() -> int:
    explicit_rcparams()
    snap = json.loads(DATA_A.read_text(encoding="utf-8"))
    export(panel_a(snap), "图4a_旧架构联网混入风险")
    export(panel_b(), "图4b_新架构结构核验")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
