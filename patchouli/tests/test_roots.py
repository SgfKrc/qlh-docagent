"""roots.resolve_root 测试：显式 / 环境变量 / cwd 上溯 / 配置 / 报错 + setup 写入。"""
from __future__ import annotations

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

import pytest  # noqa: E402

from patchouli import roots  # noqa: E402


def _docs(tmp_path: Path) -> Path:
    docs = tmp_path / "docs"
    docs.mkdir(parents=True, exist_ok=True)
    (docs / "x.md").write_text("# x\n", encoding="utf-8")
    return tmp_path


def test_explicit_root(tmp_path: Path) -> None:
    _docs(tmp_path)
    assert roots.resolve_root(tmp_path) == tmp_path.resolve()


def test_cwd_ancestor(tmp_path: Path) -> None:
    _docs(tmp_path)
    deep = tmp_path / "a" / "b"
    deep.mkdir(parents=True)
    assert roots.resolve_root(None, cwd=deep) == tmp_path.resolve()


def test_env_var(tmp_path: Path, monkeypatch) -> None:
    _docs(tmp_path)
    monkeypatch.setenv("PATCHOULI_ROOT", str(tmp_path))
    empty = tmp_path / "empty"
    empty.mkdir()
    assert roots.resolve_root(None, cwd=empty) == tmp_path.resolve()


def test_config_fallback(tmp_path: Path, monkeypatch) -> None:
    target = _docs(tmp_path / "repo")
    cfg = tmp_path / "config.json"
    cfg.write_text(json.dumps({"default_root": str(target)}), encoding="utf-8")
    monkeypatch.setattr(roots, "CONFIG_PATH", cfg)
    empty = tmp_path / "empty"
    empty.mkdir()
    monkeypatch.delenv("PATCHOULI_ROOT", raising=False)
    assert roots.resolve_root(None, cwd=empty) == target.resolve()


def test_missing_all_raises(tmp_path: Path, monkeypatch) -> None:
    cfg = tmp_path / "none.json"
    monkeypatch.setattr(roots, "CONFIG_PATH", cfg)
    monkeypatch.delenv("PATCHOULI_ROOT", raising=False)
    empty = tmp_path / "empty"
    empty.mkdir()
    with pytest.raises(SystemExit) as exc:
        roots.resolve_root(None, cwd=empty)
    assert "未找到文档库" in str(exc.value)


def test_write_config_roundtrip(tmp_path: Path, monkeypatch) -> None:
    cfg = tmp_path / "sub" / "config.json"
    monkeypatch.setattr(roots, "CONFIG_PATH", cfg)
    path = roots.write_config(tmp_path)
    assert path == cfg and cfg.is_file()
    assert json.loads(cfg.read_text(encoding="utf-8"))["default_root"] == str(tmp_path.resolve())
