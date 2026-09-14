"""统一 CLI 入口测试：分派路由 + splash_delay 纯函数。"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

import pytest  # noqa: E402

from patchouli.cli import main  # noqa: E402
from patchouli.splash import splash_delay  # noqa: E402


def _fixture(tmp_path: Path) -> None:
    docs = tmp_path / "docs"
    docs.mkdir()
    (docs / "甲专项计划.md").write_text("# 甲\n> 状态：现行\n", encoding="utf-8")


def test_splash_delay_bounds() -> None:
    assert splash_delay(0.2, 1.0) == pytest.approx(0.8)
    assert splash_delay(1.5, 1.0) == 0.0
    assert splash_delay(0.0, 0.0) == 0.0
    assert splash_delay(10.0, 1.0) == 0.0


def test_cli_summary_route(tmp_path: Path, capsys) -> None:
    _fixture(tmp_path)
    assert main(["summary", "--root", str(tmp_path)]) == 0
    out = capsys.readouterr().out
    assert "docs: 1" in out


def test_cli_json_route(tmp_path: Path, capsys) -> None:
    _fixture(tmp_path)
    assert main(["json", "--root", str(tmp_path)]) == 0
    out = capsys.readouterr().out
    assert '"qlh.patchouli.catalog.v1"' in out


def test_cli_passthrough_to_catalog(tmp_path: Path, capsys) -> None:
    _fixture(tmp_path)
    assert main(["--root", str(tmp_path)]) == 0  # 无子命令 → 直通 catalog（摘要）
    out = capsys.readouterr().out
    assert "docs: 1" in out


def test_cli_no_args_non_tty_falls_back_summary(tmp_path: Path, capsys, monkeypatch) -> None:
    _fixture(tmp_path)
    monkeypatch.chdir(tmp_path)
    assert main([]) == 0  # pytest 环境非 TTY → summary（不起 TUI）
    out = capsys.readouterr().out
    assert "docs: 1" in out


def test_cli_help() -> None:
    assert main(["help"]) == 0
