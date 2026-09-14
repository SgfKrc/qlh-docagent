"""Patchouli MCP server（stdio，只读）——把文档库查询能力暴露给任意 MCP 客户端。

工具（8 件，全部只读；无任何写路径）：
  catalog_query / search_docs / read_doc / doc_scan / audit_entry / doc_history / catalog_stats / lib_list

协议：MCP 2024-11-05 基础子集（initialize / notifications/initialized / ping /
tools/list / tools/call），JSON-RPC 2.0 over stdio。**零第三方**（手写循环）；
stdout 只输出协议消息，日志一律走 stderr。

用法：
    python -m patchouli.mcp_server

注册示例（Python 用 <库根>/tools/docagent 的 venv 解释器）：
    reasonix mcp add patchouli -- <python> -m patchouli.mcp_server
    claude  mcp add patchouli -- <python> -m patchouli.mcp_server
"""
from __future__ import annotations

import json
import re
import subprocess
import sys
from pathlib import Path
from typing import Any, Callable

PROTOCOL_VERSION = "2024-11-05"
SERVER_NAME = "patchouli"
SERVER_VERSION = "0.2.0"

# --------------------------------------------------------------------------- #
# 工具定义（JSON Schema）
# --------------------------------------------------------------------------- #
_ROOT_PROPS = {
    "root": {"type": "string", "description": "库根路径（缺省：自动解析，见 roots.resolve_library）"},
    "lib": {"type": "string", "description": "已注册库名（patchouli lib add）"},
}

TOOL_DEFS: list[dict[str, Any]] = [
    {
        "name": "catalog_query",
        "description": "查询文档库目录：按类型/缺状态行/票号过滤，返回文档元数据（状态/更新日期/票号/互链）。",
        "inputSchema": {
            "type": "object",
            "properties": {
                **_ROOT_PROPS,
                "kind": {"type": "string", "description": "分类过滤：decision/special-plan/ticket-plan/report/guide/reference/other"},
                "missing_status": {"type": "boolean", "description": "仅缺状态行的文档"},
                "ticket": {"type": "string", "description": "按票号过滤（如 EX-CACHE-01）"},
                "include_archive": {"type": "boolean", "description": "是否包含 archive/（默认 true）"},
                "limit": {"type": "integer", "description": "最多返回条数（默认 50）"},
            },
        },
    },
    {
        "name": "search_docs",
        "description": "全文/票号检索（行级）：纯文本=全文子串；t:票号 / k:类型 / s:缺状态 / a:含归档 可混用。",
        "inputSchema": {
            "type": "object",
            "properties": {
                **_ROOT_PROPS,
                "query": {"type": "string", "description": "查询（如『缓存』或『t:PATCH-01』）"},
                "top_k": {"type": "integer", "description": "最多结果数（默认 10）"},
                "context": {"type": "boolean", "description": "附带每条命中行的上下文片段"},
                "plain": {"type": "boolean", "description": "去掉 markdown 标记符号并压缩空白（降低上下文符号密度）"},
            },
            "required": ["query"],
        },
    },
    {
        "name": "read_doc",
        "description": "读取文档（可指定行区间或章节）：『看一份 chunk 知全貌』。",
        "inputSchema": {
            "type": "object",
            "properties": {
                **_ROOT_PROPS,
                "path": {"type": "string", "description": "库内相对路径（如 docs/总体下一步计划.md）"},
                "section": {"type": "string", "description": "章节标题（读取该标题所属区块）"},
                "line_start": {"type": "integer"},
                "line_end": {"type": "integer"},
                "max_chars": {"type": "integer", "description": "截断上限（默认 20000）"},
                "plain": {"type": "boolean", "description": "去掉 markdown 标记符号并压缩空白（降低上下文符号密度）"},
            },
            "required": ["path"],
        },
    },
    {
        "name": "doc_scan",
        "description": "运行 docagent 规则扫描（R1 完成未收口 / R2 未提交登记 / R3 状态行滞后 / R4 链接失效 / R5 状态行缺失），返回 findings。",
        "inputSchema": {"type": "object", "properties": {**_ROOT_PROPS}},
    },
    {
        "name": "audit_entry",
        "description": "单条目定点审查（docagent audit-entry）：给定文档与条目文本/标题锚，核对表述与仓库证据。",
        "inputSchema": {
            "type": "object",
            "properties": {
                **_ROOT_PROPS,
                "doc": {"type": "string", "description": "库内相对 md 路径"},
                "entry": {"type": "string", "description": "条目原文（与 anchor 二选一）"},
                "anchor": {"type": "string", "description": "标题锚（与 entry 二选一）"},
            },
            "required": ["doc"],
        },
    },
    {
        "name": "doc_history",
        "description": "文档流转记录（git log --follow 时间线：提交/日期/±行）。",
        "inputSchema": {
            "type": "object",
            "properties": {
                **_ROOT_PROPS,
                "path": {"type": "string", "description": "库内相对路径"},
                "limit": {"type": "integer", "description": "最多提交数（默认 30）"},
            },
            "required": ["path"],
        },
    },
    {
        "name": "catalog_stats",
        "description": "馆藏统计：总量/归档/状态行覆盖率/分类分布/更新月份/票号 Top/互链。",
        "inputSchema": {"type": "object", "properties": {**_ROOT_PROPS}},
    },
    {
        "name": "lib_list",
        "description": "已注册文档库列表与当前解析结果。",
        "inputSchema": {"type": "object", "properties": {}},
    },
]


