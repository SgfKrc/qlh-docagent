"""Patchouli MCP server 测试：协议层 / 工具 handlers / 端到端 stdio。"""
from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from patchouli.mcp_server import TOOL_DEFS, handle_message  # noqa: E402

REPO_ROOT = Path(__file__).resolve().parents[4]


def _fixture(tmp_path: Path) -> None:
    docs = tmp_path / "docs"
    docs.mkdir()
    (docs / "甲专项计划.md").write_text(
        "# 甲\n> 状态：现行\n> 更新日期：2026-09-01\n\n前缀缓存机制。票 `EX-CACHE-01`。\n",
        encoding="utf-8",
    )
    (docs / "乙报告.md").write_text("# 乙\n> 状态：历史参考\n\n缓存实验结论。\n", encoding="utf-8")
    (docs / "丙说明.md").write_text("# 丙\n\n无状态行。\n", encoding="utf-8")


def _call(name: str, args: dict) -> dict:
    resp = handle_message({"jsonrpc": "2.0", "id": 9, "method": "tools/call", "params": {"name": name, "arguments": args}})
    assert resp is not None and "result" in resp, resp
    payload = json.loads(resp["result"]["content"][0]["text"])
    return payload


def test_initialize_and_capabilities() -> None:
    resp = handle_message({"jsonrpc": "2.0", "id": 1, "method": "initialize"})
    assert resp["result"]["protocolVersion"] == "2024-11-05"
    assert resp["result"]["serverInfo"]["name"] == "patchouli"
    assert "tools" in resp["result"]["capabilities"]


def test_tools_list_eight_readonly() -> None:
    names = {t["name"] for t in TOOL_DEFS}
    assert names == {
        "catalog_query",
        "search_docs",
        "read_doc",
        "doc_scan",
        "audit_entry",
        "doc_history",
        "catalog_stats",
        "lib_list",
    }


def test_ping_and_unknown_method_and_tool() -> None:
    assert handle_message({"jsonrpc": "2.0", "id": 2, "method": "ping"})["result"] == {}
    err = handle_message({"jsonrpc": "2.0", "id": 3, "method": "nope"})
    assert err["error"]["code"] == -32601
    bad = handle_message({"jsonrpc": "2.0", "id": 4, "method": "tools/call", "params": {"name": "rm_rf"}})
    assert bad["result"]["isError"] is True


def test_catalog_query_filters(tmp_path: Path) -> None:
    _fixture(tmp_path)
    root = str(tmp_path)
    full = _call("catalog_query", {"root": root})
    assert full["total"] == 3
    assert _call("catalog_query", {"root": root, "missing_status": True})["total"] == 1
    assert _call("catalog_query", {"root": root, "ticket": "EX-CACHE-01"})["total"] == 1
    assert _call("catalog_query", {"root": root, "kind": "report"})["total"] == 1


def test_search_and_read_doc(tmp_path: Path) -> None:
    _fixture(tmp_path)
    root = str(tmp_path)
    found = _call("search_docs", {"root": root, "query": "缓存"})
    assert found["count"] >= 2
    doc = _call("read_doc", {"root": root, "path": "docs/甲专项计划.md", "line_start": 1, "line_end": 2})
    assert doc["text"].startswith("# 甲")
    escape = handle_message(
        {"jsonrpc": "2.0", "id": 9, "method": "tools/call", "params": {"name": "read_doc", "arguments": {"root": root, "path": "../secret.md"}}}
    )
    assert escape["result"]["isError"] is True
    assert "非法路径" in escape["result"]["content"][0]["text"]


def test_stats_and_lib_list(tmp_path: Path) -> None:
    _fixture(tmp_path)
    stats = _call("catalog_stats", {"root": str(tmp_path)})
    assert stats["total"] == 3 and stats["status_missing"] == 1
    libs = _call("lib_list", {})
    assert isinstance(libs["libraries"], list)


def test_scan_missing_root_fail_soft() -> None:
    resp = handle_message(
        {"jsonrpc": "2.0", "id": 9, "method": "tools/call", "params": {"name": "doc_scan", "arguments": {"root": str(Path("Z:/definitely/not/exist"))}}}
    )
    assert resp["result"]["isError"] is True  # 引导信息（root 不存在）


def test_real_repo_smoke() -> None:
    payload = _call("catalog_query", {"root": str(REPO_ROOT), "limit": 3})
    assert payload["total"] >= 70
    assert len(payload["documents"]) == 3


def test_stdio_end_to_end() -> None:
    lines = [
        json.dumps({"jsonrpc": "2.0", "id": 1, "method": "initialize"}),
        json.dumps({"jsonrpc": "2.0", "id": 2, "method": "tools/list"}),
        json.dumps({"jsonrpc": "2.0", "id": 3, "method": "tools/call", "params": {"name": "catalog_stats", "arguments": {"root": str(REPO_ROOT)}}}),
    ]
    proc = subprocess.run(
        [sys.executable, "-m", "patchouli.mcp_server"],
        input="\n".join(lines) + "\n",
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=120,
        check=False,
        cwd=str(REPO_ROOT),
    )
    assert proc.returncode == 0, proc.stderr[:300]
    responses = [json.loads(line) for line in proc.stdout.splitlines() if line.strip()]
    assert len(responses) == 3
    assert responses[0]["result"]["serverInfo"]["name"] == "patchouli"
    assert len(responses[1]["result"]["tools"]) == 8
    stats = json.loads(responses[2]["result"]["content"][0]["text"])
    assert stats["total"] >= 70
