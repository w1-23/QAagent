# -*- coding: utf-8 -*-
"""2026927latest 共享样式模块 —— 11 色莫兰迪语义色板 + 原生可编辑 PPT 图表/图形助手。

约束（用户制定）：
  · 全部图为 PPT 原生对象（图表可改数据、形状可拖动），不落 PNG、不出 PDF。
  · 单图 ≤5 色；浅色（3/5/6/7）白底必须加 #333333 细边框。
  · 双判官 kimi / glm 一律左右分面，禁止用颜色区分判官。
  · 白底、极浅网格；坐标轴 #333333；图例无边框。
  · 字体：中文 微软雅黑 / 拉丁 Times New Roman。

数据图版式：16:9（33.867×19.05 cm），标题 + 灰色导读 + 图区 + 底部数据源行。
"""

import os
import math
from lxml import etree

from pptx import Presentation
from pptx.util import Cm, Pt, Emu
from pptx.dml.color import RGBColor
from pptx.enum.shapes import MSO_SHAPE, MSO_CONNECTOR
from pptx.enum.text import MSO_ANCHOR, PP_ALIGN, MSO_AUTO_SIZE
from pptx.oxml.ns import qn
from pptx.chart.data import CategoryChartData
from pptx.enum.chart import (
    XL_CHART_TYPE, XL_LEGEND_POSITION, XL_LABEL_POSITION, XL_TICK_MARK,
    XL_MARKER_STYLE,
)
from pptx.enum.dml import MSO_LINE_DASH_STYLE
from PIL import ImageFont

# ════════════════════════════════════════════════════════════════════════
# 1. 全局 11 色色板与语义映射
# ════════════════════════════════════════════════════════════════════════

SEQ = ["#7b95c6", "#49c2d9", "#a1d8e8", "#67a583", "#a2c986", "#d0e2c0",
       "#fded95", "#ffc1a6", "#f59c7c", "#f47254", "#c85e62"]
C1, C2, C3, C4, C5, C6, C7, C8, C9, C10, C11 = SEQ
# revised ordered palette (task book v3): S1..S8
S1, S2, S3, S4, S5, S6, S7, S8 = ("#49c2d9", "#a1d8e8", "#a2c986", "#d0e2c0",
                                  "#fded95", "#ffc1a6", "#f59c7c", "#f47254")
SEQ8 = [S1, S2, S3, S4, S5, S6, S7, S8]

INK = "#26343B"
INK_SOFT = "#52616A"
INK_MUTED = "#B9C2C7"
RULE = "#52616A"
FRAME = "#A6A6A6"
GRID = "#E5E5E5"

# —— 语义映射（用户逐图指定，勿改）——
SYS5 = {  # 五配置主色板（实验5 系统层）
    "citrusqa": C10,            # CitrusQA 主强调（加黑细边框）
    "same_base_trackA": C1,     # DeepSeek-A
    "same_base_trackB": C3,     # DeepSeek-B（浅色→加边框）
    "mainstream_trackA": C4,    # Qwen-max-A
    "mainstream_trackB": C6,    # Qwen-max-B（浅色→加边框）
}
SYS5_LABEL = {
    "citrusqa": "CitrusQA",
    "same_base_trackA": "DeepSeek-A",
    "same_base_trackB": "DeepSeek-B",
    "mainstream_trackA": "Qwen-max-A",
    "mainstream_trackB": "Qwen-max-B",
}
EXP1_CELLS = {"Z_Z": C1, "Z_E": C3, "E_Z": C2, "E_E": C5}      # 实验1 四格
ANCHOR_ON, ANCHOR_OFF = C2, C3                                  # 有/无锚点：实心 vs 空心
EXP2_COND = {"C1": C1, "C2": C3, "C3": C4, "C4": C10}           # 实验2 四条件
P_STRATA = {"P1": C6, "P2": C5, "P3": C4}                       # 位置分层 绿色阶梯
EXP3_RECIPE = {  # 实验3 九配方（其余组合条件 = 极浅绿，靠形状/边框区分）
    "raw": C10, "hyde": C1, "mq": C4, "summary": C11,
    "raw_hyde": C6, "raw_mq": C6, "hyde_summary": C6,
    "raw_hyde_mq": C6, "all_four": C6,
}
EXP3_LABEL = {"raw": "raw", "hyde": "HyDE", "mq": "mq", "summary": "sum",
              "raw_hyde": "r+H", "raw_mq": "r+mq", "hyde_summary": "H+sum",
              "raw_hyde_mq": "r+H+mq", "all_four": "all"}
CHANNELS = {"dense": C1, "bm25": C10, "fused": C2}              # 机制通道
GOLD_COND = {"G0": C1, "G1": C10, "G2": C11}                    # Gold 消融
FOREST = {"pos": C4, "neg": C11, "ns": C3}                      # 森林图 显著正/负/不显著
MDD_BAND = C6                                                   # MDD 灰带（30% 透明度）
DIVERGE = {"neg": C11, "zero": C7, "pos": C4}                   # 发散色板
WEB_ARMS = {"A": C1, "B": C10, "C": C11, "C'": C7, "D": C9}     # 联网五臂（A=基线冷色，代价=暖色）

NEED_EDGE = {C3, C5, C6, C7, C8}   # 浅色必须加 #333 细边框

# ════════════════════════════════════════════════════════════════════════
# 2. 基础工具
# ════════════════════════════════════════════════════════════════════════

NS_A = "http://schemas.openxmlformats.org/drawingml/2006/main"
NS_C = "http://schemas.openxmlformats.org/drawingml/2006/chart"


def rgb(hex_str):
    h = hex_str.lstrip("#")
    return RGBColor(int(h[0:2], 16), int(h[2:4], 16), int(h[4:6], 16))


def tint(hex_color, strength=0.32):
    h = hex_color.lstrip("#")
    r, g, b = (int(h[i:i + 2], 16) for i in (0, 2, 4))
    f = lambda c: int(round(255 - (255 - c) * strength))
    return "#%02X%02X%02X" % (f(r), f(g), f(b))