# --------------------------------------------------------------------------- #
# 工具实现（薄包装既有引擎；全部只读）
# --------------------------------------------------------------------------- #
_PLAIN_RULES = (
    (re.compile(r"\*\*|__|`"), ""),          # 强调与行内代码标记
    (re.compile(r"^\s*#{1,6}\s*", re.M), ""),  # 标题井号
    (re.compile(r"^\s*>+\s?", re.M), ""),     # 引用符
    (re.compile(r"^\s*[-*_]{3,}\s*$", re.M), ""),  # 分隔线
    (re.compile(r"\|"), " "),                 # 表格竖线
    (re.compile(r"[ 	]+"), " "),             # 水平空白压缩
    (re.compile(r"\n{3,}"), "\n\n"),          # 空行压缩
)


def plain_text(text: str) -> str:
    """去掉 markdown 标记符号（强调/井号/引用/分隔线/表格线）并压缩空白。

    目的：降低交给调用方上下文的**符号密度**——高符号密度长上下文会显著提高
    长会话的复读退化概率（2026-09-15 排查结论）。路径等标识字段不在此列。
    """
    out = str(text)
    for pattern, repl in _PLAIN_RULES:
        out = pattern.sub(repl, out)
    return out.strip()


def _resolve(args: dict[str, Any]) -> dict[str, Any]:
    from .roots import resolve_library

    return resolve_library(args.get("root"), lib=args.get("lib"))


def _safe_rel(value: Any) -> str:
    rel = Path(str(value))
    if rel.is_absolute() or ".." in rel.parts:
        raise ValueError(f"非法路径（须为库内相对路径）: {value}")
    return rel.as_posix()


def _catalog(resolved: dict[str, Any], include_archive: bool = True) -> dict[str, Any]:
    from .catalog import scan

    return scan(resolved["root"], docs_dir=resolved["docs_dir"], include_archive=include_archive)


