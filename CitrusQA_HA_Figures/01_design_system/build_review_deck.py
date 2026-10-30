# -*- coding: utf-8 -*-
"""组图审阅母版：把各面板 PNG 按 174 mm 实际宽度排入 PPT，每面板一页。

跨图件共享工具，放在设计系统目录（与 fig_style_ha.py 同处），避免在每个图目录里重复一份。

规范：`paper/图件方案_CitrusQA_HA投稿版.md` §5.3 —— 组图与后置标注走 ppt-studio；
面板字母、跨面板说明等**不入图**的元素在 PPT 里后置添加（本脚本不添加字母，
只保证每页以 174 mm 实际尺寸呈现，便于按最终尺寸审核字号与留白）。

产出（写入各自图目录的 绘图产物/）：
  图4_组图_审阅.pptx · 图3_候选_组图_审阅.pptx · 附图_组图_审阅.pptx
"""
from __future__ import annotations

import pathlib

from pptx import Presentation
from pptx.util import Mm, Pt
from PIL import Image

EXT = pathlib.Path(r"E:\codex_WORKSPACES\Citrus_QA_Agent\实验结果data")
GROUPS = [
    ("图4_联网混池风险与交付可靠性", "图4_组图_审阅.pptx", [
        "图4a_旧架构联网混入风险", "图4b_新架构结构核验"]),
    ("图3_候选_语言补偿与能力比较", "图3_候选_组图_审阅.pptx", [
        "图3a_跨语言四配置点图", "图3b_五配置十维热图_GLM", "图3c_五配置十维热图_Kimi",
        "图3d_配对效应森林图_GLM", "图3e_配对效应森林图_Kimi",
        "图3乙_五配置热图_GLM", "图3乙_五配置热图_Kimi"]),
    ("附图_S1-S4", "附图_组图_审阅.pptx", [
        "图S1_检索三通道点图", "图S2a_多比较森林图_GLM", "图S2b_多比较森林图_Kimi",
        "图S3_判官一致性点图", "图S4a_联网候选过滤漏斗", "图S4b_留存来源分级"]),
]
TARGET_W_MM = 174.0          # HA 双栏通栏宽度
SLIDE_W_MM, SLIDE_H_MM = 297.0, 210.0   # A4 横向（审阅母版）


def main() -> int:
    for folder, out_name, panels in GROUPS:
        outdir = EXT / folder / "绘图产物"
        prs = Presentation()
        prs.slide_width = Mm(SLIDE_W_MM)
        prs.slide_height = Mm(SLIDE_H_MM)
        blank = prs.slide_layouts[6]
        n = 0
        for name in panels:
            png = outdir / f"{name}.png"
            if not png.exists():
                print("MISSING", png)
                continue
            w_px, h_px = Image.open(png).size
            w_mm = TARGET_W_MM
            h_mm = w_mm * h_px / w_px
            if h_mm > SLIDE_H_MM - 34:
                scale = (SLIDE_H_MM - 34) / h_mm
                h_mm, w_mm = h_mm * scale, w_mm * scale
            slide = prs.slides.add_slide(blank)
            tb = slide.shapes.add_textbox(Mm(12), Mm(6), Mm(SLIDE_W_MM - 24), Mm(10))
            run = tb.text_frame.paragraphs[0].add_run()
            run.text = name.replace("_", " ")
            run.font.size = Pt(12)
            run.font.name = "Arial"
            slide.shapes.add_picture(str(png), Mm((SLIDE_W_MM - w_mm) / 2), Mm(22),
                                     width=Mm(w_mm), height=Mm(h_mm))
            n += 1
        out = outdir / out_name
        prs.save(str(out))
        print(f"WROTE {out}  ({n} slides)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
