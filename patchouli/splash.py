"""PATCH-08 启动动画（splash v5）——逐格半块像素字 + 扫描线 + 打字机。

视觉设计（色孽配色：紫 #7b4fc0 / 黑 #0d0a10 / 白）：
- 标题 "PATCHOULI" 用 **▀ 半块字符逐格上色**：每个像素格 = 上半黑 / 下半紫
  （`▀` fg=#0d0a10 on bg=#7b4fc0）——**颜色贴合字形、无底色溢出**；
  字形最底行像素用白色（上白下紫）作为**下沿白描边**；
- 动画（极客 + Undertale 风）：打字机逐列显现 + **扫描线**（亮紫半块自上下扫）；
- 状态行：窄紫条（3 行）+ spinner 转圈；任意键跳过。

硬约束不变：与数据加载并行、加载完即关（不延长启动）；--no-splash 可关。
"""
from __future__ import annotations

import time

from textual.app import ComposeResult
from textual.containers import Center, Vertical
from textual.screen import ModalScreen
from textual.widgets import Static

# ---- 5x3 像素字形（# 实心）----
GLYPHS: dict[str, list[str]] = {
    "P": ["###", "#.#", "###", "#..", "#.."],
    "A": ["###", "#.#", "###", "#.#", "#.#"],
    "T": ["###", ".#.", ".#.", ".#.", ".#."],
    "C": ["###", "#..", "#..", "#..", "###"],
    "H": ["#.#", "#.#", "###", "#.#", "#.#"],
    "O": ["###", "#.#", "#.#", "#.#", "###"],
    "U": ["#.#", "#.#", "#.#", "#.#", "###"],
    "L": ["#..", "#..", "#..", "#..", "###"],
    "I": ["###", ".#.", ".#.", ".#.", "###"],
}

COLOR_TOP = "#0d0a10"     # 像素上半：黑
COLOR_BOTTOM = "#7b4fc0"  # 像素下半：紫（色孽）
COLOR_SCAN = "#c9a0ff"    # 扫描线：亮紫
COLOR_EDGE = "white"      # 下沿描边：白
UPPER = "\u2580"          # ▀ 上半块（fg 占上半、bg 占下半）

WORD = "PATCHOULI"
GRID_H = 5


def splash_delay(elapsed: float, min_show: float) -> float:
    """需补足的等待秒数：加载快于最小展示时补齐（可感知）；加载更慢则不额外等待。"""
    return max(0.0, float(min_show) - float(elapsed))


def _build_grid(word: str = WORD) -> list[list[str]]:
    """字形网格（F=实心，空=透明）；不含膨胀描边（颜色贴合字形）。"""
    width = len(word) * 4 - 1
    grid = [[" "] * width for _ in range(GRID_H)]
    for i, ch in enumerate(word):
        glyph = GLYPHS[ch]
        for r in range(GRID_H):
            for c in range(3):
                if glyph[r][c] == "#":
                    grid[r][i * 4 + c] = "F"
    return grid


GRID = _build_grid()
GRID_W = len(GRID[0])
GRID_ROWS = len(GRID)
LAST_ROW = GRID_ROWS - 1


def _style_for(row: int, scan_row: int) -> str:
    if row == scan_row:
        return f"{COLOR_SCAN} on {COLOR_BOTTOM}"
    if row == LAST_ROW:
        return f"{COLOR_EDGE} on {COLOR_BOTTOM}"
    return f"{COLOR_TOP} on {COLOR_BOTTOM}"


def render_logo_markup(grid: list[list[str]], scan_row: int = -1, revealed_cols: int = 10**9) -> str:
    """网格 → Textual markup（RLE 合并同色）：打字机（revealed_cols）+ 扫描线（scan_row）。"""
    rows = []
    for r in range(len(grid)):
        style = _style_for(r, scan_row)
        parts: list[str] = []
        run = 0
        for c in range(len(grid[0])):
            if grid[r][c] == "F" and c < revealed_cols:
                run += 1
            else:
                if run:
                    parts.append(f"[{style}]{UPPER * run}[/]")
                    run = 0
                parts.append(" ")
        if run:
            parts.append(f"[{style}]{UPPER * run}[/]")
        rows.append("".join(parts))
    return "\n".join(rows)


