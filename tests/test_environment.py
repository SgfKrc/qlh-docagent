from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest


PACKAGE_ROOT = Path(__file__).parents[1]
sys.path.insert(0, str(PACKAGE_ROOT))

from docagent.cli import main
from docagent.compat import run_compat
from docagent.environment import (
    EnvironmentConfigError,
    load_environment,
    profile_reference,
    public_environment,
    render_environment_json,
)


def _write(path: Path, text: str) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")
    return path


def _project(tmp_path: Path) -> Path:
    project = tmp_path / "project"
    docs = project / "docs"
    docs.mkdir(parents=True)
    (docs / "README.md").write_text("# Docs\n\n> 状态：规划\n", encoding="utf-8")
    return project


def test_required_missing_file_has_chinese_creation_hint_without_absolute_path(tmp_path):
    missing = tmp_path / "private" / ".env.docagent"

    with pytest.raises(EnvironmentConfigError) as exc_info:
        load_environment(missing)

    message = str(exc_info.value)
    assert "环境配置文件不存在" in message
    assert ".env.docagent.example" in message
    assert str(tmp_path) not in message


def test_local_profile_applies_safe_defaults_and_field_sources(tmp_path):
    env_path = _write(tmp_path / ".env.docagent", "DOCAGENT_PROVIDER=ollama\n")

    config = load_environment(env_path)
    public = public_environment(config, source=".env.docagent")

    assert config.provider == "ollama"
    assert config.profile == "qlh"
    assert config.ollama_base_url == "http://127.0.0.1:11434/v1"
    assert public["provider"] == {"value": "ollama", "source": "file"}
    assert public["profile"] == {"value": "qlh", "source": "default"}
    assert "DOCAGENT_PROFILE" in public["defaults_applied"]


def test_remote_key_never_appears_in_repr_or_public_json(tmp_path):
    secret = "sk-super-secret-material"
    env_path = _write(
        tmp_path / ".env.docagent",
        "DOCAGENT_PROVIDER=opencode\n"
        "DOCAGENT_DEEPSEEK_BASE_URL=https://example.invalid/v1\n"
        "DOCAGENT_DEEPSEEK_MODEL=deepseek-flash\n"
        f"DOCAGENT_DEEPSEEK_API_KEY={secret}\n",
    )

    config = load_environment(env_path)
    output = render_environment_json(config, source=".env.docagent")

    assert secret not in repr(config)
    assert secret not in output
    assert "example.invalid" not in output
    assert json.loads(output)["remote"]["api_key"] == {
        "configured": True,
        "source": "file",
        "redacted": True,
    }


def test_invalid_values_are_not_echoed_in_field_errors(tmp_path):
    invalid = "sk-value-that-must-not-leak"
    env_path = _write(tmp_path / ".env.docagent", f"DOCAGENT_PROVIDER={invalid}\n")

    with pytest.raises(EnvironmentConfigError, match="DOCAGENT_PROVIDER") as exc_info:
        load_environment(env_path)

    assert invalid not in str(exc_info.value)


@pytest.mark.parametrize(
    "body,field",
    [
        ("DOCAGENT_PROVIDER=ollama\nDOCAGENT_PROVIDER=ollama\n", "DOCAGENT_PROVIDER"),
        ("DOCAGENT_PROVDER=ollama\n", "DOCAGENT_PROVDER"),
        ("DOCAGENT_CONFIDENCE_FLOOR=1.1\n", "DOCAGENT_CONFIDENCE_FLOOR"),
        ("DOCAGENT_OLLAMA_BASE_URL=https://example.invalid/v1\n", "DOCAGENT_OLLAMA_BASE_URL"),
        ("DOCAGENT_PROFILE=../private.yaml\n", "DOCAGENT_PROFILE"),
    ],
)
def test_field_level_validation_fails_closed(tmp_path, body, field):
    env_path = _write(tmp_path / ".env.docagent", body)

    with pytest.raises(EnvironmentConfigError, match=field):
        load_environment(env_path)


def test_incomplete_remote_profile_names_missing_field_only(tmp_path):
    env_path = _write(tmp_path / ".env.docagent", "DOCAGENT_PROVIDER=opencode\n")

    with pytest.raises(EnvironmentConfigError) as exc_info:
        load_environment(env_path)

    assert "DOCAGENT_DEEPSEEK_API_KEY" in str(exc_info.value)
    assert "sk-" not in str(exc_info.value)


def test_remote_profile_requires_all_connection_fields_to_be_explicit(tmp_path):
    env_path = _write(
        tmp_path / ".env.docagent",
        "DOCAGENT_PROVIDER=opencode\n"
        "DOCAGENT_DEEPSEEK_API_KEY=sk-explicit-secret\n",
    )

    with pytest.raises(EnvironmentConfigError) as exc_info:
        load_environment(env_path)

    message = str(exc_info.value)
    assert "DOCAGENT_DEEPSEEK_BASE_URL" in message
    assert "DOCAGENT_DEEPSEEK_MODEL" in message
    assert "sk-explicit-secret" not in message


def test_inline_comments_and_quoted_values_are_supported(tmp_path):
    env_path = _write(
        tmp_path / ".env.docagent",
        "export DOCAGENT_PROVIDER=ollama # local only\n"
        "DOCAGENT_OLLAMA_MODEL='model-variant'\n",
    )

    config = load_environment(env_path)

    assert config.provider == "ollama"
    assert config.ollama_model == "model-variant"