def shade(hex_color, strength=0.18):
    """加深（用于边框色 = 同色系深一档）。"""
    h = hex_color.lstrip("#")
    r, g, b = (int(h[i:i + 2], 16) for i in (0, 2, 4))
    f = lambda c: int(round(c * (1 - strength)))
    return "#%02X%02X%02X" % (f(r), f(g), f(b))


FONT_LATIN = "Arial"
FONT_CJK = "SimSun"

F_TIMES = r"C:\Windows\Fonts\times.ttf"
F_TIMES_BD = r"C:\Windows\Fonts\timesbd.ttf"
F_SONG = r"C:\Windows\Fonts\simsun.ttc"

_SCALE = 16
_fcache = {}


def _is_cjk(ch):
    o = ord(ch)
    return (0x2E80 <= o <= 0x9FFF or 0xF900 <= o <= 0xFAFF
            or 0x3000 <= o <= 0x303F or 0xFF00 <= o <= 0xFFEF)


def _font(size_pt, bold, cjk):
    key = (round(size_pt, 1), bold, cjk)
    if key not in _fcache:
        if cjk:
            # 宋体无独立粗体字库（Windows 合成加粗），量宽用同一文件
            path = F_SONG
        else:
            path = F_TIMES_BD if bold else F_TIMES
        _fcache[key] = ImageFont.truetype(path, int(round(size_pt * _SCALE)))
    return _fcache[key]


def text_w_cm(text, size_pt, bold=False):
    total = 0.0
    for ch in str(text):
        f = _font(size_pt, bold, _is_cjk(ch))
        total += f.getlength(ch)
    return total / _SCALE / 72.0 * 2.54


def wrap_cm(text, max_w_cm, size_pt, bold=False):
    out = []
    for para in str(text).split("\n"):
        if not para.strip():
            out.append("")
            continue
        toks, cur = [], ""
        for ch in para:
            if _is_cjk(ch) or ch == " ":
                if cur:
                    toks.append(cur)
                    cur = ""
                toks.append(ch)
            else:
                cur += ch
        if cur:
            toks.append(cur)
        line = ""
        for tk in toks:
            trial = line + tk
            if line and text_w_cm(trial.rstrip(), size_pt, bold) > max_w_cm:
                out.append(line.rstrip())
                line = "" if tk == " " else tk
            else:
                line = trial
        out.append(line.rstrip())
    return out


def line_h_cm(size_pt):
    return size_pt * 1.2 / 72.0 * 2.54


def set_cjk(font_obj, name=FONT_CJK):
    """在 Font 对象的 rPr 上补 <a:ea>，使图表中文不回退默认字体。"""
    try:
        rPr = font_obj._rPr
    except AttributeError:
        return
    latin = rPr.find(qn("a:latin"))
    ea = rPr.find(qn("a:ea"))
    if ea is None:
        ea = rPr.makeelement(qn("a:ea"), {"typeface": name})
        if latin is not None:
            latin.addnext(ea)
        else:
            rPr.append(ea)
    else:
        ea.set("typeface", name)


def _style_run(run, size_pt, bold=False, color=INK, italic=False, cjk=None):
    f = run.font
    f.size = Pt(size_pt)
    f.bold = bold
    f.italic = italic
    f.color.rgb = rgb(color)
    f.name = FONT_LATIN
    set_cjk(f, FONT_CJK)


# ════════════════════════════════════════════════════════════════════════
# 3. 幻灯片骨架
# ════════════════════════════════════════════════════════════════════════

SLIDE_W = Cm(33.867)
SLIDE_H = Cm(19.05)


def new_deck():
    prs = Presentation()
    prs.slide_width = SLIDE_W
    prs.slide_height = SLIDE_H
    return prs


def add_slide(prs, title, note="", source="", tag=""):
    """标准数据图页：标题 + 灰色导读 + 底部数据源 + 页角 tag。返回 slide。"""
    slide = prs.slides.add_slide(prs.slide_layouts[6])
    if title:
        add_text(slide, 1.0, 0.5, 26.0, 1.15, title, size=16, bold=True, color=INK)
    if note:
        add_text(slide, 1.0, 1.72, 30.0, 1.0, note, size=10.5, color=INK_MUTED)
    if source:
        add_text(slide, 1.0, 17.75, 27.0, 0.8, source, size=7, color=INK_MUTED)
    if tag:
        add_text(slide, 28.6, 17.75, 4.3, 0.8, tag, size=7, color=INK_MUTED, align="right")
    return slide


def add_text(slide, x, y, w, h, text, size=8, bold=False, color=INK,
             align="left", anchor="top", line_spacing=1.15, cjk=None,
             name=None, wrap=True):
    """x/y/w/h 单位 cm。返回 textbox 形状。"""
    tb = slide.shapes.add_textbox(Cm(x), Cm(y), Cm(w), Cm(h))
    if name:
        tb.name = name
    tf = tb.text_frame
    tf.word_wrap = wrap
    tf.auto_size = MSO_AUTO_SIZE.NONE
    tf.margin_left = tf.margin_right = tf.margin_top = tf.margin_bottom = 0
    tf.vertical_anchor = {"top": MSO_ANCHOR.TOP, "middle": MSO_ANCHOR.MIDDLE,
                          "bottom": MSO_ANCHOR.BOTTOM}[anchor]
    lines = str(text).split("\n")
    for i, ln in enumerate(lines):
        p = tf.paragraphs[0] if i == 0 else tf.add_paragraph()
        p.alignment = {"left": PP_ALIGN.LEFT, "center": PP_ALIGN.CENTER,
                       "right": PP_ALIGN.RIGHT}[align]
        p.line_spacing = line_spacing
        run = p.add_run()
        run.text = ln
        _style_run(run, size, bold, color, cjk=cjk)
    return tb


