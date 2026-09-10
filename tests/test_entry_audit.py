from __future__ import annotations

import json
import shutil
import subprocess
import sys
from pathlib import Path

import pytest


PACKAGE_ROOT = Path(__file__).parents[1]
sys.path.insert(0, str(PACKAGE_ROOT))

from docagent.cli import main
from docagent.entry_audit import EntryAuditError, audit_entry
from docagent.scanner import scan_repository


def _write(path: Path, text: str) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")
    return path


def _project(tmp_path: Path, body: str) -> Path:
    root = tmp_path / "project"
    _write(root / "docs" / "plan.md", body)
    return root


def _git(root: Path, *args: str) -> None:
    subprocess.run(["git", *args], cwd=root, check=True, capture_output=True, text=True)


def _init_git(root: Path) -> None:
    if shutil.which("git") is None:
        pytest.skip("git is unavailable")
    _git(root, "init")
    _git(root, "config", "user.email", "docagent@example.invalid")
    _git(root, "config", "user.name", "Docagent Test")


def test_anchor_audit_is_scoped_and_reuses_r1(tmp_path):
    root = _project(
        tmp_path,
        "# 计划\n\n> 状态：规划\n> 更新日期：2026-01-01\n\n"
        "## 无关章节\n\n`src/unrelated.py` 已完成。\n\n"
        "## 交付项\n\n✅ 已完成；实现见 `src/feature.py`。\n",
    )
    _write(root / "src" / "feature.py", "FEATURE = True\n")

    report = audit_entry(root, doc="docs/plan.md", anchor="#交付项", profile="minimal")

    assert report["schema_version"] == "qlh.docagent.entry-audit.v1"
    assert report["read_only"] is True
    assert report["tests_executed"] is False
    assert report["target"]["anchor"] == "交付项"
    assert [item["path"] for item in report["evidence"]["paths"]] == ["src/feature.py"]
    assert any(item["rule"] == "R1" for item in report["signals"]["findings"])
    assert any(item["code"] == "R1_TARGETED_SIGNAL" for item in report["differences"])
    assert "src/unrelated.py" not in json.dumps(report, ensure_ascii=False)


def test_duplicate_heading_uses_github_style_numeric_anchor(tmp_path):
    root = _project(
        tmp_path,
        "# Plan\n\n## Repeat\nfirst\n\n## Repeat\nsecond\n",
    )

    report = audit_entry(root, doc="docs/plan.md", anchor="repeat-1", profile="minimal")

    assert report["target"]["start_line"] == 6
    assert report["claims"]["lines"] == [{"line": 7, "text": "second"}]


def test_entry_selector_fails_closed_on_ambiguity_and_supports_occurrence(tmp_path):
    root = _project(
        tmp_path,
        "# 票据\n\n> 状态：规划\n\n"
        "| 编号 | 内容 | 状态 |\n|---|---|---|\n"
        "| DOC-1 | first | 规划 |\n| DOC-1 | second | 已完成 |\n",
    )

    with pytest.raises(EntryAuditError, match="ambiguous"):
        audit_entry(root, doc="docs/plan.md", entry="DOC-1", profile="minimal")

    report = audit_entry(
        root, doc="docs/plan.md", entry="DOC-1", occurrence=2, profile="minimal",
    )

    assert report["target"]["selector"]["occurrence"] == 2
    assert report["claims"]["lifecycle"]["state"] == "completed"
    assert report["claims"]["lines"][0]["text"].endswith("已完成 |")


@pytest.mark.parametrize("doc", ["../secret.md", "/absolute.md", "C:/secret.md"])
def test_document_path_must_be_repository_relative(tmp_path, doc):
    root = _project(tmp_path, "# Plan\n\n## Entry\ntext\n")

    with pytest.raises(EntryAuditError, match="repository-relative"):
        audit_entry(root, doc=doc, anchor="entry", profile="minimal")


def test_missing_required_evidence_and_unbound_test_result_are_reported(tmp_path):
    root = _project(
        tmp_path,
        "# 验收\n\n> 状态：已完成\n\n## 结果\n\n测试为 12 passed, 2 skipped。\n",
    )

    report = audit_entry(
        root,
        doc="docs/plan.md",
        anchor="结果",
        evidence=["build/results/junit.xml"],
        profile="minimal",
    )

    codes = {item["code"] for item in report["differences"]}
    assert "EVIDENCE_PATH_MISSING" in codes
    assert "TEST_RESULT_UNBOUND" in codes
    assert report["claims"]["test_results"][0]["counts"] == {"passed": 12, "skipped": 2}


def test_json_test_artifact_binds_matching_counts(tmp_path):
    root = _project(
        tmp_path,
        "# 验收\n\n> 状态：已完成\n\n## 结果\n\n测试为 12 passed, 2 skipped。\n",
    )
    _write(root / "test-results" / "result.json", '{"summary":{"passed":12,"skipped":2}}\n')

    report = audit_entry(
        root, doc="docs/plan.md", anchor="结果",
        evidence=["test-results/result.json"], profile="minimal",
    )

    codes = {item["code"] for item in report["differences"]}
    assert "TEST_RESULT_UNBOUND" not in codes
    assert "TEST_RESULT_MISMATCH" not in codes
    artifact = report["evidence"]["paths"][0]
    assert artifact["test_result"] == {
        "status": "parsed", "counts": {"passed": 12, "skipped": 2},
    }


def test_test_artifact_count_mismatch_is_warn(tmp_path):
    root = _project(
        tmp_path,
        "# 验收\n\n> 状态：已完成\n\n## 结果\n\n测试为 12 passed。\n",
    )
    _write(root / "test-results" / "result.json", '{"summary":{"passed":11}}\n')

    report = audit_entry(
        root, doc="docs/plan.md", anchor="结果",
        evidence=["test-results/result.json"], profile="minimal",
    )

    mismatch = next(item for item in report["differences"] if item["code"] == "TEST_RESULT_MISMATCH")
    assert mismatch["level"] == "warn"


