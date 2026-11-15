"""Verified, pinned Nature Figure text skill with the local execution adapter.

Upstream files are kept unmodified. They are reference text, never Python code.
"""
import hashlib
from pathlib import Path
from src.prompts import skill_registry

ROOT = Path(__file__).resolve().parent / "skills/vendor/nature-skills"
COMMIT = "7bff1d0539c0dfad6c30178bb93ce6dd8a5cebf8"
GUIDANCE_FILES = ("skills/nature-figure/SKILL.md", "skills/nature-figure/static/core/contract.md",
                  "skills/nature-figure/static/core/stance.md",
                  "skills/nature-figure/static/fragments/backend/python.md")
ADAPTER = """CitrusSchalor 运行兼容约定（优先于下方上游原文的宿主操作说明）：
- 本平台已选择 Python/Matplotlib 隔离绘图后端，不再要求用户选择 Python/R，不另开 FigureForge、R、终端或安装器。
- 上游是制图工作流参考，不是可导入的 Python 包；未定义的 save_pub_set、make_trend 等 helper 不能直接调用。
- 先检查表结构、变量语义、重复/分组、缺失和单位，再建立 figure contract 与选图；不要把样本/基因 ID 当作几十个默认柱类别，不能擅自选择第一列数值或无提示地取前 N 行。
- 优先通过 run_research_figure_script 在现有隔离 worker 内执行 Matplotlib/Seaborn，用户不必说工具名或要求写脚本。工具不可用时，只在声明式图型和数据语义充分受支持时使用 create_research_figure；不得在主进程执行代码。
- 同一Agent可编写pandas代码将原始数据整理为内部中间表，再生成多张独立图或一张多panel合图；不要机械要求用户先整理数据，也不必为每图先做统计检验。目的、指标和字段关系明确就直接执行，缺少关键语义才调用通用request_user_input给出基于实际字段的候选方案。\n- run_research_figure_script的outputs_json版本2支持零图探索、内部表和命名多图；用emit.table登记内部DataFrame、emit.figure登记Figure，普通临时变量随执行清理。中间表只通过dataset_ref在后续执行复用，默认不作为用户成果或附件；不要把表或长脚本反复放入上下文。\n- 仅使用当前工具schema、source_ref及输入快照变量读取已授权完整数据，不写宿主绝对路径、不联网、不安装包。读取dataset_ref输入使用工具文档中的load_dataset助手。遵守本平台manifest/gid/override编辑协议；每个独立图单独保存项目，编辑以命名图为目标。
- 导出由本平台统一 exporter/publisher 完成 SVG/PNG/PDF 与 manifest；不直接调用上游导出 helper，不把代码或手写路径当下载成果。字体、尺寸、图例、标签密度需在最终尺寸核对。
- 上游关于图件结论的要求不允许预设或编造科学结论；探索任务应先呈现数据支持的结构与限制。统计必须来自真实计算，标识符、单位或实验设计不明确时请求必要澄清。
- 绘图默认由本技能指导LLM自主编写或修改脚本；优先参数化复用已有脚本和可信方法，不能把技能当作固定图型路由。源码先暂存，后续按script_ref读取和patch，不反复从头写。
- 用research_workspace(operation='read_skill', skill_id='nature-figure')获取资源目录；按需用read_skill_resource读取manifest、references和nature-shared材料。资源路径以目录返回的包内完整相对路径为准，禁止猜测宿主路径。
- 科研沙箱集成Python绘图核心：from src.figures.nature_runtime import configure_style, interp_monotone, label_y_above；可先configure_style()应用字体、线条和美学默认，再按真实数据调整。图交给emit.figure，导出由worker负责，不调用会关图的上游save_bundle。不运行R、外部图像API或安装器。
- worker在最后一次布局/编辑后执行Nature源级preflight（保留诊断建议）、实际面板对齐、PDF文字和碰撞检查。以nature_skill_receipt为准；未执行/不可审计不得当通过，失败依真实诊断有限修复。源码检查不能看见宿主导出配置，原finding保留为建议；最终渲染检查阻断真实失败。未部署新版沙箱时须如实说明不可用。
- 用户只看简洁的任务理解、关键进展和实际结果；源脚本保留在工作区与可重绘项目中。仅规划、解释或图件点评默认文字回答，不额外导出文件；绘图请求则交付真实图件。不能仅凭加载本技能就声称已满足期刊投稿规范。
"""


def verified_sources():
    try:
        return skill_registry.verified_sources("nature-figure")
    except (OSError, ValueError, KeyError, TypeError) as exc:
        raise RuntimeError("nature-figure 技能未安装完整或校验失败，不能声明已加载") from exc


def guidance():
    sources = verified_sources()
    parts = [ADAPTER, "以下为固定版本上游原文，按上述兼容约定使用："]
    parts += [f"<reference name=\"{name}\">\n{sources[name]}\n</reference>" for name in GUIDANCE_FILES]
    return "\n\n".join(parts)


def status():
    sources = verified_sources()
    text = guidance()
    return dict(name="nature-figure", installed=True, verified=True, commit=COMMIT,
                source="https://github.com/Yuan1z0825/nature-skills", license="Apache-2.0",
                version="2.8.0", upstream_auditors_executed=False,
                reference_files=len(sources), prompt_files=list(GUIDANCE_FILES),
                adapted_backend="Python/Matplotlib + isolated figure worker",
                guidance_sha256=hashlib.sha256(text.encode()).hexdigest())