def add_rect(slide, x, y, w, h, fill=None, edge=None, lw=0.5, dash=None,
             rounded=False, radius=0.10, transparency=None, name=None):
    shp = slide.shapes.add_shape(
        MSO_SHAPE.ROUNDED_RECTANGLE if rounded else MSO_SHAPE.RECTANGLE,
        Cm(x), Cm(y), Cm(w), Cm(h))
    if rounded:
        try:
            shp.adjustments[0] = radius
        except Exception:
            pass
    if fill is None:
        shp.fill.background()
    else:
        shp.fill.solid()
        shp.fill.fore_color.rgb = rgb(fill)
        if transparency:
            _set_fill_transparency(shp.fill, transparency)
    if edge is None:
        shp.line.fill.background()
    else:
        shp.line.color.rgb = rgb(edge)
        shp.line.width = Pt(lw)
        if dash:
            shp.line.dash_style = dash
    shp.shadow.inherit = False
    if name:
        shp.name = name
    return shp


def _set_fill_transparency(fill, pct):
    """pct: 0-100 透明度。直接改 XML alpha。"""
    try:
        srgb = fill.fore_color._xFill.find(".//" + qn("a:srgbClr"))
        alpha = srgb.makeelement(qn("a:alpha"), {"val": str(int((100 - pct) * 1000))})
        srgb.append(alpha)
    except Exception:
        pass


def _set_fill_alpha(fill, alpha_val):
    """alpha_val: 0-100000（100000=不透明）。直接写 <a:alpha val="..."/>。"""
    try:
        srgb = fill.fore_color._xFill.find(".//" + qn("a:srgbClr"))
        for old in srgb.findall(qn("a:alpha")):
            srgb.remove(old)
        srgb.append(srgb.makeelement(qn("a:alpha"), {"val": str(int(alpha_val))}))
    except Exception:
        pass


def _set_arrow_end(conn, end=True, begin=False):
    ln = conn.line._get_or_add_ln()
    if begin:
        hd = ln.makeelement(qn("a:headEnd"), {"type": "triangle", "w": "med", "len": "med"})
        ln.append(hd)
    if end:
        tl = ln.makeelement(qn("a:tailEnd"), {"type": "triangle", "w": "med", "len": "med"})
        ln.append(tl)


def add_arrow(slide, x1, y1, x2, y2, color="#8A8A8A", lw=0.75, dash=None, name=None):
    conn = slide.shapes.add_connector(MSO_CONNECTOR.STRAIGHT, Cm(x1), Cm(y1), Cm(x2), Cm(y2))
    conn.line.color.rgb = rgb(color)
    conn.line.width = Pt(lw)
    if dash:
        conn.line.dash_style = dash
    _set_arrow_end(conn, end=True)
    conn.shadow.inherit = False
    if name:
        conn.name = name
    return conn


def add_line(slide, x1, y1, x2, y2, color=RULE, lw=0.5, dash=None, name=None):
    conn = slide.shapes.add_connector(MSO_CONNECTOR.STRAIGHT, Cm(x1), Cm(y1), Cm(x2), Cm(y2))
    conn.line.color.rgb = rgb(color)
    conn.line.width = Pt(lw)
    if dash:
        conn.line.dash_style = dash
    conn.shadow.inherit = False
    if name:
        conn.name = name
    return conn


def add_ellipse(slide, x, y, w, h, fill, edge=None, lw=0.5, name=None):
    shp = slide.shapes.add_shape(MSO_SHAPE.OVAL, Cm(x), Cm(y), Cm(w), Cm(h))
    if fill is None:
        shp.fill.background()
    else:
        shp.fill.solid()
        shp.fill.fore_color.rgb = rgb(fill)
    if edge is None:
        shp.line.fill.background()
    else:
        shp.line.color.rgb = rgb(edge)
        shp.line.width = Pt(lw)
    shp.shadow.inherit = False
    if name:
        shp.name = name
    return shp


def legend_chips(slide, x, y, entries, size=8, gap=0.55, horizontal=True,
                 box_w=0.5, box_h=0.28, line_h=0.5):
    """自绘图例（无边框）。entries = [(color, label) | (color, label, 'hollow'|'line')]"""
    cx, cy = x, y
    for ent in entries:
        color, label = ent[0], ent[1]
        mode = ent[2] if len(ent) > 2 else "solid"
        if mode == "line":
            add_line(slide, cx, cy + box_h / 2, cx + box_w, cy + box_h / 2, color=color, lw=1.5)
            add_ellipse(slide, cx + box_w / 2 - 0.09, cy + box_h / 2 - 0.09, 0.18, 0.18, color, color, 0.5)
        elif mode == "hollow":
            add_rect(slide, cx, cy, box_w, box_h, fill="FFFFFF", edge=color, lw=0.9)
        else:
            edge = shade(color) if color.lstrip("#").upper() in {c.lstrip("#").upper() for c in NEED_EDGE} else None
            add_rect(slide, cx, cy, box_w, box_h, fill=color, edge=edge or shade(color), lw=0.5)
        wlab = text_w_cm(label, size) + 0.2
        add_text(slide, cx + box_w + 0.12, cy - 0.06, wlab + 0.6, line_h, label, size=size,
                 color=INK_SOFT, anchor="middle")
        if horizontal:
            cx += box_w + 0.12 + wlab + gap
        else:
            cy += line_h
    return cx, cy


# ════════════════════════════════════════════════════════════════════════
# 4. 原生图表（可编辑数据）
# ════════════════════════════════════════════════════════════════════════

