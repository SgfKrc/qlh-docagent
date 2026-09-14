"""splash v5 渲染测试：grid 尺寸 / 逐格双色 / 打字机 / 扫描线。"""
from __future__ import annotations

import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from patchouli.splash import GRID, GRID_ROWS, GRID_W, UPPER, render_logo_markup, splash_delay  # noqa: E402


def _blocks(text: str) -> int:
    return len(re.sub(r"\[[^\]]+\]", "", text).replace(" ", "").strip())


def test_grid_shape() -> None:
    assert (GRID_W, GRID_ROWS) == (35, 5)
    assert all(len(row) == GRID_W for row in GRID)


def test_dual_color_per_cell() -> None:
    text = render_logo_markup(GRID)
    assert "#0d0a10 on #7b4fc0" in text  # 逐格：上黑（▀前景）下紫（背景）
    assert "white on #7b4fc0" in text  # 底沿白描边
    assert text.splitlines()[4].count("white on #7b4fc0") >= 5
    assert UPPER in text


def test_typing_reveal_cuts_columns() -> None:
    full = render_logo_markup(GRID)
    part = render_logo_markup(GRID, -1, 6)
    assert _blocks(part) < _blocks(full)
    assert part.splitlines()[0].startswith("[#0d0a10 on #7b4fc0]" + UPPER * 3)


def test_scan_row_highlight() -> None:
    assert "#c9a0ff on #7b4fc0" in render_logo_markup(GRID, scan_row=2)
    assert "#c9a0ff on #7b4fc0" not in render_logo_markup(GRID, scan_row=-1)


def test_splash_delay_contract_kept() -> None:
    assert splash_delay(0.2, 1.0) > 0 and splash_delay(2.0, 1.0) == 0.0
