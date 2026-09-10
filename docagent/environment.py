"""Strict, secret-safe configuration for the standalone docagent package."""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from pathlib import Path, PurePosixPath, PureWindowsPath
from typing import Any
from urllib.parse import urlsplit


ENVIRONMENT_SCHEMA_VERSION = "qlh.docagent.environment.v1"
DEFAULT_ENV_FILENAME = ".env.docagent"
MAX_ENV_BYTES = 64 * 1024
SUPPORTED_PROVIDERS = frozenset({"opencode", "deepseek", "ollama"})
ENVIRONMENT_FIELDS = frozenset({
    "DOCAGENT_PROVIDER",
    "DOCAGENT_PROFILE",
    "DOCAGENT_DEEPSEEK_BASE_URL",
    "DOCAGENT_DEEPSEEK_MODEL",
    "DOCAGENT_DEEPSEEK_API_KEY",
    "DOCAGENT_OLLAMA_BASE_URL",
    "DOCAGENT_OLLAMA_MODEL",
    "DOCAGENT_CONFIDENCE_FLOOR",
})
SECRET_FIELDS = frozenset({"DOCAGENT_DEEPSEEK_API_KEY"})
DEFAULTS = {
    "DOCAGENT_PROVIDER": "ollama",
    "DOCAGENT_PROFILE": "qlh",
    "DOCAGENT_DEEPSEEK_BASE_URL": "https://opencode.ai/zen/go/v1",
    "DOCAGENT_DEEPSEEK_MODEL": "deepseek-v4-flash",
    "DOCAGENT_DEEPSEEK_API_KEY": "",
    "DOCAGENT_OLLAMA_BASE_URL": "http://127.0.0.1:11434/v1",
    "DOCAGENT_OLLAMA_MODEL": "gemma4:12b",
    "DOCAGENT_CONFIDENCE_FLOOR": "0.6",
}

_KEY_RE = re.compile(r"^[A-Z][A-Z0-9_]*$")
_PROFILE_NAME_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.-]{0,63}$")
_MODEL_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:/+@-]{0,199}$")
_SECRET_RE = re.compile(r"^sk-[^\s\x00-\x1f]{6,}$")
_SECRET_SHAPE_RE = re.compile(r"(?i)^(?:sk-|gh[pousr]_|hf_|bearer\b)")
_LOOPBACK_HOSTS = frozenset({"127.0.0.1", "localhost", "::1"})


class EnvironmentConfigError(ValueError):
    """Raised for a missing or unsafe dedicated docagent environment file."""


@dataclass(frozen=True)
class DocAgentEnvironment:
    provider: str
    profile: str
    deepseek_base_url: str
    deepseek_model: str
    deepseek_api_key: str = field(repr=False, compare=False)
    ollama_base_url: str = "http://127.0.0.1:11434/v1"
    ollama_model: str = "gemma4:12b"
    confidence_floor: float = 0.6
    explicit_fields: frozenset[str] = field(default_factory=frozenset, repr=False)
    source_path: Path = field(default=Path(DEFAULT_ENV_FILENAME), repr=False, compare=False)


def _error(message: str) -> EnvironmentConfigError:
    return EnvironmentConfigError(message)


def _strip_inline_comment(raw: str, line_number: int) -> str:
    quote: str | None = None
    escaped = False
    end = len(raw)
    for index, char in enumerate(raw):
        if escaped:
            escaped = False
            continue
        if char == "\\" and quote == '"':
            escaped = True
            continue
        if char in {"'", '"'}:
            if quote is None:
                quote = char
            elif quote == char:
                quote = None
        elif char == "#" and quote is None and (index == 0 or raw[index - 1].isspace()):
            end = index
            break
    if quote is not None:
        raise _error(f"第 {line_number} 行引号未闭合")
    value = raw[:end].strip()
    if value[:1] in {"'", '"'}:
        if len(value) < 2 or value[-1] != value[0]:
            raise _error(f"第 {line_number} 行引号格式无效")
        value = value[1:-1]
    return value


def _parse_environment(text: str) -> dict[str, str]:
    values: dict[str, str] = {}
    for line_number, raw_line in enumerate(text.splitlines(), start=1):
        line = raw_line.strip()
        if not line or line.startswith("#"):
            continue
        if line.startswith("export "):
            line = line[7:].lstrip()
        if "=" not in line:
            raise _error(f"第 {line_number} 行必须使用 KEY=value 格式")
        raw_key, raw_value = line.split("=", 1)
        key = raw_key.strip()
        if not _KEY_RE.fullmatch(key):
            raise _error(f"第 {line_number} 行字段名格式无效")
        if key not in ENVIRONMENT_FIELDS:
            raise _error(f"不支持的环境字段：{key}")
        if key in values:
            raise _error(f"环境字段重复：{key}")
        value = _strip_inline_comment(raw_value, line_number)
        if any(ord(char) < 32 and char not in {"\t"} for char in value):
            raise _error(f"环境字段 {key} 含控制字符")
        values[key] = value
    return values