def _style_axis(axis, tick_size=7, grid=False, grid_color=GRID, line_color=RULE,
                num_fmt=None, title=None, title_size=8, log_base=None,
                y_min=None, y_max=None, major_unit=None):
    axis.has_major_gridlines = grid
    if grid:
        gl = axis.major_gridlines.format.line
        gl.color.rgb = rgb(grid_color)
        gl.width = Pt(0.3)
        gl.dash_style = MSO_LINE_DASH_STYLE.SQUARE_DOT
    axis.format.line.color.rgb = rgb(line_color)
    axis.format.line.width = Pt(0.5)
    axis.major_tick_mark = XL_TICK_MARK.INSIDE
    axis.minor_tick_mark = XL_TICK_MARK.NONE
    tl = axis.tick_labels
    tl.font.size = Pt(tick_size)
    tl.font.name = FONT_LATIN
    tl.font.color.rgb = rgb(RULE)
    set_cjk(tl.font)
    if num_fmt:
        tl.number_format = num_fmt
        tl.number_format_is_linked = False
    try:
        if y_min is not None:
            axis.minimum_scale = y_min
        if y_max is not None:
            axis.maximum_scale = y_max
        if major_unit is not None:
            axis.major_unit = major_unit
    except Exception:
        pass
    if log_base:
        scaling = axis._element.find(qn("c:scaling"))
        lb = scaling.makeelement(qn("c:logBase"), {"val": str(log_base)})
        scaling.insert(0, lb)
    if title:
        axis.has_title = True
        tf = axis.axis_title.text_frame
        tf.text = title
        for p in tf.paragraphs:
            for r in p.runs:
                _style_run(r, title_size, color=INK_SOFT)


def _style_chart_fonts(chart, tick=7.5):
    """图表占位默认字体（防中文回退）。"""
    cs = chart._chartSpace
    txPr = cs.find(qn("c:txPr"))
    if txPr is None:
        txPr = cs.makeelement(qn("c:txPr"), {})
        # chartSpace 子顺序：date1904? lang? roundedCorners? style? clrMapOvr? pivotSource? protection? chart, spPr, txPr, externalData...
        chartEl = cs.find(qn("c:chart"))
        chartEl.addnext(txPr)
        body = ("<a:bodyPr xmlns:a='%s'/><a:lstStyle xmlns:a='%s'/>" % (NS_A, NS_A))
        frag = etree.fromstring(
            "<a:p xmlns:a='%s'><a:pPr><a:defRPr sz=\"750\"><a:solidFill><a:srgbClr val=\"%s\"/></a:solidFill>"
            "<a:latin typeface=\"%s\"/><a:ea typeface=\"%s\"/></a:defRPr></a:pPr><a:endParaRPr lang=\"zh-CN\"/></a:p>"
            % (NS_A, RULE, FONT_LATIN, FONT_CJK))
        txPr.append(etree.fromstring(body))
        txPr.append(frag)


def add_bar_chart(slide, x, y, w, h, cats, series, bar_dir="col",
                  y_min=None, y_max=None, y_title=None, y_fmt="0.00",
                  dl_fmt="0.00", dl_size=7, dl_on=True, dl_pos="outEnd",
                  gap=None, overlap=-10, grid=True, legend=False, log_base=None,
                  major_unit=None, tick_size=7, cat_size=7):
    """series: [{"name":str, "values":[...], "color":hex|str列表, "hollow":[idx...],
                 "edge_only":False, "err_plus":[...], "err_minus":[...]}]
    color 为列表时逐点着色（dPt）。hollow 下标处空心（白底+边框）。
    柱宽目标 0.6：单系列 gap=67；多系列 gap=50（紧凑出版样式）。"""
    cd = CategoryChartData()
    cd.categories = cats
    for s in series:
        cd.add_series(s["name"], s["values"])
    xlt = XL_CHART_TYPE.COLUMN_CLUSTERED if bar_dir == "col" else XL_CHART_TYPE.BAR_CLUSTERED
    gf = slide.shapes.add_chart(xlt, Cm(x), Cm(y), Cm(w), Cm(h), cd)
    chart = gf.chart
    _style_chart_fonts(chart, tick_size)
    plot = chart.plots[0]
    if gap is None:
        gap = 67 if len(series) == 1 else 50
    plot.gap_width = gap
    try:
        plot.overlap = overlap
    except Exception:
        pass
    n_series = len(series)
    for si, s in enumerate(series):
        ser = plot.series[si]
        colors = s["color"] if isinstance(s["color"], list) else [s["color"]] * len(s["values"])
        hollow = set(s.get("hollow", []))
        if n_series == 1 and (len(set(colors)) > 1 or hollow):
            ser.format.fill.background()
            for pi, col in enumerate(colors):
                pt = ser.points[pi]
                if pi in hollow:
                    pt.format.fill.solid()
                    pt.format.fill.fore_color.rgb = rgb("FFFFFF")
                    pt.format.line.color.rgb = rgb(RULE)
                    pt.format.line.width = Pt(1.0)
                else:
                    pt.format.fill.solid()
                    pt.format.fill.fore_color.rgb = rgb(col)
                    pt.format.line.color.rgb = rgb(RULE)
                    pt.format.line.width = Pt(0.5)
        else:
            col0 = colors[0]
            ser.format.fill.solid()
            ser.format.fill.fore_color.rgb = rgb(col0)
            ser.format.line.color.rgb = rgb(RULE)
            ser.format.line.width = Pt(0.5)
        if s.get("err_plus") is not None:
            _inject_err_bars(chart, ser, s["err_plus"], s.get("err_minus") or s["err_plus"])
    if dl_on:
        plot.has_data_labels = True
        dl = plot.data_labels
        dl.number_format = dl_fmt
        dl.number_format_is_linked = False
        dl.position = {"outEnd": XL_LABEL_POSITION.OUTSIDE_END,
                       "inEnd": XL_LABEL_POSITION.INSIDE_END,
                       "ctr": XL_LABEL_POSITION.CENTER}[dl_pos]
        dl.font.size = Pt(dl_size)
        dl.font.name = FONT_LATIN
        dl.font.color.rgb = rgb(INK_SOFT)
        set_cjk(dl.font)
        _copy_dlbls_to_series(plot, n_series)
    ca = chart.category_axis
    ca.has_major_gridlines = False
    ca.format.line.color.rgb = rgb(RULE)
    ca.format.line.width = Pt(0.5)
    ca.major_tick_mark = XL_TICK_MARK.INSIDE
    ca.tick_labels.font.size = Pt(cat_size)
    ca.tick_labels.font.name = FONT_LATIN
    ca.tick_labels.font.color.rgb = rgb(RULE)
    set_cjk(ca.tick_labels.font)
    va = chart.value_axis
    _style_axis(va, tick_size, grid=grid, num_fmt=y_fmt, title=y_title,
                log_base=log_base, y_min=y_min, y_max=y_max, major_unit=major_unit)
    if legend:
        chart.has_legend = True
        chart.legend.position = XL_LEGEND_POSITION.BOTTOM
        chart.legend.include_in_layout = False
        chart.legend.font.size = Pt(8)
        chart.legend.font.name = FONT_LATIN
        set_cjk(chart.legend.font)
    else:
        chart.has_legend = False
    return chart