FRAMES = ("/", "-", "\\", "|")
TICK_SECONDS = 0.08
TYPING_COLS_PER_TICK = 3.75  # 打字速度（相对原速 1.25x）
SIGNATURE = "Minne ist wân, haz ist tump."  # 启动签名（中古高地德语）
SIG_CHARS_PER_TICK = 2  # 签名流式速度（字符/tick，从左往右）
SIG_CURSOR = "▌"  # ▌
SCAN_STEP_TICKS = 2  # 每 2 tick 扫描线下移一行
HOLD_TICKS = 10  # 扫描完毕后静止 hold（看清标题）


class SplashScreen(ModalScreen):
    """启动屏：PATCHOULI 半块像素字（上黑下紫、底沿白）+ 扫描线 + 窄紫条。"""

    CSS = """
    SplashScreen { background: #000000 90%; }
    #splash-box { border: round white; background: #0d0a10; padding: 0 3; height: auto; }
    #splash-logo { width: auto; margin: 1 0 0 0; }
    #splash-sign { color: #a37fd0; height: 1; margin-top: 1; width: 1fr; text-align: center; }
    #splash-line { background: #7b4fc0; color: white; height: 3; margin-top: 1; content-align: center middle; }
    """

    def __init__(self, status: str = "启动中…", min_show: float = 1.0, **kwargs):
        super().__init__(**kwargs)
        self.status_text = status
        self.min_show = float(min_show)
        self._frame = 0
        self._timer = None
        self._t0: float | None = None
        self._loaded = False
        self._typing_done_frame: int | None = None
        self._sig_done_frame: int | None = None

    def compose(self) -> ComposeResult:
        with Center():
            with Vertical(id="splash-box"):
                yield Static(render_logo_markup(GRID, -1, TYPING_COLS_PER_TICK * 2), id="splash-logo")
                yield Static("", id="splash-sign", markup=False)
                yield Static("", id="splash-line", markup=False)

    def on_mount(self) -> None:
        self._t0 = time.monotonic()
        self._render_line()
        self._timer = self.set_interval(TICK_SECONDS, self._tick)

    def _tick(self) -> None:
        self._frame += 1
        revealed = min(GRID_W, int(TYPING_COLS_PER_TICK * (self._frame + 2)))
        typing_done = revealed >= GRID_W
        if typing_done and self._typing_done_frame is None:
            self._typing_done_frame = self._frame
        sig_revealed = min(len(SIGNATURE), SIG_CHARS_PER_TICK * self._frame)
        if sig_revealed >= len(SIGNATURE) and self._sig_done_frame is None:
            self._sig_done_frame = self._frame
        try:
            sign_text = SIGNATURE[:sig_revealed]
            if sig_revealed < len(SIGNATURE):
                sign_text += SIG_CURSOR
            self.query_one("#splash-sign", Static).update(sign_text)
        except Exception:  # noqa: BLE001 — 屏幕已卸载
            pass
        scan_row = -1
        if typing_done:
            since = self._frame - self._typing_done_frame
            if since < GRID_ROWS * SCAN_STEP_TICKS:  # 扫描一轮后进入静止 hold
                scan_row = (since // SCAN_STEP_TICKS) % GRID_ROWS
        try:
            self.query_one("#splash-logo", Static).update(render_logo_markup(GRID, scan_row, revealed))
        except Exception:  # noqa: BLE001 — 屏幕已卸载
            pass
        self._render_line()
        self._maybe_finish()

    def _anim_done(self) -> bool:
        if self._typing_done_frame is None or self._sig_done_frame is None:
            return False
        title_hold = (self._frame - self._typing_done_frame) >= (GRID_ROWS * SCAN_STEP_TICKS + HOLD_TICKS)
        sig_hold = (self._frame - self._sig_done_frame) >= HOLD_TICKS
        return title_hold and sig_hold

    def notify_loaded(self) -> None:
        """数据加载完成：动画播完（打字+扫描+hold）且 min_show 满足后自行关闭。"""
        self._loaded = True
        self._maybe_finish()

    def _maybe_finish(self) -> None:
        if not (self._loaded and self._anim_done()):
            return
        if self._t0 is not None and (time.monotonic() - self._t0) < self.min_show:
            return
        try:
            self.dismiss()
        except Exception:  # noqa: BLE001
            pass

    def _render_line(self) -> None:
        spin = FRAMES[self._frame % len(FRAMES)]
        try:
            self.query_one("#splash-line", Static).update(f"  [{spin}] {self.status_text}    （任意键跳过）")
        except Exception:  # noqa: BLE001 — 屏幕已卸载
            pass

    def set_status(self, text: str) -> None:
        self.status_text = text
        self._render_line()

    def on_key(self) -> None:
        self.dismiss()

    def on_click(self) -> None:
        self.dismiss()