def test_junit_artifact_derives_passed_count(tmp_path):
    root = _project(
        tmp_path,
        "# Acceptance\n\n## Result\n\nTests: 3 passed, 1 skipped.\n",
    )
    _write(
        root / "test-results" / "junit.xml",
        '<testsuite tests="4" failures="0" errors="0" skipped="1"></testsuite>\n',
    )

    report = audit_entry(
        root, doc="docs/plan.md", anchor="result",
        evidence=["test-results/junit.xml"], profile="minimal",
    )

    codes = {item["code"] for item in report["differences"]}
    assert "TEST_RESULT_MISMATCH" not in codes
    assert report["evidence"]["paths"][0]["test_result"]["counts"]["passed"] == 3


def test_explicit_evidence_rejects_traversal(tmp_path):
    root = _project(tmp_path, "# Plan\n\n## Entry\ntext\n")

    with pytest.raises(EntryAuditError, match="traversal"):
        audit_entry(
            root, doc="docs/plan.md", anchor="entry",
            evidence=["docs/../secret.json"], profile="minimal",
        )


def test_completed_claim_reports_dirty_evidence_and_targeted_r3(tmp_path):
    root = _project(
        tmp_path,
        "# Runtime\n\n> 状态：已完成\n> 更新日期：2020-01-01\n\n"
        "## 验收\n\n已完成 `src/runtime.py`，测试 `tests/test_runtime.py`：1 passed。\n",
    )
    _write(root / "src" / "runtime.py", "READY = False\n")
    test_path = _write(root / "tests" / "test_runtime.py", "def test_runtime():\n    assert True\n")
    _init_git(root)
    _git(root, "add", "docs/plan.md", "src/runtime.py", "tests/test_runtime.py")
    _git(root, "commit", "-m", "initial evidence")
    test_path.write_text("def test_runtime():\n    assert 1 == 1\n", encoding="utf-8")

    report = audit_entry(root, doc="docs/plan.md", anchor="验收", profile="qlh")

    codes = {item["code"] for item in report["differences"]}
    assert "COMPLETED_CLAIM_WITH_DIRTY_STATE" in codes, json.dumps(report, ensure_ascii=False, indent=1)
    assert "R3_TARGETED_SIGNAL" in codes
    assert "EVIDENCE_NEWER_THAN_DOCUMENT_DATE" in codes
    test_evidence = next(item for item in report["evidence"]["paths"] if item["path"] == "tests/test_runtime.py")
    assert test_evidence["worktree"]["dirty"] is True
    assert report["evidence"]["git_available"] is True


def test_cli_json_output_and_fail_policy(tmp_path, capsys):
    root = _project(
        tmp_path,
        "# 验收\n\n> 状态：已完成\n\n## 结果\n\n实现见 `src/missing.py`。\n",
    )
    output = root / "build" / "docagent" / "entry.json"

    result = main([
        "audit-entry", "--root", str(root), "--doc", "docs/plan.md",
        "--anchor", "结果", "--profile", "minimal", "--json",
        "--output", str(output), "--fail-on", "warn",
    ])

    captured = capsys.readouterr()
    assert result == 1, captured.out
    payload = json.loads(captured.out)
    assert payload["summary"]["status"] == "review_required"
    assert output.is_file()
    assert str(tmp_path) not in output.read_text(encoding="utf-8")


def test_cli_rejects_output_inside_documentation_tree(tmp_path, capsys):
    root = _project(tmp_path, "# Plan\n\n## Entry\ntext\n")

    result = main([
        "audit-entry", "--root", str(root), "--doc", "docs/plan.md",
        "--anchor", "entry", "--profile", "minimal",
        "--output", str(root / "docs" / "entry.json"), "--fail-on", "none",
    ])

    assert result == 2
    assert "cannot be inside target docs" in capsys.readouterr().err


def test_cli_uses_dedicated_environment_profile(tmp_path, capsys):
    root = _project(tmp_path, "# Plan\n\n## Entry\ntext\n")
    _write(root / ".env.docagent", "DOCAGENT_PROFILE=minimal\n")

    result = main([
        "audit-entry", "--root", str(root), "--doc", "docs/plan.md",
        "--anchor", "entry", "--json", "--fail-on", "none",
    ])

    assert result == 0
    assert json.loads(capsys.readouterr().out)["profile"]["name"] == "minimal"


def test_unstaged_document_keeps_porcelain_leading_state_for_r2(tmp_path):
    root = _project(tmp_path, "# Plan\n\n> 状态：已完成\n")
    _init_git(root)
    _git(root, "add", "docs/plan.md")
    _git(root, "commit", "-m", "initial docs")
    _write(root / "docs" / "plan.md", "# Plan\n\n> 状态：已完成\n\nchanged\n")

    report = scan_repository(root, profile="qlh")

    assert any(item["rule"] == "R2" for item in report["docs"][0]["findings"])


def test_claim_output_redacts_secret_and_absolute_path(tmp_path):
    root = _project(
        tmp_path,
        "# Plan\n\n## Entry\n\n"
        "token sk-very-secret-value at C:/Users/private/result.log\n",
    )

    report = audit_entry(root, doc="docs/plan.md", anchor="entry", profile="minimal")
    serialized = json.dumps(report, ensure_ascii=False)

    assert "sk-very-secret-value" not in serialized
    assert "C:/Users/private" not in serialized
    assert "<redacted-secret>" in serialized
    assert "<absolute-path>" in serialized