def _copy_dlbls_to_series(plot, n):
    """WPS 不读绘图区级 dLbls 字号 —— 把 dLbls 深拷贝进每个系列（保持 schema 顺序）。"""
    import copy
    chart_el = plot._element
    dlbls = chart_el.find(qn("c:dLbls"))
    if dlbls is None:
        return
    for ser in plot.series:
        serEl = ser._element
        if serEl.find(qn("c:dLbls")) is not None:
            continue
        dup = copy.deepcopy(dlbls)
        placed = False
        for tag in ("c:trendline", "c:errBars", "c:cat", "c:val"):
            el = serEl.find(qn(tag))
            if el is not None:
                el.addprevious(dup)
                placed = True
                break
        if not placed:
            serEl.append(dup)


def _inject_err_bars(chart, ser, plus, minus, color=RULE, w_pt=0.8):
    serEl = ser._element
    for eb in serEl.findall(qn("c:errBars")):
        serEl.remove(eb)

    def numlit(vals):
        pts = "".join('<c:pt idx="%d"><c:v>%s</c:v></c:pt>' % (i, v)
                      for i, v in enumerate(vals))
        return ('<c:numLit><c:formatCode>General</c:formatCode>'
                '<c:ptCount val="%d"/>%s</c:numLit>' % (len(vals), pts))

    xml = ('<c:errBars xmlns:c="%s" xmlns:a="%s">'
           '<c:errDir val="y"/><c:errBarType val="both"/><c:errValType val="cust"/>'
           '<c:noEndCap val="0"/>'
           '<c:spPr><a:ln w="%d"><a:solidFill><a:srgbClr val="%s"/></a:solidFill></a:ln></c:spPr>'
           '<c:plus>%s</c:plus><c:minus>%s</c:minus></c:errBars>'
           % (NS_C, NS_A, int(w_pt * 12700), color, numlit(plus), numlit(minus)))
    el = etree.fromstring(xml)
    cat = serEl.find(qn("c:cat"))
    if cat is not None:
        cat.addprevious(el)
    else:
        serEl.append(el)


def add_line_chart(slide, x, y, w, h, cats, series, y_min=None, y_max=None,
                   y_title=None, y_fmt="0.00", grid=True, legend=False,
                   log_base=False, tick_size=7, cat_size=7, major_unit=None):
    """series: [{"name","values","color","width_pt"(默认1.2),"dash","marker":bool,
                 "marker_size"(默认4),"marker_shape":"circle|square|triangle|diamond|x"}]"""
    shape_map = {"circle": XL_MARKER_STYLE.CIRCLE, "square": XL_MARKER_STYLE.SQUARE,
                 "triangle": XL_MARKER_STYLE.TRIANGLE, "diamond": XL_MARKER_STYLE.DIAMOND,
                 "x": XL_MARKER_STYLE.X}
    cd = CategoryChartData()
    cd.categories = cats
    for s in series:
        cd.add_series(s["name"], s["values"])
    gf = slide.shapes.add_chart(XL_CHART_TYPE.LINE_MARKERS, Cm(x), Cm(y), Cm(w), Cm(h), cd)
    chart = gf.chart
    _style_chart_fonts(chart, tick_size)
    plot = chart.plots[0]
    for si, s in enumerate(series):
        ser = plot.series[si]
        ser.format.line.color.rgb = rgb(s["color"])
        ser.format.line.width = Pt(s.get("width_pt", 1.2))
        if s.get("dash"):
            ser.format.line.dash_style = s["dash"]
        if s.get("marker", True):
            try:
                ser.marker.style = shape_map.get(s.get("marker_shape", "circle"),
                                                 XL_MARKER_STYLE.CIRCLE)
                ser.marker.size = s.get("marker_size", 4)
                ser.marker.format.fill.solid()
                ser.marker.format.fill.fore_color.rgb = rgb(s["color"])
                ser.marker.format.line.color.rgb = rgb(RULE)
                ser.marker.format.line.width = Pt(0.5)
            except Exception:
                pass
        else:
            try:
                ser.marker.style = XL_MARKER_STYLE.NONE
            except Exception:
                pass
        try:
            ser.smooth = False
        except Exception:
            pass
    ca = chart.category_axis
    ca.has_major_gridlines = False
    ca.format.line.color.rgb = rgb(RULE)
    ca.format.line.width = Pt(0.5)
    ca.tick_labels.font.size = Pt(cat_size)
    ca.tick_labels.font.name = FONT_LATIN
    ca.tick_labels.font.color.rgb = rgb(RULE)
    set_cjk(ca.tick_labels.font)
    va = chart.value_axis
    _style_axis(va, tick_size, grid=grid, num_fmt=y_fmt, title=y_title,
                y_min=y_min, y_max=y_max, major_unit=major_unit)
    if legend:
        chart.has_legend = True
        chart.legend.position = XL_LEGEND_POSITION.BOTTOM
        chart.legend.include_in_layout = False
        chart.legend.font.size = Pt(8)
        chart.legend.font.name = FONT_LATIN
        set_cjk(chart.legend.font)
    else:
        chart.has_legend = False
    return chart