def h_catalog_query(args: dict[str, Any]) -> dict[str, Any]:
    resolved = _resolve(args)
    catalog = _catalog(resolved, include_archive=bool(args.get("include_archive", True)))
    docs = catalog["documents"]
    if args.get("kind"):
        docs = [d for d in docs if d["kind"] == args["kind"]]
    if args.get("missing_status"):
        docs = [d for d in docs if d["status"] is None]
    if args.get("ticket"):
        needle = str(args["ticket"]).upper()
        docs = [d for d in docs if needle in {t.upper() for t in d["tickets"]}]
    limit = int(args.get("limit", 50))
    slim = [
        {
            "path": d["path"],
            "name": d["name"],
            "kind": d["kind"],
            "status": d["status"],
            "updated": d["updated"],
            "tickets": d["tickets"][:8],
            "archived": d["archived"],
        }
        for d in docs[:limit]
    ]
    return {
        "root": str(resolved["root"]),
        "total": len(docs),
        "by_kind": catalog["by_kind"],
        "no_status_count": catalog["no_status_count"],
        "documents": slim,
    }


def h_search_docs(args: dict[str, Any]) -> dict[str, Any]:
    from .search import context_snippet, search_docs

    resolved = _resolve(args)
    catalog = _catalog(resolved)
    query = str(args["query"])
    top_k = int(args.get("top_k", 10))
    results = search_docs(resolved["root"], catalog, query, limit=top_k)
    if args.get("plain"):
        results = [
            {**r, "line": plain_text(r.get("line", "")), "name": plain_text(r.get("name", ""))}
            for r in results
        ]
    if args.get("context") and results:
        first = results[0]
        if first["line_no"]:
            snippet = context_snippet(resolved["root"], first["path"], first["line_no"])
            first = dict(first, context=plain_text(snippet) if args.get("plain") else snippet)
            results = [first, *results[1:]]
    return {"query": query, "count": len(results), "results": results}


def h_read_doc(args: dict[str, Any]) -> dict[str, Any]:
    resolved = _resolve(args)
    rel = _safe_rel(args["path"])
    path = resolved["root"] / rel
    if not path.is_file():
        return {"error": f"not found: {rel}"}
    text = path.read_text(encoding="utf-8", errors="replace")
    if args.get("section"):
        from .chunks import split_chunks

        want = str(args["section"]).lstrip("#").strip()
        lines = text.splitlines()
        for chunk in split_chunks(text):
            if chunk["heading"] == want:
                text = "\n".join(lines[chunk["start_line"] - 1 : chunk["end_line"]])
                break
    elif args.get("line_start"):
        lines = text.splitlines()
        start = max(1, int(args["line_start"]))
        end = int(args.get("line_end") or start + 200)
        text = "\n".join(lines[start - 1 : end])
    if args.get("plain"):
        text = plain_text(text)
    max_chars = int(args.get("max_chars", 20000))
    return {"path": rel, "chars": len(text), "truncated": len(text) > max_chars, "text": text[:max_chars]}


def h_doc_scan(args: dict[str, Any]) -> dict[str, Any]:
    from .catalog_diag import run_scan

    resolved = _resolve(args)
    result = run_scan(resolved["root"])
    result.pop("rules", None)
    return result


def h_audit_entry(args: dict[str, Any]) -> dict[str, Any]:
    from .roots import docagent_cmd

    resolved = _resolve(args)
    cmd = docagent_cmd(resolved["root"])
    if cmd is None:
        return {"ok": False, "error": "docagent 不可用（未安装且无 tools/docagent/run.py）"}
    doc = _safe_rel(args["doc"])
    argv = [*cmd, "audit-entry", "--root", str(resolved["root"]), "--doc", doc, "--json"]
    if args.get("entry"):
        argv += ["--entry", str(args["entry"])]
    elif args.get("anchor"):
        argv += ["--anchor", str(args["anchor"])]
    else:
        return {"ok": False, "error": "需要 entry 或 anchor 之一"}
    try:
        proc = subprocess.run(argv, capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=120, check=False)
    except (OSError, subprocess.TimeoutExpired) as exc:  # noqa: BLE001
        return {"ok": False, "error": f"subprocess: {exc!r}"}
    try:
        payload = json.loads(proc.stdout)
    except json.JSONDecodeError:
        return {"ok": False, "error": f"exit={proc.returncode}: {proc.stderr.strip()[:200]}"}
    return {"ok": True, "report": payload}


