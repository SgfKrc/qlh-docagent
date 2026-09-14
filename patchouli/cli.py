"""Patchouli 统一入口（patchouli 命令 / python -m patchouli）。

用法：
    patchouli                 # 书架 TUI（非 TTY 自动退回 summary）
    patchouli shelf           # 书架 TUI（显式）
    patchouli summary         # 馆藏摘要
    patchouli json            # 全量结构化 JSON
    patchouli <任意 catalog 参数>   # 直通 catalog（如 --root X --summary）
"""
from __future__ import annotations

import sys
from pathlib import Path
from typing import Any


def main(argv: Any = None) -> int:
    args = list(sys.argv[1:] if argv is None else argv)
    if args and args[0] in {"shelf", "bookshelf", "tui"}:
        from .bookshelf import main as shelf_main

        return shelf_main(args[1:])
    if args and args[0] == "summary":
        from .catalog import main as catalog_main

        return catalog_main(args[1:] + ["--summary"])
    if args and args[0] == "json":
        from .catalog import main as catalog_main

        return catalog_main(args[1:] + ["--json"])
    if args and args[0] == "lib":
        from .roots import add_library, list_libraries, remove_library

        sub = args[1] if len(args) > 1 else "list"
        if sub == "add" and len(args) > 2:
            import argparse as _argparse

            parser = _argparse.ArgumentParser(prog="patchouli lib add")
            parser.add_argument("path")
            parser.add_argument("--name", default=None)
            ns = parser.parse_args(args[2:])
            entry = add_library(ns.path, ns.name)
            print(f"已注册: {entry['name']} -> {entry['root']}")
        elif sub == "remove" and len(args) > 2:
            ok = remove_library(args[2])
            print("已移除: " + args[2] if ok else f"未找到库: {args[2]}")
        else:
            libs = list_libraries()
            for lib in libs:
                print(f"- {lib['name']}  {lib['root']}")
            if not libs:
                print("（未注册库；patchouli lib add <库根>）")
        return 0
    if args and args[0] == "setup":
        from .roots import resolve_root, write_config

        import argparse as _argparse

        parser = _argparse.ArgumentParser(prog="patchouli setup", description="写入用户默认根配置（~/.patchouli/config.json）")
        parser.add_argument("--root", type=Path, default=None)
        ns = parser.parse_args(args[1:])
        root = resolve_root(ns.root)
        path = write_config(root)
        print(f"默认根已写入: {path}")
        print(f"default_root = {root}")
        return 0
    if args and args[0] in {"help", "-h", "--help"}:
        print(__doc__)
        return 0
    if not args:
        # 无参：TTY → 书架；非 TTY（管道/脚本）→ summary
        try:
            interactive = sys.stdout.isatty() and sys.stdin.isatty()
        except Exception:  # noqa: BLE001
            interactive = False
        if interactive:
            from .bookshelf import main as shelf_main

            return shelf_main([])
        from .catalog import main as catalog_main

        return catalog_main(["--summary"])
    from .catalog import main as catalog_main

    return catalog_main(args)


if __name__ == "__main__":
    raise SystemExit(main())