def add_radar_chart(slide, x, y, w, h, cats, series, y_min=0, y_max=5,
                    legend=False, tick_size=7, cat_size=8, fill_alpha=0.15):
    """series: [{"name","values","color","width_pt"(默认1.2)}] —— 填充雷达，
    填充透明度默认 15%（即 alpha=0.15 的浅填充），线 1.2pt。"""
    cd = CategoryChartData()
    cd.categories = cats
    for s in series:
        cd.add_series(s["name"], s["values"])
    gf = slide.shapes.add_chart(XL_CHART_TYPE.RADAR_FILLED, Cm(x), Cm(y), Cm(w), Cm(h), cd)
    chart = gf.chart
    _style_chart_fonts(chart, tick_size)
    plot = chart.plots[0]
    for si, s in enumerate(series):
        ser = plot.series[si]
        ser.format.line.color.rgb = rgb(s["color"])
        ser.format.line.width = Pt(s.get("width_pt", 1.2))
        if fill_alpha is not None:
            ser.format.fill.solid()
            ser.format.fill.fore_color.rgb = rgb(s["color"])
            _set_fill_alpha(ser.format.fill, int((1 - fill_alpha) * 100000))
        try:
            ser.smooth = False
        except Exception:
            pass
    va = chart.value_axis
    _style_axis(va, tick_size, grid=True, num_fmt="0", y_min=y_min, y_max=y_max)
    ca = chart.category_axis
    ca.tick_labels.font.size = Pt(cat_size)
    ca.tick_labels.font.name = FONT_LATIN
    ca.tick_labels.font.color.rgb = rgb(RULE)
    set_cjk(ca.tick_labels.font)
    if legend:
        chart.has_legend = True
        chart.legend.position = XL_LEGEND_POSITION.BOTTOM
        chart.legend.include_in_layout = False
        chart.legend.font.size = Pt(8)
        chart.legend.font.name = FONT_LATIN
        set_cjk(chart.legend.font)
    else:
        chart.has_legend = False
    return chart


# —— 表格热图（原生表格，逐格可编辑）——

def heat_color(v, vmin, vmax, kind="seq"):
    """顺序色板 #d0e2c0→#a2c986→#67a583→#49c2d9→#7b95c6；发散 #c85e62→#fded95→#67a583"""
    t = 0.0 if vmax == vmin else (v - vmin) / (vmax - vmin)
    t = max(0.0, min(1.0, t))
    if kind == "seq":
        stops = [C6, C5, C4, C2, C1]
    else:
        stops = [C11, C7, C4]
        t = t * 2
        if t > 1:
            t -= 1
            stops = [C7, C4]
    n = len(stops) - 1
    i = min(int(t * n), n - 1)
    f = t * n - i

    def mix(h1, h2, f):
        a = [int(h1.lstrip("#")[j:j + 2], 16) for j in (0, 2, 4)]
        b = [int(h2.lstrip("#")[j:j + 2], 16) for j in (0, 2, 4)]
        return "#%02X%02X%02X" % tuple(int(a[k] + (b[k] - a[k]) * f) for k in range(3))

    return mix(stops[i], stops[i + 1], f)


def _set_cell_borders(cell, edges=("L", "R", "T", "B"), color="#E5E5E5", w_pt=0.5):
    """给表格单元格加边框（a:lnL/lnR/lnT/lnB），用于热图 0.5pt 浅灰格线。"""
    tcPr = cell._tc.get_or_add_tcPr()
    tag_map = {"L": "a:lnL", "R": "a:lnR", "T": "a:lnT", "B": "a:lnB"}
    for e in edges:
        tag = tag_map[e]
        for old in tcPr.findall(qn(tag)):
            tcPr.remove(old)
        ln = tcPr.makeelement(qn(tag), {"w": str(int(w_pt * 12700)), "cap": "flat"})
        fill = ln.makeelement(qn("a:solidFill"), {})
        clr = fill.makeelement(qn("a:srgbClr"), {"val": color.lstrip("#")})
        fill.append(clr)
        ln.append(fill)
        # 顺序要求：lnL/lnR/lnT/lnB 须在 tcPr 最前（按 L,R,T,B 顺序插入）
        tcPr.insert(0, ln)


def add_heat_table(slide, x, y, w, h, row_labels, col_labels, values,
                   vmin=None, vmax=None, kind="seq", fmt="%.2f", size=7,
                   header_size=8, row_size=7.5, null_text="null", star_cols=(),
                   null_fill="#F0F0F0"):
    """values[i][j]；None → #F0F0F0 灰格 + null 标注；格线 0.5pt #E5E5E5。返回 table。"""
    nr, nc = len(row_labels) + 1, len(col_labels) + 1
    gf = slide.shapes.add_table(nr, nc, Cm(x), Cm(y), Cm(w), Cm(h))
    tbl = gf.table
    vmin = (min(v for r in values for v in r if v is not None)
            if vmin is None else vmin)
    vmax = (max(v for r in values for v in r if v is not None)
            if vmax is None else vmax)
    # 列宽：首列稍宽
    total_first = w * 0.185
    other = (w - total_first) / len(col_labels)
    tbl.columns[0].width = Cm(total_first)
    for j in range(1, nc):
        tbl.columns[j].width = Cm(other)
    row_h = h / nr
    for i in range(nr):
        tbl.rows[i].height = Cm(row_h)

    def put(cell, text, fill, bold=False, color=INK, sz=size):
        cell.fill.solid()
        cell.fill.fore_color.rgb = rgb(fill)
        cell.margin_left = cell.margin_right = Cm(0.08)
        cell.margin_top = cell.margin_bottom = Cm(0.02)
        cell.vertical_anchor = MSO_ANCHOR.MIDDLE
        tf = cell.text_frame
        tf.word_wrap = False
        p = tf.paragraphs[0]
        p.alignment = PP_ALIGN.CENTER
        run = p.add_run()
        run.text = text
        _style_run(run, sz, bold, color)
        _set_cell_borders(cell)

    for j, cl in enumerate(col_labels):
        hdr = put(tbl.cell(0, j + 1), cl, "F2F2F2", bold=True, sz=header_size)
    put(tbl.cell(0, 0), "", "F2F2F2")   # 角单元格：显式覆盖表格默认样式
    for i, rl in enumerate(row_labels):
        put(tbl.cell(i + 1, 0), rl, "F2F2F2", bold=True, sz=row_size)
        for j in range(len(col_labels)):
            v = values[i][j]
            if v is None:
                put(tbl.cell(i + 1, j + 1), null_text, null_fill, color="#8C8C8C")
            else:
                fillc = heat_color(v, vmin, vmax, kind)
                txt = (fmt % v) + ("*" if j in star_cols else "")
                lum = int(fillc.lstrip("#")[0:2], 16) * 0.299 + int(fillc.lstrip("#")[2:4], 16) * 0.587 + int(fillc.lstrip("#")[4:6], 16) * 0.114
                put(tbl.cell(i + 1, j + 1), txt, fillc, color=INK if lum > 140 else "FFFFFF")
    return tbl