def _validate_url(field_name: str, value: str, *, loopback_only: bool = False) -> str:
    try:
        parsed = urlsplit(value)
        port = parsed.port
    except ValueError as exc:
        raise _error(f"环境字段 {field_name} 不是有效 URL") from exc
    if (
        parsed.scheme not in {"http", "https"}
        or not parsed.hostname
        or parsed.username is not None
        or parsed.password is not None
        or parsed.query
        or parsed.fragment
        or port is not None and not 1 <= port <= 65535
    ):
        raise _error(f"环境字段 {field_name} 不是安全的 HTTP(S) base URL")
    hostname = parsed.hostname.lower()
    if loopback_only and hostname not in _LOOPBACK_HOSTS:
        raise _error(f"环境字段 {field_name} 必须指向 loopback")
    if not loopback_only and parsed.scheme != "https" and hostname not in _LOOPBACK_HOSTS:
        raise _error(f"环境字段 {field_name} 的非 loopback 地址必须使用 HTTPS")
    return value.rstrip("/")


def _validate_profile(value: str) -> str:
    candidate = value.strip()
    is_path = (
        "/" in candidate
        or "\\" in candidate
        or Path(candidate).suffix.lower() in {".yaml", ".yml", ".json"}
    )
    if not is_path:
        if not _PROFILE_NAME_RE.fullmatch(candidate):
            raise _error("环境字段 DOCAGENT_PROFILE 必须是 profile 名或安全相对路径")
        return candidate
    normalized = candidate.replace("\\", "/").strip()
    path = PurePosixPath(normalized)
    if (
        not normalized
        or path.is_absolute()
        or PureWindowsPath(normalized).is_absolute()
        or ".." in path.parts
        or "." in path.parts
    ):
        raise _error("环境字段 DOCAGENT_PROFILE 必须位于 env 文件目录内")
    return path.as_posix()


def _validate_model(field_name: str, value: str) -> str:
    selected = value.strip()
    if (
        not _MODEL_RE.fullmatch(selected)
        or "://" in selected
        or _SECRET_SHAPE_RE.search(selected) is not None
    ):
        raise _error(f"环境字段 {field_name} 必须是非空模型名")
    return selected


def load_environment(path: str | Path, *, required: bool = True) -> DocAgentEnvironment:
    """Read only the dedicated file; never merge the process environment or .env."""
    try:
        source = Path(path).expanduser().resolve()
    except (OSError, RuntimeError) as exc:
        raise _error("环境配置路径不可解析") from exc
    if not source.is_file():
        if required:
            raise _error(
                "环境配置文件不存在；请复制 .env.docagent.example 为 .env.docagent，"
                "或使用 --env 显式指定"
            )
        values: dict[str, str] = {}
    else:
        try:
            if source.stat().st_size > MAX_ENV_BYTES:
                raise _error("环境配置文件超过 64 KiB 上限")
            text = source.read_text(encoding="utf-8-sig")
        except EnvironmentConfigError:
            raise
        except (OSError, UnicodeError) as exc:
            raise _error("环境配置文件不可读取或不是 UTF-8") from exc
        values = _parse_environment(text)

    configured = {**DEFAULTS, **values}
    provider = configured["DOCAGENT_PROVIDER"].strip().lower()
    if provider not in SUPPORTED_PROVIDERS:
        raise _error("环境字段 DOCAGENT_PROVIDER 仅支持 opencode、deepseek 或 ollama")
    profile = _validate_profile(configured["DOCAGENT_PROFILE"])
    deepseek_base_url = _validate_url(
        "DOCAGENT_DEEPSEEK_BASE_URL", configured["DOCAGENT_DEEPSEEK_BASE_URL"]
    )
    deepseek_model = _validate_model(
        "DOCAGENT_DEEPSEEK_MODEL", configured["DOCAGENT_DEEPSEEK_MODEL"]
    )
    ollama_base_url = _validate_url(
        "DOCAGENT_OLLAMA_BASE_URL",
        configured["DOCAGENT_OLLAMA_BASE_URL"],
        loopback_only=True,
    )
    ollama_model = _validate_model(
        "DOCAGENT_OLLAMA_MODEL", configured["DOCAGENT_OLLAMA_MODEL"]
    )
    try:
        confidence_floor = float(configured["DOCAGENT_CONFIDENCE_FLOOR"])
    except ValueError as exc:
        raise _error("环境字段 DOCAGENT_CONFIDENCE_FLOOR 必须是 0 到 1 的数字") from exc
    if not 0.0 <= confidence_floor <= 1.0:
        raise _error("环境字段 DOCAGENT_CONFIDENCE_FLOOR 必须在 0 到 1 之间")

    api_key = configured["DOCAGENT_DEEPSEEK_API_KEY"]
    if api_key and not _SECRET_RE.fullmatch(api_key):
        raise _error("环境字段 DOCAGENT_DEEPSEEK_API_KEY 格式无效")
    if provider in {"opencode", "deepseek"}:
        missing = [
            field_name
            for field_name in (
                "DOCAGENT_DEEPSEEK_BASE_URL",
                "DOCAGENT_DEEPSEEK_MODEL",
                "DOCAGENT_DEEPSEEK_API_KEY",
            )
            if field_name not in values or not values[field_name]
        ]
        if missing:
            raise _error(f"远程 provider 缺少必需字段：{', '.join(missing)}")

    return DocAgentEnvironment(
        provider=provider,
        profile=profile,
        deepseek_base_url=deepseek_base_url,
        deepseek_model=deepseek_model,
        deepseek_api_key=api_key,
        ollama_base_url=ollama_base_url,
        ollama_model=ollama_model,
        confidence_floor=confidence_floor,
        explicit_fields=frozenset(values),
        source_path=source,
    )


