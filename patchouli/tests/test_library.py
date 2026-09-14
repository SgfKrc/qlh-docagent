"""通用化 v2 测试：文档目录探测 / 多库注册 / docagent 定位。"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

import pytest  # noqa: E402

from patchouli import roots  # noqa: E402
from patchouli.catalog import scan  # noqa: E402


def _mk(tmp_path: Path, rel: str, count: int = 1) -> Path:
    d = tmp_path / rel if rel else tmp_path
    d.mkdir(parents=True, exist_ok=True)
    for i in range(count):
        (d / f"d{i}.md").write_text(f"# d{i}\n> 状态：现行\n", encoding="utf-8")
    return d


def test_discover_variants(tmp_path: Path) -> None:
    assert roots.discover_docs_dir(tmp_path / "none") is None
    _mk(tmp_path, "a/docs")
    assert roots.discover_docs_dir(tmp_path / "a").name == "docs"
    _mk(tmp_path, "b/documentation")
    assert roots.discover_docs_dir(tmp_path / "b").name == "documentation"
    assert roots.discover_docs_dir(_mk(tmp_path, "c", count=3)) == tmp_path / "c"
    assert roots.discover_docs_dir(_mk(tmp_path, "d", count=2)) is None  # 裸目录需 >=3 md


def test_scan_bare_md_dir(tmp_path: Path) -> None:
    _mk(tmp_path, "", count=3)
    assert scan(tmp_path)["doc_count"] == 3  # 无 docs/ 也能扫（通用化）


def test_scan_explicit_docs_dir(tmp_path: Path) -> None:
    d = _mk(tmp_path, "mydocs", count=2)  # 非标准目录名
    assert scan(tmp_path, docs_dir=d)["doc_count"] == 2


def test_library_registry_roundtrip(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setattr(roots, "CONFIG_PATH", tmp_path / "cfg" / "config.json")
    repo = _mk(tmp_path / "repo", "docs", 2).parent
    entry = roots.add_library(repo)
    assert entry["name"] == "repo"
    assert roots.list_libraries()[0]["root"] == str(repo.resolve())
    resolved = roots.resolve_library(lib="repo")
    assert resolved["root"] == repo.resolve() and resolved["docs_dir"].name == "docs"
    assert roots.remove_library("repo") is True
    assert roots.list_libraries() == []
    assert roots.remove_library("repo") is False


def test_add_library_rejects_non_docs(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setattr(roots, "CONFIG_PATH", tmp_path / "c.json")
    empty = tmp_path / "empty"
    empty.mkdir()
    with pytest.raises(SystemExit):
        roots.add_library(empty)


def test_docagent_cmd_installed_preferred(monkeypatch, tmp_path: Path) -> None:
    monkeypatch.setattr(roots.importlib.util, "find_spec", lambda name: object() if name == "docagent" else None)
    cmd = roots.docagent_cmd(tmp_path)
    assert cmd is not None and cmd[1:] == ["-m", "docagent"]


def test_docagent_cmd_fallback_runpy(monkeypatch, tmp_path: Path) -> None:
    monkeypatch.setattr(roots.importlib.util, "find_spec", lambda name: None)
    runner = tmp_path / "tools" / "docagent"
    runner.mkdir(parents=True)
    (runner / "run.py").write_text("# stub\n", encoding="utf-8")
    cmd = roots.docagent_cmd(tmp_path)
    assert cmd is not None and cmd[-1].endswith("run.py")


def test_docagent_cmd_none(monkeypatch, tmp_path: Path) -> None:
    monkeypatch.setattr(roots.importlib.util, "find_spec", lambda name: None)
    assert roots.docagent_cmd(tmp_path) is None