# —— 组合图形：森林图（逐行 CI 线 + 点 + MDD 带）——

def compose_forest(slide, x, y, w, h, rows, xlim=(0, 1), show_mdd=True,
                   label_size=8, ann_size=7.5, zero=0.0, mdd_val=None,
                   row_h=None, x_title="配对差（95% bootstrap CI）", row_mdd=False):
    """rows: [{"label":str, "diff":f, "lo":f, "hi":f, "sig":"pos|neg|ns",
               "ann":str, "mdd":f}] —— 全部原生形状，可拖动。
    row_mdd=True 时按每行 r['mdd'] 画零点两侧灰带。"""
    n = len(rows)
    row_h = row_h or (h / max(n, 1))
    lab_w = w * 0.30
    ann_w = w * 0.30
    plot_x, plot_w = x + lab_w, w - lab_w - ann_w
    x0, x1 = xlim

    def X(v):
        return plot_x + (v - x0) / (x1 - x0) * plot_w

    # MDD 灰带（#d0e2c0，透明度 30%）
    if show_mdd and mdd_val is not None:
        add_rect(slide, X(zero - mdd_val), y, X(zero + mdd_val) - X(zero - mdd_val), h,
                 fill=MDD_BAND, edge=None, transparency=70, name="MDD band")
    # 垂直参考线（0 线）：黑色虚线 0.8pt
    add_line(slide, X(zero), y - 0.15, X(zero), y + h, color=RULE, lw=0.8,
             dash=MSO_LINE_DASH_STYLE.DASH)
    for i, r in enumerate(rows):
        cy = y + row_h * (i + 0.5)
        if row_mdd and r.get("mdd") is not None:
            bw = X(zero + r["mdd"]) - X(zero - r["mdd"])
            add_rect(slide, X(zero - r["mdd"]), cy - row_h * 0.31, bw, row_h * 0.62,
                     fill=MDD_BAND, edge=None, transparency=70, name="mdd-row-%d" % i)
        add_text(slide, x, cy - 0.45, lab_w - 0.3, 0.9, r["label"], size=label_size,
                 color=INK_SOFT, align="left", anchor="middle")
        # 点估计按语义着色；CI 统一 #333333 1.0pt 带帽
        color = FOREST[r.get("sig", "ns")]
        add_line(slide, X(r["lo"]), cy, X(r["hi"]), cy, color=RULE, lw=1.0)
        for v in (r["lo"], r["hi"]):
            add_line(slide, X(v), cy - 0.14, X(v), cy + 0.14, color=RULE, lw=1.0)
        d = 0.24
        add_ellipse(slide, X(r["diff"]) - d / 2, cy - d / 2, d, d, color, RULE, 0.5)
        if r.get("ann"):
            add_text(slide, plot_x + plot_w + 0.2, cy - 0.45, ann_w, 0.9, r["ann"],
                     size=ann_size, color=INK_SOFT, anchor="middle")
    add_text(slide, plot_x, y + h + 0.05, plot_w, 0.6, x_title, size=7,
             color=INK_MUTED, align="center")


def panel_title(slide, x, y, w, text, size=10, bold=True, color=INK, align="center"):
    add_text(slide, x, y, w, 0.75, text, size=size, bold=bold, color=color, align=align)


# ════════════════════════════════════════════════════════════════════════
# 5. 流程图盒（HA 机制图用）
# ════════════════════════════════════════════════════════════════════════

FS_TITLE = 9.0
FS_BOX = 8.0
FS_BODY = 7.0


def box_metrics(title, body, w_cm, pad_v=0.22, gap=0.10, size_title=FS_TITLE,
                size_body=FS_BODY):
    th = len(wrap_cm(title, w_cm - 0.5, size_title, True)) * line_h_cm(size_title)
    bh = 0.0
    if body:
        bh = len(wrap_cm(body, w_cm - 0.5, size_body)) * line_h_cm(size_body)
        if bh > 0:
            bh += gap
    return pad_v * 2 + th + bh


def draw_box(slide, x, y, w, title, body="", fill=None, edge=None, bid=None,
             size_title=FS_TITLE, size_body=FS_BODY, h=None, edge_lw=0.5,
             title_color=INK, body_color=INK_SOFT, rounded=True):
    """实测定高圆角盒：标题（黑体）+ 说明（灰体）。返回 (x,y,w,h)。"""
    if h is None:
        h = box_metrics(title, body, w, size_title=size_title, size_body=size_body)
    edge = edge or FRAME
    add_rect(slide, x, y, w, h, fill=fill, edge=edge, lw=edge_lw, rounded=rounded,
             radius=0.12, name=bid)
    th = len(wrap_cm(title, w - 0.5, size_title, True)) * line_h_cm(size_title)
    add_text(slide, x + 0.25, y + 0.20, w - 0.5, th, title, size=size_title,
             bold=True, color=title_color, align="center", cjk=None)
    if body:
        add_text(slide, x + 0.25, y + 0.20 + th + 0.06, w - 0.5,
                 h - 0.40 - th, body, size=size_body, color=body_color,
                 align="center")
    return (x, y, w, h)