def discover_environment(
    root: str | Path,
    explicit_path: str | Path | None = None,
) -> DocAgentEnvironment | None:
    """Load an explicit env fail-closed, or an existing project env if present."""
    try:
        repo_root = Path(root).expanduser().resolve()
    except (OSError, RuntimeError) as exc:
        raise _error("仓库根目录不可解析") from exc
    if explicit_path is not None:
        return load_environment(explicit_path, required=True)
    candidate = repo_root / DEFAULT_ENV_FILENAME
    return load_environment(candidate, required=True) if candidate.is_file() else None


def profile_reference(config: DocAgentEnvironment) -> str | Path:
    """Resolve an env-selected profile path relative to the env file, not cwd."""
    value = config.profile
    if "/" in value or "\\" in value or Path(value).suffix.lower() in {".yaml", ".yml", ".json"}:
        parent = config.source_path.parent.resolve()
        candidate = (parent / value).resolve()
        try:
            candidate.relative_to(parent)
        except ValueError as exc:
            raise _error("环境字段 DOCAGENT_PROFILE 解析后越出 env 文件目录") from exc
        return candidate
    return value


def _source(config: DocAgentEnvironment, field_name: str) -> str:
    return "file" if field_name in config.explicit_fields else "default"


def public_environment(config: DocAgentEnvironment, *, source: str) -> dict[str, Any]:
    """Return a stable summary that cannot contain the API key or endpoint path."""
    return {
        "schema_version": ENVIRONMENT_SCHEMA_VERSION,
        "status": "valid",
        "source": source,
        "provider": {"value": config.provider, "source": _source(config, "DOCAGENT_PROVIDER")},
        "profile": {"value": config.profile, "source": _source(config, "DOCAGENT_PROFILE")},
        "remote": {
            "enabled": config.provider in {"opencode", "deepseek"},
            "base_url": {
                "configured": bool(config.deepseek_base_url),
                "source": _source(config, "DOCAGENT_DEEPSEEK_BASE_URL"),
                "transport": urlsplit(config.deepseek_base_url).scheme,
            },
            "model": {
                "value": config.deepseek_model,
                "source": _source(config, "DOCAGENT_DEEPSEEK_MODEL"),
            },
            "api_key": {
                "configured": bool(config.deepseek_api_key),
                "source": _source(config, "DOCAGENT_DEEPSEEK_API_KEY"),
                "redacted": True,
            },
        },
        "ollama": {
            "base_url": {
                "configured": bool(config.ollama_base_url),
                "source": _source(config, "DOCAGENT_OLLAMA_BASE_URL"),
                "scope": "loopback",
            },
            "model": {
                "value": config.ollama_model,
                "source": _source(config, "DOCAGENT_OLLAMA_MODEL"),
            },
        },
        "confidence_floor": {
            "value": config.confidence_floor,
            "source": _source(config, "DOCAGENT_CONFIDENCE_FLOOR"),
        },
        "defaults_applied": sorted(set(DEFAULTS) - config.explicit_fields - SECRET_FIELDS),
    }


def render_environment_json(config: DocAgentEnvironment, *, source: str) -> str:
    return json.dumps(public_environment(config, source=source), ensure_ascii=False, indent=1) + "\n"


def render_environment_text(config: DocAgentEnvironment, *, source: str) -> str:
    summary = public_environment(config, source=source)
    key_state = "已配置（值不显示）" if summary["remote"]["api_key"]["configured"] else "未配置"
    defaults = ", ".join(summary["defaults_applied"]) or "无"
    return (
        f"环境配置有效：{source}\n"
        f"provider={config.provider} ({summary['provider']['source']})\n"
        f"profile={config.profile} ({summary['profile']['source']})\n"
        f"remote_api_key={key_state}\n"
        f"ollama=loopback model={config.ollama_model}\n"
        f"confidence_floor={config.confidence_floor}\n"
        f"使用默认值的字段：{defaults}\n"
    )


__all__ = [
    "DEFAULT_ENV_FILENAME",
    "DEFAULTS",
    "ENVIRONMENT_FIELDS",
    "ENVIRONMENT_SCHEMA_VERSION",
    "DocAgentEnvironment",
    "EnvironmentConfigError",
    "discover_environment",
    "load_environment",
    "profile_reference",
    "public_environment",
    "render_environment_json",
    "render_environment_text",
]
