"""仓库根解析（任意目录可用）：显式 > 环境变量 > cwd 上溯 > 用户配置 > 报错引导。

用户配置文件：`~/.patchouli/config.json` 的 `default_root`（安装脚本自动写入）。
"""
from __future__ import annotations

import json
import os
from pathlib import Path

ENV_VAR = "PATCHOULI_ROOT"
CONFIG_PATH = Path.home() / ".patchouli" / "config.json"


def _has_docs(path: Path) -> bool:
    docs = path / "docs"
    return docs.is_dir() and any(docs.glob("*.md"))


def _load_config_root() -> Path | None:
    try:
        payload = json.loads(CONFIG_PATH.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    root = payload.get("default_root")
    if isinstance(root, str) and root.strip():
        candidate = Path(root).expanduser()
        return candidate if candidate.is_dir() else None
    return None


def resolve_root(explicit: str | Path | None = None, *, cwd: Path | None = None) -> Path:
    """解析目标仓库根；找不到时给出引导性报错。"""
    if explicit:
        candidate = Path(explicit).expanduser().resolve()
        if candidate.is_dir():
            return candidate
        raise SystemExit(f"--root 不存在或不是目录: {candidate}")

    env_root = os.environ.get(ENV_VAR)
    if env_root and env_root.strip():
        candidate = Path(env_root).expanduser()
        if candidate.is_dir():
            return candidate.resolve()

    start = (Path(cwd) if cwd else Path.cwd()).resolve()
    for candidate in (start, *start.parents):
        if _has_docs(candidate):
            return candidate

    config_root = _load_config_root()
    if config_root is not None:
        return config_root.resolve()

    raise SystemExit(
        "未找到文档库（docs/）。请任选其一：\n"
        f"  1) 在含 docs/ 的目录（或其父目录）下执行；\n"
        f"  2) 显式指定：--root <仓库根>；\n"
        f"  3) 设置环境变量 {ENV_VAR}=<仓库根>；\n"
        f"  4) 运行安装脚本自动写入默认根：python -m patchouli.setup 或 tools/docagent/patchouli/install.ps1"
    )


def write_config(default_root: str | Path) -> Path:
    """写入用户默认根配置（安装脚本使用）。"""
    CONFIG_PATH.parent.mkdir(parents=True, exist_ok=True)
    payload: dict[str, str] = {}
    try:
        loaded = json.loads(CONFIG_PATH.read_text(encoding="utf-8"))
        if isinstance(loaded, dict):
            payload.update({k: v for k, v in loaded.items() if isinstance(v, str)})
    except (OSError, json.JSONDecodeError):
        pass
    payload["default_root"] = str(Path(default_root).resolve())
    CONFIG_PATH.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    return CONFIG_PATH