def v_chain(slide, x, y, w, items, gap=0.55, arrow_color="#8A8A8A"):
    """纵向盒链，items = [(title, body, fill)]；返回结束 y。"""
    cy = y
    for i, (t, b, f) in enumerate(items):
        h = draw_box(slide, x, cy, w, t, b, fill=f)
        cy += h[3]
        if i < len(items) - 1:
            add_arrow(slide, x + w / 2, cy + 0.06, x + w / 2, cy + gap - 0.06)
            cy += gap
    return cy


# ════════════════════════════════════════════════════════════════════════
# 6. 科研三线表（顶线1.5pt / 栏目线0.75pt / 底线1.5pt，无竖线无底纹）
# ════════════════════════════════════════════════════════════════════════

NO_STYLE_NO_GRID = "{2D5ABB26-0587-4C30-8999-92F81FD0307C}"


def _strip_table_style(tbl):
    tblPr = tbl._tbl.find(qn("a:tblPr"))
    if tblPr is None:
        return
    tblPr.set("firstRow", "0")
    tblPr.set("bandRow", "0")
    sid = tblPr.find(qn("a:tableStyleId"))
    if sid is None:
        sid = tblPr.makeelement(qn("a:tableStyleId"), {})
        tblPr.append(sid)
    sid.text = NO_STYLE_NO_GRID


def _stars(q):
    if q is None:
        return ""
    return "***" if q < 0.001 else ("**" if q < 0.01 else ("*" if q < 0.05 else ""))


def _cell_text(cell, text, size, bold=False, italic=False, color=INK,
               align="c", wrap=False):
    cell.fill.background()
    cell.margin_left = cell.margin_right = Cm(0.10)
    cell.margin_top = cell.margin_bottom = Cm(0.03)
    cell.vertical_anchor = MSO_ANCHOR.MIDDLE
    tf = cell.text_frame
    tf.word_wrap = wrap
    p = tf.paragraphs[0]
    p.alignment = {"l": PP_ALIGN.LEFT, "c": PP_ALIGN.CENTER,
                   "r": PP_ALIGN.RIGHT}[align]
    run = p.add_run()
    run.text = str(text)
    f = run.font
    f.size = Pt(size)
    f.bold = bold
    f.italic = italic
    f.color.rgb = rgb(color)
    f.name = FONT_LATIN
    set_cjk(f, FONT_CJK)


def add_three_line(slide, x, y, w, title, headers, rows, note="",
                   col_align=None, col_widths=None, size=8, header_size=8,
                   italic_cols=(), row_h=0.62, header_h=0.72, table_id="",
                   title_size=10):
    """科研三线表：表题上方居中 10pt 加粗；顶线 1.5pt / 栏目线 0.75pt / 底线 1.5pt；
    无竖线、无底纹、无彩色表头。返回 (table, 底线y)。"""
    nr, nc = len(rows) + 1, len(headers)
    th = 0.75
    tbl_h = header_h + row_h * len(rows)
    # 表题
    add_text(slide, x, y, w, th, ("%s  %s" % (table_id, title)).strip(),
             size=title_size, bold=True, align="center", color=INK)
    gf = slide.shapes.add_table(nr, nc, Cm(x), Cm(y + th), Cm(w), Cm(tbl_h))
    tbl = gf.table
    _strip_table_style(tbl)
    if col_widths:
        tot = sum(col_widths)
        for j, cwv in enumerate(col_widths):
            tbl.columns[j].width = Cm(w * cwv / tot)
    else:
        for j in range(nc):
            tbl.columns[j].width = Cm(w / nc)
    tbl.rows[0].height = Cm(header_h)
    for i in range(1, nr):
        tbl.rows[i].height = Cm(row_h)
    ca = col_align or (["l"] + ["c"] * (nc - 1))
    # 表头（顶线 1.5pt + 栏目线 0.75pt）
    for j, htxt in enumerate(headers):
        cell = tbl.cell(0, j)
        _cell_text(cell, htxt, header_size, bold=True,
                   italic=(j in italic_cols), align="c")
        _set_cell_borders(cell, edges=("T",), color=RULE, w_pt=1.5)
        _set_cell_borders(cell, edges=("B",), color=RULE, w_pt=0.75)
    # 内容行
    for i, row in enumerate(rows):
        last = (i == len(rows) - 1)
        for j, v in enumerate(row):
            cell = tbl.cell(i + 1, j)
            _cell_text(cell, v if v is not None else "—", size, align=ca[j])
            if last:
                _set_cell_borders(cell, edges=("B",), color=RULE, w_pt=1.5)
    ny = y + th + tbl_h + 0.08
    if note:
        add_text(slide, x, ny, w, 0.9, note, size=7, color=INK_MUTED,
                 line_spacing=1.15)
        ny += 0.9
    return tbl, ny


def md_three_line(headers, rows, title=""):
    """Markdown 三线表（|---| 分隔形式）。"""
    out = []
    if title:
        out.append("**%s**\n" % title)
    out.append("| " + " | ".join(str(h) for h in headers) + " |")
    out.append("|" + "|".join(["---"] * len(headers)) + "|")
    for r in rows:
        out.append("| " + " | ".join("—" if v is None else str(v) for v in r) + " |")
    return "\n".join(out)


def latex_three_line(headers, rows, caption="", note="", colspec=None):
    """LaTeX booktabs 三线表。"""
    n = len(headers)
    cs = colspec or ("l" + "r" * (n - 1))
    lines = [r"\begin{table}[htbp]", r"\centering",
             r"\caption{%s}" % caption,
             r"\begin{tabular}{%s}" % cs, r"\toprule",
             " & ".join(str(h) for h in headers) + r" \\", r"\midrule"]
    for r in rows:
        lines.append(" & ".join("--" if v is None else str(v) for v in r) + r" \\")
    lines += [r"\bottomrule", r"\end{tabular}"]
    if note:
        lines += [r"\begin{minipage}{0.9\linewidth}\footnotesize",
                  r"\item %s" % note, r"\end{minipage}"]
    lines += [r"\end{table}"]
    return "\n".join(lines)


def set_notes(slide, text):
    """把 Markdown/LaTeX 表代码写入演讲者备注（不占版面）。"""
    slide.notes_slide.notes_text_frame.text = text
