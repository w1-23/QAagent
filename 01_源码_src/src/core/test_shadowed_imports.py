# -*- coding: utf-8 -*-
"""静态回归：函数内重复 import 造成的"局部变量遮蔽"（UnboundLocalError 类）。

真实事故：`process_graph` 在成功路径上调用 `get_request_journal()`，而同一
函数后面又写了一次 `from src.core.progress_bus import get_request_journal`。
Python 因此把这个名字在整个函数里当成局部变量 —— 于是成功路径在执行到
第一次调用时抛 `UnboundLocalError`，**整轮 done 事件缺失**，而单元测试和
取消路径都发现不了（取消路径不经过那一行）。

这类缺陷只能靠静态检查兜住：同一函数内，某个名字若被 import 成局部变量，
则它在此之前的任何读取都是"读在赋值前"。
"""
from __future__ import annotations

import ast
import os
import pathlib
import sys

ROOT = pathlib.Path(__file__).resolve().parents[2]        # 01_源码_src
TARGETS = (
    ROOT / "src" / "api" / "main.py",
    ROOT / "src" / "graph" / "general_agent_graph.py",
    ROOT / "src" / "core" / "jobs.py",
)


def _own_nodes(node: ast.AST):
    """Descendants of this function, excluding nested functions/classes."""
    for child in ast.iter_child_nodes(node):
        if isinstance(child, (ast.FunctionDef, ast.AsyncFunctionDef,
                              ast.Lambda, ast.ClassDef)):
            continue
        yield child
        yield from _own_nodes(child)


def _local_imports(node: ast.AST) -> dict[str, int]:
    """Names imported inside a function body -> first import line."""
    found: dict[str, int] = {}
    for child in _own_nodes(node):
        if isinstance(child, ast.ImportFrom):
            for alias in child.names:
                found.setdefault(alias.asname or alias.name, child.lineno)
        elif isinstance(child, ast.Import):
            for alias in child.names:
                name = alias.asname or alias.name.split(".")[0]
                found.setdefault(name, child.lineno)
    return found


def _reads_before(path: pathlib.Path) -> list[str]:
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    problems: list[str] = []
    for node in ast.walk(tree):
        if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            continue
        shadowed = _local_imports(node)
        if not shadowed:
            continue
        for child in _own_nodes(node):
            if not isinstance(child, ast.Name) or not isinstance(child.ctx, ast.Load):
                continue
            line = shadowed.get(child.id)
            if line is None:
                continue
            if child.lineno < line:
                problems.append(
                    f"{path.name}:{child.lineno} 读取 {child.id}，但该名字在 "
                    f"{line} 行才被 import 成局部变量（会抛 UnboundLocalError）")
    return problems


def test_no_read_before_local_import():
    problems: list[str] = []
    for target in TARGETS:
        if target.exists():
            problems.extend(_reads_before(target))
    assert not problems, "函数内 import 遮蔽导致的前向读取：\n" + "\n".join(problems)


if __name__ == "__main__":                       # 允许单独跑
    sys.exit(0 if not test_no_read_before_local_import() else 1)
