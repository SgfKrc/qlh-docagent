"""文档库解析与注册（通用化 v2）：任意目录的任意文档库都能被管理。

- 文档目录探测：`docs/` > `documentation/` > `doc/` > 目录本身（≥3 个 md）；
- 库解析优先级：`--root` > `--lib`（注册名/路径） > `PATCHOULI_ROOT` >
  当前目录上溯探测 > `~/.patchouli/config.json` 的 `default_root` > 注册表首个库；
- 多库注册：`add_library / list_libraries / remove_library`（`patchouli lib add|list|remove`）；
- docagent 定位：已安装（`python -m docagent`）优先，退回 `<root>/tools/docagent/run.py`。
"""
from __future__ import annotations

import importlib.util
import json
import os
import sys
from pathlib import Path

ENV_VAR = "PATCHOULI_ROOT"
CONFIG_PATH = Path.home() / ".patchouli" / "config.json"
DOC_DIR_CANDIDATES = ("docs", "documentation", "doc")
BARE_MD_THRESHOLD = 3


# ---------- 文档目录探测 ----------

def discover_docs_dir(root: Path) -> Path | None:
    """在 root 下探测文档目录（返回目录路径；不存在返回 None）。"""
    root = Path(root)
    if not root.is_dir():
        return None
    for name in DOC_DIR_CANDIDATES:
        candidate = root / name
        if candidate.is_dir() and any(candidate.glob("*.md")):
            return candidate
    if len(list(root.glob("*.md"))) >= BARE_MD_THRESHOLD:
        return root
    return None


# ---------- 配置读写（v2：default_root + libraries[]） ----------

def _load_config() -> dict:
    try:
        payload = json.loads(CONFIG_PATH.read_text(encoding="utf-8"))
        return payload if isinstance(payload, dict) else {}
    except (OSError, json.JSONDecodeError):
        return {}


def _save_config(payload: dict) -> None:
    CONFIG_PATH.parent.mkdir(parents=True, exist_ok=True)
    CONFIG_PATH.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")


def list_libraries() -> list[dict]:
    libs = _load_config().get("libraries")
    return [lib for lib in libs if isinstance(lib, dict) and lib.get("root")] if isinstance(libs, list) else []


def add_library(root: str | Path, name: str | None = None) -> dict:
    """注册文档库（必须探测到文档目录）；返回库条目。"""
    root_path = Path(root).expanduser().resolve()
    docs = discover_docs_dir(root_path)
    if docs is None:
        raise SystemExit(f"不是文档库（未探测到 docs/ 或 ≥{BARE_MD_THRESHOLD} 个 md 文件）: {root_path}")
    entry = {"name": name or root_path.name, "root": str(root_path)}
    payload = _load_config()
    libs = [lib for lib in list_libraries() if lib.get("name") != entry["name"]]
    libs.append(entry)
    payload["libraries"] = libs
    payload.setdefault("default_root", str(root_path))
    _save_config(payload)
    return entry


def remove_library(name: str) -> bool:
    payload = _load_config()
    libs = list_libraries()
    remaining = [lib for lib in libs if lib.get("name") != name]
    if len(remaining) == len(libs):
        return False
    payload["libraries"] = remaining
    _save_config(payload)
    return True


def write_config(default_root: str | Path) -> Path:
    """写入默认根（安装脚本使用；保留既有 libraries）。"""
    payload = _load_config()
    payload["default_root"] = str(Path(default_root).resolve())
    _save_config(payload)
    return CONFIG_PATH


# ---------- 库解析 ----------

def _from_registry(name_or_path: str) -> Path | None:
    for lib in list_libraries():
        if lib.get("name") == name_or_path:
            candidate = Path(lib["root"]).expanduser()
            if candidate.is_dir():
                return candidate
    path_candidate = Path(name_or_path).expanduser()
    if path_candidate.is_dir() and discover_docs_dir(path_candidate) is not None:
        return path_candidate.resolve()
    return None


def resolve_library(
    explicit_root: str | Path | None = None,
    *,
    lib: str | None = None,
    cwd: Path | None = None,
) -> dict:
    """返回 {"root": Path, "docs_dir": Path}。"""
    if explicit_root:
        root = Path(explicit_root).expanduser().resolve()
        if not root.is_dir():
            raise SystemExit(f"--root 不存在或不是目录: {root}")
        docs = discover_docs_dir(root)
        if docs is None:
            raise SystemExit(f"--root 下未探测到文档目录（docs/ 或 ≥{BARE_MD_THRESHOLD} 个 md）: {root}")
        return {"root": root, "docs_dir": docs}

    if lib:
        found = _from_registry(lib)
        if found is None:
            raise SystemExit(f"未注册的库（用 `patchouli lib add` 注册，或直接用 --root）: {lib}")
        return {"root": found, "docs_dir": discover_docs_dir(found)}

    env_root = os.environ.get(ENV_VAR)
    if env_root and env_root.strip():
        candidate = Path(env_root).expanduser()
        docs = discover_docs_dir(candidate)
        if docs is not None:
            return {"root": candidate.resolve(), "docs_dir": docs}

    start = (Path(cwd) if cwd else Path.cwd()).resolve()
    for candidate in (start, *start.parents):
        docs = discover_docs_dir(candidate)
        if docs is not None:
            return {"root": candidate, "docs_dir": docs}

    config = _load_config()
    default_root = config.get("default_root")
    if isinstance(default_root, str) and default_root.strip():
        candidate = Path(default_root).expanduser()
        docs = discover_docs_dir(candidate)
        if docs is not None:
            return {"root": candidate.resolve(), "docs_dir": docs}

    libs = list_libraries()
    if libs:
        candidate = Path(libs[0]["root"]).expanduser()
        docs = discover_docs_dir(candidate)
        if docs is not None:
            return {"root": candidate.resolve(), "docs_dir": docs}

    raise SystemExit(
        "未找到文档库。任选其一：\n"
        "  1) 在文档目录（或其父目录）下执行；\n"
        "  2) --root <库根>（含 docs/ 或 ≥3 个 md）；\n"
        f"  3) 设置 {ENV_VAR}=<库根>；\n"
        "  4) patchouli lib add <库根>（注册后可用 --lib <名字>）；\n"
        "  5) powershell -File tools/docagent/patchouli/install.ps1（写入默认根）"
    )


def resolve_root(explicit: str | Path | None = None, *, cwd: Path | None = None) -> Path:
    """兼容包装：仅返回库根。"""
    return resolve_library(explicit, cwd=cwd)["root"]


# ---------- docagent 定位 ----------

def docagent_cmd(root: Path) -> list[str] | None:
    """定位 docagent：已安装（python -m docagent）优先；退回 <root>/tools/docagent/run.py。"""
    try:
        if importlib.util.find_spec("docagent") is not None:
            return [sys.executable, "-m", "docagent"]
    except (ImportError, ValueError):
        pass
    runner = Path(root) / "tools" / "docagent" / "run.py"
    if runner.is_file():
        return [sys.executable, str(runner)]
    return None