def h_doc_history(args: dict[str, Any]) -> dict[str, Any]:
    from .history import file_history

    resolved = _resolve(args)
    rel = _safe_rel(args["path"])
    return file_history(resolved["root"], rel, limit=int(args.get("limit", 30)))


def h_catalog_stats(args: dict[str, Any]) -> dict[str, Any]:
    from .stats import compute_stats

    resolved = _resolve(args)
    return compute_stats(_catalog(resolved))


def h_lib_list(args: dict[str, Any]) -> dict[str, Any]:
    from .roots import CONFIG_PATH, list_libraries, resolve_library

    payload: dict[str, Any] = {"config": str(CONFIG_PATH), "libraries": list_libraries()}
    try:
        resolved = resolve_library(args.get("root"), lib=args.get("lib"))
        payload["resolved"] = {"root": str(resolved["root"]), "docs_dir": str(resolved["docs_dir"])}
    except SystemExit as exc:
        payload["resolved"] = None
        payload["note"] = str(exc)
    return payload


HANDLERS: dict[str, Callable[[dict[str, Any]], Any]] = {
    "catalog_query": h_catalog_query,
    "search_docs": h_search_docs,
    "read_doc": h_read_doc,
    "doc_scan": h_doc_scan,
    "audit_entry": h_audit_entry,
    "doc_history": h_doc_history,
    "catalog_stats": h_catalog_stats,
    "lib_list": h_lib_list,
}


# --------------------------------------------------------------------------- #
# JSON-RPC / MCP 协议循环
# --------------------------------------------------------------------------- #
def _result(req_id: Any, payload: Any) -> dict[str, Any]:
    return {"jsonrpc": "2.0", "id": req_id, "result": payload}


def _error(req_id: Any, code: int, message: str) -> dict[str, Any]:
    return {"jsonrpc": "2.0", "id": req_id, "error": {"code": code, "message": message}}


def handle_message(msg: dict[str, Any]) -> dict[str, Any] | None:
    """处理一条 JSON-RPC 消息；通知返回 None。"""
    method = msg.get("method")
    req_id = msg.get("id")
    if method == "initialize":
        return _result(
            req_id,
            {
                "protocolVersion": PROTOCOL_VERSION,
                "capabilities": {"tools": {}},
                "serverInfo": {"name": SERVER_NAME, "version": SERVER_VERSION},
            },
        )
    if method in ("notifications/initialized", "initialized"):
        return None
    if method == "ping":
        return _result(req_id, {})
    if method == "tools/list":
        return _result(req_id, {"tools": TOOL_DEFS})
    if method == "tools/call":
        params = msg.get("params") or {}
        name = str(params.get("name"))
        args = params.get("arguments") or {}
        handler = HANDLERS.get(name)
        if handler is None:
            return _result(req_id, {"content": [{"type": "text", "text": f"unknown tool: {name}"}], "isError": True})
        try:
            payload = handler(args)
            text = json.dumps(payload, ensure_ascii=False, indent=2, default=str)
            return _result(req_id, {"content": [{"type": "text", "text": text}]})
        except SystemExit as exc:  # roots 引导信息
            return _result(req_id, {"content": [{"type": "text", "text": str(exc)}], "isError": True})
        except Exception as exc:  # noqa: BLE001 — 工具失败不崩协议
            return _result(req_id, {"content": [{"type": "text", "text": f"{type(exc).__name__}: {exc}"}], "isError": True})
    return _error(req_id, -32601, f"method not found: {method}")


def main() -> int:
    for raw in sys.stdin:
        line = raw.strip()
        if not line:
            continue
        try:
            msg = json.loads(line)
        except json.JSONDecodeError as exc:
            print(f"patchouli-mcp: bad json: {exc}", file=sys.stderr)
            continue
        response = handle_message(msg)
        if response is not None:
            sys.stdout.write(json.dumps(response, ensure_ascii=False) + "\n")
            sys.stdout.flush()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