def test_profile_path_is_relative_to_explicit_env_not_current_directory(tmp_path):
    env_dir = tmp_path / "config"
    env_path = _write(env_dir / "team.env", "DOCAGENT_PROFILE=profiles/minimal.yaml\n")
    _write(env_dir / "profiles" / "minimal.yaml", "{}")

    reference = profile_reference(load_environment(env_path))

    assert reference == (env_dir / "profiles" / "minimal.yaml").resolve()


def test_process_environment_and_main_dotenv_are_never_merged(monkeypatch, tmp_path):
    secret = "sk-process-secret"
    monkeypatch.setenv("DOCAGENT_DEEPSEEK_API_KEY", secret)
    _write(tmp_path / ".env", f"DOCAGENT_DEEPSEEK_API_KEY={secret}\n")
    env_path = _write(tmp_path / ".env.docagent", "DOCAGENT_PROVIDER=ollama\n")

    config = load_environment(env_path)

    assert config.deepseek_api_key == ""
    assert secret not in repr(config)


def test_model_field_rejects_secret_shaped_or_whitespace_values(tmp_path):
    env_path = _write(
        tmp_path / ".env.docagent",
        "DOCAGENT_OLLAMA_MODEL=sk-secret-accident\n",
    )

    with pytest.raises(EnvironmentConfigError, match="DOCAGENT_OLLAMA_MODEL") as exc_info:
        load_environment(env_path)

    assert "sk-secret-accident" not in str(exc_info.value)


def test_config_cli_missing_file_fails_closed_in_chinese(capsys, tmp_path):
    result = main(["config", "--root", str(tmp_path)])

    captured = capsys.readouterr()
    assert result == 2
    assert "环境配置错误" in captured.err
    assert "环境配置文件不存在" in captured.err
    assert str(tmp_path) not in captured.err


def test_config_cli_json_is_redacted(capsys, tmp_path):
    project = _project(tmp_path)
    secret = "sk-never-print-this"
    _write(
        project / ".env.docagent",
        "DOCAGENT_PROVIDER=opencode\n"
        "DOCAGENT_PROFILE=minimal\n"
        "DOCAGENT_DEEPSEEK_BASE_URL=https://example.invalid/v1\n"
        "DOCAGENT_DEEPSEEK_MODEL=deepseek-flash\n"
        f"DOCAGENT_DEEPSEEK_API_KEY={secret}\n",
    )

    result = main(["config", "--root", str(project), "--json"])

    captured = capsys.readouterr()
    assert result == 0
    assert secret not in captured.out + captured.err
    assert json.loads(captured.out)["source"] == ".env.docagent"


def test_scan_uses_env_profile_but_cli_profile_has_precedence(capsys, tmp_path):
    project = _project(tmp_path)
    env_path = _write(project / "config" / "team.env", "DOCAGENT_PROFILE=minimal\n")

    result = main([
        "scan", "--root", str(project), "--env", str(env_path),
        "--json", "--fail-on", "none",
    ])
    env_report = json.loads(capsys.readouterr().out)
    override = main([
        "scan", "--root", str(project), "--env", str(env_path),
        "--profile", "qlh", "--json", "--fail-on", "none",
    ])
    cli_report = json.loads(capsys.readouterr().out)

    assert result == override == 0
    assert env_report["profile"]["name"] == "minimal"
    assert cli_report["profile"]["name"] == "qlh"


def test_scan_without_env_remains_backward_compatible(capsys, tmp_path):
    project = _project(tmp_path)

    result = main(["scan", "--root", str(project), "--profile", "minimal", "--json", "--fail-on", "none"])

    assert result == 0
    assert json.loads(capsys.readouterr().out)["profile"]["name"] == "minimal"


def test_explicit_missing_env_blocks_scan_without_leaking_path(capsys, tmp_path):
    project = _project(tmp_path)
    missing = tmp_path / "secret-location" / "team.env"

    result = main([
        "scan", "--root", str(project), "--env", str(missing),
        "--profile", "minimal", "--fail-on", "none",
    ])

    captured = capsys.readouterr()
    assert result == 2
    assert "环境配置文件不存在" in captured.err
    assert str(missing) not in captured.err


def test_env_profile_load_error_does_not_leak_absolute_path(capsys, tmp_path):
    project = _project(tmp_path)
    env_path = _write(
        project / "private-config" / "team.env",
        "DOCAGENT_PROFILE=profiles/missing.yaml\n",
    )

    result = main([
        "scan", "--root", str(project), "--env", str(env_path), "--fail-on", "none",
    ])

    captured = capsys.readouterr()
    assert result == 2
    assert "DOCAGENT_PROFILE" in captured.err
    assert str(tmp_path) not in captured.err


def test_compat_env_profile_error_is_redacted(capsys, tmp_path):
    project = _project(tmp_path)
    env_path = _write(
        project / "private-config" / "team.env",
        "DOCAGENT_PROFILE=profiles/missing.yaml\n",
    )

    result = run_compat(["--env", str(env_path), "--fail-on", "none"], project)

    captured = capsys.readouterr()
    assert result == 2
    assert "DOCAGENT_PROFILE" in captured.err
    assert str(tmp_path) not in captured.err
